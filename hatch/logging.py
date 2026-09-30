"""JSON 1行のログ。秘密の値（設定のキーやトークン）はマスクしてから出す。

from hatch.logging import setup_logging, bind
setup_logging()
log = logging.getLogger("hatch.jobs")
log.info("手順を開始", extra=bind(job_id=12, server_id="..."))
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

_EXTRA_KEYS = ("job_id", "server_id", "user_id", "step", "service")
# 値が分からなくても消すべき形（Bearer トークン、ptla_/ptlc_ キー、URL の認証情報）
_PATTERNS = [
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bptl[ac]_[A-Za-z0-9]{8,}"),
    re.compile(r"(?i)(://[^:/@\s]+:)[^@\s]+(@)"),
]
_secrets: list[str] = []


def register_secrets(values: list[str]) -> None:
    """設定の秘密値を登録する（長いものから置換するため並べ替える）。"""
    global _secrets
    _secrets = sorted({v for v in values if v and len(v) >= 6}, key=len, reverse=True)


def mask(text: str) -> str:
    for v in _secrets:
        text = text.replace(v, "***")
    text = _PATTERNS[0].sub(r"\1***", text)
    text = _PATTERNS[1].sub("ptl*_***", text)
    text = _PATTERNS[2].sub(r"\1***\2", text)
    return text


def bind(**kw: Any) -> dict[str, Any]:
    return {k: v for k, v in kw.items() if v is not None}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for k in _EXTRA_KEYS:
            if hasattr(record, k):
                out[k] = getattr(record, k)
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return mask(json.dumps(out, ensure_ascii=False, default=str))


def setup_logging(level: str = "INFO") -> None:
    from .config import get_core_settings, get_settings

    for getter in (get_settings, get_core_settings):  # 初期設定の前は本体の秘密だけでも消す
        try:
            register_secrets(getter().secret_values())
            break
        except Exception:
            continue
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(level)
    # httpx は URL をそのまま INFO で出すので抑える
    logging.getLogger("httpx").setLevel(logging.WARNING)
