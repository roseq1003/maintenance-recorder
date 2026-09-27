from datetime import date
from pathlib import Path
import sqlite3

from fastapi import FastAPI, Request, Form
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse


# FastAPIアプリ作成
app = FastAPI()


# main.pyのあるフォルダ
BASE_DIR = Path(__file__).resolve().parent

# DBファイル
DB_PATH = BASE_DIR / "maintenance.db"


# staticフォルダ公開
app.mount(
    "/static",
    StaticFiles(directory=BASE_DIR / "static"),
    name="static"
)


# templatesフォルダ設定
templates = Jinja2Templates(
    directory=BASE_DIR / "templates"
)


# ==================================================
# トップページ
# ==================================================

@app.get("/")
def home(request: Request):

    connection = sqlite3.connect(DB_PATH)
    cursor = connection.cursor()

    # メンテナンス一覧取得
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

    # Jinja2で扱いやすい辞書形式へ変換
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

    # 登録機器数を取得
    cursor.execute("""
        SELECT COUNT(*) FROM devices
    """)

    device_count = cursor.fetchone()[0]

    # DB接続終了
    connection.close()

    # HTMLを返す
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "device_count": device_count,
            "maintenance_list": maintenance_list
        }
    )


# ==================================================
# メンテナンス登録画面
# ==================================================

@app.get("/maintenance/new")
def maintenance_new(request: Request):

    return templates.TemplateResponse(
        request=request,
        name="maintenance_new.html"
    )


# ==================================================
# メンテナンス登録処理
# ==================================================

@app.post("/maintenance/new")
def create_maintenance(
    date: date = Form(...),
    device: str = Form(...),
    task: str = Form(...),
    location: str = Form(...),
    priority: str = Form(...)
):

    connection = sqlite3.connect(DB_PATH)
    cursor = connection.cursor()

    cursor.execute("""
        INSERT INTO maintenance (
            date,
            device,
            task,
            location,
            priority
        )
        VALUES (?, ?, ?, ?, ?)
    """, (
        date,
        device,
        task,
        location,
        priority
    ))

    connection.commit()
    connection.close()

    return RedirectResponse(
        url="/",
        status_code=303
    )


@app.get("/devices/new")
def device_new(request: Request):

    return templates.TemplateResponse(
        request=request,
        name="device_new.html"
    )


@app.post("/devices/new")
def create_device(
    name: str = Form(...),
    manufacturer: str = Form(""),
    model_number: str = Form(""),
    location: str = Form(""),
    purchase_date: str = Form("")
):

    connection = sqlite3.connect(DB_PATH)
    cursor = connection.cursor()

    cursor.execute("""
        INSERT INTO devices (
            name,
            manufacturer,
            model_number,
            location,
            purchase_date
        )
        VALUES (?, ?, ?, ?, ?)
    """, (
        name,
        manufacturer,
        model_number,
        location,
        purchase_date
    ))

    connection.commit()
    connection.close()

    return RedirectResponse(
        url="/",
        status_code=303
    )
