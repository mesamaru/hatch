# 実装チケット

## 進捗（2026-09-30 時点）

| チケット | 状態 | メモ |
|---|---|---|
| T00 開発環境 | 完了 | テストごとに移行済みの DB を複製（テンプレート DB 方式） |
| T01 設定・エラー・ログ | 完了 | 不足している設定を日本語で列挙。ログの秘密値マスク |
| T02 DB ヘルパー | 完了 | 非同期の接続プール |
| T03 名前とアドレス枠 | 完了 | 名前の正規表現の不具合を発見・修正（SPEC #37） |
| T04 権限 | 完了 | 権限表をそのままテストに |
| T05 ログインとセッション | 完了 | Discord OAuth・セッション（DB にはハッシュのみ）・CSRF・管理者の TOTP（RFC 6238 のテストベクターで確認、再利用と連続失敗を防止） |
| T06 ゲームパネルのアダプター | 完了 | 実機での確認は未（EXTERNAL.md の「要確認」） |
| T07 DNS のアダプター | 完了 | インスタンスごとのコメントで他環境のレコードを保護 |
| T08 ジョブ実行基盤 | 完了 | 再開・取り消しの続き・サーバー単位の排他を確認 |
| T10 アドレス枠の管理 API | 完了 | プレビュー・作成・変更の制限・DNS の事前作成と削除・スロットの停止・作成画面の選択肢 |
| T11 edge 設定の生成と配布 | 完了 | 生成物を haproxy -c・nft -c で検証。不具合2件を修正（SPEC #38・#39） |
| T12 デプロイ | 完了 | `POST /api/servers`・`GET /api/jobs/{id}` まで。API 越しの通しテストあり |
| T09 ドメイン・IP・紐付け | 完了 | ゾーン名の確認、edge.<ドメイン> の A レコードを自動作成、使用中の削除を防止 |
| T13 サーバーの参照と基本操作 | 完了 | 一覧・詳細（名前でも ID でも）・電源・メンテナンス・設定・使用量。他人のサーバーは 404 |
| T14 ゴミ箱・復元・完全削除 | 完了 | 期限切れの自動削除（定期処理）。最終バックアップは T23 で追加（今は「準備中」として飛ばす） |
| T15 画面の骨組み | 完了 | ナビゲーション（サイドバー・タブバー・ナビゲーションバー）・シート・トースト・検索の枠組み・URL 同期・API クライアント（CSRF・エラー表示）・ログイン・二段階認証（任意）・利用規約の同意・テーマと背景の切り替え |
| T16 画面：サーバーと作成・管理のアドレス | 完了 | サーバーの一覧・詳細・作成（3段階のシート）・ゴミ箱、管理のドメイン・IP と紐付け・アドレス枠（プレビュー付きの編集・DNS の事前作成・スロットの停止）。ジョブの進捗バナー。外部サービスをフェイクにした Playwright の通しテスト（`tests/e2e/test_servers_flow.py`）。API の無い画面（バックアップ・共有・監視など）は「準備中」 |
| T41 初期設定画面 | 完了 | コンテナ内の対話式設定をやめ、ブラウザのウィザードに。最初の管理者のロールもここで登録 |
| T42 権限の3段階とログインの改善 | 完了 | 管理者・サポーター・利用者。Discord サーバーのオーナーは常に管理者。初期設定の選択肢を独自のメニューに。サポーターの割り当ての画面は T22 で作る（API は完成）。二段階認証は任意（設定からオン・オフ） |
| T43 Linode のアカウント・edge・ファイアウォール | 完了 | 複数契約、共有・個別のファイアウォール、作成で開けて完全削除で締める。管理 → ノードと edge の画面。開けるポートはスロットの使用状況から毎回計算（別の表は持たない） |
| T44 Java 版の PROXY プロトコルの自動設定 | 未着手 | |
| T45 統合版（Geyser）の UDP 中継 | 未着手 | |
| T46 透過転送（統合版の専用サーバー・UDP のゲーム） | 未着手 | 要検証（Wings の Docker との組み合わせ） |
| T47 複数のゲームパネルと Wings | 未着手 | |
| T48 はじめの設定 | 完了 | 初期設定の後に管理画面で登録するものを、順番どおりの手順の一覧に。状態は登録済みのデータから毎回計算 |


**1回の作業で1チケット**。上から順に進めます（「依存」が終わっていないチケットには着手しない）。各チケットは単独でテストが通り、`update` で配布できる状態で終わらせます。

- 難易度：★ 仕様どおりに書けば終わる／★★ 判断が少しある／★★★ 失敗時の動きなど考えることが多い
- ★★★ のチケットは、最も能力の高いモデルで実装するか、実装後に別のモデル（または人）がレビューしてください。★・★★ は小型・安価なモデルでも実装できるように書いています。
- 「ファイル」に書いていないファイルは変更しないこと（テストファイルの追加は可）。

---

## フェーズ1：土台と、作って公開するまで

### T00 開発環境 ★
- 依存：なし
- ファイル：`requirements-dev.txt`、`pyproject.toml`（ruff と pytest の設定のみ）、`tests/conftest.py`、`.github/workflows/ci.yml`
- 内容：pytest・pytest-asyncio・respx・ruff を入れる。`conftest.py` にテストごとに空の DB を作り、移行を当てるフィクスチャ（`db`）を作る。CI で ruff と pytest を実行。
- 受け入れ条件：`pytest -q` が0件で成功し、CI が緑。

### T01 設定・エラー・ログ ★
- 依存：T00
- ファイル：`hatch/config.py`、`hatch/errors.py`、`hatch/logging.py`、`hatch/main.py`
- 内容：`docs/IMPLEMENTATION.md` 8.1 の全キーを Pydantic の Settings で読む（必須キーが無ければ起動時に日本語で「◯◯ が設定されていません」と出して終了）。`AppError` と、FastAPI の例外ハンドラ（API のエラー形式）。JSON 1行のログ（秘密値をマスク）。
- 受け入れ条件：必須キーが欠けると起動しない。`AppError` が API のエラー形式で返る。ログに `PANEL_APP_KEY` の値が出ない（テストで確認）。

### T02 DB ヘルパーとリポジトリの型 ★
- 依存：T01
- ファイル：`hatch/db.py`、`hatch/repo/__init__.py`、`hatch/models.py`
- 内容：psycopg の非同期接続プール、`async with transaction() as conn`。DB の各テーブルに対応する Pydantic モデル（読み取り用）。
- 受け入れ条件：トランザクション内で例外が起きるとロールバックされる（テスト）。

### T03 名前とアドレス枠の業務ルール ★★
- 依存：T00
- ファイル：`hatch/domain/names.py`、`hatch/domain/slots.py`
- 内容：`docs/IMPLEMENTATION.md` 4.1 の関数。サーバー名・ホスト名の検証。
- 受け入れ条件（すべてテストにする）：
  - `25560–25569 / mc{nn}trt / start=0 / 対象外=[25560]` → 25561=`mc01trt`、25569=`mc09trt`、25560 は対象外
  - `start=1` → 25560=`mc01trt`
  - `{server}` と `{nn}` の併用はエラー。番号も `{port}` も無い固定名はエラー
  - 他の枠と範囲が重なるとエラー。同じドメインでホスト名が重なるとエラー。IP の紐付けと重なるとエラー
  - 予約名（`edge` など）になるとエラー。64文字以上になるとエラー

### T04 権限 ★
- 依存：T00
- ファイル：`hatch/domain/permissions.py`
- 内容：`docs/IMPLEMENTATION.md` 3.5 の表。
- 受け入れ条件：表の全マス（役割 × action）をパラメータ化テストで確認。利用停止中の制限も確認。

### T05 ログインとセッション ★★★
- 依存：T02、T04
- ファイル：`hatch/auth/*`、`hatch/api/me.py`、`hatch/repo/users.py`、`hatch/repo/sessions.py`
- 内容：Discord OAuth（state の検証）、セッション Cookie、CSRF、`/api/me`、ログアウト、管理者の TOTP（設定・確認）。ロールの確認は Discord API でログイン時に行い、`discord_role_rules` に一致しない・招待もない人は `not_allowed`。
- 受け入れ条件：state 不一致で拒否。CSRF なしの POST は 403。セッション期限切れで 401。二段階認証を有効にした人は、コード入力前に `/api/me` など以外が 403。

### T06 ゲームパネルのアダプター ★★★
- 依存：T01
- ファイル：`hatch/adapters/panel.py`、`tests/fakes/panel.py`
- 内容：`docs/IMPLEMENTATION.md` 6 の `PanelAdapter` を Pterodactyl 用に実装。呼び方は `docs/EXTERNAL.md` 1。フェイクはメモリ上で同じ動きをする（アロケーションの ID が作成応答に無い点も再現）。
- 受け入れ条件：respx で各メソッドのリクエスト内容（パス・本文）を確認。`update_user` が全項目を送る。429 で再試行する。

### T07 DNS のアダプター ★★
- 依存：T01
- ファイル：`hatch/adapters/dns.py`、`tests/fakes/dns.py`
- 内容：Cloudflare 用。`upsert` は `docs/EXTERNAL.md` 2 の手順（手動レコードは上書きしない）。コメントには必ず `hatch:<PD_INSTANCE>` を入れ、一覧・削除は自分のインスタンスのものだけにする。
- 受け入れ条件：SRV の本文が `data` 形式。`proxied:false`・`comment` 付き。手動の同名レコードがあると失敗。削除の404は成功。`stg` のレコードが混ざったゾーンで、`prod` の一覧に `stg` が含まれない。

### T08 ジョブ実行基盤 ★★★
- 依存：T02
- ファイル：`hatch/jobs/engine.py`、`hatch/jobs/steps/__init__.py`、`hatch/worker.py`、`hatch/repo/jobs.py`、`deploy/systemd/hatch-worker.service`、`deploy/services.txt`
- 内容：`docs/IMPLEMENTATION.md` 5.1・5.2。
- 受け入れ条件（ダミーの手順でテスト）：全成功／3番目で失敗すると2→1の順に undo／undo が失敗すると `failed`／`locked_at` が古いジョブを別ワーカーが最後の完了手順の次から再開／同じサーバーのジョブが同時に走らない。

### T09 ドメイン・IP・紐付けの管理 API ★★
- 依存：T05、T07、T08
- ファイル：`hatch/api/domains.py`、`hatch/repo/domains.py`、`hatch/jobs/bindings.py`
- 内容：`docs/API.md` の `/admin/domains`・`/admin/ips`・`/admin/bindings`。ドメイン追加時にゾーン名を確認し、`edge.<domain>` の紐付けを作るジョブを登録。
- 受け入れ条件：ゾーン名が違うと `zone_mismatch`。使用中のドメイン・IP は削除できない。操作ログに残る。

### T10 アドレス枠の管理 API ★★
- 依存：T03、T09
- ファイル：`hatch/api/slots.py`、`hatch/repo/slots.py`、`hatch/jobs/publish_slots.py`
- 内容：`/admin/slot-rules`（一覧・作成・詳細・変更・削除・preview・publish・unpublish）、`/admin/slots/{port}`、`/slots/available`。変更の制限は `docs/IMPLEMENTATION.md` 4.3。
- 受け入れ条件：preview が T03 と同じ問題点を返す。範囲を変えるとスロット行が増減する。使用中スロットがあると削除・テンプレート変更ができない。publish で全スロットの CNAME・SRV がフェイク DNS に作られ、2回実行しても重複しない。

### T11 edge 設定の生成と配布 ★★
- 依存：T08
- ファイル：`hatch/edge/haproxy.py`、`hatch/edge/nft.py`、`hatch/api/edge.py`、`hatch/repo/edges.py`
- 内容：`docs/IMPLEMENTATION.md` 7。`/api/edge/config`・`/api/edge/report`。
- 受け入れ条件：同じ入力で同じ文字列。内容が変わらなければ版が増えない。`haproxy -c` と `nft -c` が通る（CI にパッケージを入れて実行）。既存の `edge/agent.py` と通信できる（結合テスト）。

### T12 デプロイ ★★★
- 依存：T06、T07、T08、T10、T11
- ファイル：`hatch/jobs/deploy.py`、`hatch/jobs/steps/*.py`、`hatch/api/servers.py`（POST のみ）、`hatch/api/jobs.py`、`hatch/domain/placement.py`
- 内容：`docs/IMPLEMENTATION.md` 5.3 の deploy（監視追加の手順は T17 まで「対象外」で飛ばす）。ノード選択 5.4。ドライラン。
- 受け入れ条件：成功時にフェイクのパネル・DNS・edge がそろう。事前作成済みの枠では DNS の手順が飛ばされる。各手順で失敗させると、作ったものがすべて消える（フェイクに何も残らない）。同じ `idempotency_key` の2回目は同じジョブを返す。上限・スロット競合・名前重複のエラー。

### T13 サーバーの参照と基本操作 ★★
- 依存：T12
- ファイル：`hatch/api/servers.py`（GET・PATCH・power・maintenance・resources）、`hatch/repo/servers.py`
- 受け入れ条件：権限表どおり（他人のサーバーは 404。存在を知らせない）。stop で状態が `stopped` になる。

### T14 ゴミ箱・復元・完全削除 ★★★
- 依存：T13
- ファイル：`hatch/jobs/trash.py`、`restore.py`、`purge.py`、`hatch/scheduler.py`（72時間後の自動削除のみ）
- 受け入れ条件：ゴミ箱の間は名前とスロットが予約される。復元で元のアドレスに戻る。完全削除で事前作成の DNS は残り、`{server}` 枠の DNS は消える。`confirm_name` 不一致は拒否。

### T15 画面の骨組み ★★
- 依存：T05
- ファイル：`web/index.html`（デモを置き換え）、`web/js/app.js`、`api.js`、`router.js`、`components/*.js`、`web/css/*.css`、デモは `web/demo.html` に移す
- 内容：`docs/UI.md` のナビゲーション・シート・トースト・検索の枠組み。URL と画面の同期。API クライアント（CSRF・エラー表示）。
- 受け入れ条件：`docs/UI.md` 3.2A の全サイズで見た目がデモと一致し、横スクロールが出ない（Playwright のスクリーンショット比較）。再読み込みで同じ画面。背景（用意したもの）とテーマの切り替えが動く。

### T16 画面：サーバーと作成・管理のアドレス ★★
- 依存：T10、T13、T14、T15
- ファイル：`web/js/pages/servers*.js`、`web/js/pages/admin-address*.js`
- 受け入れ条件：デモと同じ手順で作成・削除・復元・枠の追加・DNS の事前作成ができる（Playwright）。

**フェーズ1の完了時点で v0.2.0 を出す**：Web から作成して公開、ゴミ箱、アドレス枠の管理まで。

---

## フェーズ2：監視・Discord・期限・整合性

### T17 Uptime Kuma 連携と自動再起動 ★★★
- 依存：T12
- ファイル：`hatch/adapters/monitor.py`、`tests/fakes/monitor.py`、`hatch/jobs/steps/monitor.py`、`hatch/api/hooks.py`、`hatch/scheduler.py`（監視の取得・push）、`hatch/domain/autorestart.py`
- 受け入れ条件：デプロイで監視が追加される。Webhook のダウン通知で、条件（`docs/SPEC.md` 5）を満たすときだけ再起動し、1時間3回で止めて通知する。メンテナンス・停止・利用停止の間は監視が一時停止。

### T18 Discord Bot ★★★
- 依存：T13、T17
- ファイル：`hatch/bot/*`、`hatch/adapters/notify.py`、`deploy/systemd/hatch-bot.service`
- 受け入れ条件：3秒以内に defer。`/deploy` の進捗メッセージが更新される。ボタンを他人が押しても操作できない。ロールの付与・剥奪でユーザーが有効・停止予約になる。DM が送れなくても落ちない。

### T19 期限と通知 ★★
- 依存：T14、T18
- ファイル：`hatch/domain/expiry.py`、`hatch/scheduler.py`（期限）、`hatch/api/servers.py`（extend）、`hatch/api/admin.py`（延長申請）
- 受け入れ条件：7・3・1日前に1回ずつ通知（2回動かしても1回）。延長すると次の期限で再び通知。期限日に停止、猶予後にゴミ箱。

### T20 整合性チェック ★★★
- 依存：T17
- ファイル：`hatch/jobs/reconcile.py`、`hatch/api/admin.py`（findings）
- 受け入れ条件：フェイクに「DB にない DNS」「DB にない監視」「proxied=true」「パネルにだけあるサーバー」を仕込むと、それぞれ正しい種類の指摘が出る。自動修正は安全なものだけ。

### T21 ユーザー管理とパスワード同期 ★★
- 依存：T05、T06
- ファイル：`hatch/api/users.py`、`hatch/api/me.py`（password）、`hatch/jobs/sync_user.py`
- 受け入れ条件：パスワードがジョブや DB やログに残らない。パネル側の更新に失敗したらパネル側もこちら側も変わらない。

### T22 画面：監視・管理・設定 ★★
- 依存：T17〜T21、T15
- 受け入れ条件：デモと同じ見た目と手順。

### T35 自己監視の仕上げ ★★
- 依存：T08、T17、T18
- ファイル：`hatch/worker.py`、`hatch/scheduler.py`、`hatch/bot/*`（beat の呼び出しのみ）、`hatch/jobs/self_monitor.py`、`hatch/main.py`（起動時の登録）、`deploy/systemd/*.service`
- 内容：`docs/IMPLEMENTATION.md` 8A。`health.py` と `/api/health/full` は実装済み（テストあり）。各プロセスからの beat と Push、Kuma への自己監視の登録、管理画面「システムの状態」の API（`/api/admin/system` → `full_report()` を返すだけ）。
- 受け入れ条件：worker を止めると2分以内に Kuma の Push 監視が停止になる（フェイクで確認）。登録を2回実行しても監視が増えない。自己監視の監視にオーケストレーター宛ての Webhook が付いていない。

### T36 退会と削除 ★★★
- 依存：T14、T21
- ファイル：`hatch/api/me.py`（withdraw）、`hatch/jobs/delete_account.py`、`hatch/scheduler.py`（期限の確認・前日の通知）、`hatch/repo/users.py`
- 内容：`docs/SPEC.md` 4「退会とデータの削除」、`docs/IMPLEMENTATION.md` 5.3 の delete_account。
- 受け入れ条件：申請でサーバーが停止し作成できなくなる。取り消しで戻る。7日後のジョブの後、フェイクのパネル・DNS・監視・ディスクに本人のものが何も残らず、users には個人情報のない行だけが残る。操作ログは残り、表示は「退会したユーザー」。最後の管理者は退会できない。途中で失敗しても再実行で最後まで進む。

### T37 背景と表示の設定 ★★
- 依存：T15、T05
- ファイル：`hatch/api/me.py`（preferences・background）、`hatch/uploads.py`、`hatch/api/uploads.py`、`web/js/pages/settings-display.js`、`requirements.txt`（Pillow・pillow-heif を追加）
- 内容：`docs/IMPLEMENTATION.md` 8B、`docs/UI.md`。
- 受け入れ条件：位置情報入りの JPEG を上げると、保存された WebP にメタデータが無い。20MB 超は 413。他人の画像は 404。全員の既定に設定した画像はログインした全員が読める。退会（T36）で画像も消える。

### T38 テスト環境 ★
- 依存：T12
- ファイル：`docs/ENVIRONMENT.md`（手順の確認と修正のみ）、`.github/workflows/release.yml`（ベータ版の配布確認）
- 内容：`docs/ENVIRONMENT.md` の「テスト環境」を実際に作り、ベータ版を `update --channel beta` で入れて一通り動かす。気づいた点を手順に反映する。
- 受け入れ条件：テスト環境から作ったサーバーの DNS と監視を、本番の整合性チェックが「不要」と判断しない。

**v0.3.0**

---

## フェーズ3：運用の便利機能

| ID | 内容 | 難易度 | 主なファイル |
|---|---|---|---|
| T23 | バックアップ（一覧・作成・DL・復元、自動の予約） | ★★ | `api/backups.py`、`jobs/restore_backup.py` |
| T24 | 共同管理者 | ★★ | `api/shares.py` |
| T25 | 独自ドメイン | ★★ | `api/custom_domain.py`、`scheduler.py`（再確認） |
| T26 | プラグイン・本体の更新 | ★★★ | `adapters/plugins.py`、`jobs/plugins.py` |
| T27 | 開発用コピー | ★★★ | `jobs/dev_copy.py` |
| T28 | Velocity 連携 | ★★★ | `jobs/proxy.py`（tomlkit・ruamel.yaml を追加） |
| T29 | 状態ページ | ★ | `api/public.py`、`web/status.html` |
| T30 | 画面：上記すべて | ★★ | `web/js/pages/server-*.js` |

**v0.4.0**

## フェーズ4：管理者向け

| ID | 内容 | 難易度 |
|---|---|---|
| T31 | お知らせ（Web・Discord・対象の絞り込み） | ★★ |
| T32 | 違反対応と利用規約（版・同意） | ★★ |
| T33 | edge の自動切り替え（オーケストレーターと Kuma の両方の判定） | ★★★ |
| T34 | 操作ログの画面・画面から変える設定 | ★ |

**v0.5.0**

## フェーズ5：料金

`docs/SPEC.md` 10 を詳細化してからチケットを作る（Stripe の Webhook、プランの価格、請求の記録、特定商取引法の表記）。

---

### T41 初期設定画面 ★★
- 依存：T01、T05、T15
- ファイル：`hatch/config.py`、`hatch/setup.py`、`hatch/api/setup.py`、`hatch/adapters/setup_checks.py`、`hatch/repo/setup.py`、`hatch/main.py`、`hatch/worker.py`、`hatch/scheduler.py`、`web/js/setup.js`、`web/js/app.js`、`web/css/components.css`、`db/migrations/0002_setup_state.sql`、`deploy/bin/hatch-setup`、`deploy/systemd/*`、`deploy/services.txt`、`misc/update.sh`、`install/hatch-install.sh`、`ct/hatch.sh`
- 内容：`docs/SPEC.md` 8「初期設定（ブラウザ）」、`docs/IMPLEMENTATION.md` 8.3、`docs/API.md`「初期設定」。
- 受け入れ条件：外部サービスの設定が無くても API が起動し、パネルを開くと初期設定画面になる。コードが違えば 403。秘密の値を応答に含めない。保存すると `setup.env`（権限 600）とロールの対応表が書かれ、完了後は 409。設定が壊れると再び初期設定が必要になる。既存の環境（管理者のロールあり）は移行で完了扱いになる。

### T42 権限の3段階とログインの改善 ★★
- 依存：T04、T05、T13、T41
- ファイル：`db/migrations/0003_supporter.sql`、`hatch/domain/permissions.py`、`hatch/repo/servers.py`、`hatch/repo/supporters.py`、`hatch/repo/setup.py`、`hatch/repo/users.py`、`hatch/api/auth.py`、`hatch/api/servers.py`、`hatch/api/setup.py`、`hatch/adapters/discord_oauth.py`、`hatch/adapters/setup_checks.py`、`web/js/setup.js`、`web/js/app.js`、`web/js/components/picker.js`、`web/js/components/icons.js`、`web/css/components.css`
- 内容：`docs/SPEC.md`「権限（3段階）」、`docs/IMPLEMENTATION.md` 3.5、`docs/API.md`「初期設定」「サーバー」、`docs/UI.md`「選択肢」。
- 受け入れ条件：サポーターは割り当てられたサーバーだけが見え、閲覧と電源操作ができ、設定・削除はできない（他のサーバーは 404）。割り当ては管理者だけができ、サポーター以外は `400 not_supporter`。サポーターでなくなると割り当てが消える。Discord サーバーのオーナーはロールが無くても管理者になる。ロールが無い人は `no_role` で理由を表示する。初期設定でロールごとに3段階から選べ、管理者が1つも無ければ保存できない。選ばなかったロールは対応表から外れる。

### T43 Linode のアカウント・edge・ファイアウォール ★★★
- 依存：T11、T12、T14
- ファイル：`db/migrations/0004_linode.sql`、`hatch/adapters/linode.py`、`hatch/jobs/firewall.py`、`hatch/api/infra.py`、`hatch/jobs/deploy.py`・`lifecycle.py`（公開と完全削除）、`tests/fakes/linode.py`、`web/js/pages/admin-infra.js`
- 内容：`docs/SPEC.md` 6B「Linode のアカウントとファイアウォール」、`docs/IMPLEMENTATION.md` 7A.1・7A.2、`docs/API.md`「Linode・edge・ゲームパネル」。
- 受け入れ条件：複数の Linode アカウントを登録でき、トークンは応答に出ない。edge ごとにファイアウォールを共有・個別で選べる。作成でポートが開き、ゴミ箱の間は開いたまま、完全削除と作成の取り消しで締まる。手で作ったルールは残る。25ルールを超えるときは反映せずに理由を記録する。連続ポートは範囲にまとまる。

### T44 Java 版の PROXY プロトコルの自動設定 ★★
- 依存：T12、T47（なくても可）
- ファイル：`hatch/games.py`（`configure`）、`hatch/adapters/panel.py`（ファイルの書き込み）、`hatch/jobs/deploy.py`、`deploy/games.example.yml`
- 内容：作成時に、Paper の `proxies.proxy-protocol: true`、Velocity の `haproxy-protocol = true` をゲームパネルのファイル API で書き込む（`games.yml` の `configure` で定義）。
- 受け入れ条件：`proxy_protocol: true` のゲームは、作成後の最初の起動から PROXY プロトコルを受け付ける設定になっている（フェイクのパネルで書き込みを確認）。

### T45 統合版（Geyser）の UDP 中継 ★★★
- 依存：T11、T43、T44
- ファイル：`edge/agent.py`（UDP 中継）、`hatch/edge/render.py`・`publish.py`（`udp_relays`）、`hatch/games.py`（`bedrock: geyser`）、画面の統合版の表示
- 内容：`docs/IMPLEMENTATION.md` 7A.3。Geyser 付きのゲームは同じポート番号の UDP も公開し、プレイヤーごとの送信ソケットで PROXY プロトコル v2 を最初のパケットにだけ付けて中継する。Geyser の設定を自動で書き込む。
- 受け入れ条件：中継の単体テストで、プレイヤーごとに送信ポートが分かれ、最初のパケットにだけ正しい PROXY v2 ヘッダー（送信元＝プレイヤー）が付き、返信が正しいプレイヤーに戻る。無通信のプレイヤーは閉じる。上限を超えると捨てる。

### T46 透過転送（統合版の専用サーバー・UDP のゲーム） ★★★
- 依存：T11、T43
- ファイル：`hatch/edge/render.py`（nftables）、`edge/agent.py`、`wings/install-hatch-route.sh`（新規）、`nodes` の「透過転送に対応済み」
- 内容：`docs/IMPLEMENTATION.md` 7A.4。**先にテスト環境で、Wings の Docker ネットワークと組み合わせて返信が edge に戻ることを確認する**（確認できない場合は、この方式をやめて報告する）。
- 受け入れ条件：対応済みのノードの UDP のゲームで、サーバーから見えるプレイヤーの IP が本物になる（テスト環境での手動確認の記録を報告に書く）。生成する nftables の設定は `nft -c` で検証するテストがある。

### T47 複数のゲームパネルと Wings ★★★
- 依存：T06、T12、T21（パスワード同期がある場合）
- ファイル：`db/migrations/0005_panels.sql`、`hatch/api/deps.py`（パネルごとのアダプター）、`hatch/jobs/deploy.py`、`hatch/api/infra.py`、`hatch/setup.py`・`api/setup.py`（1件目のパネル）、画面の管理
- 内容：`docs/IMPLEMENTATION.md` 7A.1・7A.5。
- 受け入れ条件：2つのパネルを登録し、それぞれのノードにサーバーを作れる（フェイクのパネル2つで確認）。利用者のアカウントは、そのパネルで初めて作るときだけ作られる。既存の環境は移行で1件目のパネルになり、そのまま動く。キーは応答に出ない。

### T48 はじめの設定 ★★
- 依存：T16、T43
- ファイル：`hatch/api/getting_started.py`（新規）、`hatch/main.py`、`web/js/pages/getting-started.js`（新規）、`web/js/pages/admin-address.js`（管理トップの案内・保存後の遷移）、`web/js/pages/admin-infra.js`（他の画面からシートを開く）、`web/js/pages/servers.js`（管理者への案内）、`web/js/app.js`、`web/js/router.js`
- 内容：`docs/SPEC.md` 8「はじめの設定」、`docs/API.md`「管理」の GettingStarted、`docs/UI.md` 3.2。
- 受け入れ条件：何も登録していない状態では edge が「次にやる」、ドメイン以降が「まだ押せない」。edge・ドメイン・枠・DNS を順に登録すると、各手順が「済み」になり `complete` が true になる。edge より先にドメインを登録した状態では、ドメインが「失敗」で理由と `retry.bindings` を返す。Linode を使わずに edge を登録すると、Linode とファイアウォールは「飛ばした」。管理者以外は 403。

### T39 オーケストレーターの DB の遠隔バックアップ ★★
- 依存：T08
- ファイル：`hatch/jobs/db_backup.py`、`hatch/scheduler.py`、`hatch/adapters/storage.py`
- 内容：毎日 4:30 に `pg_dump -Fc` を作り、Object Storage のバケットの `hatch/<PD_INSTANCE>/db/` に送る。30日分を残す。失敗したら管理者に通知し、自己監視の `degraded` にする。
- 受け入れ条件：フェイクのストレージに毎日1つずつ増え、31日目に最も古いものが消える。復元手順を `README.md` に書く。

## 未定：Pelican への移行

現在の開発（フェーズ4まで）が一段落してから検討します。着手するときは、まず「T40 Pelican の調査」として、Pelican の API と Pterodactyl の API の差（ユーザー・サーバー・アロケーション・バックアップ・サブユーザー・ファイル）を `docs/EXTERNAL.md` に表でまとめ、`PanelAdapter` のインターフェースを変えずに済むかを確認します。

---

## チケットを AI に渡すときの定型文

```
あなたは Hatch リポジトリの実装担当です。
1. AGENTS.md を読み、ルールに従ってください。
2. docs/TASKS.md の「T12 デプロイ」だけを実装してください。
3. 関連する仕様：docs/SPEC.md 3章、docs/IMPLEMENTATION.md 4・5・6・7章、docs/API.md の「サーバー」「ジョブ」、docs/EXTERNAL.md 1・2章。
4. 仕様に書かれていない判断が必要になったら、実装を止めて質問してください。
5. 完了したら「変更したファイル」「受け入れ条件ごとのテスト名」「残った課題」を報告してください。
```
