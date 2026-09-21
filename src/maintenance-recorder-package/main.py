# FastAPI本体
from fastapi import FastAPI
from pathlib import Path

# HTMLファイルを扱うための機能
from fastapi.templating import Jinja2Templates

# CSSやJavaScriptなどの静的ファイルを扱う
from fastapi.staticfiles import StaticFiles

# ブラウザから送られてきたリクエスト情報を扱う
from fastapi import Request

import sqlite3

# FastAPIアプリを作成
app = FastAPI()
BASE_DIR = Path(__file__).resolve().parent


# 「/static」というURLで
# staticフォルダの中身を使えるようにする
app.mount(
    "/static",  # 「プロジェクト内の static フォルダを、
    StaticFiles(directory=BASE_DIR / "static"),  # 起動する場所が変わっても読み込めるようにします。
    name="static"  # この設定の名前は static にします」
)


# HTMLテンプレートはtemplatesフォルダに置く
templates = Jinja2Templates(directory=BASE_DIR / "templates")


# "/" にアクセスされたときの処理
@app.get("/")
def home(request: Request):

    # --------------------------------------------------
    # ① SQLiteへ接続
    # --------------------------------------------------

    connection = sqlite3.connect("maintenance.db")

    cursor = connection.cursor()

    # --------------------------------------------------
    # ② maintenanceテーブルからデータ取得
    # --------------------------------------------------

    cursor.execute("""
        SELECT
            id,
            date,
            device,
            task,
            location,
            priority
        FROM maintenance
    """)

    rows = cursor.fetchall()

    # --------------------------------------------------
    # ③ SQLiteから取得したデータを
    #    Jinja2で扱いやすい辞書形式へ変換
    # --------------------------------------------------

    maintenance_list = []

    for row in rows:

        maintenance_list.append({
            "id": row[0],
            "date": row[1],
            "device": row[2],
            "task": row[3],
            "location": row[4],
            "priority": row[5]
        })

    # --------------------------------------------------
    # ④ 登録件数を取得
    # --------------------------------------------------

    device_count = len(maintenance_list)

    # --------------------------------------------------
    # ⑤ DB接続終了
    # --------------------------------------------------

    connection.close()

    # --------------------------------------------------
    # ⑥ Jinja2へ渡す
    # --------------------------------------------------

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "device_count": device_count,
            "maintenance_list": maintenance_list
        }
    )
