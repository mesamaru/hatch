# Hatch の作業ルール（GitHub Copilot 向け）

## このプロジェクト
Pterodactyl のゲームサーバー貸し出しを自動化するオーケストレーター。
利用者が Web パネル（または Discord）から「ゲーム・名前・アドレス」を選ぶだけで、
ゲームパネルでのサーバー作成、edge（HAProxy / nftables）の公開、Cloudflare DNS、監視までを1回で行う。
構成：Python 3.12 / FastAPI / PostgreSQL（psycopg3、ORM なし）/ 画面はビルド不要の ES モジュール。

## 最初に読むもの（この順番）
1. AGENTS.md（作業ルール。必ず守る）
2. docs/TASKS.md（先頭に進捗表あり。チケット単位で作業する）
3. docs/SPEC.md → docs/IMPLEMENTATION.md → docs/API.md → docs/EXTERNAL.md → docs/UI.md

## 絶対に守ること
- 1回の作業は1チケットだけ。チケットに書かれていないファイルは変えない
- v0.1.0 のタグを打ったので、db/migrations/0001_initial.sql は今後一切変更しない。
  DB の変更は 0002_xxx.sql を追加する（追加のみ。列の削除・改名は2リリースに分ける）
- 外部サービス（パネル・Cloudflare・Uptime Kuma・Discord）は pterodeploy/adapters/ 経由でのみ呼ぶ。
  テストでは tests/fakes/ のフェイクを使い、実サービスには絶対に接続しない
- 外部サービスへの作成・削除は API の中で行わず、ジョブ（pterodeploy/jobs/）にする。
  ジョブの手順は run と undo を対で書き、何度呼ばれても同じ結果になるようにする
- 画面に出す文言・エラーメッセージは日本語（です・ます）。英語のエラーコードは API の code にだけ入れる
- 秘密情報（API キー・トークン・パスワード）をログ・例外・応答に出さない
- 操作ログ（audit_log）の target・detail にユーザー名・メール・Discord 名を書かない
- ブラウザの JavaScript で top・name・status・parent などをグローバル変数名にしない（画面が真っ白になる）

## 完了の条件
- `ruff check .` と `ruff format --check .` が通る
- `pytest -q` が通る（PostgreSQL 16 が必要。CI では services で起動している）
- 新しい API は docs/API.md に、新しいエラーコードは docs/API.md 末尾の表に追記
- docs/TASKS.md の進捗表を更新
- PR の説明に「変更したファイル」「受け入れ条件ごとのテスト名」「残った課題」を書く

## 迷ったら
仕様に書かれていない判断が必要なときは、実装せずに PR / Issue のコメントで質問する。
仕様書同士が食い違う場合の優先順位：SPEC.md > API.md > IMPLEMENTATION.md > UI.md。

## ローカルでのテスト
```bash
export DATABASE_URL=postgresql://pterodeploy:dev@127.0.0.1:5432/pterodeploy
pip install -r requirements-dev.txt
python -m pterodeploy.migrate
pytest -q
```
Windows でスクリプト（*.sh、deploy/bin/*、edge/agent.py）を追加・変更したら、
`git update-index --chmod=+x <ファイル>` で実行権限を付ける。