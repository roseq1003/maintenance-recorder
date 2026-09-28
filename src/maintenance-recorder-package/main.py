"""機器・メンテナンス項目・実施記録の画面表示とフォーム保存を担当する。"""

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator, Optional

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse


# 「変数名: 型 = 値」は型注釈付きの代入。型注釈だけで値が変換されるわけではない。
# strは文字列、intは整数、floatは小数を扱う数値。エディターで型を確認する目印にもなる。
app: FastAPI = FastAPI()

# __file__ はこのファイルのパス。resolve() で絶対パスにし、parent で親フォルダを得る。
# 起動した場所に左右されないよう、DB・HTML・CSSの場所を main.py 基準で指定する。
BASE_DIR: Path = Path(__file__).resolve().parent
DB_PATH: Path = BASE_DIR / "maintenance.db"

# /static/... へのリクエストを static フォルダ内のファイルに対応付ける。
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates: Jinja2Templates = Jinja2Templates(directory=BASE_DIR / "templates")


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


# @ はデコレータの記法。ここでは GET / を処理する関数として home を登録する。
# GET は画面の取得、POST はフォームから送られたデータの保存に使う。
@app.get("/")
def home(request: Request) -> HTMLResponse:
    """登録機器数と、既存の maintenance テーブルの一覧を表示する。"""
    with database() as connection:
        # 三重引用符は複数行の文字列。SQLを読みやすく改行して記述できる。
        # この一覧はまだ日付で絞り込んでいない。月別・期限超過の判定は未実装。
        # connectionはDB接続、cursorはSQLの実行結果から行を取り出すオブジェクト。
        # execute()の戻り値はCursorであり、行のリストそのものではない。
        cursor: sqlite3.Cursor = connection.execute("""
            SELECT id, date, device, task, location, priority
            FROM maintenance
        """)
        # fetchall()はカーソルに残っている全行を取得するメソッド（オブジェクトの関数）。
        # list[sqlite3.Row]は「各要素がRowであるリスト」。0件なら空リスト[]になる。
        # 各行にはSELECTしたid・date・device・task・location・priorityの列が入る。
        maintenance_list: list[sqlite3.Row] = cursor.fetchall()

        # COUNT(*) の結果は1行1列。fetchone() で行、[0] で先頭列の値を得る。
        cursor = connection.execute("SELECT COUNT(*) FROM devices")
        # GROUP BYのないCOUNT(*)は、対象が0件でも値0の行を1つ返す。
        # そのため、このSQLではfetchone()がNoneになることはない。
        device_count: int = cursor.fetchone()[0]

    # requestはRequest型のアクセス情報。contextは「名前: 値」の組を持つdict（辞書）。
    # キーがHTML内の変数名となり、値にはintやlist[Row]など異なる型を渡せる。
    # TemplateResponse()はHTMLResponseを継承したレスポンスを返す。HTML文字列そのものではない。
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"device_count": device_count,
                 "maintenance_list": maintenance_list}
    )


@app.get("/devices")
def device_list(request: Request) -> HTMLResponse:
    """登録機器をID順に表示する。"""
    with database() as connection:
        # execute() はカーソルを返すため、別途 cursor() を作らず結果を取得できる。
        cursor: sqlite3.Cursor = connection.execute("""
            SELECT id, name, manufacturer, model_number, location, purchase_date
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
    purchase_date: str = Form("")
) -> RedirectResponse:
    """機器登録フォームの値を保存し、トップページへ戻す。"""
    # : str は型注釈。FastAPI は型と Form の指定を使って入力を受け取り検証する。
    # Form(...) は必須、Form("") は省略時に空文字列を使う指定。
    with database() as connection:
        # ? は値を後から渡すプレースホルダ。SQLに入力文字列を直接連結しない。
        # 第2引数のタプルの値を、左から順に各 ? へ割り当てる。
        connection.execute("""
            INSERT INTO devices (name, manufacturer, model_number, location, purchase_date)
            VALUES (?, ?, ?, ?, ?)
        """, (name, manufacturer, model_number, location, purchase_date))

    # 303 は保存後の移動先を GET で取得させる。再読み込みによる再送信を避ける。
    return RedirectResponse(url="/", status_code=303)


@app.get("/devices/{device_id}")
def device_detail(request: Request, device_id: int) -> HTMLResponse:
    """指定した機器の基本情報と、その機器に属するメンテナンス項目を表示する。"""
    # URLの {device_id} が引数に入る。int の指定により整数以外は検証エラーになる。
    with database() as connection:
        cursor: sqlite3.Cursor = connection.execute("""
            SELECT id, name, manufacturer, model_number, location, purchase_date
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
            SELECT id, device_id, name, description, interval_value, interval_unit, priority
            FROM maintenance_plans
            WHERE device_id = ?
            ORDER BY id
        """, (device_id,))
        # 1つの機器に複数の項目があるため、単一のRowではなくlist[Row]で受け取る。
        maintenance_plans: list[sqlite3.Row] = cursor.fetchall()

    return templates.TemplateResponse(
        request=request,
        name="device_detail.html",
        context={"device": device, "maintenance_plans": maintenance_plans}
    )


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
    priority: str = Form(...)
) -> RedirectResponse:
    """周期・単位・重要度を機器にひも付けて保存する。次回予定日は計算しない。"""
    with database() as connection:
        connection.execute("""
            INSERT INTO maintenance_plans (
                device_id, name, description, interval_value, interval_unit, priority
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (device_id, name, description, interval_value, interval_unit, priority))

    # f"..." は文字列内の {式} を値に置き換える、f文字列の記法。
    return RedirectResponse(url=f"/devices/{device_id}", status_code=303)


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
        meter_value_db: Optional[float] = float(
            meter_value) if meter_value else None
        connection.execute("""
            INSERT INTO maintenance_records (maintenance_plan_id, performed_at, meter_value, note)
            VALUES (?, ?, ?, ?)
        """, (plan_id, performed_at, meter_value_db, note))

    return RedirectResponse(url=f"/devices/{device_id}", status_code=303)
