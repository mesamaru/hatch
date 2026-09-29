"""秘密値の暗号化と、TOTP（RFC 6238）。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..config import get_settings


def _key(purpose: str) -> bytes:
    return hashlib.sha256(f"{purpose}:".encode() + get_settings().PD_SECRET_KEY.get_secret_value().encode()).digest()


def encrypt(plain: str, purpose: str) -> str:
    nonce = secrets.token_bytes(12)
    ct = AESGCM(_key(purpose)).encrypt(nonce, plain.encode(), purpose.encode())
    return base64.urlsafe_b64encode(nonce + ct).decode()


def decrypt(token: str, purpose: str) -> str:
    raw = base64.urlsafe_b64decode(token.encode())
    return AESGCM(_key(purpose)).decrypt(raw[:12], raw[12:], purpose.encode()).decode()


def hmac_hex(value: str, purpose: str) -> str:
    return hmac.new(_key(purpose), value.encode(), hashlib.sha256).hexdigest()


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# ---- TOTP ----
STEP = 30
DIGITS = 6


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _hotp(secret_b32: str, counter: int) -> str:
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[o : o + 4])[0] & 0x7FFFFFFF) % 10**DIGITS
    return f"{code:0{DIGITS}d}"


def totp_now(secret_b32: str, at: float | None = None) -> str:
    return _hotp(secret_b32, int((at or time.time()) // STEP))


def totp_match(secret_b32: str, code: str, *, after_counter: int = 0, at: float | None = None) -> int | None:
    """前後1枠（±30秒）まで許す。一致した時刻枠を返す（after_counter 以下は再利用として拒否）。"""
    code = (code or "").strip().replace(" ", "")
    if len(code) != DIGITS or not code.isdigit():
        return None
    now = int((at or time.time()) // STEP)
    for c in (now - 1, now, now + 1):
        if c > after_counter and hmac.compare_digest(_hotp(secret_b32, c), code):
            return c
    return None


def otpauth_url(secret_b32: str, account: str, issuer: str = "pterodeploy") -> str:
    return (
        f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret_b32}&issuer={quote(issuer)}&digits=6&period=30"
    )
