"""サーバー名・ホスト名・ユーザー名の検証（純粋関数）。

戻り値は「問題点の日本語の説明」。問題が無ければ None。
予約名は DB の reserved_names が正本。ここでは呼び出し側から集合として受け取る。
"""

from __future__ import annotations

import re

SERVER_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,30}[a-z0-9]$")
HOST_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,31}$")
DOMAIN_RE = re.compile(r"^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")

# DB の初期データと同じ。DB を読めない場面（画面の即時チェックなど）の既定値
DEFAULT_RESERVED = frozenset(
    {
        "www",
        "edge",
        "edge-1",
        "edge-2",
        "panel",
        "gp",
        "api",
        "status",
        "mail",
        "admin",
        "play",
        "kuma",
        "dev",
        "_minecraft",
    }
)


def server_name_problem(name: str, reserved: frozenset[str] | set[str] = DEFAULT_RESERVED) -> str | None:
    if not name:
        return "名前を入力してください"
    if name != name.lower():
        return "英小文字で入力してください"
    if not SERVER_NAME_RE.match(name):
        return "英小文字・数字・ハイフンで3〜32文字にしてください（先頭と末尾は英数字）"
    if "--" in name:
        return "ハイフンを2つ続けることはできません"
    if name in reserved:
        return "システムで使う名前のため選べません"
    return None


def host_label_problem(label: str, reserved: frozenset[str] | set[str] = DEFAULT_RESERVED) -> str | None:
    if not label:
        return "ホスト名が空です"
    if len(label) > 63:
        return f"「{label[:20]}…」は長すぎます（63文字まで）"
    if not HOST_LABEL_RE.match(label):
        return f"「{label}」はホスト名に使えません（英小文字・数字・ハイフン、先頭と末尾は英数字）"
    if label in reserved:
        return f"「{label}」はシステムで使う名前です"
    return None


def username_problem(name: str) -> str | None:
    if not USERNAME_RE.match(name or ""):
        return "英小文字・数字・_ . - で3〜32文字にしてください（先頭は英数字）"
    return None


def domain_problem(name: str) -> str | None:
    if not DOMAIN_RE.match(name or "") or len(name) > 253:
        return "ドメインの形式が正しくありません（例 nuids.jp）"
    return None


def fqdn(host: str, domain: str) -> str:
    return domain if host == "@" else f"{host}.{domain}"
