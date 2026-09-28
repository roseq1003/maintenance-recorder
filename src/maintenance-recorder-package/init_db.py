"""機器・メンテナンス項目・実施記録のテーブルを作成する初期化スクリプト。

既存データは削除しない。サンプル機器も追加しないため、繰り返し実行できる。
トップ画面が参照する旧 maintenance テーブルは、このスクリプトでは作成しない。
"""

from contextlib import closing
from pathlib import Path
import sqlite3
from typing import Union


# main.py と同じ場所のDBを使う。Path の / はフォルダ名とファイル名の連結。
DB_PATH: Path = Path(__file__).resolve().parent / "maintenance.db"


def initialize_database(db_path: Union[str, Path] = DB_PATH) -> None:
    """指定したDBに必要なテーブルを作る。引数を省略するとアプリのDBを使う。"""
    # Union[str, Path]は文字列またはPathを受け取る指定。-> Noneは値を返さない関数を表す。
    # connect() はDBファイルがなければ作成する。closing() は終了時に接続を閉じる。
    with closing(sqlite3.connect(db_path)) as connection:
        # connectionの型はsqlite3.Connection。execute()の戻り値はsqlite3.Cursor。
        # CREATE TABLEでは表示する検索結果がないため、戻り値の保存やfetchall()は不要。
        # IF NOT EXISTS は、同名テーブルがある場合に作成をスキップする指定。
        # 既存テーブルへの列追加など、スキーマの変更は行わない。
        # INTEGER PRIMARY KEY AUTOINCREMENT は自動採番のID、NOT NULL はNULL禁止。
        connection.execute("""
            CREATE TABLE IF NOT EXISTS devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                manufacturer TEXT,
                model_number TEXT,
                location TEXT,
                purchase_date TEXT
            )
        """)

        # FOREIGN KEY は、device_id が devices.id を参照する関係を定義する。
        # SQLiteで参照整合性を強制するには、接続ごとに PRAGMA foreign_keys = ON が必要。
        # 現在のアプリはその指定をしていないため、定義だけでは不正なIDを拒否しない。
        connection.execute("""
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

        # 1つの項目に対して複数の実施記録を保存できる。
        # 日付はTEXT、メーター値は小数も扱えるREAL。任意項目はNULLを許可する。
        connection.execute("""
            CREATE TABLE IF NOT EXISTS maintenance_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                maintenance_plan_id INTEGER NOT NULL,
                performed_at TEXT NOT NULL,
                meter_value REAL,
                note TEXT,
                FOREIGN KEY (maintenance_plan_id) REFERENCES maintenance_plans(id)
            )
        """)


# 直接実行した場合だけ初期化する。importしただけではDBに触らない。
if __name__ == "__main__":
    initialize_database()
    print("データベースを初期化しました")
