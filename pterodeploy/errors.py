"""業務エラーと、API のエラー応答の形。

    raise AppError("slot_taken", "このアドレスは使用中です。別のアドレスを選んでください。", status=409)

応答: {"error": {"code": "...", "message": "...", "detail": {...}}}
code の一覧は docs/API.md の末尾。新しい code を作ったらそちらにも追記する。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("pterodeploy.errors")


class AppError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, detail: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.detail = detail

    def body(self) -> dict[str, Any]:
        err: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.detail:
            err["detail"] = self.detail
        return {"error": err}


class TransientError(Exception):
    """一時的な失敗（429・502・503・504・タイムアウト）。ジョブの手順は再試行してよい。"""

    def __init__(self, service: str, message: str):
        super().__init__(f"{service}: {message}")
        self.service = service


class UpstreamError(AppError):
    """外部サービスがエラーを返した（再試行しても直らない）。"""

    def __init__(self, service: str, message: str, status_code: int | None = None):
        super().__init__(
            "upstream_error",
            f"{service} がエラーを返しました：{message}",
            status=502,
            detail={"service": service, "status_code": status_code},
        )
        self.service = service
        self.status_code = status_code


# ---- 入力検証のエラーを日本語に ----
_PYDANTIC_JA = {
    "missing": "入力してください",
    "string_too_short": "短すぎます",
    "string_too_long": "長すぎます",
    "string_pattern_mismatch": "形式が正しくありません",
    "int_parsing": "整数で入力してください",
    "greater_than_equal": "小さすぎます",
    "less_than_equal": "大きすぎます",
    "literal_error": "選べない値です",
    "json_invalid": "JSON の形式が正しくありません",
}


def _field_errors(exc: RequestValidationError) -> dict[str, str]:
    out: dict[str, str] = {}
    for e in exc.errors():
        loc = [str(x) for x in e.get("loc", []) if x not in ("body", "query", "path")]
        out[".".join(loc) or "_"] = _PYDANTIC_JA.get(e.get("type", ""), "値が正しくありません")
    return out


def install_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(exc.body(), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        err = AppError("validation", "入力内容を確認してください。", 400, {"fields": _field_errors(exc)})
        return JSONResponse(err.body(), status_code=400)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code, msg = {
            404: ("not_found", "見つかりません。"),
            405: ("method_not_allowed", "この操作はできません。"),
        }.get(exc.status_code, ("http_error", "リクエストを処理できませんでした。"))
        return JSONResponse(AppError(code, msg, exc.status_code).body(), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.exception("想定外のエラー")
        err = AppError("internal", "内部でエラーが起きました。時間をおいてもう一度試してください。", 500)
        return JSONResponse(err.body(), status_code=500)
