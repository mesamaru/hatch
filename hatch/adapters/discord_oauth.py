"""Discord の OAuth2（ログイン）。Bot とは別。docs/EXTERNAL.md 4章。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlencode

import httpx

from ..errors import AppError
from .http import ServiceClient

API = "https://discord.com/api/v10"
AUTHORIZE = "https://discord.com/oauth2/authorize"
SCOPES = "identify email guilds.members.read"


@dataclass(frozen=True)
class DiscordIdentity:
    id: str
    username: str
    email: str | None
    email_verified: bool
    role_ids: tuple[str, ...]  # ギルドでのロール。メンバーでなければ None ではなく例外
    is_owner: bool = False  # Discord サーバーのオーナー（ロールが無くても管理者にする）


class DiscordOAuth(Protocol):
    def authorize_url(self, state: str) -> str: ...
    async def identify(self, code: str) -> DiscordIdentity: ...


class DiscordOAuthClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        guild_id: str,
        bot_token: str = "",
        *,
        client: httpx.AsyncClient | None = None,
    ):
        self.client_id, self.client_secret, self.redirect_uri, self.guild_id = (
            client_id,
            client_secret,
            redirect_uri,
            guild_id,
        )
        self.bot_token = bot_token
        self.http = ServiceClient("Discord", API, {}, client=client)

    def authorize_url(self, state: str) -> str:
        q = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": SCOPES,
            "state": state,
            "prompt": "none",
        }
        return f"{AUTHORIZE}?{urlencode(q)}"

    async def identify(self, code: str) -> DiscordIdentity:
        tok = await self.http.request(
            "POST",
            "/oauth2/token",
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
            },
            ok=(200,),
        )
        access = tok.json()["access_token"]
        auth = {"Authorization": f"Bearer {access}"}
        me = (await self.http.request("GET", "/users/@me", headers=auth)).json()
        res = await self.http.request("GET", f"/users/@me/guilds/{self.guild_id}/member", headers=auth, ok=(200, 404))
        if res.status_code == 404:
            raise AppError("not_member", "Discord サーバーに参加しているアカウントでログインしてください。", 403)
        member = res.json()
        return DiscordIdentity(
            id=str(me["id"]),
            username=str(me.get("username") or ""),
            email=me.get("email"),
            email_verified=bool(me.get("verified")),
            role_ids=tuple(map(str, member.get("roles") or [])),
            is_owner=await self._owner_id() == str(me["id"]),
        )

    async def _owner_id(self) -> str | None:
        """Discord サーバーのオーナー（Bot で確認する。取れなければ None でログインは続ける）。"""
        if not self.bot_token:
            return None
        try:
            res = await self.http.request(
                "GET", f"/guilds/{self.guild_id}", headers={"Authorization": f"Bot {self.bot_token}"}
            )
        except Exception:
            return None
        return str(res.json().get("owner_id") or "") or None
