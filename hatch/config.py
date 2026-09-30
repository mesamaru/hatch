"""設定ファイル（/etc/hatch/hatch.env と、初期設定画面が書く PD_DATA_DIR/setup.env → 環境変数）の読み込みと検証。

使い方:
    from hatch.config import get_settings, get_core_settings
    s = get_settings()          # 必須の値が無ければ ConfigError（日本語で不足項目を列挙）
    c = get_core_settings()     # 初期設定の前から必要な値だけ（DB・秘密鍵など。インストーラーが作る）

一覧と意味は docs/IMPLEMENTATION.md 8.1。項目を増やしたら必ずそちらにも追記する。
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 画面・エラー表示で使う日本語名
LABELS: dict[str, str] = {
    "PD_PUBLIC_URL": "このパネルの公開 URL",
    "PD_INTERNAL_URL": "Tailscale 内から見たこのパネルの URL",
    "PD_INSTANCE": "環境の名前（prod / stg）",
    "PD_SECRET_KEY": "署名・暗号化用の秘密鍵",
    "PD_HEALTH_TOKEN": "自己監視用のトークン",
    "DATABASE_URL": "データベースの接続先",
    "PANEL_URL": "ゲームパネルの URL",
    "PANEL_APP_KEY": "ゲームパネルの Application API キー",
    "PANEL_CLIENT_KEY": "ゲームパネルの Client API キー",
    "CF_API_TOKEN": "Cloudflare の API トークン",
    "KUMA_URL": "Uptime Kuma の URL",
    "KUMA_USERNAME": "Uptime Kuma のユーザー名",
    "KUMA_PASSWORD": "Uptime Kuma のパスワード",
    "KUMA_METRICS_KEY": "Uptime Kuma の API キー",
    "KUMA_WEBHOOK_SECRET": "Uptime Kuma の Webhook 用の秘密値",
    "DISCORD_BOT_TOKEN": "Discord Bot のトークン",
    "DISCORD_CLIENT_ID": "Discord アプリのクライアントID",
    "DISCORD_CLIENT_SECRET": "Discord アプリのクライアントシークレット",
    "DISCORD_GUILD_ID": "Discord サーバーID",
    "EDGE_AGENT_TOKEN": "edge エージェントのトークン",
}


class ConfigError(RuntimeError):
    """設定の不足・誤り。メッセージはそのまま利用者（管理者）に見せる。"""


class CoreSettings(BaseSettings):
    """初期設定の前から必要な値。インストーラーが自動で作るので、利用者は入力しない。"""

    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=True, validate_default=True)

    PD_HOST: str = "0.0.0.0"
    PD_PORT: int = 8080
    PD_PUBLIC_URL: str = ""
    PD_INTERNAL_URL: str = ""
    PD_INSTANCE: str = Field(pattern=r"^[a-z0-9]{1,8}$")
    PD_SECRET_KEY: SecretStr
    PD_HEALTH_TOKEN: SecretStr
    PD_DATA_DIR: Path = Path("/var/lib/hatch")
    PD_TIMEZONE: str = "Asia/Tokyo"
    DATABASE_URL: SecretStr

    @field_validator("PD_PUBLIC_URL", "PD_INTERNAL_URL")
    @classmethod
    def _core_url(cls, v: str) -> str:
        return _check_url(v)

    @field_validator("PD_SECRET_KEY")
    @classmethod
    def _secret_len(cls, v: SecretStr) -> SecretStr:
        if len(v.get_secret_value()) < 32:
            raise ValueError("32文字以上にしてください（openssl rand -hex 32 で作れます）")
        return v

    def secret_values(self) -> list[str]:
        """ログから消すべき値の一覧（logging のマスクで使う）。"""
        out = []
        for name in type(self).model_fields:
            v = getattr(self, name)
            if isinstance(v, SecretStr) and len(v.get_secret_value()) >= 6:
                out.append(v.get_secret_value())
        return out


def _check_url(v: str) -> str:
    v = v.strip().rstrip("/")
    if v and not re.match(r"^https?://[^\s/]+", v):
        raise ValueError("http:// か https:// で始まる URL を入力してください")
    return v


class Settings(CoreSettings):
    """すべての設定。外部サービスの値は初期設定画面（または hatch.env）で入れる。"""

    # ---- ゲームパネル ----
    PANEL_KIND: str = Field(default="pterodactyl", pattern=r"^(pterodactyl|pelican)$")
    PANEL_URL: str
    PANEL_APP_KEY: SecretStr
    PANEL_CLIENT_KEY: SecretStr
    PANEL_PUBLIC_URL: str = ""

    # ---- Cloudflare ----
    CF_API_TOKEN: SecretStr

    # ---- Uptime Kuma ----
    KUMA_URL: str
    KUMA_USERNAME: str
    KUMA_PASSWORD: SecretStr
    KUMA_METRICS_KEY: SecretStr
    KUMA_WEBHOOK_SECRET: SecretStr
    KUMA_PUSH_WORKER: SecretStr | None = None
    KUMA_PUSH_SCHEDULER: SecretStr | None = None
    KUMA_PUSH_BOT: SecretStr | None = None

    # ---- Discord ----
    DISCORD_BOT_TOKEN: SecretStr
    DISCORD_CLIENT_ID: str
    DISCORD_CLIENT_SECRET: SecretStr
    DISCORD_GUILD_ID: str
    DISCORD_CHANNEL_ANNOUNCE: str = ""
    DISCORD_CHANNEL_OPS: str = ""

    # ---- edge ----
    EDGE_AGENT_TOKEN: SecretStr
    HAPROXY_PER_IP_CONN: int = Field(default=20, ge=1, le=10_000)
    HAPROXY_PER_IP_RATE: int = Field(default=30, ge=1, le=100_000)
    HAPROXY_MAX_CONN: int = Field(default=500, ge=1, le=100_000)
    UDP_PER_IP_PPS: int = Field(default=2000, ge=1, le=1_000_000)

    @field_validator("PANEL_URL", "KUMA_URL", "PANEL_PUBLIC_URL")
    @classmethod
    def _url(cls, v: str) -> str:
        return _check_url(v)

    @field_validator("PD_PUBLIC_URL", "PD_INTERNAL_URL")
    @classmethod
    def _required(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("設定されていません")
        return v

    @property
    def panel_public_url(self) -> str:
        return self.PANEL_PUBLIC_URL or self.PANEL_URL


def problems_of(err: ValidationError) -> dict[str, str]:
    """検証エラーを {キー: 日本語の説明} にする。"""
    out: dict[str, str] = {}
    for e in err.errors():
        key = str(e["loc"][0]) if e["loc"] else "?"
        if e["type"] == "missing":
            out[key] = "設定されていません"
        else:
            out[key] = e.get("msg", "").removeprefix("Value error, ")
    return out


def _explain(err: ValidationError) -> str:
    lines = [
        "設定に問題があります。パネルの初期設定画面で直してください"
        "（開き方はコンテナ内で hatch-setup を実行すると表示されます）。"
    ]
    for key, msg in problems_of(err).items():
        lines.append(f"  - {key}（{LABELS.get(key, key)}）: {msg}")
    return "\n".join(lines)


@lru_cache(maxsize=1)
def get_core_settings() -> CoreSettings:
    try:
        return CoreSettings()  # type: ignore[call-arg]
    except ValidationError as e:
        raise ConfigError(_explain(e)) from None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as e:
        raise ConfigError(_explain(e)) from None


def setup_problems(**overrides: str) -> dict[str, str]:
    """すべての設定が揃っているかを確かめる（揃っていれば空）。overrides は環境変数より優先する。"""
    try:
        Settings(**overrides)  # type: ignore[arg-type]
    except ValidationError as e:
        return problems_of(e)
    return {}
