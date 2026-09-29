# Hatch

Discord と Web パネルから、Pterodactyl のゲームサーバーを **一回の指示で作成・公開・監視・運用** するための基盤です。
ポート割当、HAProxy、Cloudflare の CNAME / SRV、Uptime Kuma の監視登録までを自動で行います。

> 現在はフェーズ0です。インストーラー・更新の仕組み・DB スキーマ・edge エージェント・UI デモが入っています。
> デプロイ処理本体は今後のリリースで追加され、`update` 一発で反映されます。詳細は [docs/SPEC.md](docs/SPEC.md)。

構築手順を画面で見たい場合は [docs/setup-guide.html](docs/setup-guide.html) を開いてください（チェックリスト付き）。

## 必要なもの

- Proxmox VE 8 以降（amd64）
- Pterodactyl パネルと Wings（Wings ノードは Tailscale に参加）
- Linode などの edge サーバー（1〜2台、Debian / Ubuntu）
- Cloudflare で管理しているドメイン
- Uptime Kuma 2.x
- Discord アプリ（Bot と OAuth）

## 最初にやること（自分のアカウントで fork した場合）

インストーラーは既定で公式リポジトリ（`mesamaru/hatch`）から取得します。自分のアカウントに fork して使う場合は、
`PD_REPO=あなたのユーザー名/hatch` を環境変数で指定してください（下のインストールコマンドの前に付けます）。

```bash
git tag v0.1.0 && git push --tags     # 最初のリリース（GitHub Actions が配布ファイルを作ります）
```

## セットアップの流れ（スクリプト実行から動作確認まで）

詳細は [docs/install-guide.html](docs/install-guide.html) を参照してください。

### 1. Proxmox ホストでコンテナ作成

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/mesamaru/hatch/main/ct/hatch.sh)"
```

対話形式でコンテナのスペック（CPU・メモリ・ディスク・ネットワーク）を入力します。完了後、コンテナ ID とアクセス URL が表示されます。

### 2. コンテナ内で初期設定

```bash
pct enter <コンテナID>
hatch-setup
```

以下を対話形式で設定します（入力値はその場で検証されます）：

- **基本情報**：ホスト名・タイムゾーン
- **Tailscale URL**：`http://hatch:8080` など（Tailscale 内からのアクセス）
- **Pterodactyl パネル**：API キー・ホスト名
- **Cloudflare**：API トークン・ゾーン ID
- **Uptime Kuma**：URL・認証情報・メトリクスキー
- **Discord**：Bot トークン・クライアント ID・シークレット
- **Object Storage**：エンドポイント・バケット・アクセスキー
- **Edge エージェント**：トークン（自動生成）

### 3. Web パネルの公開設定

Cloudflare Tunnel または edge の nginx 経由で Web パネルを公開します。

### 4. 画面からの初期設定

`https://<パネルのドメイン>/` を開き、Discord でログインして：

1. **管理 → ドメイン**：ゲームサーバーのドメインを登録
2. **管理 → IP と紐付け**：edge サーバーの IP を確認
3. **管理 → アドレス枠**：ポート範囲とホスト名パターンを作成
4. **管理 → Discord ロール連携**：ロールと権限の対応を設定
5. **管理 → 利用規約**：利用規約を登録

### 5. 動作確認

- テスト用アカウントでログイン
- サーバーを1台作成
- アドレスに接続確認
- 監視画面に反映確認
- バックアップ・復元・退会を一通りテスト

## インストール（従来のセクション）

**詳細は上記「セットアップの流れ」または [docs/install-guide.html](docs/install-guide.html) を参照してください。**

Proxmox VE ホストのシェルで実行します。

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/mesamaru/hatch/main/ct/hatch.sh)"
```

完了したら、コンテナに入って初期設定をします（入力した値はその場で接続確認されます）。

```bash
pct enter <コンテナID>
hatch-setup
```

### 非対話でインストール

```bash
PD_YES=1 var_cpu=2 var_ram=2048 var_disk=10 var_net=192.168.1.50/24 var_gateway=192.168.1.1 \
var_ts_authkey=tskey-auth-xxxx \
bash -c "$(curl -fsSL https://raw.githubusercontent.com/mesamaru/hatch/main/ct/hatch.sh)"
```

| 変数 | 既定値 | 内容 |
|---|---|---|
| `var_ctid` | 次の空き番号 | コンテナID |
| `var_hostname` | `hatch` | ホスト名 |
| `var_cpu` / `var_ram` / `var_disk` | `2` / `2048` / `10` | コア数 / MB / GB |
| `var_storage` | 最初の rootdir ストレージ | コンテナの保存先 |
| `var_bridge` / `var_vlan` | `vmbr0` / なし | ネットワーク |
| `var_net` / `var_gateway` | `dhcp` / なし | 固定IPは `192.168.1.50/24` の形式 |
| `var_ts_authkey` | なし | Tailscale 認証キー |
| `PD_CHANNEL` | `stable` | `beta` でベータ版を使う |
| `PD_KEEP_ON_FAIL` | `0` | `1` で失敗時にコンテナを残す |

## 更新

コンテナ内で:

```bash
update                 # 最新の安定版へ
update --check         # 更新があるか確認だけ
update --channel beta  # ベータ版を含めて最新へ
update --rollback      # 1つ前に戻す（--restore-db で DB も戻す）
```

更新前に DB を自動で退避し、起動を確認できなければ自動で前のバージョンに戻します。
Proxmox ホスト側のインストールコマンドをコンテナ内で実行しても、更新として動作します。

## edge（Linode など）のセットアップ

```bash
PD_REPO=mesamaru/hatch bash -c "$(curl -fsSL https://raw.githubusercontent.com/mesamaru/hatch/main/edge/install-edge.sh)"
```

edge エージェントはオーケストレーターから HAProxy 設定を取りに行き、検証してから適用します。オーケストレーターが止まっていても最後の設定で動き続けます。

## 導入後の最初の設定（画面から）

1. 管理 → ドメイン で `nuids.jp` などを登録（Cloudflare のゾーンIDを入力。`edge.<ドメイン>` が自動で作られます）
2. 管理 → IP と紐付け で edge-1・edge-2 の IP を確認
3. 管理 → アドレス枠 で、例えば「25560–25569 / `mc{nn}trt` / 対象外 25560」を作成し、「DNS を作成」

これで利用者は作成画面で `mc01trt.nuids.jp` などを選ぶだけになります。

## 本番・テスト環境に必要なもの

`docs/ENVIRONMENT.md` にまとめています（Proxmox・Pterodactyl・Tailscale・Linode・Cloudflare・Uptime Kuma・Object Storage・Discord・GitHub）。

## 開発に参加する（AI を含む）

`AGENTS.md` → `docs/SPEC.md` → `docs/IMPLEMENTATION.md` → `docs/API.md` → `docs/EXTERNAL.md` → `docs/UI.md` の順に読み、`docs/TASKS.md` のチケットを1つずつ実装します。

## 構成

```
ct/hatch.sh               Proxmox ホストで実行するインストーラー
install/hatch-install.sh  コンテナ内のインストール処理
misc/update.sh                  update コマンドの本体
edge/                           edge エージェントとセットアップ
hatch/                    API（Python / FastAPI）
web/                            Web パネル
db/migrations/                  DB の移行（追加のみ）
deploy/                         systemd ユニット、初期設定コマンド
docs/SPEC.md                    仕様書
```

## セキュリティ

- API キーなどの秘密情報は `/etc/hatch/hatch.env`（権限 640）にだけ置き、リポジトリには入れません。
- オーケストレーターの API はインターネットに直接公開しないでください（Cloudflare Tunnel か edge の nginx 経由で Web パネルだけを公開）。
- 脆弱性を見つけた場合は Issue ではなく、GitHub の Security Advisories から連絡してください。

## ライセンス

MIT
