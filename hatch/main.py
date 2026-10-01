"""API サーバー（Web パネルの配信を含む）。ルートの登録だけを行い、処理は各モジュールに置く。"""

from __future__ import annotations

import hmac
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import BASE_DIR, __version__, db
from .api import auth as auth_api
from .api import domains as domains_api
from .api import edge as edge_api
from .api import getting_started as getting_started_api
from .api import infra as infra_api
from .api import me as me_api
from .api import servers as servers_api
from .api import setup as setup_api
from .api import slots as slots_api
from .config import ConfigError, get_core_settings, get_settings
from .errors import AppError, install_handlers
from .health import full_report
from .logging import setup_logging

log = logging.getLogger("hatch.api")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    try:
        get_core_settings()
    except ConfigError as e:
        log.error(str(e))
        raise
    try:
        get_settings()
    except ConfigError:
        # 外部サービスの設定がまだ無い：初期設定画面だけが使える状態で起動する
        log.warning("初期設定が終わっていません。パネルを開くと初期設定画面が表示されます。")
    await db.open_pool()
    log.info("起動しました", extra={})
    try:
        yield
    finally:
        await db.close_pool()


app = FastAPI(title="hatch", version=__version__, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
install_handlers(app)


@app.exception_handler(ConfigError)
async def _config_error(_, __: ConfigError) -> JSONResponse:
    err = AppError("setup_required", "初期設定が終わっていません。パネルを開いて初期設定を済ませてください。", 503)
    return JSONResponse(err.body(), status_code=503)


app.include_router(setup_api.router)
app.include_router(edge_api.router)
app.include_router(auth_api.router)
app.include_router(me_api.router)
app.include_router(domains_api.router)
app.include_router(slots_api.router)
app.include_router(servers_api.router)
app.include_router(infra_api.router)
app.include_router(getting_started_api.router)


@app.get("/api/health")
def health():
    """update コマンドと外部の死活確認が使う。DB に接続できなければ 503。仕様を変えないこと。"""
    try:
        with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=3) as conn:
            conn.execute("SELECT 1")
    except Exception as exc:
        return JSONResponse({"status": "error", "db": exc.__class__.__name__}, status_code=503)
    return {"status": "ok", "version": __version__}


@app.get("/api/health/full")
def health_full(authorization: str | None = Header(default=None)):
    """自己監視の詳細。Uptime Kuma から `Authorization: Bearer <PD_HEALTH_TOKEN>` 付きで呼ぶ。

    - status が error なら 503（Kuma の HTTP 監視で「停止」になる）
    - degraded でも 200。Kuma のキーワード監視で `"status":"ok"` を探せば、軽い異常も検知できる
    """
    token = os.environ.get("PD_HEALTH_TOKEN", "")
    given = (authorization or "").removeprefix("Bearer ").strip()
    if not token or not hmac.compare_digest(given.encode(), token.encode()):
        return JSONResponse(
            {"error": {"code": "unauthenticated", "message": "監視用のトークンが必要です。"}}, status_code=401
        )
    rep = full_report()
    return JSONResponse(rep.as_dict(), status_code=503 if rep.status == "error" else 200)


@app.get("/api/version")
def version():
    return {"version": __version__}


class _WebFiles(StaticFiles):
    """画面は URL とスタックが正本の SPA（web/js/router.js）。存在しないパスは index.html を返し、
    そこから先の画面の切り替えはブラウザ側で行う（再読み込みで同じ画面に戻るため）。
    """

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404 and not path.startswith(("api/", "css/", "js/")):
                return await super().get_response("index.html", scope)
            raise


app.mount("/", _WebFiles(directory=BASE_DIR / "web", html=True), name="web")
