# initialでDB作成用

import sqlite3
from pathlib import Path

# --------------------------------------------------
# ① SQLiteデータベースへ接続
# --------------------------------------------------

# maintenance.db が存在すれば接続
# 存在しなければ新しく作成される
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "maintenance.db"
connection = sqlite3.connect(DB_PATH)

# --------------------------------------------------
# ② SQLを実行するためのカーソルを作る
# --------------------------------------------------

cursor = connection.cursor()


# --------------------------------------------------
# ③ テーブルを作成
# --------------------------------------------------
cursor.execute("""
    CREATE TABLE IF NOT EXISTS devices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        manufacturer TEXT,
        model_number TEXT,
        location TEXT,
        purchase_date TEXT
    )
""")

cursor.execute("""
    CREATE TABLE IF NOT EXISTS maintenance_plans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        description TEXT,
        interval_value INTEGER,
        interval_unit TEXT,
        priority TEXT,
        FOREIGN KEY (device_id) REFERENCES devices(id)
    )
""")


cursor.execute("""
    CREATE TABLE IF NOT EXISTS maintenance_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        maintenance_plan_id INTEGER NOT NULL,
        performed_at TEXT NOT NULL,
        meter_value REAL,
        note TEXT,
        FOREIGN KEY (maintenance_plan_id)
            REFERENCES maintenance_plans(id)
    )
""")

# --------------------------------------------------
# ④ とりあえず1件登録
# --------------------------------------------------

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
    "エアコン",
    "ダイキン",
    "ABC-123",
    "リビング",
    "2024-05-01"
))


# --------------------------------------------------
# ⑤ 変更を確定
# --------------------------------------------------
connection.commit()

cursor.execute("""
    SELECT * FROM devices
""")

devices = cursor.fetchall()

print(devices)
# --------------------------------------------------
# ⑥ DBとの接続終了
# --------------------------------------------------

connection.close()


print("データベースを作成しました")
