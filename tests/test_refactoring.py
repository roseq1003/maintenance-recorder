"""予定計算とHTTP経由の登録・編集を、実データに触れず確認する回帰テスト。"""

from contextlib import closing
from datetime import date
import http.client
import json
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "maintenance-recorder-package"))
import main
from init_db import init_db
from scheduling import group_schedules, next_date, scheduled_date


def plan(plan_id=1, due="2026-09-30", **changes):
    """DB行と同じキーを持つテスト用項目を作る。変更したい値だけ上書きする。"""
    values = dict(id=plan_id, first_due_date=due, last_performed=None,
                  interval_value=1, interval_unit="month")
    values.update(changes)
    return values


class SchedulingTests(unittest.TestCase):
    def test_calendar_boundaries(self):
        for base, value, unit, expected in [
            (date(2024, 1, 31), 1, "month", date(2024, 2, 29)),
            (date(2024, 2, 29), 1, "year", date(2025, 2, 28)),
            (date(2026, 12, 31), 1, "month", date(2027, 1, 31)),
            (date(2026, 12, 31), 1, "day", date(2027, 1, 1)),
        ]:
            with self.subTest(base=base, unit=unit):
                self.assertEqual(next_date(base, value, unit), expected)

    def test_latest_record_takes_precedence(self):
        self.assertEqual(scheduled_date(plan(last_performed="2026-08-31")), date(2026, 9, 30))
        self.assertEqual(scheduled_date(plan(due="2026-01-01")), date(2026, 1, 1))

    def test_undated_and_invalid_values(self):
        for changes in [dict(interval_unit="km"), dict(interval_unit="hour"),
                        dict(interval_unit="count"), dict(first_due_date=""),
                        dict(interval_value=0), dict(interval_value=None),
                        dict(last_performed="invalid"),
                        dict(last_performed="9999-12-31")]:
            with self.subTest(changes=changes):
                self.assertIsNone(scheduled_date(plan(**changes)))

    def test_group_boundaries_and_stable_order(self):
        rows = [plan(3), plan(2), plan(1, "2026-09-29"),
                plan(4, "2026-10-01"), plan(5, "2026-10-31"),
                plan(6, "2026-11-01"), plan(7, "")]
        grouped = group_schedules(rows, date(2026, 9, 30))
        for key, expected in [("maintenance_list", [2, 3]), ("overdue_list", [1]),
                              ("next_month_list", [4, 5]), ("undated_list", [7])]:
            self.assertEqual([item["id"] for item in grouped[key]], expected)
        self.assertNotIn("date", rows[0])
        december = group_schedules([plan(due="2027-01-01")], date(2026, 12, 31))
        self.assertEqual(december["next_month"], "2027年1月")
        self.assertEqual(len(december["next_month_list"]), 1)


class HttpTests(unittest.TestCase):
    def setUp(self):
        """空きポートと一時DBを使い、実際のFastAPIアプリを起動する。"""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db_path = Path(temporary.name) / "test.db"
        config_path = Path(temporary.name) / "config.json"
        config_path.write_text(json.dumps({"qr_base_url": "http://example.test:8000"}))
        for name, value in [("DB_PATH", self.db_path), ("QR_CONFIG_PATH", config_path),
                            ("today", lambda: date(2026, 9, 30))]:
            replacement = patch.object(main, name, value)
            replacement.start()
            self.addCleanup(replacement.stop)
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        self.addCleanup(listener.close)
        self.port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(main.app, log_level="critical"))
        worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        worker.start()

        def stop_server():
            server.should_exit = True
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive(), "テスト用サーバーが終了しませんでした")

        self.addCleanup(stop_server)
        deadline = time.monotonic() + 5
        while not server.started and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(server.started, "テスト用サーバーが起動しませんでした")

    def request(self, path, data=None):
        """リダイレクトを追わず、ステータス・ヘッダー・本文を返す。"""
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request("GET" if data is None else "POST", path,
                               body=None if data is None else urlencode(data),
                               headers={"Content-Type": "application/x-www-form-urlencoded"})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def seed(self):
        """登録APIで機器と項目を用意する。各テストは新規DBなのでIDは1。"""
        self.assertEqual(self.request("/devices/new", {"name": "空気清浄機"})[0], 303)
        self.assertEqual(self.request("/devices/1/maintenance/new", {
            "name": "フィルター掃除", "interval_value": "1", "interval_unit": "month",
            "priority": "中", "first_due_date": "2026-09-30",
        })[0], 303)

    def test_pages_and_qr(self):
        self.seed()
        for path in ["/", "/devices", "/devices/new", "/devices/1", "/devices/1/edit",
                     "/devices/1/maintenance/new", "/devices/1/maintenance-plans/1/edit",
                     "/devices/1/maintenance-plans/1/records/new", "/devices/1/qr"]:
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 200)
        status, headers, body = self.request("/devices/1/qr.png?download=true")
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertIn("attachment", headers["content-disposition"])

    def test_record_lifecycle_updates_schedule(self):
        self.seed()
        root = "/devices/1/maintenance-plans/1/records"
        self.assertEqual(self.request(root, {"performed_at": "2026-09-30", "meter_value": "0"})[0], 303)
        self.assertIn(b"2026-10-30", self.request("/")[2])
        self.assertEqual(self.request(root + "/1/edit")[0], 200)
        self.assertEqual(self.request(root + "/1/edit", {"performed_at": "2026-08-30"})[0], 303)
        self.assertIn(b"2026-09-30", self.request("/")[2])
        self.assertEqual(self.request(root + "/1/delete")[0], 200)
        with closing(sqlite3.connect(self.db_path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM maintenance_records").fetchone()[0], 1)
        self.assertEqual(self.request(root + "/1/delete", {})[0], 303)
        with closing(sqlite3.connect(self.db_path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM maintenance_records").fetchone()[0], 0)
        self.assertIn(b"2026-09-30", self.request("/")[2])

    def test_form_errors_preserve_values(self):
        self.seed()
        for path, data, heading in [
            ("/devices/new", {"name": "入力保持", "purchase_date": "bad"}, "機器を登録"),
            ("/devices/1/edit", {"name": "入力保持", "status": "bad", "maintenance_scope": "対象"}, "機器情報を編集"),
            ("/devices/1/maintenance/new", {"name": "入力保持", "interval_value": "bad",
                "interval_unit": "month", "priority": "中"}, "メンテナンス項目を追加"),
            ("/devices/1/maintenance-plans/1/records", {"performed_at": "bad", "note": "入力保持"}, "実施記録を追加"),
        ]:
            with self.subTest(path=path):
                status, _, body = self.request(path, data)
                self.assertEqual(status, 422)
                self.assertIn("入力保持".encode(), body)
                self.assertIn(heading.encode(), body)
        with closing(sqlite3.connect(self.db_path)) as connection:
            self.assertEqual(connection.execute("SELECT name FROM devices").fetchone()[0], "空気清浄機")

    def test_wrong_ownership_and_missing_entities(self):
        self.seed()
        self.request("/devices/new", {"name": "別の機器"})
        self.request("/devices/1/maintenance-plans/1/records", {"performed_at": "2026-09-30"})
        for path in ["/devices/999", "/devices/999/edit", "/devices/999/qr", "/devices/999/maintenance/new",
                     "/devices/2/maintenance-plans/1/edit", "/devices/2/maintenance-plans/1/records/new",
                     "/devices/2/maintenance-plans/1/records/1/edit",
                     "/devices/2/maintenance-plans/1/records/1/delete"]:
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 404)
        self.assertEqual(self.request("/devices/2/maintenance-plans/1/records", {"performed_at": "2026-09-30"})[0], 404)
        self.assertEqual(self.request("/devices/2/maintenance-plans/1/records/1/delete", {})[0], 404)

    def test_database_rollback_and_repeat_initialization(self):
        self.seed()
        with self.assertRaises(RuntimeError):
            with main.database() as connection:
                connection.execute("UPDATE devices SET name = 'rollback'")
                raise RuntimeError("意図したロールバック")
        init_db(self.db_path)
        with main.database() as connection:
            self.assertEqual(connection.execute("SELECT name FROM devices").fetchone()[0], "空気清浄機")

    def test_edits_preserve_history_and_filter_schedule(self):
        self.seed()
        self.request("/devices/1/maintenance-plans/1/records", {"performed_at": "2026-08-30"})
        self.assertEqual(self.request("/devices/1/maintenance-plans/1/edit", {
            "name": "変更後の項目", "interval_value": "1", "interval_unit": "month", "priority": "高",
        })[0], 303)
        for status, scope, visible in [("休止中", "対象", False), ("稼働中", "非該当", False),
                                       ("稼働中", "未設定", True)]:
            with self.subTest(status=status, scope=scope):
                self.assertEqual(self.request("/devices/1/edit", {
                    "name": "変更後の機器", "status": status, "maintenance_scope": scope,
                })[0], 303)
                # 履歴は対象外機器でも表示されるので、予定の判定にはテンプレートへ渡す値を使う。
                request = main.Request({"type": "http", "method": "GET", "path": "/", "headers": []})
                response = main.home(request)
                self.assertEqual(bool(response.context["maintenance_list"]), visible)
        with main.database() as connection:
            row = connection.execute("SELECT maintenance_plan_id FROM maintenance_records").fetchone()
            self.assertEqual(row[0], 1)


if __name__ == "__main__":
    unittest.main()
