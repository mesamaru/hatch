# Discord Bot セットアップ詳細ガイド

Hatch が Discord でログイン・通知・スラッシュコマンドを使うための Bot を招待し、設定する完全な手順です。

---

## 1. Discord Developer Portal での Bot 作成

### ステップ 1-1：Developer Portal にアクセス

1. **Discord Developer Portal** を開く
   - [discord.com/developers/applications](https://discord.com/developers/applications)
2. Discord アカウントでログイン

### ステップ 1-2：新しいアプリケーション作成

1. 右上の **「New Application」** をクリック
2. アプリケーション名を入力（例：`Hatch`）
3. **「Create」** をクリック

### ステップ 1-3：Bot ユーザーを追加

1. 左メニューから **「Bot」** をクリック
2. **「Add Bot」** をクリック
3. Bot が作成されます

---

## 2. Bot トークン（秘密情報）の取得

### トークンのコピー

1. 「Bot」ページの **「TOKEN」** セクション
2. **「Copy」** をクリック
3. テキストエディタに貼付（秘密情報として扱う）

**例：** `MTk4NjIyNDgzNTk5MTU4MzQw.XXXXXX.XXXXXXXXXXXXXXXXXXXXXXXX`（実際のトークンは別形式）

この値は Hatch セットアップ時に「Discord Bot トークン」として使用します。

---

## 3. クライアント ID・シークレットの取得

### ステップ 3-1：General Information ページを開く

1. 左メニューから **「General Information」** をクリック
2. 以下を確認・コピー：

| 項目 | 説明 |
|---|---|
| **CLIENT ID** | Bot を識別する ID。招待 URL 生成に使う |
| **CLIENT SECRET** | 秘密情報。絶対に公開しない |

**例：**
```
CLIENT ID: 123456789012345678
CLIENT SECRET: XXXXXXXXXXXXXXXXXXXXXXXXXXXX
```

---

## 4. OAuth2 設定

### ステップ 4-1：Redirect URL を設定

1. 左メニューから **「OAuth2」** → **「General」** をクリック
2. **「Redirects」** セクション
3. **「Add Redirect」** をクリック
4. 以下を入力（`<panel-domain>` は自分のパネルドメイン）：
   ```
   https://<panel-domain>/api/auth/callback
   ```
   **例：** `https://hatch.nuids.jp/api/auth/callback`
5. **「Save Changes」** をクリック

> ⚠️ **重要：** HTTPS で始まる必要があります。HTTP ではエラーになります。

### ステップ 4-2：OAuth2 スコープを設定

1. **「OAuth2」** → **「URL Generator」** をクリック
2. **Scopes** から以下にチェック：
   - ✅ `bot`
   - ✅ `applications.commands`

### ステップ 4-3：Bot 権限を選択

**Scopes で `bot` を選んだ後、以下の権限にチェック：**

#### メッセージ関連（必須）
- ✅ `Send Messages` - メッセージ送信
- ✅ `Embed Links` - 埋め込み送信
- ✅ `Read Message History` - メッセージ履歴読み取り
- ✅ `Read Messages/View Channels` - チャンネル表示

#### スラッシュコマンド関連
- ✅ `Use Slash Commands` - スラッシュコマンド使用

#### ロール管理（オプション。ユーザーに役割を付与する場合）
- ☑️ `Manage Roles` - ロール管理
- ☑️ `Manage Guild` - サーバー管理

#### 推奨権限セット

```
Send Messages
Embed Links
Read Message History
Read Messages/View Channels
Use Slash Commands
```

### ステップ 4-4：招待 URL を生成

1. 上記の設定後、**URL Generator** の最下部に招待 URL が表示されます
2. **「Copy」** をクリック
3. ブラウザで開く、またはメモ

**例：**
```
https://discord.com/api/oauth2/authorize?client_id=123456789012345678&permissions=274948699136&scope=bot%20applications.commands
```

---

## 5. Bot をサーバーに招待

### ステップ 5-1：招待 URL を開く

1. ステップ 4-4 でコピーした URL をブラウザで開く
2. または、Developer Portal の URL Generator から直接開く

### ステップ 5-2：サーバーを選択

1. 「サーバーを選択」ドロップダウンから、Hatch を使うサーバーを選択
2. **「認証」** をクリック

### ステップ 5-3：権限を確認

権限一覧が表示されます。以下を確認：
- ✅ `メッセージを送信`
- ✅ `埋め込みリンク`
- ✅ `メッセージの履歴を読み取り`
- ✅ `スラッシュコマンドを使用`

確認後、**「認証」** をクリック

### ステップ 5-4：Bot がサーバーに追加されたか確認

1. Discord のサーバーを開く
2. **サーバー設定** → **インテグレーション** → **Bot** を確認
3. `Hatch` が表示されていることを確認

---

## 6. Intent 設定（重要）

### Server Members Intent を有効化する

**Hatch がユーザー情報を取得するために必須です。**

1. Developer Portal で Bot のアプリケーションを開く
2. 左メニュー **「Bot」** をクリック
3. **「Privileged Gateway Intents」** セクション
4. **「Server Members Intent」** をオンにする
5. **「Save Changes」** をクリック

> ⚠️ **警告が出ても大丈夫：** 「このインテントを有効にするとボットは認証を必要とします」というメッセージが出ても、開発段階では問題ありません。

---

## 7. Discord サーバーの準備

### ステップ 7-1：チャンネルを作成

Hatch が通知を送るための 2 つのチャンネルを作成：

1. **#お知らせ** - ユーザーへの通知（サーバー作成完了など）
2. **#運営通知** - 運営者向けの通知（エラー、重要な出来事）

作成方法：
1. サーバーを右クリック → **「テキストチャンネルを作成」**
2. チャンネル名を入力
3. **「作成」**

### ステップ 7-2：チャンネル ID を取得

各チャンネルを右クリック → **「IDをコピー」**

**例：**
```
お知らせ チャンネル ID: 1234567890123456789
運営通知 チャンネル ID: 9876543210987654321
```

### ステップ 7-3：ロール を作成

Hatch で利用者の権限を管理するためのロール：

1. **サーバー設定** → **ロール** → **「＋ロールを作成」**
2. ロール名を入力（例：`ユーザー`、`サポーター`、`運営`）
3. 各ロール ID をコピー

**例：**
```
ユーザー ロール ID: 1111111111111111111
サポーター ロール ID: 2222222222222222222
運営 ロール ID: 3333333333333333333
```

---

## 8. Hatch セットアップでの設定

### hatch-setup で入力する値

コンテナで `hatch-setup` を実行する際に、以下を入力：

```
Discord Bot トークン:
  → ステップ 2 で取得した TOKEN
  例: MTk4NjIyNDgzNTk5MTU4MzQw.XXXXXX.XXXXXXXXXXXXXXXXXXXX

Discord クライアント ID:
  → ステップ 3 の CLIENT ID
  例: 123456789012345678

Discord クライアント シークレット:
  → ステップ 3 の CLIENT SECRET
  例: XXXXXXXXXXXXXXXXXXXXXXXXXXXX

Discord サーバー（ギルド）ID:
  → サーバーを右クリック → IDをコピー
  例: 9999999999999999999

Discord お知らせ channel ID:
  → ステップ 7-2 の チャンネル ID
  例: 1234567890123456789

Discord 運営通知 channel ID:
  → ステップ 7-2 の チャンネル ID
  例: 9876543210987654321
```

---

## 9. Hatch での Discord ロール連携設定

### 画面から設定

セットアップ完了後、Hatch パネルで：

1. **管理** → **Discord ロール連携** をクリック
2. **「追加」** をクリック
3. 以下を入力：

| 項目 | 例 | 説明 |
|---|---|---|
| **Discord ロール名** | ユーザー | サーバーで作成したロール名 |
| **ロール ID** | 1111111111111111111 | Discord でコピーしたロール ID |
| **サーバー作成上限台数** | 3 | このロールが作成できるサーバー台数 |
| **権限レベル** | 利用者 | `利用者` / `サポーター` / `運営` など |

4. **「保存」** をクリック

複数のロールを登録する場合は、ステップを繰り返す

---

## 10. トラブルシューティング

### Bot が Discord に表示されない

**症状：** Bot をサーバーに招待しても表示されない

**確認：**
1. Developer Portal で Bot が作成されているか
2. 招待 URL が正しいか（CLIENT ID が含まれているか）
3. サーバーの権限があるか（管理者権限が必要）

### ログイン画面で「ロールがない」と表示される

**症状：** 「利用できるロールがなく、招待もされていません」

**原因と対策：**
1. Discord ロール連携が設定されていない
   → Hatch 管理画面で設定を追加
2. ユーザーが Discord のロールを持っていない
   → Discord でロールを付与
3. Bot が Member Intent を有効にしていない
   → ステップ 6 を確認

### スラッシュコマンドが動作しない

**症状：** Bot コマンドが表示されない

**確認：**
1. `applications.commands` スコープが有効か（ステップ 4-2）
2. Bot のトークンが正しいか
3. サーバーで Bot が見えているか

### OAuth2 コールバックでエラー

**症状：** ログイン時に「Invalid Redirect URI」エラー

**原因と対策：**
1. Developer Portal の Redirect URL が間違っている
   → ステップ 4-1 を確認。`https://` で始まる必要あり
2. Hatch パネルのドメイン名が変わった
   → Developer Portal の URL を更新

---

## チェックリスト

セットアップ完了確認：

- [ ] Developer Portal で Bot を作成
- [ ] Bot トークンを取得
- [ ] CLIENT ID・SECRET を取得
- [ ] OAuth2 Redirect URL を設定（https://...）
- [ ] Bot スコープを設定（bot、applications.commands）
- [ ] Bot 権限を設定（Send Messages、Embed Links など）
- [ ] Server Members Intent を有効化
- [ ] Bot を Discord サーバーに招待
- [ ] Bot がサーバーに表示されている
- [ ] Discord チャンネル 2 つを作成（お知らせ、運営通知）
- [ ] チャンネル ID を取得
- [ ] Discord ロールを作成
- [ ] ロール ID を取得
- [ ] hatch-setup ですべての値を入力
- [ ] Hatch 管理画面で Discord ロール連携を設定

---

## セキュリティに関する注意

### 秘密情報の取り扱い

- **Bot トークン** - 絶対に公開しない（GitHub に入れない）
- **CLIENT SECRET** - 同様に秘密
- これらは `/etc/hatch/hatch.env` に保存される（権限 640）

### 本番運用への移行

1. **テスト用 Bot を別途作成** して、テスト環境で試す
2. 問題がないことを確認してから本番 Bot を使う
3. 定期的にトークンをリセット（Developer Portal で可能）
