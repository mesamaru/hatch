# 実装設計

どの AI・人が実装しても同じ構造になるよう、技術・ファイル配置・規約を固定します。ここに書いていない選択が必要になったら、実装前に報告してください。

---

## 1. 技術の選定（変更しない）

| 用途 | 採用 | 理由 |
|---|---|---|
| 言語 | Python 3.12 以上（Debian 13 は 3.13） | Kuma 連携ライブラリが Python |
| Web API | FastAPI + Pydantic v2 + uvicorn | 型から入力検証ができる |
| DB | PostgreSQL 16 以上、psycopg 3（**ORM は使わない**。SQL は `repo/` に集約） | SQL がそのまま仕様になる |
| HTTP クライアント | httpx（同期でなく **async**） | タイムアウト・再試行を統一 |
| Discord | discord.py 2.x | スラッシュコマンドとボタン |
| Uptime Kuma | uptime-kuma-api2（Socket.IO）＋ `/metrics` の直接取得 | 公式 REST API がないため |
| 画面 | ビルド不要の ES モジュール（素の JavaScript ＋ JSDoc 型注釈） | Node を LXC に入れない。どの AI でも読める |
| テスト | pytest、pytest-asyncio、respx（HTTP のフェイク）、Playwright（画面） | |
| 静的検査 | ruff（lint と format） | |

`requirements-dev.txt` には pytest、pytest-asyncio、respx、ruff、playwright を入れる。

---

## 2. ファイル配置

```
pterodeploy/
  __init__.py            バージョン
  main.py                FastAPI アプリの組み立て（ルートの登録だけ）
  config.py              設定ファイル（環境変数）の読み込みと検証
  db.py                  接続プール、トランザクションのヘルパー
  migrate.py             DB 移行
  auth/                  Discord OAuth、セッション、CSRF、TOTP、権限チェック
  api/                   HTTP ルート（1ファイル = 1リソース。docs/API.md と1対1）
    servers.py  slots.py  domains.py  users.py  admin.py  me.py  edge.py  hooks.py
  domain/                業務ルール（純粋な関数。DB も外部も触らない → テストしやすい）
    names.py             名前・ホスト名の検証
    slots.py             アドレス枠の展開（テンプレート → ホスト名）
    plans.py  permissions.py  expiry.py
  repo/                  SQL（1テーブル群 = 1ファイル。関数は必ず接続を引数で受け取る）
  adapters/              外部サービス（インターフェース ＋ 実装）
    panel.py             PanelAdapter（Pterodactyl / 将来 Pelican）
    dns.py               DnsAdapter（Cloudflare）
    monitor.py           MonitorAdapter（Uptime Kuma）
    notify.py            Notifier（Discord の DM・チャンネル）
    storage.py           （バックアップの署名付き URL など）
  jobs/                  ジョブ実行基盤と各ジョブの手順
    engine.py            取り出し・実行・取り消し・再開
    steps/               手順（1手順 = 1クラス）
    deploy.py  trash.py  restore.py  purge.py  dev_copy.py  publish_slots.py ...
  edge/                  HAProxy 設定の生成
  scheduler.py           定期処理（期限・整合性チェック・監視の取得）
  worker.py              ジョブとスケジューラーを動かすプロセスの入口
  bot/                   Discord Bot のプロセス
web/
  index.html             画面の入口（現在はデモ。フェーズ1で本物に置き換える）
  js/  app.js  api.js  router.js  pages/*.js  components/*.js
  css/ tokens.css  components.css
tests/
  unit/  api/  jobs/  fakes/  e2e/
```

プロセスは3つ：`pterodeploy-api`（Web と API）、`pterodeploy-worker`（ジョブと定期処理）、`pterodeploy-bot`（Discord）。すべて同じコードベース・同じ設定ファイル。

---

## 3. 共通の規約

### 3.1 エラー

- 業務エラーは `pterodeploy/errors.py` の `AppError(code, message_ja, status=400, detail=None)` を投げる。
- API はすべて次の形で返す：`{"error": {"code": "slot_taken", "message": "このアドレスは使用中です。別のアドレスを選んでください。", "detail": {...}}}`
- `code` は英小文字とアンダースコア。一覧は `docs/API.md` の末尾。新しい `code` を作ったら一覧に追記。

### 3.2 時刻と ID

- DB は `timestamptz`（UTC）。API も ISO 8601（UTC、`Z` 付き）で返す。表示の JST 変換は画面側。
- ユーザーとサーバーの ID は UUID。画面の URL にはサーバー名（`/servers/storia`）を使い、API ではどちらでも引けるようにする（`/api/servers/{id_or_name}`）。

### 3.3 トランザクション

- 1つの API 呼び出しは1トランザクション。外部サービスを呼ぶ処理は **API の中でやらず、ジョブにする**（API はジョブを登録して `202` を返す）。
- 例外：読み取りだけの外部呼び出し（リソース使用量の取得など）は API から直接呼んでよい。タイムアウトは5秒。

### 3.4 ログ

- 標準出力に JSON 1行（`ts`, `level`, `msg`, `job_id`, `server_id`, `user_id`）。journald が保存する。
- 秘密情報を含む可能性のある値（ヘッダー、URL のクエリ、例外の全文）はマスクしてから出す。

### 3.5 権限

`domain/permissions.py` の1関数にまとめる：`can(user, action, server=None) -> bool`。ルートでは `require(user, action, server)` を呼び、ダメなら `AppError("forbidden", ..., 403)`。

| action | admin | 所有者 | 共同管理者 view | console | files | full |
|---|---|---|---|---|---|---|
| server.view | ○ | ○ | ○ | ○ | ○ | ○ |
| server.power | ○ | ○ | — | ○ | ○ | ○ |
| server.backup | ○ | ○ | — | — | ○ | ○ |
| server.restore | ○ | ○ | — | — | — | ○ |
| server.plugins | ○ | ○ | — | — | ○ | ○ |
| server.settings / share / domain / trash / dev_copy | ○ | ○ | — | — | — | — |
| server.extend | 実行 | 申請 | — | — | — | — |
| admin.* | ○ | — | — | — | — | — |

利用停止中（`suspended`）のサーバーは、admin 以外は `server.view` しかできない。

---

## 4. アドレス枠（スロット）

### 4.1 テンプレートの規則

- 使える記号：`{n}`（番号）、`{nn}`（2桁ゼロ埋め）、`{nnn}`（3桁ゼロ埋め）、`{port}`（ポート番号）、`{server}`（サーバー名）。
- 番号 `n = number_start + (port - port_start)`。**対象外のポートがあっても番号はずれない**（ポートと名前の対応が常に一定になるため）。
- `{server}` は単独でのみ使える。`{server}` の枠は DNS の事前作成ができない。
- 展開後のホスト名は `^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$` を満たし、予約名（`reserved_names`）でなく、同じドメインの他の枠・IP の紐付けと重ならないこと。
- 1つの枠は200ポートまで。枠同士のポート範囲は重ならない（DB の排他制約）。

実装は `domain/slots.py` に純粋関数として置く：

```python
def expand(rule: SlotRule) -> list[SlotPlan]: ...  # 対象外を含めた全ポート
def validate_rule(rule: SlotRule, others: list[SlotRule], bindings: list[IpBinding]) -> list[str]: ...
def host_for(rule: SlotRule, port: int, server_name: str | None = None) -> str: ...
```

テストケース（必須）：`25560–25569 / mc{nn}trt / start=0 / 対象外 25560` → 25561 が `mc01trt`、25569 が `mc09trt`、25560 は対象外。`start=1` なら 25560 が `mc01trt`。

### 4.2 スロットの状態

| status | 意味 | 変わる契機 |
|---|---|---|
| free | 空き | 枠の保存時に作成、完全削除で戻る |
| assigned | サーバーが使用中 | デプロイ手順2で確保 |
| held | ゴミ箱のサーバーが予約中 | ゴミ箱へ移動 |
| disabled | 管理者が停止（新規割り当てに使わない） | 管理画面 |

確保は `UPDATE slots SET status='assigned', server_id=$1 WHERE port=$2 AND status='free' RETURNING port`。0行なら `AppError("slot_taken")`。**確保は作成の依頼を受けた時点**（`services/deploy.py`、サーバーの行の作成と同じトランザクション）で行う。同時に同じアドレスを選んだ2人のうち、後の人にすぐ知らせるため。デプロイの手順2は確保が残っているかの確認と、取り消し時の解放を担う。

### 4.3 枠の変更

- 使用中・予約中のスロットがある間は、テンプレート・ドメイン・開始番号を変えられない。範囲を狭めて使用中のポートを外すこともできない。
- 範囲を変えたら、増えたポートは `free` で作成、減ったポートは削除（`free` と `disabled` のみ）。
- 事前作成済みの DNS がある枠でテンプレート等を変えたら、`publish_slots` ジョブで作り直す（古いレコードを消して新しく作る）。

### 4.4 DNS の形

- `record_mode = cname_edge`（推奨）：`<host>.<domain> CNAME <edge_host>.<domain>`、SRV の target も `<edge_host>.<domain>`。
- `record_mode = a_ip`：`<host>.<domain> A <ip>`、SRV の target は `<host>.<domain>` 自身（A レコードなので可）。
- SRV：`_minecraft._tcp.<host>.<domain>  priority=0 weight=5 port=<port> target=…`。Minecraft 系（`kind` が mc・mod・proxy）のときだけ作る。
- すべて `proxied: false`、`ttl: 60`、`comment: "pterodeploy:<PD_INSTANCE> slot=<port>"`（紐付けは `binding=<id>`）。**自分のインスタンスのコメントが付いたレコードだけ**を作成・変更・削除・整合性チェックの対象にする。

### 4.5 IP の紐付けと edge の切り替え

- `ip_bindings.follow_active_edge = true` のレコードは、使用中の edge の IP を向く。edge を切り替えたら、これらをまとめて更新する（`switch_edge` ジョブ）。
- ドメイン追加時に `edge.<domain>` の紐付けを自動で作る（`follow_active_edge = true`）。
- `a_ip` モードの枠は edge 切り替えの影響を受けない（その IP を直接向くため）。画面で警告を出す。

---

## 5. ジョブ実行基盤

### 5.1 手順（Step）の書き方

```python
class Step(Protocol):
    name: str  # 画面に出す日本語（例 "ノードを選んでサーバーを作成"）

    async def run(self, ctx: JobContext) -> dict | None: ...  # 取り消しに必要な情報を返す（job_steps.undo に保存）
    async def undo(self, ctx: JobContext, saved: dict | None) -> None: ...
    def skip(self, ctx: JobContext) -> str | None: ...  # 飛ばす理由（"作成済み" など）。None なら実行
```

- `run` は **何度呼ばれても同じ結果**になるように書く（「既にあれば使う」）。ワーカーが途中で落ちて再開したときのため。
- 外部に作ったものの ID は、作った直後に `job_steps.undo` に保存してから次へ進む。
- `undo` も何度呼ばれてもよいように書く（「もうなければ何もしない」）。

### 5.2 エンジン

- 取り出し：`SELECT … FROM jobs WHERE status='queued' AND run_after<=now() ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1`。
- 実行中は30秒ごとに `locked_at` を更新。`locked_at` が5分以上古い `running` のジョブは、別のワーカーが引き継いで **最後に完了した手順の次から** 再開する。
- 手順が失敗したら、完了済みの手順を逆順に `undo` し、`status='rolled_back'`。`undo` 自体が失敗したら `status='failed'`、管理者に通知し、整合性チェックの対象にする。
- 一時的な失敗（HTTP 429・502・503・504、タイムアウト）は手順の中で最大3回まで再試行（1秒、4秒、10秒）。それ以外は即失敗。
- 同じサーバーに対するジョブは同時に1つだけ（`pg_advisory_xact_lock(hashtext(server_id))`）。
- 進捗は `jobs.current_step` と `job_steps` に書き、API（`GET /api/jobs/{id}`）と Discord の進捗メッセージが読む。

### 5.3 ジョブの一覧と手順

| kind | 手順 |
|---|---|
| deploy | 検証 → スロット確保 → パネルユーザー確認 → ノード選択＋アロケーション＋サーバー作成 → edge 設定の版を作り適用を待つ → DNS CNAME（事前作成済みなら飛ばす）→ DNS SRV（同）→ 起動と公開確認 → バックアップ予約と期限 → 監視追加 |
| trash | 停止 → 監視を一時停止 → edge から外す → DNS を外す（事前作成の枠は残す）→ スロットを held に → サーバーを trashed に |
| restore | trash の逆（サーバー起動まで） |
| purge | 最終バックアップ → 監視削除 → DNS 削除（事前作成の枠は残す）→ パネルから削除 → スロットを free に → サーバーを purged に |
| publish_slots | 枠の全スロットの CNAME・SRV を作成（既にあれば内容を確認して更新） |
| unpublish_slots | 枠の全スロットのレコードを削除 |
| switch_edge | edges.is_active を切り替え → 追従する紐付けを更新 → a_ip の枠を警告として記録 |
| dev_copy | スロット確保 → サーバー作成 → 元の最新バックアップの URL を発行 → 取り込み → 展開 → 起動 |
| join_proxy / leave_proxy | Velocity 設定の更新 → 配下サーバーの設定更新・再起動 → edge 公開と DNS の切り替え |
| sync_user | パネルユーザーの作成または更新 |
| delete_account | 退会の7日後に scheduler が登録。所有サーバーごとに purge（最終バックアップなし・既存バックアップも削除）→ 独自ドメイン・共有・プラグイン記録を削除 → パネルユーザー削除 → sessions・user_totp・user_prefs・notification_prefs・uploads（ファイルも）を削除 → users の個人情報を消す（username=`deleted-<idの先頭8文字>`、email=`<id>@deleted.invalid`、discord_id=NULL、status=`deleted`）。違反で停止中なら `banned_identities` に Discord ID の HMAC を記録 |

### 5.4 ノードの選び方

`nodes.accepting = true` のうち、`(割当済みメモリ + プランのメモリ) / memory_mb <= overcommit_percent / 100` を満たし、空きメモリが最大のノード。同点なら ID 順。ディスクも同じ条件で確認する。

---

## 6. アダプター（インターフェース）

実装は `adapters/*.py`、テスト用のフェイクは `tests/fakes/*.py`。**インターフェースを変えるときは両方を同時に直す**。呼び方の詳細は `docs/EXTERNAL.md`。

```python
class PanelAdapter(Protocol):
    async def find_user(self, *, external_id: str | None = None, email: str | None = None) -> PanelUser | None: ...
    async def create_user(self, u: NewPanelUser) -> PanelUser: ...
    async def update_user(
        self, panel_user_id: int, changes: PanelUserChanges
    ) -> PanelUser: ...  # 内部で取得→差分適用→全項目送信
    async def create_allocation(self, node_id: int, ip: str, port: int) -> int: ...  # 既にあれば既存の ID
    async def delete_allocation(self, node_id: int, allocation_id: int) -> None: ...
    async def create_server(self, s: NewPanelServer) -> PanelServer: ...  # external_id 必須
    async def find_server(self, external_id: str) -> PanelServer | None: ...
    async def delete_server(self, panel_server_id: int, *, force: bool = False) -> None: ...
    async def suspend(self, panel_server_id: int) -> None: ...
    async def unsuspend(self, panel_server_id: int) -> None: ...
    async def power(self, identifier: str, signal: Literal["start", "stop", "restart", "kill"]) -> None: ...
    async def state(self, identifier: str) -> Literal["running", "starting", "stopping", "offline"]: ...
    async def resources(self, identifier: str) -> Resources: ...
    async def create_backup(self, identifier: str, name: str) -> Backup: ...
    async def list_backups(self, identifier: str) -> list[Backup]: ...
    async def backup_download_url(self, identifier: str, backup_uuid: str) -> str: ...
    async def restore_backup(self, identifier: str, backup_uuid: str, *, truncate: bool) -> None: ...
    async def ensure_schedule(self, identifier: str, name: str, cron: Cron, action: str) -> int: ...
    async def add_subuser(self, identifier: str, email: str, permissions: list[str]) -> None: ...
    async def remove_subuser(self, identifier: str, subuser_uuid: str) -> None: ...
    async def read_file(self, identifier: str, path: str) -> str: ...
    async def write_file(self, identifier: str, path: str, content: str) -> None: ...
    async def pull_file(self, identifier: str, url: str, directory: str) -> None: ...


class DnsAdapter(Protocol):
    async def verify_zone(self, zone_id: str) -> str: ...  # ゾーン名を返す
    async def list_managed(self, zone_id: str) -> list[DnsRecord]: ...  # comment が "pterodeploy" で始まるもの
    async def upsert(self, zone_id: str, r: DnsRecordSpec) -> DnsRecord: ...
    async def delete(self, zone_id: str, record_id: str) -> None: ...  # 既にない場合は成功扱い


class MonitorAdapter(Protocol):
    async def add(self, m: MonitorSpec) -> int: ...
    async def pause(self, monitor_id: int) -> None: ...
    async def resume(self, monitor_id: int) -> None: ...
    async def delete(self, monitor_id: int) -> None: ...
    async def statuses(self) -> dict[int, MonitorStatus]: ...  # /metrics から


class Notifier(Protocol):
    async def dm(self, discord_user_id: str, message: Message) -> None: ...
    async def channel(self, channel_key: Literal["announce", "ops"], message: Message) -> None: ...
```

---

## 7. edge 設定の生成

- `edge/haproxy.py` の `render(servers, edges) -> str` は **同じ入力なら同じ文字列**を返す（ポート順に並べる）。
- 公開するのは `servers.exposed = true` かつ `status IN ('provisioning','running','stopped','maintenance')` のサーバー（停止中も公開したままにして、起動したらすぐ繋がるようにする。停止中は HAProxy のヘルスチェックで自然に落ちる）。
- ゲームの接続は長時間続くので、生成する設定には専用の `defaults pd_tcp`（`timeout client/server 2h`）を入れる。配布元の既定（50秒）のままだと、無操作のプレイヤーが切断される。
- 設定の適用を待つ対象は「使用中の edge」と「直近2分以内に応答した edge」。止まっている待機側のせいでデプロイが失敗しないようにする。
- 生成した文字列のハッシュが前回と同じなら新しい版を作らない。
- 1サーバーあたりのテンプレート（値は設定ファイルで変更可）：

```
frontend fe_{port}
    bind :{port}
    mode tcp
    maxconn {max_conn_per_server}
    stick-table type ip size 100k expire 10m store conn_cur,conn_rate(10s)
    tcp-request connection track-sc0 src
    tcp-request connection reject if { sc0_conn_cur gt {per_ip_conn} } || { sc0_conn_rate gt {per_ip_rate} }
    default_backend be_{port}

backend be_{port}
    mode tcp
    server s{port} {node_tailscale_ip}:{port} {proxy_opts} check inter 10s
```

`proxy_opts` は PROXY プロトコルを使うゲームだけ `send-proxy-v2 check-send-proxy`。

**UDP のゲーム**（`games.yml` の `protocol: udp`。Palworld など）は HAProxy で中継できないため、同じ版の設定に nftables のルールを含め、エージェントが適用する：

```
table ip pterodeploy {
  chain prerouting { type nat hook prerouting priority dstnat;
    udp dport {port} dnat to {node_tailscale_ip}:{port} }
  chain postrouting { type nat hook postrouting priority srcnat;
    ip daddr {node_tailscale_ip} udp dport {port} masquerade }
}
```

UDP には接続数の制限をかけられないので、1 IP あたりのパケット数制限（`limit rate over 2000/second drop`）を入れる。プレイヤーの IP はゲームサーバーからは edge の IP に見える。
`/api/edge/config` の応答は `{"version","haproxy_cfg","nft_rules"}`。エージェントは `nft -c -f`（検証）→ `nft -f` で適用する。

---

## 8. 設定一覧

### 8.1 設定ファイル（`/etc/pterodeploy/pterodeploy.env`）

| キー | 必須 | 例 | 説明 |
|---|---|---|---|
| PD_HOST / PD_PORT | ○ | 0.0.0.0 / 8080 | 待ち受け |
| PD_PUBLIC_URL | ○ | https://panel.nuids.jp | OAuth の戻り先に使う |
| PD_INTERNAL_URL | ○ | http://pterodeploy:8080 | Tailscale 内から見たオーケストレーターの URL（Kuma の Webhook 先） |
| PD_INSTANCE | ○ | prod | 環境の名前（英小文字・数字、8文字まで）。本番は prod、テスト環境は stg。DNS のコメントと監視名に入れ、同じゾーンや Kuma を共有しても互いに干渉しない |
| PD_DATA_DIR | ○ | /var/lib/pterodeploy | アップロードした画像などの保存先 |
| PD_HEALTH_TOKEN | ○ | 48桁の16進 | `/api/health/full` の認証（Kuma の HTTP 監視のヘッダーに設定） |
| KUMA_PUSH_WORKER / KUMA_PUSH_SCHEDULER / KUMA_PUSH_BOT | | Push トークン | 自己監視の Push 監視（初期設定で自動登録） |
| PD_SECRET_KEY | ○ | 64桁の16進 | Cookie 署名・TOTP 秘密の暗号化 |
| DATABASE_URL | ○ | postgresql://… | |
| PANEL_KIND | ○ | pterodactyl | 将来 pelican |
| PANEL_URL / PANEL_APP_KEY / PANEL_CLIENT_KEY | ○ | | |
| PANEL_PUBLIC_URL | | https://gp.nuids.jp | 利用者に見せるゲームパネルの URL |
| CF_API_TOKEN | ○ | | 登録する全ゾーンの DNS 編集権限 |
| KUMA_URL / KUMA_USERNAME / KUMA_PASSWORD / KUMA_METRICS_KEY / KUMA_WEBHOOK_SECRET | ○ | | |
| DISCORD_BOT_TOKEN / DISCORD_CLIENT_ID / DISCORD_CLIENT_SECRET / DISCORD_GUILD_ID | ○ | | |
| DISCORD_CHANNEL_ANNOUNCE / DISCORD_CHANNEL_OPS | | チャンネルID | |
| EDGE_AGENT_TOKEN | ○ | | edge エージェントの認証 |
| HAPROXY_PER_IP_CONN / HAPROXY_PER_IP_RATE / HAPROXY_MAX_CONN | | 20 / 30 / 500 | |
| STATUS_PUBLIC_URL | | https://status.nuids.jp | 状態ページ |

ゲームごとのパネルの nest・egg・変数の上書きは `/etc/pterodeploy/games.yml` に書く（`deploy/games.example.yml` が見本）。

以前の `CF_ZONE_ID`、`PD_GAME_DOMAIN`、`PD_EDGE_HOSTNAME` は廃止（ドメインは画面から登録し、DB に保存する）。

### 8.2 画面から変える設定（`app_settings`）

| key | 既定 | 意味 |
|---|---|---|
| trash_hours | 72 | ゴミ箱に置く時間 |
| expiry_grace_days | 14 | 期限切れから削除までの日数 |
| final_backup_keep_days | 30 | 最終バックアップの保管日数 |
| auto_restart_max_per_hour | 3 | 自動再起動の上限 |
| tos_current_version | null | 現在の利用規約の版 |
| account_deletion_days | 7 | 退会の申請から削除までの日数 |
| default_background | {"kind":"preset","id":"aurora"} | 全員の既定の背景 |
| default_bg_dim | 0.35 | 既定の「見やすさ」 |

---

## 8A. 自己監視

実装は `pterodeploy/health.py`（済み）。

- 各プロセスは30秒ごとに `health.beat("<component>")` を呼ぶ（worker はジョブの取り出しループ、scheduler は毎回の処理後、bot は `discord.ext.tasks` のループ）。あわせて Kuma の Push URL（`KUMA_PUSH_*`）へ `GET …?status=up` を送る。送信の失敗で本処理を止めない。
- `/api/health`：DB に `SELECT 1` できれば 200。`update` が使うので **仕様を変えない**。
- `/api/health/full`：`Authorization: Bearer <PD_HEALTH_TOKEN>`。判定：
  - error（503）：DB に接続できない／worker・scheduler の報告が90秒以上ない／ディスクの空きが5%未満かつ2GB未満
  - degraded（200）：bot の報告が90秒以上ない／プロセスのバージョンが API と違う／待ちジョブが120秒以上待っている／24時間以内に `failed` のジョブがある／edge の報告が180秒以上ない／ディスクの空きが15%未満かつ10GB未満
- `pterodeploy-setup` の最後に MonitorAdapter で次の監視を作る（名前は固定、既にあれば更新）：`pd:<instance>:self-http`（HTTP、`/api/health`）、`pd:<instance>:self-full`（HTTP キーワード、`"status":"ok"`、ヘッダーにトークン）、`pd:<instance>:push-worker`・`push-scheduler`・`push-bot`（Push、間隔60秒）。通知先は Kuma に登録した Discord の Webhook（名前 `pterodeploy-self`）。**オーケストレーター宛ての Webhook は付けない**。

## 8B. アップロード（背景画像）

- 受け付け：`image/jpeg`・`image/png`・`image/webp`・`image/heic`（HEIC は `pillow-heif` で読む）、20MB まで。
- Pillow で開き、`ImageOps.exif_transpose` で向きを直してから、長辺2560px に縮小し、WebP（品質82）で保存。**元のメタデータは書き出さない**。3MB を超えたら品質を下げて再保存。
- 保存先：`PD_DATA_DIR/uploads/<user_id>/<upload_id>.webp`。1人あたり背景は1枚（新しく上げたら古いものを削除）。
- 配信：`GET /api/uploads/{id}` は本人のもの、または `default_background` に設定されたものだけ。`Cache-Control: private, max-age=86400`。
- 画面側でも縮小してから送る（通信量の削減）が、サーバー側の変換は省略しない。

## 9. テストの方針

- `domain/` は100%テスト（純粋関数なので簡単）。特にアドレス枠の展開と検証。
- `jobs/` は「全手順成功」「各手順で失敗 → 取り消しが逆順に呼ばれる」「途中で止まって再開」の3種類を、フェイクのアダプターで必ず書く。
- `api/` は権限の表（3.5）をそのままテストにする（admin・所有者・共同管理者・他人 × 各 action）。
- 画面は Playwright で主要な流れ（作成・削除・復元・枠の追加）を確認。スマホ幅（390px）とデスクトップ幅（1280px）の両方。
