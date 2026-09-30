"""機器・メンテナンス項目・実施記録の画面表示とフォーム保存を担当する。"""

from contextlib import contextmanager, asynccontextmanager
from datetime import date, datetime, timedelta, timezone
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


# uvicorn main:app（単独）とパッケージ経由のどちらの読み込みにも対応する。
# 「.」付きは同じパッケージ内からの相対インポート。
# next_date / scheduled_dateは、従来のmain経由の参照も使えるように公開を維持する。
if __package__:
    from .scheduling import group_schedules, next_date, scheduled_date
else:
    from scheduling import group_schedules, next_date, scheduled_date


# デコレーター（@...）は直後の関数に機能を付ける。この場合は起動・終了処理として使う。
@asynccontextmanager
async def lifespan(app: FastAPI):
    """起動時にDBを準備する。yield以降はサーバー終了時の処理になる。"""
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
# 「変数名: 型 = 値」は型注釈付きの代入。型注釈だけで値が変換されるわけではない。
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
    """静的ファイルの更新時刻をURLに付け、更新後のCSSを取得させる。"""
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
    # Rowは辞書そのものではない。Pythonではrow["name"]またはrow[0]で値を取り出す。
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


# @app.getは「このURLをGETで開いたら、この関数を呼ぶ」というFastAPIへの登録。
@app.get("/")
def home(request: Request) -> HTMLResponse:
    """DBから項目と最新実施日を読み、予定の分類結果と履歴を画面へ渡す。"""
    current = today()
    with database() as connection:
        device_count = connection.execute(
            "SELECT COUNT(*) FROM devices").fetchone()[0]
        # 相関サブクエリ：項目pごとに記録rを調べ、MAXで最新実施日を取り出す。
        # 未実施ならlast_performedはNULL（PythonではNone）になる。
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
    schedule_context = group_schedules(plans, current)
    return templates.TemplateResponse(request=request, name="index.html", context={
        "device_count": device_count, "recent_history": recent_history,
        # **辞書 は、その辞書のキーと値を別の辞書に展開する記法。
        **schedule_context,
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
    # Annotated[str, Form()] はstr型に「フォームから受け取る」という情報を添える記法。
    # この書き方では省略時の値を = "" などで指定する。
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
        device = require_device(connection, device_id)

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
        device = require_device(connection, device_id)

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
        device = require_device(connection, device_id)

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
    # 303でブラウザーに機器詳細のGETを指示し、再読み込みによる二重登録を避ける。
    return RedirectResponse(url=f"/devices/{device_id}", status_code=303)


def validate_date(value: str, label: str, required: bool = False) -> None:
    """任意／必須の日付をYYYY-MM-DD形式で検証し、不正なら422を返す。"""
    if not value and not required:
        return
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{label}を正しい日付で入力してください")


def validate_device(name: str, purchase_date: str, status: str, scope: str) -> None:
    """機器名・購入日・状態・区分を保存前に確認する。"""
    if not name.strip():
        raise HTTPException(status_code=422, detail="機器名を入力してください")
    validate_date(purchase_date, "購入日")
    if status not in STATUSES or scope not in SCOPES:
        raise HTTPException(status_code=422, detail="状態とメンテナンス区分を選択してください")


def validate_plan(name: str, interval_value: int, interval_unit: str, priority: str) -> None:
    """項目名、正の周期、選択肢の単位と重要度を保存前に確認する。"""
    if not name.strip() or interval_value < 1 or interval_unit not in UNITS or priority not in PRIORITIES:
        raise HTTPException(
            status_code=422, detail="項目名・1以上の周期・単位・重要度を確認してください")


def validate_record(performed_at: str, meter_value: str) -> Optional[float]:
    """実施日を検証し、メーター値を数値または未入力のNoneへ変換する。"""
    # Optional[float]は「floatまたはNone」。未入力と数値の0を区別して保存する。
    validate_date(performed_at, "実施日", required=True)
    try:
        # 条件式 A if 条件 else B。空欄はNone、入力された0は数値0として区別する。
        value = float(meter_value) if meter_value.strip() else None
        if value is not None and (not math.isfinite(value) or value < 0):
            raise ValueError
        return value
    except ValueError:
        raise HTTPException(status_code=422, detail="メーター値は0以上の数値で入力してください")


def require_device(connection: sqlite3.Connection, device_id: int) -> sqlite3.Row:
    """機器を1件取得する。なければ404で終了するため、呼び出し元で再確認は不要。"""
    # ? は値のプレースホルダ。(device_id,) は要素1つのタプル。
    # fetchone()は1行のRowを返し、該当行がなければNoneになる。
    device = connection.execute(
        "SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
    if device is None:
        raise HTTPException(status_code=404, detail="機器が見つかりません")
    return device


def require_plan(connection: sqlite3.Connection, device_id: int, plan_id: int) -> sqlite3.Row:
    """指定機器に属する項目を取得する。項目が別の機器に属する場合も404にする。"""
    plan = connection.execute(
        "SELECT * FROM maintenance_plans WHERE id = ? AND device_id = ?",
        (plan_id, device_id)).fetchone()
    if plan is None:
        raise HTTPException(status_code=404, detail="メンテナンス項目が見つかりません")
    return plan


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
    """入力を検証して機器を更新し、詳細画面の機器情報へ戻す。"""
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
    """機器と項目のIDを照合して更新する。項目IDと既存の記録は維持する。"""
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


def require_record(connection: sqlite3.Connection, device_id: int, plan_id: int, record_id: int) -> sqlite3.Row:
    """機器→項目→記録の所属を照合し、表示・更新・削除に使う記録を返す。"""
    # JOINで項目のdevice_idまで調べ、URLのIDを変えた誤操作を防ぐ。
    record = connection.execute("""
        SELECT r.* FROM maintenance_records r
        JOIN maintenance_plans p ON p.id = r.maintenance_plan_id
        WHERE r.id = ? AND p.id = ? AND p.device_id = ?
    """, (record_id, plan_id, device_id)).fetchone()
    if record is None:
        raise HTTPException(status_code=404, detail="実施記録が見つかりません")
    return record


@app.post("/devices/{device_id}/maintenance-plans/{plan_id}/records/{record_id}/edit")
def update_maintenance_record(
    request: Request, device_id: int, plan_id: int, record_id: int,
    performed_at: str = Form(...), meter_value: str = Form(""), note: str = Form("")
) -> RedirectResponse:
    """入力と所属を検証して記録を修正し、詳細画面の該当記録へ戻す。"""
    value = validate_record(performed_at, meter_value)
    with database() as connection:
        require_record(connection, device_id, plan_id, record_id)
        connection.execute("""
            UPDATE maintenance_records SET performed_at = ?, meter_value = ?, note = ? WHERE id = ?
        """, (performed_at, value, note, record_id))
    return RedirectResponse(url=f"/devices/{device_id}?saved=record#record-{record_id}", status_code=303)


@app.post("/devices/{device_id}/maintenance-plans/{plan_id}/records/{record_id}/delete")
def delete_maintenance_record(request: Request, device_id: int, plan_id: int, record_id: int) -> RedirectResponse:
    """所属を確認して記録を削除する。次回表示時の予定計算にも反映される。"""
    with database() as connection:
        require_record(connection, device_id, plan_id, record_id)
        connection.execute(
            "DELETE FROM maintenance_records WHERE id = ?", (record_id,))
    return RedirectResponse(url=f"/devices/{device_id}?saved=deleted#plan-{plan_id}", status_code=303)


@app.get("/devices/{device_id}/maintenance-plans/{plan_id}/records/new")
def maintenance_record_new(request: Request, device_id: int, plan_id: int) -> HTMLResponse:
    """指定機器のメンテナンス項目に対する、実施記録の入力画面を表示する。"""
    with database() as connection:
        plan = require_plan(connection, device_id, plan_id)

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
        require_plan(connection, device_id, plan_id)

        # validate_recordで変換済みの数値を保存する。NoneはSQLiteのNULLになる。
        # 実施日はYYYY-MM-DDの文字列のまま保存するため、SQLでも日付順に並べられる。
        connection.execute("""
            INSERT INTO maintenance_records (maintenance_plan_id, performed_at, meter_value, note)
            VALUES (?, ?, ?, ?)
        """, (plan_id, performed_at, meter_value_db, note))

    return RedirectResponse(url=f"/devices/{device_id}", status_code=303)


def render_edit_form(request: Request, kind: str, values, back: str, error: str = "",
                     status_code: int = 200, *, creating: bool = False) -> HTMLResponse:
    """共通フォームに値・見出し・戻り先を渡す。入力エラー時にも同じ画面を使う。"""
    # * より後の引数は creating=True のように名前を付けて渡す。
    if creating:
        labels = {"device": "機器を登録", "plan": "メンテナンス項目を追加", "record": "実施記録を追加"}
    else:
        labels = {"device": "機器情報を編集", "plan": "メンテナンス項目を編集", "record": "実施記録を編集"}
    return templates.TemplateResponse(request=request, name="edit.html", context={
        "kind": kind, "values": values, "back": back, "heading": labels[kind],
        "form_action": request.url.path, "error": error,
    }, status_code=status_code)


@app.get("/devices/{device_id}/edit")
def device_edit(request: Request, device_id: int):
    """機器の保存済みの値を取得し、共通の編集フォームへ渡す。"""
    with database() as connection:
        device = require_device(connection, device_id)
    return render_edit_form(request, "device", device, f"/devices/{device_id}")


@app.get("/devices/{device_id}/maintenance-plans/{plan_id}/edit")
def plan_edit(request: Request, device_id: int, plan_id: int):
    """機器に属する項目を取得し、共通の編集フォームへ渡す。"""
    with database() as connection:
        plan = require_plan(connection, device_id, plan_id)
    return render_edit_form(request, "plan", plan, f"/devices/{device_id}#plan-{plan_id}")


@app.get("/devices/{device_id}/maintenance-plans/{plan_id}/records/{record_id}/edit")
def record_edit(request: Request, device_id: int, plan_id: int, record_id: int):
    """所属を確認して記録を取得し、共通の編集フォームへ渡す。"""
    with database() as connection:
        record = require_record(connection, device_id, plan_id, record_id)
    return render_edit_form(request, "record", record, f"/devices/{device_id}?history=open#record-{record_id}")


@app.get("/devices/{device_id}/maintenance-plans/{plan_id}/records/{record_id}/delete")
def record_delete_confirmation(request: Request, device_id: int, plan_id: int, record_id: int):
    """所属を確認した記録の削除確認画面を返す。このGETでは削除しない。"""
    with database() as connection:
        record = require_record(connection, device_id, plan_id, record_id)
    return templates.TemplateResponse(request=request, name="record_delete.html", context={
        "record": record, "back": f"/devices/{device_id}?history=open#record-{record_id}",
    })


async def form_error_response(request: Request, message: str):
    """送信済みの入力値を保持し、種類に合うフォームを422で再表示する。"""
    # awaitでフォームの読み取り完了を待つ。dict化した値を入力欄の再表示に使う。
    values = dict(await request.form())
    parts = request.url.path.strip("/").split("/")
    # URLの階層でフォームの種類を判断する。複数の条件式を重ねず順に読む。
    if "records" in parts:
        kind = "record"
    elif "maintenance" in parts or "maintenance-plans" in parts:
        kind = "plan"
    else:
        kind = "device"
    back = "/devices"
    if len(parts) > 1 and parts[1].isdigit():
        back = f"/devices/{parts[1]}"
    return render_edit_form(
        request, kind, values, back, message, status_code=422,
        creating=not request.url.path.endswith("/edit"),
    )


@app.exception_handler(HTTPException)
async def handle_form_error(request: Request, error: HTTPException):
    """自前の入力検証による422をフォームへ戻し、404などは通常の応答にする。"""
    if request.method == "POST" and error.status_code == 422:
        return await form_error_response(request, str(error.detail))
    return await http_exception_handler(request, error)


@app.exception_handler(RequestValidationError)
async def handle_input_error(request: Request, error: RequestValidationError):
    """FastAPIの型・必須検証の失敗を処理し、POSTならフォームを再表示する。"""
    if request.method == "POST":
        return await form_error_response(request, "入力内容を確認してください。日付・周期などに誤りがあります。")
    from fastapi.exception_handlers import request_validation_exception_handler
    return await request_validation_exception_handler(request, error)
