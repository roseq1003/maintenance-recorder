"""LINEへの実施通知と、期限前・期限超過の定期通知を担当する。

外部からのWebhookは受け取らず、アプリ／定期実行側からLINE Messaging APIへ
HTTPSで送信するだけの構成。認証情報は環境変数から取得する。
"""

from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from typing import Optional, Union

if __package__:
    from .init_db import init_db
    from .scheduling import scheduled_date
else:
    from init_db import init_db
    from scheduling import scheduled_date


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "maintenance.db"
LINE_TOKEN_URL = "https://api.line.me/oauth2/v3/token"
LINE_BROADCAST_URL = "https://api.line.me/v2/bot/message/broadcast"
JST = timezone(timedelta(hours=9))


def line_configured() -> bool:
    """LINE送信に必要なChannel ID / Secretが両方あるか確認する。"""
    return bool(
        os.environ.get("LINE_CHANNEL_ID", "").strip()
        and os.environ.get("LINE_CHANNEL_SECRET", "").strip()
    )


def issue_stateless_token() -> str:
    """Channel ID / Secretから15分有効のステートレスアクセストークンを取得する。"""
    channel_id = os.environ.get("LINE_CHANNEL_ID", "").strip()
    channel_secret = os.environ.get("LINE_CHANNEL_SECRET", "").strip()
    if not channel_id or not channel_secret:
        raise RuntimeError("LINE_CHANNEL_ID / LINE_CHANNEL_SECRET が設定されていません")

    body = urlencode({
        "grant_type": "client_credentials",
        "client_id": channel_id,
        "client_secret": channel_secret,
    }).encode("utf-8")
    request = Request(
        LINE_TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        data = json.loads(response.read().decode("utf-8"))
    token = data.get("access_token", "")
    if not token:
        raise RuntimeError("LINEのアクセストークンを取得できませんでした")
    return token


def send_line_broadcast(text: str) -> bool:
    """公式アカウントの友だち全員へテキストを送る。失敗してもアプリ本体は止めない。"""
    if not line_configured():
        return False

    try:
        token = issue_stateless_token()
        body = json.dumps({
            "messages": [{"type": "text", "text": text}],
            "notificationDisabled": False,
        }, ensure_ascii=False).encode("utf-8")
        request = Request(
            LINE_BROADCAST_URL,
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            return 200 <= response.status < 300
    except (HTTPError, URLError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        # 通知失敗で実施記録の保存や定期処理そのものを失敗させない。
        print(f"LINE通知に失敗しました: {error}", file=sys.stderr)
        return False


def completion_message(device_name: str, plan, performed_at: str) -> str:
    """実施記録登録後に送る本文を作る。日付周期なら次回予定も載せる。"""
    plan_for_schedule = dict(plan)
    # 呼び出し側が最新実施日を渡した場合はそれを優先する。
    # 過去日の記録を後から追加しても、次回予定を古い日付基準にしないため。
    plan_for_schedule["last_performed"] = (
        plan_for_schedule.get("last_performed") or performed_at
    )
    due = scheduled_date(plan_for_schedule)

    lines = [
        "【メンテナンス実施】",
        f"{device_name} / {plan['name']}",
        f"実施日: {performed_at}",
    ]
    if due is not None:
        lines.append(f"次回予定: {due.isoformat()}")
    return "\n".join(lines)


def notify_completion(device_name: str, plan, performed_at: str) -> bool:
    """実施記録の保存後にLINEへアナウンスする。"""
    return send_line_broadcast(completion_message(device_name, plan, performed_at))


def _scheduled_candidates(
    connection: sqlite3.Connection,
    current: date,
    reminder_days: int,
) -> list[dict]:
    """未通知の期限前リマインド・期限超過アラートを抽出する。"""
    plans = connection.execute("""
        SELECT p.*, d.name AS device,
               (SELECT MAX(r.performed_at)
                FROM maintenance_records r
                WHERE r.maintenance_plan_id = p.id) AS last_performed
        FROM maintenance_plans p
        JOIN devices d ON d.id = p.device_id
        WHERE d.status = '稼働中'
          AND d.maintenance_scope != '非該当'
        ORDER BY p.id
    """).fetchall()

    candidates: list[dict] = []
    for plan in plans:
        due = scheduled_date(plan)
        if due is None:
            continue

        days = (due - current).days
        if 0 <= days <= reminder_days:
            notification_type = "reminder"
        elif days < 0:
            notification_type = "overdue"
        else:
            continue

        target_date = due.isoformat()
        already_sent = connection.execute("""
            SELECT 1
            FROM notification_logs
            WHERE notification_type = ?
              AND maintenance_plan_id = ?
              AND target_date = ?
        """, (notification_type, plan["id"], target_date)).fetchone()
        if already_sent is not None:
            continue

        candidates.append({
            "notification_type": notification_type,
            "maintenance_plan_id": plan["id"],
            "device": plan["device"],
            "task": plan["name"],
            "target_date": target_date,
            "days": days,
        })
    return candidates


def scheduled_message(candidates: list[dict]) -> str:
    """定期チェック対象を1通にまとめる。"""
    reminders = [item for item in candidates if item["notification_type"] == "reminder"]
    overdue = [item for item in candidates if item["notification_type"] == "overdue"]

    lines = ["【メンテナンス通知】"]
    if reminders:
        lines.append("")
        lines.append("■ 期限が近い項目")
        for item in reminders:
            timing = "本日" if item["days"] == 0 else f"あと{item['days']}日"
            lines.append(
                f"・{item['device']} / {item['task']} "
                f"({item['target_date']}・{timing})"
            )
    if overdue:
        lines.append("")
        lines.append("■ 期限超過")
        for item in overdue:
            lines.append(
                f"・{item['device']} / {item['task']} "
                f"({item['target_date']}・{-item['days']}日超過)"
            )
    return "\n".join(lines)


def send_scheduled_notifications(
    db_path: Union[str, Path] = DB_PATH,
    current: Optional[date] = None,
    reminder_days: int = 7,
) -> int:
    """期限前・期限超過をLINEへ1通で送り、成功した対象だけ送信済みにする。"""
    current = current or datetime.now(JST).date()
    if reminder_days < 0:
        raise ValueError("reminder_daysは0以上で指定してください")

    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        candidates = _scheduled_candidates(connection, current, reminder_days)
        if not candidates:
            return 0

        if not send_line_broadcast(scheduled_message(candidates)):
            return 0

        sent_at = datetime.now(JST).isoformat(timespec="seconds")
        with connection:
            connection.executemany("""
                INSERT OR IGNORE INTO notification_logs (
                    notification_type, maintenance_plan_id, target_date, sent_at
                )
                VALUES (?, ?, ?, ?)
            """, [
                (
                    item["notification_type"],
                    item["maintenance_plan_id"],
                    item["target_date"],
                    sent_at,
                )
                for item in candidates
            ])
        return len(candidates)


def reminder_days_from_env() -> int:
    """REMINDER_DAYS未設定なら7日前から1回だけ通知する。"""
    raw = os.environ.get("REMINDER_DAYS", "7").strip()
    try:
        value = int(raw)
        if value < 0:
            raise ValueError
        return value
    except ValueError:
        raise RuntimeError("REMINDER_DAYSは0以上の整数で設定してください")


if __name__ == "__main__":
    if not line_configured():
        print("LINE_CHANNEL_ID / LINE_CHANNEL_SECRET が未設定のため通知しません。")
        raise SystemExit(1)

    try:
        sent = send_scheduled_notifications(
            DB_PATH,
            reminder_days=reminder_days_from_env(),
        )
    except RuntimeError as error:
        print(error, file=sys.stderr)
        raise SystemExit(1)

    print(f"LINE定期通知の対象: {sent}件")
