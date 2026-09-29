# HTTP API 仕様

画面・Discord Bot・edge エージェントが使う API です。**Bot も画面と同じ API を内部から呼ぶ**（同じ権限チェックを通すため。Bot は `X-Acting-User: <user_id>` とサービス用トークンで呼ぶ）。

## 共通

- ベース：`/api`。JSON のみ。文字コードは UTF-8。
- 認証：Cookie `pd_session`（HttpOnly・Secure・SameSite=Lax、30日）。変更系（POST・PUT・PATCH・DELETE）は `X-CSRF-Token` ヘッダーが必須（値は `GET /api/me` が返す）。
- 管理者は二段階認証（TOTP）を通過したセッションでなければ `/api/admin/*` を使えない（`403 totp_required`）。
- 時刻は ISO 8601（UTC）。一覧はカーソル方式：`?limit=50&cursor=<前回の next_cursor>`、応答は `{"items": [...], "next_cursor": "…"|null}`。
- 外部サービスを伴う操作は **ジョブ** を作って `202 {"job": Job}` を返す。画面は `GET /api/jobs/{id}` を1秒ごと（完了まで）に取得して進捗を表示する。
- エラー：`{"error": {"code": "...", "message": "日本語の説明", "detail": {...}}}`。コード一覧は末尾。
- ドライラン：作成・削除系は `?dry_run=1` を付けると、実行せずに「実行する内容」を返す（`200 {"plan": [PlanItem]}`）。

## オブジェクト

```jsonc
// Server
{
  "id": "uuid", "name": "storia", "owner": {"id": "uuid", "username": "tanaka"},
  "game": "storiamc", "plan": "standard",
  "status": "running",            // pending|provisioning|running|maintenance|suspended|stopped|trashed|failed
  "address": "mc05trt.nuids.jp",  // 利用者が接続するアドレス（独自ドメインが有効ならそれ）
  "slot": {"port": 25565, "host": "mc05trt", "domain": "nuids.jp", "rule_id": 1},
  "direct": "edge.nuids.jp:25565",
  "node": "node-tokyo-1", "is_dev_copy": false, "proxy": null,     // proxy: {"id","name"}|null
  "auto_restart": true, "public_status": false,
  "expires_at": "2026-10-29T00:00:00Z", "maintenance_until": null,
  "suspend_reason": null,
  "my_permission": "owner",        // admin|owner|view|console|files|full
  "monitor": {"state": "up", "ping_ms": 38, "uptime_24h": 99.9} ,  // state: up|down|paused|pending|unknown
  "job": null                      // 実行中のジョブがあれば Job
}
// Job
{"id": 1842, "kind": "deploy", "status": "running", "current_step": "ノードを選んでサーバーを作成",
 "steps": [{"seq": 1, "name": "依頼の検証", "status": "done"}, ...], "error": null, "server_id": "uuid"}
// PlanItem（ドライラン）
{"kind": "create_dns", "title": "DNS を作成", "detail": "CNAME mc05trt.nuids.jp → edge.nuids.jp"}
```

---

## 認証・自分

| メソッド | パス | 説明 |
|---|---|---|
| GET | /auth/login | Discord の認可画面へリダイレクト（`state` を Cookie に保存） |
| GET | /auth/callback | Discord から戻る。成功なら `/` へ、失敗なら `/?login_error=<code>` へ移動する。code：`state`（やり直し）、`not_member`（Discord サーバーに未参加）、`not_allowed`（対象ロールも招待も無い・利用禁止）、`email`（メールアドレスが未確認）、`email_taken`（同じメールの別アカウントがある）、`suspended`（利用停止中）、`discord`（Discord との通信に失敗） |
| POST | /auth/logout | セッション削除 |
| POST | /auth/totp/setup | 管理者のみ。`{"otpauth_url","secret"}` を返す（未登録時のみ） |
| POST | /auth/totp/verify | `{"code":"123456"}` → セッションの `totp_ok=true` |
| GET | /me | `{"user", "csrf_token", "needs_tos": bool, "needs_totp": bool, "limits": {"max_servers", "used"}}` |
| POST | /me/tos | `{"version"}` 同意を記録 |
| PUT | /me/password | `{"password"}`（12〜128文字）。**同期処理**：パネル側のパスワードを直接更新し、成功したら `204`。平文はジョブに保存しない |
| GET / PUT | /me/notifications | `{"down","expiry","disk"}` |
| GET | /me/audit | 自分の操作ログ |
| GET / PUT | /me/preferences | `{"theme":"auto|light|dark","glass":0.72,"background":{"kind":"default"}|{"kind":"preset","id":"aurora"}|{"kind":"upload","upload_id":"uuid"},"bg_dim":0.35,"bg_blur":0}`。GET は `default_background` も返す |
| POST | /me/background | `multipart/form-data`（`file`）。変換して保存し `{"upload_id","url"}` を返す。背景は自動で `upload` に切り替える。20MB 超は `413 file_too_large`、画像でなければ `415 unsupported_media` |
| DELETE | /me/background | アップロードした背景を削除し、既定に戻す |
| GET | /uploads/{id} | 画像の実体（本人のもの、または全員の既定に設定されたものだけ） |
| POST | /me/withdraw | 退会の申請。`{"confirm_username"}`。`{"deletion_due_at"}` を返す。最後の管理者は `409 last_admin` |
| DELETE | /me/withdraw | 退会の取り消し（期限前のみ） |

## サーバー

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | /servers | ログイン | 見えるサーバーの一覧。`?owner=`（admin のみ）、`?status=` |
| POST | /servers | ログイン | 作成。`{"name","game","plan","slot_port","owner_id"?}`（owner_id は admin のみ）→ `202 {job, server}` |
| GET | /servers/{id} | server.view | 詳細 |
| PATCH | /servers/{id} | server.settings | `{"auto_restart"?, "public_status"?}` |
| GET | /servers/{id}/resources | server.view | `{"cpu_percent","memory_bytes","memory_limit","disk_bytes","disk_limit","players":{"online","max"}}`（パネルから直接取得、5秒でタイムアウト） |
| POST | /servers/{id}/power | server.power | `{"signal":"start|stop|restart"}` → `204`。stop 中は監視を一時停止 |
| POST | /servers/{id}/maintenance | server.settings | `{"enabled": true, "until": "…"?}`（既定2時間後） |
| POST | /servers/{id}/extend | server.extend | admin：`{"days":30}` で延長。利用者：申請を作成（既に申請中なら `409 already_requested`） |
| POST | /servers/{id}/proxy | server.settings | `{"proxy_server_id": uuid|null}` → ジョブ |
| POST | /servers/{id}/dev-copy | server.dev_copy | → ジョブ |
| POST | /servers/{id}/core-update | server.plugins | → ジョブ（更新前バックアップ込み） |
| DELETE | /servers/{id} | server.trash | ゴミ箱へ → ジョブ |
| POST | /servers/{id}/restore | server.trash | ゴミ箱から戻す → ジョブ |
| POST | /servers/{id}/purge | server.trash | `{"confirm_name"}` が名前と一致しないと `400 confirm_mismatch` → ジョブ |

### バックアップ・共有・プラグイン・ドメイン

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | /servers/{id}/backups | server.view | 一覧（パネルから取得） |
| POST | /servers/{id}/backups | server.backup | 今すぐ作成（上限なら最も古い自動バックアップを削除してから） |
| GET | /servers/{id}/backups/{uuid}/download | server.backup | `{"url"}`（15分有効） |
| POST | /servers/{id}/backups/{uuid}/restore | server.restore | → ジョブ（直前に自動バックアップ） |
| GET | /servers/{id}/shares | server.view | |
| POST | /servers/{id}/shares | server.share | `{"username","permission":"view|console|files|full"}`（相手がパネルユーザーでなければ `404 user_not_found`） |
| DELETE | /servers/{id}/shares/{user_id} | server.share | |
| GET | /servers/{id}/plugins | server.view | 導入済み＋更新の有無 |
| GET | /plugins/search | ログイン | `?server=&q=` Modrinth・Hangar を検索。StoriaMC は Folia 対応のみ |
| POST | /servers/{id}/plugins | server.plugins | `{"source","project_id"}` → ジョブ |
| POST | /servers/{id}/plugins/{source}/{project_id}/update | server.plugins | → ジョブ |
| DELETE | /servers/{id}/plugins/{source}/{project_id} | server.plugins | → ジョブ |
| PUT | /servers/{id}/domain | server.domain | `{"hostname"}` → `{"records":[{"type","name","value"}]}`（利用者が設定するもの） |
| POST | /servers/{id}/domain/verify | server.domain | TXT と CNAME を確認し、成功なら active |
| DELETE | /servers/{id}/domain | server.domain | |

### アドレス・ジョブ・検索

| メソッド | パス | 説明 |
|---|---|---|
| GET | /slots/available | `?owner_id=`（admin のみ）。作成画面の選択肢：`[{"port","fqdn"|null,"rule":{"id","name","domain","host_template"},"dns_ready":bool}]`。固定ホスト名の枠を先、`{server}` の枠を後に並べる |
| GET | /jobs/{id} | 自分のサーバーのジョブか admin |
| GET | /jobs | `?server_id=` 直近20件 |
| GET | /search | `?q=` サーバー（名前・アドレス・ポート）、admin はユーザー・アドレス・管理画面も。最大30件 |

## 管理（admin のみ・二段階認証必須）

| メソッド | パス | 説明 |
|---|---|---|
| GET / POST | /admin/domains | 追加：`{"name","cf_zone_id"}` → ゾーンを確認（名前が一致しなければ `400 zone_mismatch`）し、`edge.<name>` の紐付けを作る |
| PATCH / DELETE | /admin/domains/{id} | `{"is_default"?, "edge_host"?}`。枠や紐付けがあると削除不可（`409 in_use`） |
| GET / POST / DELETE | /admin/ips, /admin/ips/{id} | `{"label","address","edge_id"?}`。紐付けや枠が使っていると削除不可 |
| GET / POST / DELETE | /admin/bindings, /admin/bindings/{id} | `{"domain_id","host","ip_id"?,"follow_active_edge"}` → ジョブ（A レコード作成） |
| GET / POST | /admin/slot-rules | 作成：SlotRule（下記） |
| POST | /admin/slot-rules/preview | 保存せずに検証と展開結果を返す：`{"problems":[str],"slots":[{"port","fqdn","excluded"}]}`。画面は入力のたびに呼ぶ（300ms 間引き） |
| GET / PATCH / DELETE | /admin/slot-rules/{id} | 変更の制限は IMPLEMENTATION.md 4.3 |
| POST | /admin/slot-rules/{id}/publish | DNS を事前作成 → ジョブ（`?dry_run=1` でレコード一覧） |
| POST | /admin/slot-rules/{id}/unpublish | → ジョブ |
| PATCH | /admin/slots/{port} | `{"status":"free|disabled"}`（assigned・held は変更不可） |
| GET | /admin/users | |
| POST | /admin/users/invite | `{"discord_username","role"}` |
| GET / PATCH | /admin/users/{id} | `{"role"?, "max_servers"?}` |
| POST | /admin/users/{id}/sync, /password-reset, /suspend, /unsuspend, /panel-admin | suspend は `{"reason"}` 必須 |
| GET / PUT | /admin/role-rules | Discord ロールの対応表を丸ごと置き換え |
| GET / POST / DELETE | /admin/announcements | `{"title","body","severity","audience":{"all":true}|{"nodes":[…]},"post_discord"}`。`?dry_run=1` で対象人数 |
| GET / POST | /admin/violations | `{"server_id","reason","action":"warning|suspend_server|suspend_user"}` |
| POST | /admin/violations/{id}/resolve | 停止も解除 |
| GET | /admin/audit | `?actor=admin|user|system&server_id=&cursor=` |
| GET | /admin/findings | 整合性チェックの結果 |
| POST | /admin/findings/run | 今すぐ実行 → ジョブ |
| POST | /admin/findings/{id}/fix | 自動修正できるもののみ |
| GET | /admin/infra | ノード（使用率・台数）、edge（使用中・適用済みの版・最終応答） |
| POST | /admin/edges/switch | `{"to":"edge-2"}` → ジョブ |
| GET / POST | /admin/extension-requests, /{id}/approve, /{id}/reject | |
| GET / PATCH | /admin/settings | `app_settings` のキーのみ。`default_background` に自分のアップロードを指定すると、その画像は全員が読めるようになる |
| POST | /admin/users/{id}/withdraw-cancel | 管理者による退会の取り消し |

```jsonc
// SlotRule
{"id": 1, "name": "Minecraft 共有枠", "domain_id": 1, "port_start": 25560, "port_end": 25569,
 "host_template": "mc{nn}trt", "number_start": 0, "excluded_ports": [25560],
 "record_mode": "cname_edge", "ip_id": null, "create_srv": true, "prepublish": true,
 "assign_to": "shared", "user_id": null,
 "stats": {"total": 9, "free": 5, "assigned": 3, "held": 1, "disabled": 0}, "dns_published": true}
```

## edge エージェント・Webhook・公開

| メソッド | パス | 認証 | 説明 |
|---|---|---|---|
| GET | /edge/config | `Authorization: Bearer EDGE_AGENT_TOKEN` | `?edge=edge-1&have=41` → 新しい版があれば `200 {"version","haproxy_cfg","nft_rules"}`、なければ `204` |
| POST | /edge/report | 同上 | `{"edge_id","applied_version","error"}` |
| POST | /hooks/kuma/{KUMA_WEBHOOK_SECRET} | URL の秘密値（Tailscale 内のみ受け付け） | Kuma の Webhook をそのまま受ける。監視IDからサーバーを引き、自動再起動の判断へ |
| GET | /public/status/{name} | なし | `public_status=true` のサーバーのみ `{"name","state","players","uptime_24h"}`。それ以外は `404` |
| GET | /health, /version | なし | `/health` は DB に繋がれば 200（`update` が使う） |
| GET | /health/full | `Authorization: Bearer PD_HEALTH_TOKEN` | 自己監視の詳細。`{"status":"ok|degraded|error","version","checked_at","checks":[{"name","level","message","value"}]}`。error なら 503 |

---

## エラーコード一覧

| code | HTTP | 意味 |
|---|---|---|
| unauthenticated | 401 | ログインしていない |
| csrf_failed | 403 | CSRF トークンが違う |
| forbidden | 403 | 権限がない |
| totp_required | 403 | 管理者の二段階認証が必要 |
| tos_required | 403 | 利用規約への同意が必要（作成・変更系） |
| not_found | 404 | |
| user_not_found | 404 | 共有相手などのユーザーが見つからない |
| validation | 400 | 入力が不正（`detail.fields` に項目ごとの日本語） |
| name_taken | 409 | サーバー名が使われている（ゴミ箱を含む） |
| name_reserved | 400 | 予約名 |
| slot_taken | 409 | アドレスが使われた（同時に作成された） |
| slot_not_allowed | 403 | 使えない枠のスロット |
| limit_reached | 409 | 台数の上限 |
| server_busy | 409 | 実行中のジョブがある |
| server_suspended | 409 | 利用停止中 |
| already_requested | 409 | 延長を申請済み |
| confirm_mismatch | 400 | 確認のための名前が一致しない |
| in_use | 409 | 使用中のため削除・変更できない |
| rule_invalid | 400 | アドレス枠の検証エラー（`detail.problems`） |
| zone_mismatch | 400 | ゾーンIDとドメイン名が一致しない |
| upstream_error | 502 | 外部サービスがエラーを返した（`detail.service`） |
| upstream_timeout | 504 | 外部サービスが応答しない |
| file_too_large | 413 | アップロードが大きすぎる |
| unsupported_media | 415 | 画像として読めない |
| last_admin | 409 | 最後の管理者は退会・降格できない |
| account_deleting | 409 | 退会の手続き中のため作成・変更できない |
