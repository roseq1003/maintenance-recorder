import sqlite3


# --------------------------------------------------
# ① SQLiteデータベースへ接続
# --------------------------------------------------

# maintenance.db が存在すれば接続
# 存在しなければ新しく作成される
connection = sqlite3.connect("maintenance.db")


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
    "2026-09-20",
    "エアコン",
    "フィルター清掃",
    "事務所 2F",
    "中"
))


# --------------------------------------------------
# ⑤ 変更を確定
# --------------------------------------------------

connection.commit()

cursor.execute("""
    SELECT * FROM maintenance
""")

rows = cursor.fetchall()

print(rows)
# --------------------------------------------------
# ⑥ DBとの接続終了
# --------------------------------------------------

connection.close()


print("データベースを作成しました")
