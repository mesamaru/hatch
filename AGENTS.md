# AGENTS.md — このリポジトリで作業する AI（と人）へ

このファイルは、どの AI（Claude の各モデル、GPT、Gemini など）が作業しても同じ品質になるように書いた「作業のルール」です。作業を始める前に必ず全文を読んでください。

## 1. 読む順番

1. `docs/SPEC.md` — 何を作るか（機能と振る舞い）
2. `docs/IMPLEMENTATION.md` — どう作るか（構成・規約・ジョブ・アダプター）
3. `docs/API.md` — HTTP API の正確な仕様
4. `docs/EXTERNAL.md` — 外部サービス（ゲームパネル・Cloudflare・Uptime Kuma・Discord）の呼び方と注意点
5. `docs/UI.md` — 画面の仕様（`web/demo.html` のデモが見た目の正解）
6. `docs/TASKS.md` — 作業単位（チケット）。**1回の作業では1チケットだけ**を扱う
7. `docs/ENVIRONMENT.md` — 本番とテスト環境の構成（外部サービスの一覧）

仕様の間で食い違いを見つけたら、推測で決めずに作業を止め、どこが食い違っているかを報告してください。優先順位は `SPEC.md` > `API.md` > `IMPLEMENTATION.md` > `UI.md` です。

## 2. 絶対に守ること

- **1チケットずつ**。チケットに書かれていないファイルは変更しない。ついでのリファクタリングもしない。
- **DB の移行ファイルは追記のみ**。`db/migrations/` の既存ファイルは書き換えない（v0.1.0 のタグを打つまでは `0001_initial.sql` の修正を例外的に許可）。新しい変更は次の番号のファイルを追加する。列の削除・改名は2リリースに分ける。
- **外部サービスへの通信は必ずアダプター経由**（`hatch/adapters/`）。ルートやジョブから `httpx` を直接呼ばない。
- **秘密情報をログ・例外メッセージ・API 応答に出さない**。API キー、パスワード、トークン、Cookie の値。
- **本番の API キーを使わない**。結合テストはテスト環境（`PD_INSTANCE=stg`）のキーだけで行う。
- **操作ログ（audit_log）の target・detail にユーザー名・メール・Discord 名を書かない**（退会後も残るため）。人は `actor_id`・ID で記録する。
- **テストなしで完了にしない**。各チケットの「受け入れ条件」をテストで確認できるようにする。外部サービスはフェイク（`tests/fakes/`）を使い、実サービスに接続しない。
- **ユーザーに見える文言は日本語**。敬体（です・ます）、簡潔に。エラー文は「何が起きたか」と「どうすればよいか」を書く。英語のエラーコードは API の `code` にだけ入れる。
- 依存パッケージを増やす場合は、理由をチケットの報告に書く。`requirements.txt` にはバージョンの上限と下限を書く。

## 3. 開発環境

```bash
# PostgreSQL 16 以上が必要（Docker でも可）
export DATABASE_URL=postgresql://hatch:dev@127.0.0.1:5432/hatch
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python -m hatch.migrate
uvicorn hatch.main:app --reload --port 8080
```

- テスト: `pytest -q`（DB を使うテストは `DATABASE_URL` のテスト用 DB を毎回作り直す）
- 静的検査: `ruff check . && ruff format --check .`
- シェル: `shellcheck -S warning ct/*.sh install/*.sh misc/*.sh edge/*.sh scripts/*.sh deploy/bin/*`
- 画面: `web/` はビルド不要の ES モジュール。ブラウザで `http://localhost:8080/` を開く

## 4. 完了の定義

- [ ] チケットの受け入れ条件をすべて満たし、対応するテストがある
- [ ] `pytest -q`、`ruff check .`、`ruff format --check .`、`shellcheck` が通る
- [ ] 新しい API は `docs/API.md` に、新しい設定値は `docs/IMPLEMENTATION.md` の設定一覧に追記した
- [ ] ユーザーの操作を伴う変更は操作ログ（`audit_log`）に記録している
- [ ] 報告に「変更したファイル」「確認した方法」「残った課題」を書いた

## 5. よくある間違い（過去に実際に起きたもの）

- ブラウザの JavaScript で `top`、`name`、`status`、`parent` などを **グローバル変数名にしない**（`window.top` などと衝突して、画面が真っ白になる）。
- Discord のスラッシュコマンドは **3秒以内に応答**しないと失敗扱い。重い処理は必ず先に `defer` する。
- ゲームパネルの Application API のユーザー更新は **全項目の送信が必要**。パスワードだけ送ると他の項目が消える。
- Cloudflare のゲーム用レコードは **必ず `proxied: false`**。
- SRV の向き先（target）は **A レコードのホスト名**にする（CNAME を向けない）。
- HAProxy の設定は **検証（`haproxy -c`）してから**差し替える。
