"""機器・メンテナンス項目・実施記録の画面表示とフォーム保存を担当する。"""

from contextlib import contextmanager, asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from calendar import monthrange
import math
import json
from io import BytesIO
from pathlib import Path
import sqlite3
import qrcode
from qrcode.exceptions import DataOverflowError
from typing import Annotated, Iterator, Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import http_exception_handler


# 「変数名: 型 = 値」は型注釈付きの代入。型注釈だけで値が変換されるわけではない。
# strは文字列、intは整数、floatは小数を扱う数値。エディターで型を確認する目印にもなる。
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 単独モジュールとしての起動・パッケージ経由の起動の両方に対応。
    if __package__:
        from .init_db import init_db
    else:
        from init_db import init_db
    # 画面が読み書きするDBと、初期化するDBを一致させる。
    init_db(DB_PATH)
    yield


app = FastAPI(lifespan=lifespan)

# __file__ はこのファイルのパス。resolve() で絶対パスにし、parent で親フォルダを得る。
# 起動した場所に左右されないよう、DB・HTML・CSSの場所を main.py 基準で指定する。
BASE_DIR: Path = Path(__file__).resolve().parent
DB_PATH: Path = BASE_DIR / "maintenance.db"
QR_CONFIG_PATH: Path = BASE_DIR / "config" / "default.json"

# /static/... へのリクエストを static フォルダ内のファイルに対応付ける。
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates: Jinja2Templates = Jinja2Templates(directory=BASE_DIR / "templates")
UNITS = {"day": "日", "month": "か月", "year": "年",
         "km": "km", "hour": "時間", "count": "回"}
PRIORITIES = {"低": "低", "中": "中", "高": "高",
              "low": "低", "medium": "中", "high": "高"}
STATUSES = ("稼働中", "休止中", "故障中", "廃止")
SCOPES = ("対象", "非該当", "未設定")


def asset_url(filename: str) -> str:
    # 更新したCSS/JSをブラウザーが古いキャッシュで表示しないようにする。
    version = (BASE_DIR / "static" / filename).stat().st_mtime_ns
    return f"/static/{filename}?v={version}"


templates.env.globals.update(units=UNITS, priorities=PRIORITIES,
                             statuses=STATUSES, scopes=SCOPES, asset_url=asset_url)


@contextmanager
def database() -> Iterator[sqlite3.Connection]:
    """処理ごとに接続し、成功時は確定、例外時は取り消して必ず接続を閉じる。"""
    # -> は関数が返す型の注釈。ここはyieldを使うため、生成する値の型をIteratorで表す。
    # @contextmanagerがこの関数をwithで使える形にし、as connectionへ接続を渡す。
    connection: sqlite3.Connection = sqlite3.connect(DB_PATH)
    # Row を使うと、列番号ではなく列名で値を参照できる。
    # Jinja2 でも device.name のように参照できるため、辞書への詰め直しは不要。
    connection.row_factory = sqlite3.Row
    # Rowは辞書そのものではない。Pythonではrow["name"]またはrow[0]で値を取り出す。ここはwiki(jinja2とSQlite3を使うときの割と便利なtips)で詳細書いている。
    # 標準のrow_factoryでは1行はtuple。この設定以降に作るカーソルではRowになる。
    try:
        # Connection の with はトランザクションを管理するが、接続自体は閉じない。
        with connection:
            # yield の間に、呼び出し側の with ブロックが実行される。
            yield connection
    finally:
        # return や HTTPException で処理が途中終了しても実行される。
        connection.close()


def today() -> date:
    """日本時間の今日。テストではこの関数だけを固定する。"""
    return datetime.now(timezone(timedelta(hours=9))).date()


def next_date(base: date, value: int, unit: str) -> date:
    if unit == "day":
        return base + timedelta(days=value)
    months = value * 12 if unit == "year" else value
    year, month = divmod(base.year * 12 + base.month - 1 + months, 12)
    month += 1
    return date(year, month, min(base.day, monthrange(year, month)[1]))


def scheduled_date(plan) -> Optional[date]:
    """最新実施日＋周期。未実施なら初回予定日を使う。"""
    if plan["interval_unit"] not in ("day", "month", "year"):
        return None
    try:
        value = int(plan["interval_value"])
        if value < 1:
            return None
        if plan["last_performed"]:
            return next_date(date.fromisoformat(plan["last_performed"]), value, plan["interval_unit"])
        return date.fromisoformat(plan["first_due_date"]) if plan["first_due_date"] else None
    except (TypeError, ValueError, OverflowError):
        return None


@app.get("/")
def home(request: Request) -> HTMLResponse:
    current = today()
    next_month = next_date(current.replace(day=1), 1, "month")
    following_month = next_date(next_month, 1, "month")
    with database() as connection:
        device_count = connection.execute(
            "SELECT COUNT(*) FROM devices").fetchone()[0]
        plans = connection.execute("""
            SELECT p.*, d.name AS device, d.location,
                   (SELECT MAX(r.performed_at) FROM maintenance_records r
                    WHERE r.maintenance_plan_id = p.id) AS last_performed
            FROM maintenance_plans p JOIN devices d ON d.id = p.device_id
            WHERE d.status = '稼働中' AND d.maintenance_scope != '非該当'
            ORDER BY p.id
        """).fetchall()
        recent_history = connection.execute("""
            SELECT r.*, p.name AS task, d.name AS device, d.id AS device_id
            FROM maintenance_records r
            JOIN maintenance_plans p ON p.id = r.maintenance_plan_id
            JOIN devices d ON d.id = p.device_id
            ORDER BY r.performed_at DESC, r.id DESC LIMIT 20
        """).fetchall()
    maintenance_list, next_month_list, overdue_list, undated_list = [], [], [], []
    for plan in plans:
        item = dict(plan)
        due = scheduled_date(plan)
        if due is None:
            item["reason"] = ("日付では管理しない周期" if plan["interval_unit"] in ("km", "hour", "count")
                              else "初回予定日・周期を確認してください")
            undated_list.append(item)
            continue
        item["date"] = due.isoformat()
        if due < current:
            overdue_list.append(item)
        elif due < next_month:
            maintenance_list.append(item)
        elif due < following_month:
            next_month_list.append(item)
    for items in (maintenance_list, next_month_list, overdue_list):
        items.sort(key=lambda item: (item["date"], item["id"]))
    return templates.TemplateResponse(request=request, name="index.html", context={
        "device_count": device_count, "maintenance_list": maintenance_list,
        "next_month_list": next_month_list, "overdue_list": overdue_list,
        "undated_list": undated_list, "recent_history": recent_history,
        "this_month": f"{current.year}年{current.month}月",
        "next_month": f"{next_month.year}年{next_month.month}月",
    })


@app.get("/devices")
def device_list(request: Request) -> HTMLResponse:
    """登録機器をID順に表示する。"""
    with database() as connection:
        # execute() はカーソルを返すため、別途 cursor() を作らず結果を取得できる。
        cursor: sqlite3.Cursor = connection.execute("""
            SELECT id, name, manufacturer, model_number, location, purchase_date,
                   note, status, maintenance_scope
            FROM devices
            ORDER BY id
        """)
        # 各Rowは機器1件。devices[0]は先頭行、devices[0]["name"]はその機器名。
        # 0件ではdevices[0]を参照できないが、forで空リストを回すのは問題ない。
        devices: list[sqlite3.Row] = cursor.fetchall()

    return templates.TemplateResponse(
        request=request, name="device_list.html", context={"devices": devices}
    )


# /devices/new は /devices/{device_id} より先に登録する。
# 固定文字列の new を、数値の device_id として解釈させないため。
@app.get("/devices/new")
def device_new(request: Request) -> HTMLResponse:
    """機器登録画面を返す。表示には device_new.html が必要。"""
    return templates.TemplateResponse(request=request, name="device_new.html")


@app.post("/devices/new")
def create_device(
    name: str = Form(...),
    manufacturer: str = Form(""),
    model_number: str = Form(""),
    location: str = Form(""),
    purchase_date: str = Form(""),
    note: Annotated[str, Form()] = "",
    status: Annotated[str, Form()] = "稼働中",
    maintenance_scope: Annotated[str, Form()] = "未設定"
) -> RedirectResponse:
    """機器登録フォームの値を保存し、トップページへ戻す。"""
    # : str は型注釈。FastAPI は型と Form の指定を使って入力を受け取り検証する。
    # Form(...) は必須、Form("") は省略時に空文字列を使う指定。
    validate_device(name, purchase_date, status, maintenance_scope)
    with database() as connection:
        # ? は値を後から渡すプレースホルダ。SQLに入力文字列を直接連結しない。
        # 第2引数のタプルの値を、左から順に各 ? へ割り当てる。
        connection.execute("""
            INSERT INTO devices (name, manufacturer, model_number, location, purchase_date, note, status, maintenance_scope)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (name.strip(), manufacturer, model_number, location, purchase_date, note, status, maintenance_scope))

    # 303 は保存後の移動先を GET で取得させる。再読み込みによる再送信を避ける。
    return RedirectResponse(url="/", status_code=303)


@app.get("/devices/{device_id}")
def device_detail(request: Request, device_id: int) -> HTMLResponse:
    """指定した機器の基本情報と、その機器に属するメンテナンス項目を表示する。"""
    # URLの {device_id} が引数に入る。int の指定により整数以外は検証エラーになる。
    with database() as connection:
        cursor: sqlite3.Cursor = connection.execute("""
            SELECT id, name, manufacturer, model_number, location, purchase_date,
                   note, status, maintenance_scope
            FROM devices
            WHERE id = ?
        """, (device_id,))
        # Optional[T]は「TまたはNone」。この時点では機器が見つからない可能性もある。
        device: Optional[sqlite3.Row] = cursor.fetchone()
        # (device_id,) は要素が1つのタプル。末尾のカンマがないと単なる括弧になる。
        # fetchone() は1行を返し、該当する行がない場合は None を返す。
        if device is None:
            raise HTTPException(status_code=404, detail="機器が見つかりません")
        # Noneなら上で処理を終了するため、ここから先のdeviceはRowとして使える。

        cursor = connection.execute("""
            SELECT id, device_id, name, description, interval_value, interval_unit, priority, first_due_date
            FROM maintenance_plans
            WHERE device_id = ?
            ORDER BY id
        """, (device_id,))
        # 1つの機器に複数の項目があるため、単一のRowではなくlist[Row]で受け取る。
        maintenance_plans: list[sqlite3.Row] = cursor.fetchall()

        cursor = connection.execute("""
            SELECT
                maintenance_records.id,
                maintenance_records.maintenance_plan_id,
                maintenance_records.performed_at,
                maintenance_records.meter_value,
                maintenance_records.note
            FROM maintenance_records
            JOIN maintenance_plans
                ON maintenance_plans.id
                = maintenance_records.maintenance_plan_id
            WHERE maintenance_plans.device_id = ?
            ORDER BY
                maintenance_records.performed_at DESC,
                maintenance_records.id DESC
        """, (device_id,))

        maintenance_records = cursor.fetchall()

    return templates.TemplateResponse(
        request=request,
        name="device_detail.html",
        context={
            "device": device,
            "maintenance_plans": maintenance_plans,
            "maintenance_records": maintenance_records
        }
    )


def device_qr_target(request: Request, device_id: int) -> str:
    """画面の接続先とは別に、設定したサーバーの機器詳細URLを組み立てる。"""
    # JSONのオブジェクトをdictとして読み込む。都度読むため設定変更だけなら再起動は不要。
    with QR_CONFIG_PATH.open(encoding="utf-8") as config_file:
        config: dict = json.load(config_file)
    base_url = config.get("qr_base_url", "")
    if not isinstance(base_url, str):
        raise HTTPException(status_code=503, detail="qr_base_urlにはURLの文字列を設定してください")
    base_url = base_url.strip().rstrip("/")
    if not base_url:
        # 空文字なら従来どおり、閲覧しているホスト・ポートを使う。
        return str(request.url_for("device_detail", device_id=device_id))
    try:
        parts = urlsplit(base_url)
        valid = (parts.scheme in ("http", "https") and parts.hostname
                 and parts.username is None and parts.password is None
                 and not parts.query and not parts.fragment)
        parts.port  # 不正なポート番号の場合もValueErrorとして検出する。
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(status_code=503, detail="qr_base_urlに有効なHTTP(S)のサーバーURLを設定してください")
    # url_path_for()はホストを含まないパスを返す。機器IDは現在の機器のものを使う。
    return base_url + str(app.url_path_for("device_detail", device_id=device_id))


@app.get("/devices/{device_id}/qr")
def device_qr(request: Request, device_id: int) -> HTMLResponse:
    """機器詳細へのURLと、そのURLを格納したQRコードを表示する。"""
    with database() as connection:
        device: Optional[sqlite3.Row] = connection.execute(
            "SELECT id, name FROM devices WHERE id = ?", (device_id,)
        ).fetchone()
    if device is None:
        raise HTTPException(status_code=404, detail="機器が見つかりません")

    # 表示するURLと画像に格納するURLは、同じ関数で決定して一致させる。
    target_url: str = device_qr_target(request, device_id)
    return templates.TemplateResponse(request=request, name="device_qr.html", context={
        "device": device, "target_url": target_url,
        "local_only": urlsplit(target_url).hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0"),
    })


@app.get("/devices/{device_id}/qr.png")
def device_qr_image(request: Request, device_id: int, download: bool = False) -> Response:
    """QR画像をPNGで返す。download=trueの場合はファイル保存用のヘッダーを付ける。"""
    with database() as connection:
        require_device(connection, device_id)
    target_url: str = device_qr_target(request, device_id)
    # QRCodeは符号化を担当するライブラリのオブジェクト。borderは読み取りに必要な白い余白。
    qr: qrcode.QRCode = qrcode.QRCode(box_size=10, border=4)
    qr.add_data(target_url)
    try:
        qr.make(fit=True)
    except DataOverflowError:
        raise HTTPException(status_code=422, detail="URLが長すぎるためQRコードを生成できません")
    # BytesIOはメモリー上のバイナリ保存先。DBやディスクに画像ファイルを残さない。
    buffer: BytesIO = BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buffer, format="PNG")
    disposition: str = "attachment" if download else "inline"
    return Response(content=buffer.getvalue(), media_type="image/png", headers={
        "Content-Disposition": f'{disposition}; filename="device-{device_id}-qr.png"',
        "Cache-Control": "no-store",
    })


@app.get("/devices/{device_id}/maintenance/new")
def maintenance_plan_new(request: Request, device_id: int) -> HTMLResponse:
    """対象機器の存在を確認して、メンテナンス項目の登録画面を表示する。"""
    with database() as connection:
        cursor: sqlite3.Cursor = connection.execute(
            "SELECT id, name FROM devices WHERE id = ?", (device_id,)
        )
        # このRowに入る列はSELECTしたid（int）とname（str）だけ。
        device: Optional[sqlite3.Row] = cursor.fetchone()

    if device is None:
        raise HTTPException(status_code=404, detail="機器が見つかりません")

    return templates.TemplateResponse(
        request=request, name="maintenance_plan_new.html", context={"device": device}
    )


@app.post("/devices/{device_id}/maintenance/new")
def create_maintenance_plan(
    device_id: int,
    name: str = Form(...),
    description: str = Form(""),
    interval_value: int = Form(...),
    interval_unit: str = Form(...),
    priority: str = Form(...),
    first_due_date: Annotated[str, Form()] = ""
) -> RedirectResponse:
    """周期と初回予定日を機器にひも付けて保存する。"""
    validate_plan(name, interval_value, interval_unit, priority)
    validate_date(first_due_date, "初回予定日")
    with database() as connection:
        require_device(connection, device_id)
        connection.execute("""
            INSERT INTO maintenance_plans (
                device_id, name, description, interval_value, interval_unit, priority, first_due_date
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (device_id, name, description, interval_value, interval_unit, priority, first_due_date))

    # f"..." は文字列内の {式} を値に置き換える、f文字列の記法。
    # 処理が終わったあと、ブラウザに「次は /devices/{device_id} を開いてね」としている。303 は HTTP ステータスコードで、特にフォーム送信後によく使い、ブラウザに「次は GET で開いてね」と指示する意味がある。
    return RedirectResponse(url=f"/devices/{device_id}", status_code=303)


def validate_date(value: str, label: str, required: bool = False) -> None:
    if not value and not required:
        return
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{label}を正しい日付で入力してください")


def validate_device(name: str, purchase_date: str, status: str, scope: str) -> None:
    if not name.strip():
        raise HTTPException(status_code=422, detail="機器名を入力してください")
    validate_date(purchase_date, "購入日")
    if status not in STATUSES or scope not in SCOPES:
        raise HTTPException(status_code=422, detail="状態とメンテナンス区分を選択してください")


def validate_plan(name: str, interval_value: int, interval_unit: str, priority: str) -> None:
    if not name.strip() or interval_value < 1 or interval_unit not in UNITS or priority not in PRIORITIES:
        raise HTTPException(
            status_code=422, detail="項目名・1以上の周期・単位・重要度を確認してください")


def validate_record(performed_at: str, meter_value: str) -> Optional[float]:
    validate_date(performed_at, "実施日", required=True)
    try:
        value = float(meter_value) if meter_value.strip() else None
        if value is not None and (not math.isfinite(value) or value < 0):
            raise ValueError
        return value
    except ValueError:
        raise HTTPException(status_code=422, detail="メーター値は0以上の数値で入力してください")


def require_device(connection: sqlite3.Connection, device_id: int) -> None:
    if not connection.execute("SELECT id FROM devices WHERE id = ?", (device_id,)).fetchone():
        raise HTTPException(status_code=404, detail="機器が見つかりません")


@app.post("/devices/{device_id}/edit")
def update_device(
    device_id: int,
    name: str = Form(...),
    manufacturer: str = Form(""),
    model_number: str = Form(""),
    location: str = Form(""),
    purchase_date: str = Form(""),
    note: str = Form(""),
    status: str = Form(...),
    maintenance_scope: str = Form(...)
) -> RedirectResponse:
    validate_device(name, purchase_date, status, maintenance_scope)
    with database() as connection:
        require_device(connection, device_id)
        connection.execute("""
            UPDATE devices SET name = ?, manufacturer = ?, model_number = ?, location = ?,
                purchase_date = ?, note = ?, status = ?, maintenance_scope = ? WHERE id = ?
        """, (name.strip(), manufacturer, model_number, location, purchase_date,
              note, status, maintenance_scope, device_id))
    return RedirectResponse(url=f"/devices/{device_id}?saved=device#device-info", status_code=303)


@app.post("/devices/{device_id}/maintenance-plans/{plan_id}/edit")
def update_maintenance_plan(
    device_id: int, plan_id: int,
    name: str = Form(...), description: str = Form(""),
    interval_value: int = Form(...), interval_unit: str = Form(...), priority: str = Form(...),
    first_due_date: Annotated[str, Form()] = ""
) -> RedirectResponse:
    validate_plan(name, interval_value, interval_unit, priority)
    validate_date(first_due_date, "初回予定日")
    with database() as connection:
        cursor = connection.execute("""
            UPDATE maintenance_plans SET name = ?, description = ?, interval_value = ?,
                interval_unit = ?, priority = ?, first_due_date = ? WHERE id = ? AND device_id = ?
        """, (name.strip(), description, interval_value, interval_unit, priority, first_due_date, plan_id, device_id))
        if cursor.rowcount != 1:
            raise HTTPException(status_code=404, detail="メンテナンス項目が見つかりません")
    return RedirectResponse(url=f"/devices/{device_id}?saved=plan#plan-{plan_id}", status_code=303)


def require_record(connection: sqlite3.Connection, device_id: int, plan_id: int, record_id: int) -> None:
    record = connection.execute("""
        SELECT r.id FROM maintenance_records r
        JOIN maintenance_plans p ON p.id = r.maintenance_plan_id
        WHERE r.id = ? AND p.id = ? AND p.device_id = ?
    """, (record_id, plan_id, device_id)).fetchone()
    if record is None:
        raise HTTPException(status_code=404, detail="実施記録が見つかりません")


@app.post("/devices/{device_id}/maintenance-plans/{plan_id}/records/{record_id}/edit")
def update_maintenance_record(
    request: Request, device_id: int, plan_id: int, record_id: int,
    performed_at: str = Form(...), meter_value: str = Form(""), note: str = Form("")
) -> RedirectResponse:
    value = validate_record(performed_at, meter_value)
    with database() as connection:
        require_record(connection, device_id, plan_id, record_id)
        connection.execute("""
            UPDATE maintenance_records SET performed_at = ?, meter_value = ?, note = ? WHERE id = ?
        """, (performed_at, value, note, record_id))
    return RedirectResponse(url=f"/devices/{device_id}?saved=record#record-{record_id}", status_code=303)


@app.post("/devices/{device_id}/maintenance-plans/{plan_id}/records/{record_id}/delete")
def delete_maintenance_record(request: Request, device_id: int, plan_id: int, record_id: int) -> RedirectResponse:
    with database() as connection:
        require_record(connection, device_id, plan_id, record_id)
        connection.execute(
            "DELETE FROM maintenance_records WHERE id = ?", (record_id,))
    return RedirectResponse(url=f"/devices/{device_id}?saved=deleted#plan-{plan_id}", status_code=303)


@app.get("/devices/{device_id}/maintenance-plans/{plan_id}/records/new")
def maintenance_record_new(request: Request, device_id: int, plan_id: int) -> HTMLResponse:
    """指定機器のメンテナンス項目に対する、実施記録の入力画面を表示する。"""
    with database() as connection:
        # 項目IDだけでなく機器IDも照合し、別の機器の項目が表示されるのを防ぐ。
        cursor: sqlite3.Cursor = connection.execute("""
            SELECT id, name FROM maintenance_plans
            WHERE id = ? AND device_id = ?
        """, (plan_id, device_id))
        # 見つかればid・nameを持つRow、見つからなければNone。
        plan: Optional[sqlite3.Row] = cursor.fetchone()

    if plan is None:
        raise HTTPException(status_code=404, detail="メンテナンス項目が見つかりません")

    return templates.TemplateResponse(
        request=request,
        name="maintenance_record_new.html",
        context={"device_id": device_id, "plan": plan}
    )


@app.post("/devices/{device_id}/maintenance-plans/{plan_id}/records")
def create_maintenance_record(
    device_id: int,
    plan_id: int,
    performed_at: str = Form(...),
    meter_value: str = Form(""),
    note: str = Form("")
) -> RedirectResponse:
    """実施日・任意のメーター値・備考をメンテナンス項目にひも付けて保存する。"""
    meter_value_db = validate_record(performed_at, meter_value)
    with database() as connection:
        # POSTだけを直接送ることも可能なので、入力画面と同じ所属確認を行う。
        cursor: sqlite3.Cursor = connection.execute("""
            SELECT id FROM maintenance_plans
            WHERE id = ? AND device_id = ?
        """, (plan_id, device_id))
        # 存在確認用なので取得する列はidのみ。型はRowまたはNone。
        plan: Optional[sqlite3.Row] = cursor.fetchone()
        if plan is None:
            raise HTTPException(status_code=404, detail="メンテナンス項目が見つかりません")

        # A if 条件 else B は条件式。未入力は None とし、SQLiteでは NULL で保存する。
        # 数値の 0 と未入力を区別するため、文字列として受け取ってから float に変換する。
        # 入力"12.5"（str）は12.5（float）へ、空文字列はNoneへ変わる。
        # Optional[float]は「floatまたはNone」。実施日のperformed_atはstrのまま保存する。
        connection.execute("""
            INSERT INTO maintenance_records (maintenance_plan_id, performed_at, meter_value, note)
            VALUES (?, ?, ?, ?)
        """, (plan_id, performed_at, meter_value_db, note))

    return RedirectResponse(url=f"/devices/{device_id}", status_code=303)


def render_edit_form(request: Request, kind: str, values, back: str, error: str = "", status_code: int = 200):
    labels = {"device": "機器情報を編集", "plan": "メンテナンス項目を編集", "record": "実施記録を編集"}
    return templates.TemplateResponse(request=request, name="edit.html", context={
        "kind": kind, "values": values, "back": back, "heading": labels[kind],
        "form_action": request.url.path, "error": error,
    }, status_code=status_code)


@app.get("/devices/{device_id}/edit")
def device_edit(request: Request, device_id: int):
    with database() as connection:
        require_device(connection, device_id)
        device = connection.execute(
            "SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
    return render_edit_form(request, "device", device, f"/devices/{device_id}")


@app.get("/devices/{device_id}/maintenance-plans/{plan_id}/edit")
def plan_edit(request: Request, device_id: int, plan_id: int):
    with database() as connection:
        plan = connection.execute(
            "SELECT * FROM maintenance_plans WHERE id = ? AND device_id = ?", (plan_id, device_id)).fetchone()
    if plan is None:
        raise HTTPException(status_code=404, detail="メンテナンス項目が見つかりません")
    return render_edit_form(request, "plan", plan, f"/devices/{device_id}#plan-{plan_id}")


@app.get("/devices/{device_id}/maintenance-plans/{plan_id}/records/{record_id}/edit")
def record_edit(request: Request, device_id: int, plan_id: int, record_id: int):
    with database() as connection:
        require_record(connection, device_id, plan_id, record_id)
        record = connection.execute(
            "SELECT * FROM maintenance_records WHERE id = ?", (record_id,)).fetchone()
    return render_edit_form(request, "record", record, f"/devices/{device_id}?history=open#record-{record_id}")


@app.get("/devices/{device_id}/maintenance-plans/{plan_id}/records/{record_id}/delete")
def record_delete_confirmation(request: Request, device_id: int, plan_id: int, record_id: int):
    with database() as connection:
        require_record(connection, device_id, plan_id, record_id)
        record = connection.execute(
            "SELECT * FROM maintenance_records WHERE id = ?", (record_id,)).fetchone()
    return templates.TemplateResponse(request=request, name="record_delete.html", context={
        "record": record, "back": f"/devices/{device_id}?history=open#record-{record_id}",
    })


async def form_error_response(request: Request, message: str):
    values = dict(await request.form())
    parts = request.url.path.strip("/").split("/")
    kind = "record" if "records" in parts else "plan" if (
        "maintenance" in parts or "maintenance-plans" in parts) else "device"
    back = f"/devices/{parts[1]}" if len(
        parts) > 1 and parts[1].isdigit() else "/devices"
    labels = {"device": "機器を登録", "plan": "メンテナンス項目を追加", "record": "実施記録を追加"}
    if not request.url.path.endswith("/edit"):
        return templates.TemplateResponse(request=request, name="edit.html", context={
            "kind": kind, "values": values, "back": back, "heading": labels[kind],
            "form_action": request.url.path, "error": message,
        }, status_code=422)
    return render_edit_form(request, kind, values, back, message, 422)


@app.exception_handler(HTTPException)
async def handle_form_error(request: Request, error: HTTPException):
    if request.method == "POST" and error.status_code == 422:
        return await form_error_response(request, str(error.detail))
    return await http_exception_handler(request, error)


@app.exception_handler(RequestValidationError)
async def handle_input_error(request: Request, error: RequestValidationError):
    if request.method == "POST":
        return await form_error_response(request, "入力内容を確認してください。日付・周期などに誤りがあります。")
    from fastapi.exception_handlers import request_validation_exception_handler
    return await request_validation_exception_handler(request, error)
