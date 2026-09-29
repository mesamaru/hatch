# Hatch

Discord と Web パネルから、Pterodactyl のゲームサーバーを **一回の指示で作成・公開・監視・運用** するための基盤です。
ポート割当、HAProxy、Cloudflare の CNAME / SRV、Uptime Kuma の監視登録までを自動で行います。

> 現在はフェーズ0です。インストーラー・更新の仕組み・DB スキーマ・edge エージェント・UI デモが入っています。
> デプロイ処理本体は今後のリリースで追加され、`update` 一発で反映されます。詳細は [docs/SPEC.md](docs/SPEC.md)。

## 必要なもの

- Proxmox VE 8 以降（amd64）
- Pterodactyl パネルと Wings（Wings ノードは Tailscale に参加）
- Linode などの edge サーバー（1〜2台、Debian / Ubuntu）
- Cloudflare で管理しているドメイン
- Uptime Kuma 2.x
- Discord アプリ（Bot と OAuth）

## 最初にやること（リポジトリを公開したら）

スクリプト内の `mesamaru` を自分の GitHub ユーザー名に置き換えて push します。

```bash
grep -rl mesamaru . | xargs sed -i 's/mesamaru/あなたのユーザー名/g'
git commit -am "リポジトリ名を設定" && git push
git tag v0.1.0 && git push --tags     # 最初のリリース（GitHub Actions が配布ファイルを作ります）
```

## インストール

Proxmox VE ホストのシェルで実行します。

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/mesamaru/hatch/main/ct/pterodeploy.sh)"
```

完了したら、コンテナに入って初期設定をします（入力した値はその場で接続確認されます）。

```bash
pct enter <コンテナID>
pterodeploy-setup
```

### 非対話でインストール

```bash
PD_YES=1 var_cpu=2 var_ram=2048 var_disk=10 var_net=192.168.1.50/24 var_gateway=192.168.1.1 \
var_ts_authkey=tskey-auth-xxxx \
bash -c "$(curl -fsSL https://raw.githubusercontent.com/mesamaru/hatch/main/ct/pterodeploy.sh)"
```

| 変数 | 既定値 | 内容 |
|---|---|---|
| `var_ctid` | 次の空き番号 | コンテナID |
| `var_hostname` | `pterodeploy` | ホスト名 |
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
ct/pterodeploy.sh               Proxmox ホストで実行するインストーラー
install/pterodeploy-install.sh  コンテナ内のインストール処理
misc/update.sh                  update コマンドの本体
edge/                           edge エージェントとセットアップ
pterodeploy/                    API（Python / FastAPI）
web/                            Web パネル
db/migrations/                  DB の移行（追加のみ）
deploy/                         systemd ユニット、初期設定コマンド
docs/SPEC.md                    仕様書
```

## セキュリティ

- API キーなどの秘密情報は `/etc/pterodeploy/pterodeploy.env`（権限 640）にだけ置き、リポジトリには入れません。
- オーケストレーターの API はインターネットに直接公開しないでください（Cloudflare Tunnel か edge の nginx 経由で Web パネルだけを公開）。
- 脆弱性を見つけた場合は Issue ではなく、GitHub の Security Advisories から連絡してください。

## ライセンス

MIT
