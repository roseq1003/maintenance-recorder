# FastAPI本体
from fastapi import FastAPI

# HTMLファイルを扱うための機能
from fastapi.templating import Jinja2Templates

# CSSやJavaScriptなどの静的ファイルを扱う
from fastapi.staticfiles import StaticFiles

# ブラウザから送られてきたリクエスト情報を扱う
from fastapi import Request


# FastAPIアプリを作成
app = FastAPI()


# 「/static」というURLで
# staticフォルダの中身を使えるようにする
app.mount(
    "/static",  # 「プロジェクト内の static フォルダを、
    StaticFiles(directory="static"),  # Web上では /static というURLで公開します。
    name="static"  # この設定の名前は static にします」
)


# HTMLテンプレートはtemplatesフォルダに置く
templates = Jinja2Templates(directory="templates")


# "/" にアクセスされたときの処理
@app.get("/")
def home(request: Request):

    # 今はDBを使わず、Python側で直接8を設定
    device_count = 8323123
    maintenance_list = [
        {
            "date": "2026/09/20",
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
        },
        {
            "date": "2026/09/23232",
            "device": "たんぽぽ",
            "task": "洗濯槽クリーニング",
            "location": "休憩室",
            "priority": "中"
        }
    ]

    return templates.TemplateResponse(
        request=request,
        name="index.html",

        # HTML（Jinja2）へ渡すデータ
        context={
            "device_count": device_count,
            "maintenance_list": maintenance_list
        }
    )
