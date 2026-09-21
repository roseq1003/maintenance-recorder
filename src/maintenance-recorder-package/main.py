# FastAPI本体
from fastapi import FastAPI
from pathlib import Path

# HTMLファイルを扱うための機能
from fastapi.templating import Jinja2Templates

# CSSやJavaScriptなどの静的ファイルを扱う
from fastapi.staticfiles import StaticFiles

# ブラウザから送られてきたリクエスト情報を扱う
from fastapi import Request


# FastAPIアプリを作成
app = FastAPI()
BASE_DIR = Path(__file__).resolve().parent


# 「/static」というURLで
# staticフォルダの中身を使えるようにする
app.mount(
    "/static",  # 「プロジェクト内の static フォルダを、
    StaticFiles(directory=BASE_DIR / "static"),  # 起動する場所が変わっても読み込めるようにします。
    name="static"  # この設定の名前は static にします」
)


# HTMLテンプレートはtemplatesフォルダに置く
templates = Jinja2Templates(directory=BASE_DIR / "templates")


# "/" にアクセスされたときの処理
@app.get("/")
def home(request: Request):

    # 今はDBを使わず、Python側で直接8を設定
    device_count = 8
    maintenance_list = [
        {
            "date": "2026/09/22",
            "device": "エアコン",
            "task": "フィルター清掃",
            "location": "事務所 2F",
            "priority": "中"
        },
        {
            "date": "2026/09/25",
            "device": "自家用車",
            "task": "エンジンオイル交換",
            "location": "駐車場",
            "priority": "高"
        },
        {
            "date": "2026/09/28",
            "device": "洗濯機",
            "task": "洗濯槽クリーニング",
            "location": "休憩室",
            "priority": "中"
        }
    ]

    # STEP 1の表示サンプル。日付による自動振り分けはSTEP 9で追加します。
    next_month_list = [
        {"date": "2026/10/05", "device": "コンプレッサー", "task": "ドレン排出",
         "location": "作業場", "priority": "中"},
        {"date": "2026/10/15", "device": "給湯器", "task": "外観点検",
         "location": "屋外", "priority": "高"},
    ]
    overdue_list = [
        {"date": "2026/09/10", "device": "エアコン", "task": "室外機点検",
         "location": "事務所 2F", "priority": "高"},
    ]
    recent_history = [
        {"date": "2026/09/18", "device": "洗濯機", "task": "糸くずフィルター清掃",
         "performed_by": "山田", "note": "汚れを除去。異常なし。", "next_due_date": "2026/10/18"},
        {"date": "2026/09/12", "device": "自家用車", "task": "タイヤ空気圧点検",
         "performed_by": "佐藤", "note": "規定値に調整。", "next_due_date": "2026/10/12"},
    ]

    return templates.TemplateResponse(
        request=request,
        name="index.html",

        # HTML（Jinja2）へ渡すデータ
        context={
            "device_count": device_count,
            "maintenance_list": maintenance_list,
            "next_month_list": next_month_list,
            "overdue_list": overdue_list,
            "recent_history": recent_history,
        }
    )
