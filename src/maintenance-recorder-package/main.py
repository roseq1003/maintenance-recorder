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
    "/static",
    StaticFiles(directory="static"),
    name="static"
)


# HTMLテンプレートはtemplatesフォルダに置く
templates = Jinja2Templates(directory="templates")


# "/" にアクセスされたときの処理
@app.get("/")
def home(request: Request):

    # templates/index.htmlをブラウザへ返す
    return templates.TemplateResponse(
        request=request,
        name="index.html"
    )