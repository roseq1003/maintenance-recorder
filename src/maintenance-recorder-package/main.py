from datetime import date
from pathlib import Path
import sqlite3

from fastapi import FastAPI, Request, Form, HTTPException
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
# メンテナンス対応機器一覧一覧
# ==================================================


@app.get("/devices")
def device_list(request: Request):

    connection = sqlite3.connect(DB_PATH)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            id,
            name,
            manufacturer,
            model_number,
            location,
            purchase_date
        FROM devices
        ORDER BY id
    """)

    rows = cursor.fetchall()

    devices = []

    for row in rows:
        devices.append({
            "id": row[0],
            "name": row[1],
            "manufacturer": row[2],
            "model_number": row[3],
            "location": row[4],
            "purchase_date": row[5]
        })

    connection.close()

    return templates.TemplateResponse(
        request=request,
        name="device_list.html",
        context={
            "devices": devices
        }
    )

# ==================================================
# メンテナンス登録画面
# ==================================================


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


@app.get("/devices/{device_id}")  # FastAPIのURLルーティング用の記法
def device_detail(
    request: Request,
    device_id: int
):

    connection = sqlite3.connect(DB_PATH)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            id,
            name,
            manufacturer,
            model_number,
            location,
            purchase_date
        FROM devices
        WHERE id = ?
    """, (
        device_id,
    ))

    row = cursor.fetchone()

    connection.close()

    # 該当する機器が存在しなかった場合
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="機器が見つかりません"
        )

    device = {
        "id": row[0],
        "name": row[1],
        "manufacturer": row[2],
        "model_number": row[3],
        "location": row[4],
        "purchase_date": row[5]
    }

    return templates.TemplateResponse(
        request=request,
        name="device_detail.html",
        context={
            "device": device
        }
    )
