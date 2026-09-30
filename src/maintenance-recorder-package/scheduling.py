"""DBや画面表示から独立した、メンテナンスの予定日計算。

main.home() がDBから取得した項目を渡し、画面用の分類結果を受け取る。
現在日を引数で渡すため、月末・年末なども任意の日付でテストできる。
"""

from calendar import monthrange
from datetime import date, timedelta
from typing import Optional


def next_date(base: date, value: int, unit: str) -> date:
    """日・月・年の周期を加算し、存在しない日は移動先の月末に合わせる。"""
    # 呼び出し側で、unitがday/month/yearであることを確認してから使う。
    if unit == "day":
        return base + timedelta(days=value)
    months = value * 12 if unit == "year" else value
    # 月を通算値に直し、divmod（商と余り）で年と0始まりの月に戻す。
    # 例：1月31日の1か月後は、minで日を切り詰めて2月末にする。
    year, month = divmod(base.year * 12 + base.month - 1 + months, 12)
    month += 1
    return date(year, month, min(base.day, monthrange(year, month)[1]))


def scheduled_date(plan) -> Optional[date]:
    """最新実施日＋周期。未実施なら初回予定日を使う。"""
    if plan["interval_unit"] not in ("day", "month", "year"):
        return None
    try:
        value = int(plan["interval_value"])
        if value < 1:
            return None
        if plan["last_performed"]:
            return next_date(date.fromisoformat(plan["last_performed"]), value, plan["interval_unit"])
        # 記録が一度もない場合だけ初回予定日を使う。期限超過でも先送りしない。
        if plan["first_due_date"]:
            return date.fromisoformat(plan["first_due_date"])
        return None
    except (TypeError, ValueError, OverflowError):
        # 古いDBの不正値や計算可能な年を超える周期は、日付未確定として扱う。
        return None


def group_schedules(plans, current: date) -> dict:
    """項目を今月・来月・期限超過・日付未確定に分け、表示順に並べる。

    plansの各行には項目の周期・初回予定日・last_performed（最新実施日）が必要。
    再来月以降の予定は今回の表示対象に含めない。元の行は変更しない。
    """
    next_month = next_date(current.replace(day=1), 1, "month")
    following_month = next_date(next_month, 1, "month")
    maintenance_list = []
    next_month_list = []
    overdue_list = []
    undated_list = []
    for plan in plans:
        # sqlite3.Rowは直接書き換えられないため、表示用の辞書へコピーする。
        item = dict(plan)
        due = scheduled_date(plan)
        if due is None:
            item["reason"] = ("日付では管理しない周期" if plan["interval_unit"] in ("km", "hour", "count")
                              else "初回予定日・周期を確認してください")
            undated_list.append(item)
            continue
        item["date"] = due.isoformat()
        if due < current:
            overdue_list.append(item)
        elif due < next_month:
            maintenance_list.append(item)
        elif due < following_month:
            next_month_list.append(item)
    # ISO形式の日付文字列は文字列順と日付順が一致する。
    # lambdaはその場で使う短い関数。同日なら項目IDで順序を安定させる。
    for items in (maintenance_list, next_month_list, overdue_list):
        items.sort(key=lambda item: (item["date"], item["id"]))
    return {
        "maintenance_list": maintenance_list,
        "next_month_list": next_month_list,
        "overdue_list": overdue_list,
        "undated_list": undated_list,
        "this_month": f"{current.year}年{current.month}月",
        "next_month": f"{next_month.year}年{next_month.month}月",
    }
