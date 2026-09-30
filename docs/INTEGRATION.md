# Hatch 連携仕様書（他のツールと連携する実装者向け）

この文書は、**Hatch と連携する機能を別のツール側で作る AI・開発者** に渡すためのものです。Hatch が何をするツールか、外から触れる入口（HTTP API・DNS・監視・ゲームパネル上の決まり）、今どこまで実装済みか、連携するときに守ってほしい決まりをまとめています。

- 文書の基準日：2026-09-30。リポジトリ `mesamaru/hatch` の `main`（最新リリース v0.2.0 ＋未リリースの変更）に合わせています。
- 詳しい一次資料は同じリポジトリの `docs/SPEC.md`（機能）・`docs/API.md`（API の完全な一覧。未実装のものも含む）・`docs/IMPLEMENTATION.md`（内部構成）・`docs/EXTERNAL.md`（外部サービスの呼び方）です。食い違いがあれば、**この文書の「実装状況」と実際のコードが正**です。
- 記号：**【実装済み】** 今のコードで動く。**【予定】** 仕様はあるが未実装（呼ぶと 404）。**【提案】** 連携のために追加が必要と考えているもの（Hatch 側にまだ仕様もない）。

---

## 1. Hatch とは

Discord と Web パネルから、Pterodactyl のゲームサーバー（Minecraft など）を **1回の指示で作成・公開・監視・運用** するための「オーケストレーター」です。自宅の Proxmox 上の LXC コンテナで動き、次の外部サービスをまとめて操作します。

| 外部サービス | Hatch が行うこと |
|---|---|
| Pterodactyl パネル＋Wings | ユーザーとサーバーの作成・削除・電源操作・使用量の取得（Application API / Client API） |
| Cloudflare（DNS のみ） | ゲーム用の CNAME・SRV・A レコードの作成と削除 |
| edge（Linode 上の HAProxy / nftables） | 外部からのゲーム接続の入口。Hatch が設定を生成し、edge 上のエージェントが取りに来て適用する |
| Uptime Kuma | サーバーの死活監視の登録（予定）と、Hatch 自身の外形監視 |
| Discord | ログイン（OAuth2）、ロールによる権限の決定、Bot（予定） |

利用者は「ゲームの種類・名前・プラン・アドレス」を選ぶだけで、パネル上のサーバー作成から DNS・入口の転送設定まで自動で行われ、`mc01trt.example.jp` のようなアドレスですぐ接続できます。

### 1.1 構成

```
 利用者 ─ Web ブラウザ ─┐                        ┌─ Pterodactyl（Application / Client API）
 利用者 ─ Discord ──────┼─► Hatch（Proxmox の LXC）─┼─ Cloudflare API（DNS）
                        │   API・ジョブ・画面       ├─ Uptime Kuma
                        │   PostgreSQL（正本）      └─ edge エージェント ◄─ 設定を取りに来る
 プレイヤー ─► edge.<ドメイン>（edge-1 / edge-2）── Tailscale ──► Wings ノード
```

| 部品 | 技術 | 備考 |
|---|---|---|
| API・画面配信 | Python 3.12+ / FastAPI / uvicorn | 既定ポート 8080。画面はビルド不要の ES モジュール（`web/`） |
| ジョブ実行（worker）・定期処理（scheduler） | 同じコードベースの別プロセス | systemd の `hatch.target` でまとめて起動 |
| DB | PostgreSQL 16+ | 唯一の正本。ジョブキューも DB（`SELECT … FOR UPDATE SKIP LOCKED`） |
| ネットワーク | Tailscale | 内部の通信（Kuma → Hatch、edge → Wings）はすべて Tailscale 内 |

- Hatch の API はインターネットに直接公開しない前提です（Cloudflare Tunnel か edge の nginx 経由で画面だけ公開）。**連携するツールは Tailscale 内から `PD_INTERNAL_URL`（例 `http://hatch:8080`）で呼ぶ** のが基本です。
- 本番とテスト環境は `PD_INSTANCE`（`prod` / `stg`）で区別します。連携の開発・テストは **テスト環境（stg）** に対して行ってください。

---

## 2. 用語と主なデータ

### 2.1 ユーザーと権限（3段階）

| 権限（`users.role`） | できること | 決まり方 |
|---|---|---|
| `admin`（管理者） | すべての操作と設定 | 管理者にした Discord ロールを持つ人。**Discord サーバーのオーナーは常に管理者** |
| `supporter`（サポーター） | 利用者のすべて＋管理者が割り当てたサーバーの閲覧と電源操作 | サポーターにした Discord ロールを持つ人 |
| `user`（利用者） | 自分のサーバーの作成・運用、共有されたサーバーの操作 | 利用者にした Discord ロールを持つ人、または招待された人 |

- ログインは Discord OAuth2 のみ。ログインのたびに Discord のロールから権限と作成できる台数（`max_servers`）を決め直します。
- ユーザー名は `^[a-z0-9][a-z0-9_.-]{2,31}$`。ゲームパネル側のユーザー名と同じにそろえます。
- 二段階認証（TOTP）は任意。有効にした人は、コードを入力するまで API をほぼ使えません（`403 totp_required`）。
- アカウントの状態（`users.status`）：`invited` / `active` / `suspended` / `deleting`（退会手続き中・7日後に削除）/ `deleted`。

### 2.2 サーバー

| 項目 | 内容 |
|---|---|
| 名前 | `^[a-z0-9][a-z0-9-]{1,30}[a-z0-9]$`（3〜32文字）。システム全体で一意（ゴミ箱の中も含む）。予約名（`edge`・`panel` など）は使えない |
| 状態（`status`） | `pending`（受付済み）→ `provisioning`（作成中）→ `running` / `stopped` / `maintenance` / `suspended`（管理者による利用停止）→ `trashed`（ゴミ箱・72時間）→ `purging` → `purged`。作成に失敗すると `failed` |
| 所有者 | 1人。ほかに「共有」（`view` / `console` / `files` / `full`）と「サポーターの割り当て」で他の人も操作できる |
| ゲーム | `/etc/hatch/games.yml` に定義したもの（例 `paper`・`storiamc`・`velocity`・`palworld`）。種類 `kind` は `mc` / `mod` / `proxy` / `other` |
| プラン | DB の `plans`。初期値は `light`（2GB・1コア・10GB）と `standard`（4GB・2コア・20GB）。利用期間は30日 |
| アドレス | 下の「スロット」を1つ使う。例 `mc01trt.nuids.jp`（ポート 25561） |
| 期限 | `expires_at`。期限で停止 → 14日後にゴミ箱 |

### 2.3 アドレス（ドメイン・アドレス枠・スロット）

```
ドメイン（Cloudflare のゾーン。複数可）
 ├─ IP の紐付け（A レコード）   edge.nuids.jp → 使用中の edge に追従／固定 IP
 └─ アドレス枠（slot_rule）      25560–25569 / ホスト名 mc{nn}trt / 対象外 25560 / 全員で共有
      └─ スロット（slot）        25561 = mc01trt.nuids.jp
```

- **スロット** は「ポート番号 ＝ 公開アドレス」の組。ポート番号はシステム全体で一意です。状態は `free`（空き）/ `assigned`（使用中）/ `held`（ゴミ箱のサーバーが予約中）/ `disabled`（停止）。
- ホスト名のテンプレートの記号：`{n}` `{nn}` `{nnn}`（番号）、`{port}`、`{server}`（サーバー名）。
- DNS の形：既定は `edge.<ドメイン>` への CNAME（プロキシ無効）。Minecraft 系は SRV も作る。

### 2.4 ジョブ

外部サービスを伴う操作（作成・削除・DNS の作成など）は、すべて **ジョブ** として非同期に実行されます。各手順は「実行」と「取り消し」を対で持ち、途中で失敗すると完了済みの手順を逆順に取り消します。

| 項目 | 値 |
|---|---|
| 状態（`status`） | `queued` / `running` / `succeeded` / `failed`（取り消しにも失敗）/ `rolled_back`（失敗してすべて取り消した）/ `cancelled` |
| 手順の状態 | `pending` / `running` / `done` / `failed` / `undone` / `skipped` |
| 種類（`kind`）【実装済み】 | `deploy`（作成）、`trash`（ゴミ箱へ）、`restore`（ゴミ箱から戻す）、`purge`（完全に削除）、`publish_slots` / `unpublish_slots`（DNS の事前作成・削除）、`sync_binding` / `delete_binding`（A レコード） |

作成（`deploy`）の手順：依頼の検証 → アドレスの確保 → パネルのユーザー確認 → ノードを選んでサーバー作成 → edge に転送設定を配布 → DNS（CNAME・SRV）→ 起動と公開の確認 → バックアップと期限の設定 → 監視の追加。

### 2.5 操作ログ

`audit_log` は追記専用（更新・削除は DB のトリガーで禁止、1年保存）。`actor_id`（ユーザーの ID）・`via`（`web` / `discord` / `system` / `cli`）・`action`（日本語）・`target`・`server_id`・`job_id`・`result`・`detail` を持ちます。**`target`・`detail` にユーザー名・メール・Discord 名は書かない決まり** です（退会後も残るため）。

---

## 3. HTTP API

### 3.1 共通の決まり

| 項目 | 内容 |
|---|---|
| ベース | `/api`。JSON（UTF-8） |
| 認証 | Cookie `pd_session`（Discord でログインしたセッション。30日）。**今のところ、人のセッション以外で API を呼ぶ方法はありません**（→ 5章） |
| CSRF | GET 以外は `X-CSRF-Token` ヘッダーが必須。値は `GET /api/me` の `csrf_token` |
| 時刻 | ISO 8601（UTC、末尾 `Z`） |
| 非同期処理 | `202 {"job": {"id", "kind", "status"}}` を返す。完了は `GET /api/jobs/{id}` で確認（1〜1.5秒間隔が目安） |
| 重複防止 | `POST /api/servers` は `Idempotency-Key` ヘッダーを受け付ける（同じキーなら同じジョブを返す） |
| エラー | `{"error": {"code": "英語のコード", "message": "日本語の説明", "detail": {...}}}`。入力エラーは `detail.fields` に項目ごとの説明 |
| 外部サービスの不調 | `502 upstream_error` / `504 upstream_timeout` |
| 初期設定が未完了 | 初期設定用とヘルスチェック以外は `503 setup_required` |

### 3.2 オブジェクト【実装済み】

```jsonc
// Server（GET /api/servers, GET /api/servers/{名前 または ID}）
{
  "id": "3f0c…-uuid", "name": "storia",
  "owner": {"id": "uuid", "username": "tanaka"},
  "game": "paper", "plan": "light",
  "status": "running",
  "address": "mc01trt.nuids.jp",          // 接続先（独自ドメインが有効ならそれ）
  "slot": {"port": 25561, "host": "mc01trt", "domain": "nuids.jp", "rule_id": 1},  // 無ければ null
  "direct": "edge.nuids.jp:25561",        // ポート指定で直接つなぐ場合
  "node": "node-1", "is_dev_copy": false,
  "proxy": null,                          // Velocity 配下なら {"id","name"}
  "auto_restart": true, "public_status": false,
  "expires_at": "2026-10-30T00:00:00Z", "maintenance_until": null,
  "suspend_reason": null,
  "trashed_at": null, "purge_after": null,   // ゴミ箱に入った時刻・完全に削除される時刻
  "my_permission": "owner",               // admin | owner | view | console | files | full | support | none
  "job": null                             // 実行中のジョブ {"id","kind","status","current_step"}
}

// Job（GET /api/jobs/{id}）
{
  "id": 1842, "kind": "deploy", "status": "running",
  "current_step": "ノードを選んでサーバーを作成",
  "steps": [{"seq": 1, "name": "依頼の検証", "status": "done", "note": null, "error": null}, …],
  "error": null, "server_id": "uuid",
  "created_at": "2026-09-30T04:00:00Z", "finished_at": null
}
```

### 3.3 実装済みのエンドポイント

**ログイン・自分**

| メソッド | パス | 説明 |
|---|---|---|
| GET | /api/auth/login | Discord の認可画面へリダイレクト |
| GET | /api/auth/callback | Discord から戻る。失敗時は `/?login_error=<code>`（`no_role`・`not_member` など） |
| POST | /api/auth/logout | ログアウト |
| POST | /api/auth/totp/setup・/verify・/disable | 二段階認証の設定・確認・解除 |
| GET | /api/me | `{"user":{"id","username","role","status","deletion_due_at"},"csrf_token","panel_url","needs_tos","tos_version","needs_totp","totp_enabled","limits":{"max_servers","used"}}` |
| POST | /api/me/tos | 利用規約への同意 `{"version"}` |

**サーバー**

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | /api/catalog | ログイン | 作成の選択肢 `{"games":[{"id","label","kind"}],"plans":[{"id","name","memory_mb","cpu_percent","disk_mb","backup_limit","period_days"}]}` |
| GET | /api/slots/available | ログイン | 選べるアドレス `{"items":[{"port","fqdn"(null なら {server} 枠),"rule":{"id","name","domain","host_template"},"dns_ready"}]}`。`?owner_id=` は管理者のみ |
| POST | /api/servers | ログイン | 作成 `{"name","game","plan","slot_port","owner_id"?}` → `202 {"server":{"id","name","address","status"},"job":{…}}` |
| GET | /api/servers | ログイン | 見える一覧 `{"items":[Server]}`。`?status=trashed,purging` などで絞り込み、`?owner=`（管理者のみ） |
| GET | /api/servers/{名前 か ID} | 閲覧 | 詳細。見えないサーバーは存在を知らせず `404` |
| PATCH | /api/servers/{key} | 設定 | `{"auto_restart"?, "public_status"?}` |
| POST | /api/servers/{key}/power | 電源 | `{"signal":"start|stop|restart"}` → `204`（パネルへ直接。ジョブにしない） |
| POST | /api/servers/{key}/maintenance | 設定 | `{"enabled": bool, "hours"?: 0〜72}` |
| GET | /api/servers/{key}/resources | 閲覧 | `{"state","cpu_percent","memory_bytes","memory_limit","disk_bytes","disk_limit","players":null}` |
| DELETE | /api/servers/{key} | 所有者 | ゴミ箱へ → ジョブ |
| POST | /api/servers/{key}/restore | 所有者 | ゴミ箱から戻す → ジョブ |
| POST | /api/servers/{key}/purge | 所有者 | 完全に削除 `{"confirm_name"}` → ジョブ |
| GET | /api/jobs/{id} | 依頼者・所有者・管理者 | ジョブの状態 |

**管理者**

| メソッド | パス | 説明 |
|---|---|---|
| GET / POST | /api/admin/domains | ドメインの一覧・追加 `{"name","cf_zone_id"}`（追加時に `edge.<name>` の A レコードを作るジョブ） |
| PATCH / DELETE | /api/admin/domains/{id} | `{"is_default": true}`。枠や紐付けがあると削除できない |
| GET / POST / DELETE | /api/admin/ips(/{id}) | IP の登録 `{"label","address","edge_id"?}` |
| GET / POST / DELETE | /api/admin/bindings(/{id}) | ホスト名 → IP の A レコード `{"domain_id","host","ip_id"?,"follow_active_edge"}` → ジョブ |
| GET / POST | /api/admin/slot-rules | アドレス枠の一覧・追加 |
| POST | /api/admin/slot-rules/preview | 保存せずに検証 `{"problems":[…],"slots":[{"port","fqdn","excluded"}]}` |
| GET / PATCH / DELETE | /api/admin/slot-rules/{id} | 詳細（`slots` 付き）・変更・削除 |
| POST | /api/admin/slot-rules/{id}/publish・/unpublish | DNS の事前作成・削除 → ジョブ |
| PATCH | /api/admin/slots/{port} | `{"status":"free|disabled"}` |
| GET | /api/admin/users | ユーザー一覧 `{"items":[{"id","username","role","status","max_servers"}]}` |
| GET | /api/supporters | サポーターの一覧 |
| GET / PUT / DELETE | /api/servers/{key}/supporters(/{user_id}) | サポーターへの割り当て |

**その他**

| メソッド | パス | 認証 | 説明 |
|---|---|---|---|
| GET | /api/health | なし | DB に接続できれば `200 {"status":"ok","version"}` |
| GET | /api/version | なし | バージョン |
| GET | /api/health/full | `Authorization: Bearer PD_HEALTH_TOKEN` | 自己監視の詳細。異常なら `503` |
| GET | /api/edge/config | `Authorization: Bearer EDGE_AGENT_TOKEN` | edge エージェント用（`?edge=edge-1&have=41` → 新しい版があれば `{"version","haproxy_cfg","nft_rules"}`、なければ `204`） |
| POST | /api/edge/report | 同上 | `{"edge_id","applied_version","error"}` |
| GET / POST | /api/setup/* | 初期設定コード | 初期設定画面専用（連携では使わない） |

### 3.4 仕様だけあって未実装のもの【予定】

`docs/API.md` に仕様があり、まだ実装されていないもの：バックアップ、共有（サブユーザー）、プラグイン、独自ドメイン、期限の延長と申請、開発用コピー、Velocity 連携、検索、ジョブ一覧（`GET /api/jobs`）、ユーザーの招待・変更・停止、ロールの対応表の編集、お知らせ、違反対応、操作ログの閲覧、整合性チェック、edge の切り替え、Uptime Kuma の Webhook 受信（`POST /api/hooks/kuma/{秘密値}`）、公開の状態ページ（`GET /api/public/status/{name}`）、パスワード同期、退会、背景画像。Discord Bot も未実装です。

---

## 4. Hatch が外部サービスに作るもの（共存の決まり）

連携するツールが同じ Pterodactyl・Cloudflare・Uptime Kuma を触る場合は、次の決まりを守ってください。**Hatch は自分が作ったものだけを管理し、それ以外には触れません**。逆に、Hatch が作ったものを他のツールが書き換えると、整合性チェック（予定）で元に戻されるか、Hatch の動作が壊れます。

| サービス | Hatch が作るもの | 見分け方 | 他のツールへのお願い |
|---|---|---|---|
| Cloudflare DNS | ゲーム用の CNAME・SRV、`edge.<ドメイン>` などの A | レコードの comment が `hatch:<PD_INSTANCE> …`（例 `hatch:prod slot=25561`） | comment が `hatch:` で始まるレコードは変更・削除しない。Hatch の枠のホスト名と同じ名前のレコードを作らない（Hatch は comment の無い同名レコードを上書きせず、作成に失敗する）。ゲーム用レコードは必ず **プロキシ無効** |
| Pterodactyl | ユーザー（Hatch のユーザー名・メールと同じ）、サーバー、アロケーション | サーバーの `external_id` に Hatch 側の ID。アロケーションは Tailscale の IP（100.x）上 | Hatch が作ったサーバーを直接削除・移動しない（Hatch の DB とずれる）。ユーザーを更新するときは **全項目を送る**（Application API はパスワードだけ送ると他の項目が消える） |
| edge（HAProxy・nftables） | アドレス枠のポートの転送設定 | 設定ファイル全体を Hatch が生成し、エージェントが差し替える | edge の設定を手で編集しない（次の版で上書きされる）。アドレス枠のポート範囲を他の用途に使わない |
| Uptime Kuma | サーバーごとの監視（予定）、Hatch 自身の監視 | 監視名が `pd:<PD_INSTANCE>:<ID>` | この名前の監視を変更・削除しない |

- `PD_INSTANCE` が違えば（`prod` と `stg`）、同じゾーン・同じ Kuma を使っても互いに干渉しません。連携ツールも同じ考え方（自分の印を付け、自分のものだけを扱う）にすると安全です。
- ポートは Hatch の DB が正本です。アドレス枠の範囲（例 25560–25569、30000 番台など）は Hatch 専用と考えてください。

---

## 5. 連携の方法と、今の制約

### 5.1 今の制約（重要）

1. **機械用の認証がありません。** API は Discord でログインした人のセッション（Cookie ＋ CSRF トークン）でしか呼べません。API キーやサービス用トークンでの呼び出し、`X-Acting-User` による代理実行（`docs/API.md` に記載）は **未実装** です。
2. **Hatch から外へ知らせる仕組み（Webhook・イベント配信）がありません。** 状態の変化は API を定期的に取得して知るしかありません。
3. Discord Bot は未実装です（スラッシュコマンドでの作成・通知は予定）。
4. DB を直接読み書きするのはやめてください。スキーマは追記方式で変わり、権限チェック・操作ログ・外部サービスとの整合を通らなくなります。

### 5.2 連携のために Hatch 側に追加が必要なもの【提案】

連携機能を作る場合は、次のどれかを Hatch 側のチケットとして先に実装する必要があります。連携側の設計はこの形を前提にしてください（名前・形は Hatch 側と相談して確定させます）。

**(a) サービス用トークン（ツールから Hatch を呼ぶ）**

- 管理者が画面で発行する長いランダムな値。DB にはハッシュだけを保存し、発行時に1回だけ表示する。
- `Authorization: Bearer <トークン>` で呼ぶ。CSRF は不要（Cookie を使わないため）。
- トークンごとに「できること」（例 `servers:read`、`servers:power`、`servers:create`）を限定する。
- 人の代わりに操作するときは `X-Acting-User: <Hatch のユーザー ID>` を付け、その人の権限で判定し、操作ログには `via` を連携ツールの名前、`actor_id` をその人にする。
- 通信は Tailscale 内に限る。

**(b) イベントの通知（Hatch からツールへ知らせる）**

- 管理者が登録した URL に、`POST` で JSON を送る。本文に HMAC-SHA256 の署名（ヘッダー `X-Hatch-Signature`）を付け、受け側で検証できるようにする。
- 想定するイベント：`server.created`・`server.deploy_failed`・`server.status_changed`（起動・停止・ダウン・復旧）・`server.trashed`・`server.restored`・`server.purged`・`server.expiring`（期限の7・3・1日前）・`user.role_changed`。
- 本文の例：`{"event":"server.created","at":"2026-09-30T04:00:00Z","instance":"prod","server":{Server オブジェクト},"job_id":1842}`。
- 届かなかったら間隔を空けて再送し、同じイベントを2回受け取っても困らないように `event_id` を付ける。

**(c) 読み取り専用の公開情報**

- 状態ページ用の `GET /api/public/status/{name}`（予定）を先に実装すれば、認証なしで「稼働中か・人数」を取れる（`public_status=true` のサーバーのみ）。

### 5.3 当面できる連携

機械用の認証ができるまでの間に使える方法です。

| やりたいこと | 方法 |
|---|---|
| Hatch が動いているか | `GET /api/health`（認証なし）、詳しくは `GET /api/health/full`（`PD_HEALTH_TOKEN`） |
| ゲームサーバーの死活 | Hatch を通さず、Uptime Kuma の `/metrics`（API キー）か、公開アドレスへの直接の接続確認 |
| 人が操作する画面から呼ぶ | 連携ツールの画面を Hatch と同じオリジンに置けないため不可。ブラウザから Hatch の API を別オリジンで呼ぶことは想定していない（CORS 未対応） |

---

## 6. 連携側の実装で守ってほしいこと

Hatch の開発ルール（`AGENTS.md`）のうち、連携に関係するものです。

- **秘密情報**（API キー、トークン、パスワード、Cookie の値）をログ・例外メッセージ・API 応答に出さない。リポジトリに入れない。
- **本番のキーで開発・テストしない**。テスト環境（`PD_INSTANCE=stg`）のキーを使う。
- 利用者に見える文言は **日本語・敬体（です・ます）**。エラーは「何が起きたか」と「どうすればよいか」を書く。英語のエラーコードは API の `code` にだけ入れる。
- Discord のスラッシュコマンドは **3秒以内に応答** しないと失敗扱い。重い処理は先に `defer` する。
- Cloudflare のゲーム用レコードは **必ず `proxied: false`**。SRV の向き先は **A レコードのホスト名**（CNAME にしない）。
- Pterodactyl の Application API でユーザーを更新するときは **全項目を送る**。
- 人を記録するときは名前ではなく **ID** で記録する（退会後に名前を消すため）。
- 時刻は UTC で保存し、表示で Asia/Tokyo に変換する。

---

## 7. 連携の設計で決めてほしいこと（質問リスト）

連携側の AI は、実装の前にユーザーへ次を確認してください。

1. 連携するツールは何か。Hatch を **呼ぶ側** か、Hatch から **知らされる側** か、両方か。
2. 連携ツールはどこで動くか（Tailscale 内から Hatch の `PD_INTERNAL_URL` に届くか）。
3. 人の代わりに操作するか（その場合、どの Hatch ユーザーとして動くか）、ツール自身の権限で動くか。
4. 必要な操作の範囲（読み取りだけ／電源操作まで／作成・削除まで）。
5. 5.2 の (a)(b)(c) のどれを Hatch 側に追加するか。

---

## 付録 A. 主なテーブル

`users`、`sessions`、`user_totp`、`discord_role_rules`（Discord ロール → 権限・台数）、`plans`、`servers`、`server_shares`、`server_supporters`、`nodes`、`edges`、`edge_config_versions`、`domains`、`ip_addresses`、`ip_bindings`、`slot_rules`、`slots`、`reserved_ports`、`reserved_names`、`custom_domains`、`dns_records`、`monitors`、`auto_restarts`、`notifications_sent`、`extension_requests`、`server_plugins`、`jobs`、`job_steps`、`announcements`、`violations`、`audit_log`、`tos_versions`、`tos_acceptances`、`notification_prefs`、`user_prefs`、`uploads`、`component_heartbeats`、`banned_identities`、`app_settings`。移行ファイルは `db/migrations/`（追記のみ）。

## 付録 B. 主なエラーコード

`unauthenticated`(401)・`csrf_failed`(403)・`forbidden`(403)・`totp_required`(403)・`tos_required`(403)・`not_found`(404)・`validation`(400)・`name_taken`(409)・`slot_taken`(409)・`slot_not_allowed`(403)・`limit_reached`(409)・`server_busy`(409)・`server_suspended`(409)・`confirm_mismatch`(400)・`in_use`(409)・`rule_invalid`(400)・`zone_mismatch`(400)・`not_supporter`(400)・`upstream_error`(502)・`upstream_timeout`(504)・`account_deleting`(409)・`setup_required`(503)。一覧は `docs/API.md` の末尾。
