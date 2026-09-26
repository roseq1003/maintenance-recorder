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
    CREATE TABLE IF NOT EXISTS maintenance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT,
        device TEXT,
        task TEXT,
        location TEXT,
        priority TEXT
    )
""")

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

# --------------------------------------------------
# ④ とりあえず1件登録
# --------------------------------------------------

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
    "2026-09-21",
    "おナホール",
    "清掃",
    "さやかの部屋",
    "高"
))

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
    SELECT * FROM maintenance
""")


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
