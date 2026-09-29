"""アプリで使用するテーブルを作成し、既存DBには不足する列を追加する。"""

from contextlib import closing
import sqlite3
from pathlib import Path
from typing import Union

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "maintenance.db"


def init_db(db_path: Union[str, Path] = DB_PATH) -> None:
    """繰り返し実行可能。既存の機器・項目・実施記録は削除しない。"""
    with closing(sqlite3.connect(db_path)) as connection, connection:
        # DDL（テーブル・列の追加）もまとめて確定・取り消しできるようにする。
        connection.execute("BEGIN")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                manufacturer TEXT,
                model_number TEXT,
                location TEXT,
                status TEXT NOT NULL DEFAULT '稼働中',
                purchase_date TEXT,
                note TEXT NOT NULL DEFAULT '',
                maintenance_scope TEXT NOT NULL DEFAULT '未設定'
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS maintenance_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                interval_value INTEGER,
                interval_unit TEXT,
                priority TEXT,
                first_due_date TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (device_id) REFERENCES devices(id)
            )
        """)
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

        # CREATE TABLE IF NOT EXISTS は既存テーブルを変更しないため、
        # 追加した項目を古いDBにも反映する。テーブル名・列名は固定値のみ。
        additions = {
            "devices": {
                "status": "TEXT NOT NULL DEFAULT '稼働中'",
                "note": "TEXT NOT NULL DEFAULT ''",
                "maintenance_scope": "TEXT NOT NULL DEFAULT '未設定'",
            },
            "maintenance_plans": {
                "first_due_date": "TEXT NOT NULL DEFAULT ''",
            },
        }
        for table, definitions in additions.items():
            columns = {row[1] for row in connection.execute(
                f"PRAGMA table_info({table})")}
            for column, definition in definitions.items():
                if column not in columns:
                    connection.execute(
                        f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

        # 旧定義の status TEXT は未入力でNULLになる。状態未入力の機器だけ
        # 初期値にそろえ、故障中・廃止など明示的に設定された状態は維持する。
        connection.execute(
            "UPDATE devices SET status = '稼働中' WHERE status IS NULL OR status = ''")


if __name__ == "__main__":
    init_db()
    print("データベースを初期化・更新しました")
