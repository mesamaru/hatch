# 外部サービスの呼び方

アダプターを実装する人向けの資料です。**「要確認」と書いた項目は、実装時に実機（または公式ドキュメント）で動作を確かめ、結果をこのファイルに追記してください。** 推測で実装しないこと。

共通：タイムアウトは接続5秒・全体15秒。429・502・503・504・タイムアウト・接続失敗は `TransientError`、それ以外のエラーは `UpstreamError`（`hatch/adapters/http.py`）。**アダプター自身は再試行しない**。再試行はジョブ基盤が手順単位で行う（1秒、4秒、10秒）。二重に再試行して回数が掛け算にならないようにするため。応答本文をログに出すときは500文字で切り、キーやトークンを含むヘッダーは出さない。

---

## 1. Pterodactyl（PANEL_KIND=pterodactyl）

- Application API：`Authorization: Bearer ptla_…`（ユーザー・ノード・サーバーの管理）
- Client API：`Authorization: Bearer ptlc_…`。**root 管理者アカウント**のキーを使う（全サーバーを操作できる）。電源・バックアップ・スケジュール・サブユーザー・ファイルに使う。
- 共通ヘッダー：`Accept: application/json`、`Content-Type: application/json`
- Client API のサーバー指定は `identifier`（UUID の先頭8文字）。Application API は数値 ID。両方を DB に保存する（`panel_server_id`、`panel_uuid`）。

### ユーザー

| 操作 | リクエスト | 注意 |
|---|---|---|
| 探す | `GET /api/application/users?filter[external_id]={users.id}`、なければ `filter[email]=` | external_id にパネル側 UUID を入れて紐付ける |
| 作る | `POST /api/application/users` `{"email","username","first_name","last_name","external_id","root_admin":false}` | first_name / last_name は必須。ユーザー名をそのまま入れる |
| 更新 | `PATCH /api/application/users/{id}` | **全項目（email, username, first_name, last_name）を毎回送る**。パスワードを変えるときだけ `password` を付ける。先に GET して差分を当てる |
| 管理者権限 | 同上で `root_admin` | |

パスワードはパネル側の最小長（8文字）より、こちらの最小（12文字）を優先する。

### アロケーション（ノードの IP:ポート）

1. `GET /api/application/nodes/{node}/allocations?filter[port]={port}` で既存を探す（**要確認：filter[port] が使えない版では全件を取得して探す**）
2. なければ `POST /api/application/nodes/{node}/allocations` `{"ip":"<ノードの Tailscale IP>","ports":["25565"]}`
3. **作成の応答には ID が含まれない**（204）ので、もう一度 1 を実行して ID を得る
4. 使用中（サーバーに割り当て済み）のアロケーションが見つかったら、`slot_taken` 相当として失敗させる（別のサーバーが使っている）

### サーバー

作成：`POST /api/application/servers`

```json
{
  "name": "storia",
  "external_id": "<servers.id>",
  "user": 12,
  "egg": 15,
  "docker_image": "<egg の既定>",
  "startup": "<egg の既定>",
  "environment": { "<egg の変数>": "<既定値。プランやゲームで上書き>" },
  "limits": {"memory": 4096, "swap": 0, "disk": 20480, "io": 500, "cpu": 200},
  "feature_limits": {"databases": 0, "allocations": 1, "backups": 5},
  "allocation": {"default": 431},
  "start_on_completion": false
}
```

- egg の既定値は `GET /api/application/nests/{nest}/eggs/{egg}?include=variables` から取る。ゲームごとの nest・egg ID と上書きする変数は `games.yml`（設定ファイルと同じ場所）で管理し、コードに埋め込まない。
- 探す：`GET /api/application/servers/external/{servers.id}`（404 ならない）
- 削除：`DELETE /api/application/servers/{id}`（失敗時は `/force` を1回だけ試す）
- 停止・解除：`POST /api/application/servers/{id}/suspend` / `/unsuspend`
- インストール完了の判定：`GET /api/client/servers/{identifier}` の `attributes.is_installing` が false になるまで5秒ごとに確認（最大10分）

### 電源・状態・使用量（Client API）

- `POST /api/client/servers/{identifier}/power` `{"signal":"start|stop|restart|kill"}`
- `GET /api/client/servers/{identifier}/resources` → `attributes.current_state`（running / starting / stopping / offline）、`attributes.resources.{memory_bytes, cpu_absolute, disk_bytes}`

### バックアップ

| 操作 | リクエスト |
|---|---|
| 一覧 | `GET /api/client/servers/{identifier}/backups` |
| 作成 | `POST …/backups` `{"name":"手動 2026-10-01 12:00"}` |
| ダウンロード URL | `GET …/backups/{uuid}/download` → `attributes.url` |
| 復元 | `POST …/backups/{uuid}/restore` `{"truncate": true}`（サーバーは事前に停止） |
| 削除 | `DELETE …/backups/{uuid}`（ロックされたものは消えない） |

上限に達しているときに作成すると失敗するので、先に「自動」で作られた最も古いものを削除する。自動・手動の区別は名前の先頭（`自動 ` / `手動 ` / `更新前 `）で行う。

### スケジュール（自動バックアップ）

1. `POST /api/client/servers/{identifier}/schedules` `{"name":"Hatch 自動バックアップ","minute":"0","hour":"4","day_of_month":"*","month":"*","day_of_week":"*","is_active":true,"only_when_online":false}`
2. `POST …/schedules/{id}/tasks` `{"action":"backup","payload":"","time_offset":0}`

同名のスケジュールが既にあれば作らない（再実行対策）。

### 共同管理者（サブユーザー）

`POST /api/client/servers/{identifier}/users` `{"email","permissions":[…]}`。相手はパネルユーザーとして同期済みであること（未同期のメールを渡すとパネルが新規ユーザーを作ってしまう）。

| 画面の選択 | permissions |
|---|---|
| 閲覧のみ | `websocket.connect` |
| コンソール操作 | `websocket.connect`, `control.console`, `control.start`, `control.stop`, `control.restart` |
| ファイル編集 | 上記 ＋ `file.read`, `file.read-content`, `file.create`, `file.update`, `file.delete`, `file.archive`, `backup.read`, `backup.create` |
| すべて | 上記 ＋ `backup.restore`, `backup.download`, `startup.read`, `startup.update`, `schedule.read` |

（要確認：権限名はパネルのバージョンで増減する。`GET /api/client/permissions` の一覧と照合してから送る）

### ファイル

- 読む：`GET …/files/contents?file=/config/paper-global.yml`
- 書く：`POST …/files/write?file=…`（本文はそのまま。JSON ではない）
- URL から取り込む：`POST …/files/pull` `{"url","directory":"/"}`（パネル設定でリモート取得が有効であること。**要確認：上限サイズ**）
- 展開：`POST …/files/decompress` `{"root":"/","file":"backup.tar.gz"}`

---

## 2. Cloudflare DNS

- `Authorization: Bearer <CF_API_TOKEN>`。トークンの権限：対象ゾーンの **Zone:Read** と **DNS:Edit** のみ。
- ゾーン確認：`GET https://api.cloudflare.com/client/v4/zones/{zone_id}` → `result.name` が登録するドメイン名と一致すること。
- 管理対象の一覧：`GET /zones/{zone}/dns_records?comment.startswith=hatch:<PD_INSTANCE>&per_page=100&page=N`（全ページ）。**他のインスタンス（テスト環境など）のレコードには触らない**。
- 作成：`POST /zones/{zone}/dns_records`
  - CNAME：`{"type":"CNAME","name":"mc05trt.nuids.jp","content":"edge.nuids.jp","ttl":60,"proxied":false,"comment":"hatch:prod slot=25565"}`
  - A：`{"type":"A","name":"edge.nuids.jp","content":"203.0.113.10","ttl":60,"proxied":false,"comment":"hatch:prod binding=1"}`
  - SRV：`{"type":"SRV","name":"_minecraft._tcp.mc05trt.nuids.jp","data":{"priority":0,"weight":5,"port":25565,"target":"edge.nuids.jp"},"ttl":60,"comment":"hatch:prod slot=25565"}`
- 更新：`PATCH /zones/{zone}/dns_records/{id}`、削除：`DELETE …/{id}`（404 は成功扱い）。
- **upsert の手順**：`GET …/dns_records?type=CNAME&name=<fqdn>` → あれば内容を比較して違えば PATCH、なければ POST。同名に **comment が `hatch:<自分のインスタンス>` で始まらないレコード**（手動で作ったもの、または別インスタンスのもの）があれば上書きせず失敗させ、管理者に知らせる。
- CNAME は同じ名前の他の種類のレコードと共存できない。作成に失敗したらエラーコードと名前を記録する。
- 整合性チェックは `proxied` が true になっていたら false に戻す。

---

## 3. Uptime Kuma

### 3.1 監視の追加・変更（Socket.IO）

- ライブラリ：`uptime-kuma-api2`（Kuma 2.x 対応のフォーク）。**同期ライブラリなので、ワーカー内の専用スレッドで1接続だけ保持**し、呼び出しはキューで直列化する。切断されたら再ログイン。
- Kuma のバージョンは設定ファイルに記録し、起動時に `api.info()["version"]` と比べて違えば管理者に警告する（非公式 API のため）。
- 監視の名前は **`pd:<PD_INSTANCE>:<servers.id>`**（固定）。`/metrics` との対応付けに使い、他のインスタンスの監視には触らない。自己監視の監視名は `docs/IMPLEMENTATION.md` 8A。
- 追加：

```python
api.add_monitor(
    type=MonitorType.GAMEDIG,
    name=f"pd:{instance}:{server_id}",
    game="minecraft",
    hostname=fqdn_or_edge,
    port=port,
    interval=60,
    retryInterval=30,
    maxretries=2,
    notificationIDList=[webhook_notification_id],
)  # 戻り値の "monitorID" を monitors.kuma_monitor_id に保存
```

  `monitor: tcp` のゲームは `type=MonitorType.PORT`。`monitor: push`（UDP のゲーム）は `type=MonitorType.PUSH` で作り、返ってきた push トークンを保存する。ワーカーが30秒ごとにパネルの `current_state` を確認し、`running` のときだけ `GET {KUMA_URL}/api/push/{token}?status=up&msg=running` を送る（送らなければ Kuma が停止と判断する）。
- Webhook 通知は初回起動時に1つだけ作る：`add_notification(type=NotificationType.WEBHOOK, name="hatch", webhookURL=f"{PD_INTERNAL_URL}/api/hooks/kuma/{KUMA_WEBHOOK_SECRET}", webhookContentType="json")`。ID は `app_settings.kuma_notification_id` に保存。
- 一時停止・再開・削除：`pause_monitor(id)`、`resume_monitor(id)`、`delete_monitor(id)`（存在しない ID は成功扱い）。

### 3.2 状態の読み取り（/metrics）

- `GET {KUMA_URL}/metrics`、Basic 認証（ユーザー名は空、パスワードに `KUMA_METRICS_KEY`）。30秒ごとにワーカーが取得し、`monitor_state` をメモリと DB のキャッシュに保存。
- `monitor_status{monitor_name="pd:<instance>:<uuid>",…} 1`（0=停止、1=稼働、2=保留、3=メンテナンス）、`monitor_response_time{…}` を使う。**要確認：Kuma 2.x で `monitor_id` ラベルが付くならそちらを優先**。

### 3.3 自己監視の通知

自己監視の監視（`pd:<instance>:self-*`、`push-*`）には、Kuma の **Discord 通知**（`NotificationType.DISCORD`、管理者用チャンネルの Webhook URL）を付ける。オーケストレーターが止まっていても Kuma から直接届くようにするため。Webhook URL は `hatch-setup` で入力し、Kuma にだけ保存する（オーケストレーターの設定ファイルには残さない）。

### 3.4 Webhook の受信

本文の `heartbeat.monitorID` と `heartbeat.status`（0=停止、1=稼働）を使う。`monitor` オブジェクトは無いこともある。同じ監視の同じ状態が続けて届いたら2回目以降は無視する。

---

## 4. Discord

- 必要なもの：Bot トークン、OAuth2 のクライアントID・シークレット、サーバー（ギルド）ID。
- Developer Portal で **Server Members Intent を有効**にする（ロールの付与・剥奪を受け取るため）。
- ログイン：OAuth2 の scope は `identify email guilds.members.read`（パネルのアカウントにメールアドレスが必要なため `email` も要る。未確認のメールアドレスではアカウントを作らない）。`GET /users/@me` と `GET /users/@me/guilds/{guild}/member`（ロール一覧）を使う。
- スラッシュコマンド：受け取ったら **3秒以内に defer**（「考え中…」）。ジョブの進捗は、interaction の followup ではなく **Bot がチャンネルに送った通常メッセージを編集**する（interaction のトークンは15分で切れるため）。メッセージの場所は `jobs.discord_message` に保存。
- ボタンの `custom_id` は `pd:<action>:<id>` 形式。押した人の権限を API で再確認する（メッセージを見た他人が押しても操作できないように）。
- DM が送れない（受信拒否）場合は、送れなかったことを記録するだけにする。チャンネルに個人宛ての内容を流さない。
- コマンド一覧：`/deploy name game plan [address]`、`/status`、`/backup server`、`/restart server`、`/maint server on|off`、`/share server user permission`、`/extend server`、`/account`（パネルへのリンク）。`address` は作成可能なスロットをオートコンプリートで出す。

---

## 5. プラグインの取得元

- Modrinth：`GET https://api.modrinth.com/v2/search?query=…&facets=[["project_type:plugin"],["categories:<loader>"]]`。`User-Agent: hatch/<version> (<PD_PUBLIC_URL>)` が必須。
  - StoriaMC → loader は `folia` のみ。Paper → `paper`。Forge → `forge`（project_type は `mod`）。
  - バージョン：`GET /v2/project/{id}/version?loaders=["folia"]&game_versions=["<サーバーの MC バージョン>"]`。ファイルは `primary: true` のもの。sha512 を検証してから置く。
- Hangar：Paper 用のみ（Folia 対応の判定が確実でないため StoriaMC では使わない）。`GET https://hangar.papermc.io/api/v1/projects?q=…&platform=PAPER`。

---

## 6. Velocity 連携で書き換えるファイル

- プロキシ側 `velocity.toml`：`[servers]` に `<name> = "<ノードの Tailscale IP>:<port>"` を追加、`try` の先頭に最初の1台を入れる。`player-info-forwarding-mode = "modern"`。secret は `forwarding.secret` ファイル。
- 配下側（Paper / StoriaMC）：`config/paper-global.yml` の `proxies.velocity.enabled: true`、`online-mode: true`、`secret: <同じ値>`。`server.properties` の `online-mode=false`。PROXY プロトコル（`proxies.proxy-protocol`）は **false** にする。
- TOML は `tomlkit`、YAML は `ruamel.yaml` で読み書きし、コメントや並びを壊さない。
- 書き換えの前にバックアップを作り、失敗したら元のファイルに戻す。

---

## 7. edge エージェント

`edge/agent.py` を参照。API は `docs/API.md` の「edge エージェント」。エージェントの変更は後方互換を保つ（古いエージェントが新しい API に繋がっても動くこと）。
