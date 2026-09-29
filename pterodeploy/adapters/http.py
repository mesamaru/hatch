"""外部サービス共通の HTTP 呼び出し。

- 429・502・503・504・タイムアウト・接続失敗 → TransientError（ジョブの手順が再試行する）
- それ以外の 4xx・5xx → UpstreamError（再試行しない）
- アダプター自身は再試行しない（ジョブ基盤の再試行と掛け算にならないように）
- 応答本文は500文字で切り、秘密をマスクしてからエラーに入れる
"""

from __future__ import annotations

from typing import Any

import httpx

from ..errors import TransientError, UpstreamError
from ..logging import mask

TRANSIENT_STATUS = {429, 502, 503, 504}
DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class ServiceClient:
    def __init__(
        self,
        service: str,
        base_url: str,
        headers: dict[str, str],
        *,
        client: httpx.AsyncClient | None = None,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    ):
        self.service = service
        self._own = client is None
        self.client = client or httpx.AsyncClient(timeout=timeout)
        self.base_url = base_url.rstrip("/")
        self.headers = {"Accept": "application/json", "User-Agent": "pterodeploy", **headers}

    async def aclose(self) -> None:
        if self._own:
            await self.client.aclose()

    async def request(
        self, method: str, path: str, *, ok: tuple[int, ...] = (200, 201, 204), **kw: Any
    ) -> httpx.Response:
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        headers = {**self.headers, **kw.pop("headers", {})}
        try:
            res = await self.client.request(method, url, headers=headers, **kw)
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as e:
            raise TransientError(self.service, f"接続できません（{type(e).__name__}）") from None
        if res.status_code in ok:
            return res
        body = mask(res.text[:500])
        if res.status_code in TRANSIENT_STATUS:
            raise TransientError(self.service, f"HTTP {res.status_code}")
        raise UpstreamError(self.service, self.explain(res, body), res.status_code)

    def explain(self, res: httpx.Response, body: str) -> str:
        """サービスごとにエラー本文から読みやすい説明を作る（上書き用）。"""
        return f"HTTP {res.status_code} {body}"
