"""初期設定画面の「接続確認」。入力された値で各サービスに問い合わせ、結果を日本語で返す。

どの関数も例外を投げず CheckResult を返す（画面にそのまま出すため）。秘密の値は結果に入れない。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from ..errors import TransientError, UpstreamError
from .http import ServiceClient

DISCORD_API = "https://discord.com/api/v10"
CF_API = "https://api.cloudflare.com/client/v4"
# Bot に付ける権限：チャンネルを見る・メッセージを送る・埋め込み・履歴を読む
BOT_PERMISSIONS = 1024 | 2048 | 16384 | 65536
TEXT_CHANNEL_TYPES = (0, 5)  # テキスト・アナウンス


@dataclass
class CheckResult:
    ok: bool
    message: str
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "message": self.message, **self.data}


def _unreachable(service: str, e: Exception) -> CheckResult:
    if isinstance(e, TransientError):
        return CheckResult(False, f"{service} に接続できません。URL と、このコンテナからの通信経路を確認してください。")
    if isinstance(e, UpstreamError):
        return CheckResult(False, e.message)
    return CheckResult(False, f"{service} の確認中にエラーが起きました（{type(e).__name__}）。")


def bot_invite_url(client_id: str) -> str:
    return (
        f"https://discord.com/oauth2/authorize?client_id={client_id}"
        f"&scope=bot+applications.commands&permissions={BOT_PERMISSIONS}"
    )


async def check_panel(
    url: str, app_key: str, client_key: str, *, client: httpx.AsyncClient | None = None
) -> CheckResult:
    http = ServiceClient("ゲームパネル", url, {}, client=client)
    try:
        res = await http.request(
            "GET",
            "/api/application/users?per_page=1",
            headers={"Authorization": f"Bearer {app_key}"},
            ok=(200, 401, 403, 404),
        )
        if res.status_code == 404:
            return CheckResult(False, "ゲームパネルの API が見つかりません。URL が正しいか確認してください。")
        if res.status_code != 200:
            return CheckResult(
                False,
                "Application API キーが使えません。管理画面の Application API で作ったキー（ptla_…）か、"
                "権限（Users・Nodes・Allocations・Servers・Nests・Eggs の読み書き）を確認してください。",
            )
        res = await http.request(
            "GET", "/api/client/account", headers={"Authorization": f"Bearer {client_key}"}, ok=(200, 401, 403)
        )
        if res.status_code != 200:
            return CheckResult(
                False, "Client API キーが使えません。アカウント設定の API で作ったキー（ptlc_…）か確認してください。"
            )
        if not res.json().get("attributes", {}).get("admin"):
            return CheckResult(
                False,
                "Client API キーが管理者のアカウントのものではありません。管理者でログインして作り直してください。",
            )
    except Exception as e:
        return _unreachable("ゲームパネル", e)
    finally:
        await http.aclose()
    return CheckResult(True, "ゲームパネルに接続できました。")


async def check_cloudflare(token: str, *, client: httpx.AsyncClient | None = None) -> CheckResult:
    http = ServiceClient("Cloudflare", CF_API, {"Authorization": f"Bearer {token}"}, client=client)
    try:
        res = await http.request("GET", "/user/tokens/verify", ok=(200, 400, 401, 403))
        if res.status_code != 200 or (res.json().get("result") or {}).get("status") != "active":
            return CheckResult(False, "API トークンが使えません。コピーし直すか、有効期限・IP 制限を確認してください。")
        res = await http.request("GET", "/zones?per_page=50", ok=(200, 403))
        zones = [z["name"] for z in res.json().get("result") or []] if res.status_code == 200 else []
    except Exception as e:
        return _unreachable("Cloudflare", e)
    finally:
        await http.aclose()
    if not zones:
        return CheckResult(
            False, "このトークンで操作できるドメインがありません。Zone Resources に対象のドメインを追加してください。"
        )
    return CheckResult(True, f"操作できるドメイン：{'、'.join(zones)}", {"zones": zones})


async def check_kuma(url: str, metrics_key: str, *, client: httpx.AsyncClient | None = None) -> CheckResult:
    http = ServiceClient("Uptime Kuma", url, {}, client=client)
    try:
        res = await http.request("GET", "/metrics", auth=("", metrics_key), ok=(200, 401, 403, 404))
    except Exception as e:
        return _unreachable("Uptime Kuma", e)
    finally:
        await http.aclose()
    if res.status_code == 404:
        return CheckResult(
            False, "Uptime Kuma の /metrics が見つかりません。URL（ポート番号を含む）を確認してください。"
        )
    if res.status_code != 200:
        return CheckResult(
            False, "API キーが使えません。Uptime Kuma の「設定 → API キー」で作ったキーか確認してください。"
        )
    return CheckResult(True, "Uptime Kuma に接続できました。")


async def check_discord(
    client_id: str,
    client_secret: str,
    bot_token: str,
    guild_id: str = "",
    *,
    client: httpx.AsyncClient | None = None,
) -> CheckResult:
    """クライアントID・シークレット・Bot トークンを確かめ、Bot が参加しているサーバーを返す。

    guild_id を渡すと、そのサーバーのロールとテキストチャンネルも返す（画面で選んでもらうため）。
    """
    http = ServiceClient("Discord", DISCORD_API, {}, client=client)
    invite = {"invite_url": bot_invite_url(client_id)}
    try:
        res = await http.request(
            "POST",
            "/oauth2/token",
            auth=(client_id, client_secret),
            data={"grant_type": "client_credentials", "scope": "identify"},
            ok=(200, 400, 401),
        )
        if res.status_code != 200:
            return CheckResult(
                False,
                "クライアントID かクライアントシークレットが正しくありません。"
                "OAuth2 の画面からコピーし直してください。",
            )
        bot = {"Authorization": f"Bot {bot_token}"}
        res = await http.request("GET", "/users/@me", headers=bot, ok=(200, 401))
        if res.status_code != 200:
            return CheckResult(
                False, "Bot のトークンが正しくありません。Bot の画面の「Reset Token」で作り直してコピーしてください。"
            )
        res = await http.request("GET", "/users/@me/guilds", headers=bot)
        guilds = [{"id": str(g["id"]), "name": g["name"]} for g in res.json()]
        if not guilds:
            return CheckResult(
                False,
                "Bot がまだどの Discord サーバーにも参加していません。招待してから、もう一度確認してください。",
                invite,
            )
        data: dict[str, Any] = {"guilds": guilds, **invite}
        if guild_id:
            if guild_id not in {g["id"] for g in guilds}:
                return CheckResult(False, "選んだ Discord サーバーに Bot が参加していません。招待してください。", data)
            roles = (await http.request("GET", f"/guilds/{guild_id}/roles", headers=bot)).json()
            channels = (await http.request("GET", f"/guilds/{guild_id}/channels", headers=bot)).json()
            guild = (await http.request("GET", f"/guilds/{guild_id}", headers=bot)).json()
            # メンバーの一覧は Server Members Intent が必要。オフなら人数は出さない（403）
            res = await http.request(
                "GET", f"/guilds/{guild_id}/members", headers=bot, params={"limit": 1000}, ok=(200, 403)
            )
            members = res.json() if res.status_code == 200 else None
            counts: dict[str, int] = {}
            owner_id = str(guild.get("owner_id") or "")
            owner: dict[str, Any] | None = None
            for m in members or []:
                for rid in m.get("roles") or []:
                    counts[str(rid)] = counts.get(str(rid), 0) + 1
                u = m.get("user") or {}
                if str(u.get("id")) == owner_id:
                    owner = {
                        "name": m.get("nick") or u.get("global_name") or u.get("username") or "",
                        "role_ids": [str(x) for x in m.get("roles") or []],
                    }
            data["roles"] = [
                {
                    "id": str(r["id"]),
                    "name": r["name"],
                    "members": counts.get(str(r["id"]), 0) if members is not None else None,
                }
                for r in sorted(roles, key=lambda r: -r.get("position", 0))
                if str(r["id"]) != guild_id and not r.get("managed")
            ]
            data["owner"] = owner
            data["members_intent"] = members is not None
            data["channels"] = [
                {"id": str(c["id"]), "name": c["name"]}
                for c in sorted(channels, key=lambda c: c.get("position", 0))
                if c.get("type") in TEXT_CHANNEL_TYPES
            ]
    except Exception as e:
        return _unreachable("Discord", e)
    finally:
        await http.aclose()
    return CheckResult(True, "Discord のアプリと Bot を確認できました。", data)
