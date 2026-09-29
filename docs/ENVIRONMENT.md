# 構築に必要なサービスと環境

開発が終わったあと、本番とテスト環境を作るときの一覧です。**テスト環境を先に作り、手順を確かめてから本番を作る**ことをおすすめします。

```
                        ┌──────────── Cloudflare（nuids.jp ほか：DNS のみ／Tunnel）
                        │
プレイヤー ─► edge-1（Linode 東京）/ edge-2（Linode 大阪）── Tailscale ──┐
                                                                         │
利用者 ─► panel.nuids.jp ─(Tunnel または edge の nginx)─► オーケストレーター（Proxmox の LXC）
                                                           │  PostgreSQL・API・worker・Bot
                                                           ├─► Pterodactyl パネル ─► Wings ノード
                                                           ├─► Uptime Kuma（別の場所に置く）─► Discord（自己監視の通知）
                                                           ├─► Object Storage（バックアップ）
                                                           └─► Discord（Bot・ログイン）
```

---

## 1. 本番に必要なもの

| # | サービス・環境 | 用途 | 用意するもの・設定 | 規模の目安 |
|---|---|---|---|---|
| 1 | **Proxmox VE**（既存） | オーケストレーターの LXC | インストーラー（`ct/hatch.sh`）を実行するだけ。Debian 13 のテンプレートは自動取得 | 2 vCPU・メモリ 2〜4GB・ディスク 20GB |
| 2 | **Pterodactyl パネル＋Wings**（既存） | ゲームサーバー本体 | Application API キー（ユーザー・ノード・アロケーション・サーバー・nest/egg を読み書き）、root 管理者アカウントの Client API キー、各ノードのアロケーションに使う Tailscale IP、nest・egg の ID（`games.yml`）、「リモートファイルの取得」を有効化（開発用コピー用） | 既存のまま |
| 3 | **Tailscale** | 全部品の内部通信 | タグ（`tag:hatch`・`tag:edge`・`tag:wings`・`tag:kuma`）、LXC と edge 用の認証キー、ACL（下記） | 無料プランの範囲 |
| 4 | **Linode ×2**（edge-1 東京・edge-2 大阪） | プレイヤーの入口 | `edge/install-edge.sh` を実行。Linode のファイアウォールで「アドレス枠のポート範囲（TCP・UDP）」と 80/443 のみ許可 | 最小プラン（1GB）で十分。回線の転送量に注意 |
| 5 | **Cloudflare** | DNS（ゲームはプロキシなし） | 2つのドメインのネームサーバーを Cloudflare に、API トークン（両ゾーンの Zone:Read・DNS:Edit のみ） | 無料プラン |
| 6 | **Web パネルの公開** | `panel.nuids.jp` | どちらか：① Cloudflare Tunnel（LXC に cloudflared。受信ポートを開けなくてよい。**おすすめ**）② edge の nginx から Tailscale 経由で中継（Let's Encrypt の証明書） | — |
| 7 | **Uptime Kuma 2.x** | 監視と自己監視 | 連携用のユーザー、`/metrics` 用の API キー、自己監視の通知用の Discord Webhook（管理者用チャンネル）。**オーケストレーターと別の場所**（Linode など）に置く。バージョンを固定 | 1 vCPU・1GB |
| 8 | **Object Storage**（S3 互換。Linode Object Storage など） | バックアップ | バケット（本番用）、アクセスキー。パネルの `.env` でバックアップの保存先を S3 に設定。オーケストレーターの DB の遠隔バックアップにも使う | サーバー数×プランの保存数 |
| 9 | **Discord** | Bot・ログイン・通知 | Developer Portal のアプリ（Bot トークン、クライアントID・シークレット、OAuth の戻り先 `https://panel.nuids.jp/api/auth/callback`、**Server Members Intent を有効**）、Discord サーバーのチャンネル（お知らせ・運営通知・サーバー管理）とロール（利用者・サポーター・運営）。Bot の招待は `bot applications.commands`、権限はメッセージの送信・埋め込み・履歴の閲覧 | — |
| 10 | **GitHub** | 配布と CI | 公開リポジトリ、Actions、Releases。`mesamaru` の置き換え | 無料 |
| 11 | **ドメイン**（既存の2つ） | nuids.jp ほか | レジストラでネームサーバーを Cloudflare に | — |
| 12 | **文書** | 公開前に必要 | 利用規約、プライバシーポリシー（退会後7日で削除・操作ログは匿名化して1年保存・外部サービスの一覧）。料金を取る段階では特定商取引法に基づく表記 | — |

メールサーバーは不要です（通知とパスワード再設定は Discord の DM で行います）。Pterodactyl 本体のパスワード再設定メールを使う場合だけ、パネル側に SMTP を設定してください。

### Tailscale ACL の例

```jsonc
{
  "tagOwners": {
    "tag:hatch": ["autogroup:admin"], "tag:edge": ["autogroup:admin"],
    "tag:wings": ["autogroup:admin"], "tag:kuma": ["autogroup:admin"], "tag:panel": ["autogroup:admin"]
  },
  "acls": [
    // edge → ゲームサーバー（アドレス枠のポート範囲だけ）
    {"action": "accept", "src": ["tag:edge"], "dst": ["tag:wings:25560-25569,30000-30999"]},
    // edge → オーケストレーター（設定の取得）
    {"action": "accept", "src": ["tag:edge"], "dst": ["tag:hatch:8080"]},
    // オーケストレーター → パネル・Kuma
    {"action": "accept", "src": ["tag:hatch"], "dst": ["tag:panel:443", "tag:kuma:3001"]},
    // Kuma → オーケストレーター（死活確認・Webhook の送り先）、Kuma → edge（edge の死活確認）
    {"action": "accept", "src": ["tag:kuma"], "dst": ["tag:hatch:8080", "tag:edge:*"]},
    // パネル → Wings（パネルの通常の通信）
    {"action": "accept", "src": ["tag:panel"], "dst": ["tag:wings:8080,2022"]},
    // 管理者の端末
    {"action": "accept", "src": ["autogroup:admin"], "dst": ["*:*"]}
  ]
}
```

ポート範囲は、実際に作るアドレス枠に合わせてください。Velocity の配下にしたサーバーへは、edge からではなくプロキシのノードからだけ届くように、別途ルールを足します。

---

## 2. テスト環境（ステージング）

本番と **混ざらないこと** と **安く済むこと** を優先した構成です。

| サービス | テスト環境での用意 |
|---|---|
| オーケストレーター | 同じ Proxmox に LXC をもう1つ（同じインストーラー）。`PD_INSTANCE=stg`、`update --channel beta` |
| Pterodactyl パネル | 本番と別に1つ。Proxmox の VM か LXC |
| Wings | 1ノード。**VM で作る**（Wings は Docker を使うため、LXC では制限が多い） |
| edge | 最小の Linode を1台（または自宅の VM）。edge の切り替えを試すときだけ一時的に2台にする |
| Cloudflare | 本番と同じゾーンでよい。アドレス枠のホスト名を `stg-mc{nn}` などに分ける。`PD_INSTANCE` によって互いのレコードを消さない。より安全にするなら、2つ目のドメインをテスト専用にする |
| Uptime Kuma | テスト用に別に立てる（Docker で可）。本番の通知チャンネルにテストの通知が流れないように |
| Object Storage | 別のバケット |
| Discord | テスト用の Bot アプリと、テスト用の Discord サーバー |
| GitHub | 同じリポジトリ。ベータ版のタグ（`v0.2.0-beta.1`）をテスト環境で確認してから正式版を出す |

実装を担当する AI に結合テストをさせる場合は、テスト環境の API キーだけを渡してください。

---

## 3. 構築の順番

1. Cloudflare：2つのドメインのネームサーバーを移し、API トークンを作る
2. Tailscale：タグと ACL を設定し、認証キーを作る
3. Uptime Kuma：別の場所に立て、連携用ユーザー・API キー・自己監視用の Discord Webhook を用意
4. Object Storage：バケットとキーを作り、パネルのバックアップ先を S3 に
5. Discord：アプリ・Bot・OAuth の戻り先・Intent、チャンネルとロール
6. GitHub：リポジトリを公開して `v0.x.0` のタグを打つ
7. edge：edge-1・edge-2 に `install-edge.sh`
8. Proxmox：`ct/hatch.sh` → コンテナ内で `hatch-setup`（接続確認と自己監視の登録まで自動）
9. Web パネルの公開（Tunnel または nginx）
10. 画面から：ドメイン → IP と紐付け → アドレス枠 → DNS の事前作成 → Discord ロールの対応表 → 利用規約の登録
11. 自分で1台作って、接続・監視・バックアップ・ゴミ箱・復元・退会（テスト用アカウント）を確認

テスト環境で 1〜11 を一度通してから、本番で同じ手順を行います。
