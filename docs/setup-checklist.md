# Hatch セットアップ：必要な情報収集ガイド

`hatch-setup` を実行する前に、以下の情報を集めておきましょう。**実行中に慌てずに済みます。**

このガイドは、各サービスのどこから情報を取得するか、具体的な手順をまとめています。

---

## 1. Pterodactyl パネル

### ① Application API キー

**用途：** Hatch がパネルのシステム（ユーザー・ノード・サーバー・nest・egg など）を操作する

**取得方法：**

1. Pterodactyl パネルに **管理者アカウント** でログイン
2. Admin Panel（管理者ページ）に入る
3. **API Management** → **Applications**
4. **Create Application Token** をクリック
5. 以下の権限にチェック（`hatch` と入力して絞り込むと速い）：
   - `user:read`
   - `user:update`
   - `node:read`
   - `allocation:read`
   - `allocation:create`
   - `server:read`
   - `server:create`
   - `nest:read`
   - `egg:read`
6. **Create** をクリック
7. 表示されたトークンをコピー → **メモ帳に貼付**

```
Pterodactyl Application API キー: ___________________________
```

### ② Client API キー（root 管理者用）

**用途：** Hatch が root 管理者として特定の操作を実行する

**取得方法：**

1. Pterodactyl パネルで、右上のユーザーアイコンをクリック
2. **Account** を開く
3. 左メニューから **API Tokens**
4. **Create Token** をクリック
5. 説明（例：`hatch-root`）を入力
6. **Create** をクリック
7. 表示されたトークンをコピー → **メモ帳に貼付**

```
Pterodactyl Client API キー（root 管理者用）: ___________________________
```

### ③ パネルの URL

**例：** `panel.example.com` または `https://panel.example.com`

```
Pterodactyl パネルの URL: ___________________________
```

---

## 2. Cloudflare

### ① API トークン

**用途：** Hatch がドメインの DNS レコードを自動作成・削除する

**取得方法：**

1. Cloudflare ダッシュボードにログイン
2. 左上プロフィール → **API tokens**
3. **Create Token** をクリック
4. Template から **Edit zone DNS** を選択
5. 権限を確認（以下が自動設定されていることを確認）：
   - `Zone:Read`
   - `DNS:Edit`
6. **Zones** の対象を、Hatch で使うゾーン（例：`nuids.jp`）に設定
7. **Continue to summary** → **Create Token**
8. 表示されたトークンをコピー → **メモ帳に貼付**

```
Cloudflare API トークン: ___________________________
```

### ② ゾーン ID

**用途：** Hatch がドメインを特定する

**取得方法：**

1. Cloudflare で管理するドメインの Zone をクリック
2. 右下の **Zone ID** をコピー → **メモ帳に貼付**

例：`abcdef1234567890abcdef1234567890`

```
Cloudflare ゾーン ID: ___________________________
```

**注意：** 複数ドメインを使う場合は、各ドメインのゾーン ID を記録

---

## 3. Tailscale（オプション）

### ① 認証キー

**用途：** コンテナを Tailscale ネットワークに自動接続する（後からでも手動設定可能）

**取得方法：**

1. Tailscale 管理画面にログイン
2. **Settings** → **Authentication tokens**
3. **Generate auth token** をクリック
4. **Reusable** にチェック（複数コンテナを作る場合）
5. **Generate** をクリック
6. 表示されたトークンをコピー → **メモ帳に貼付**

例：`tskey-auth-abcdef1234567890-1234567890`

```
Tailscale 認証キー: ___________________________
```

### ② ホスト名

**用途：** Tailscale 内でこのコンテナにアクセスする際の名前

**デフォルト：** `hatch`

Tailscale に接続後、`http://hatch:8080` でアクセス可能になります。

```
Tailscale ホスト名（デフォルト: hatch）: ___________________________
```

---

## 4. Discord

### ① Bot トークン

**取得方法：**

1. Discord Developer Portal にログイン
2. **Applications** → アプリを選択（ない場合は **New Application**)
3. 左メニュー **Bot** → **TOKEN** の下の **Copy** をクリック
4. → **メモ帳に貼付**

```
Discord Bot トークン: ___________________________
```

### ② クライアント ID・シークレット

**取得方法：**

1. 同じアプリの **General Information**
2. **CLIENT ID** と **CLIENT SECRET** をコピー → **メモ帳に貼付**

```
Discord クライアント ID: ___________________________
Discord クライアント シークレット: ___________________________
```

### ③ OAuth2 リダイレクト URL 設定

**やること：** Developer Portal で「リダイレクト URL」を設定

1. **OAuth2** → **Redirects**
2. **Add Redirect** をクリック
3. 以下を入力（`<パネルドメイン>` は自分のもの）：
   ```
   https://<パネルドメイン>/api/auth/callback
   ```
   例：`https://panel.nuids.jp/api/auth/callback`
4. **Save Changes**

### ④ ギルド（Discord サーバー）ID

**取得方法：**

1. Discord アプリで、Hatch を使う Discord サーバーを右クリック
2. **ID をコピー** → **メモ帳に貼付**

```
Discord ギルド ID: ___________________________
```

### ⑤ チャンネル ID（2 つ）

**お知らせチャンネル：** Hatch がシステム通知を送信（例：サーバー作成完了）

**運営通知チャンネル：** Hatch が運営者向けの通知を送信（例：エラー発生）

**取得方法：**

各チャンネルを右クリック → **ID をコピー** → **メモ帳に貼付**

```
Discord お知らせチャンネル ID: ___________________________
Discord 運営通知チャンネル ID: ___________________________
```

### ⑥ Bot の権限設定（初回のみ）

**やること：** Bot が必要な権限を持つか確認

1. Developer Portal → **Bot** → **Permissions**
2. 以下にチェック：
   - `Send Messages`
   - `Embed Links`
   - `Read Message History`
   - `Use Slash Commands`
3. **Copy** で URL を取得 → サーバーに Bot を招待

---

## 5. Uptime Kuma

### ① URL

**例：** `http://192.168.1.60:3001` または `http://kuma:3001`（Tailscale の場合）

```
Uptime Kuma URL: ___________________________
```

### ② ユーザー名・パスワード

**注意：** Kuma に存在しないユーザーなら自動作成されます

```
Uptime Kuma ユーザー名: ___________________________
Uptime Kuma パスワード: ___________________________
```

### ③ メトリクス API キー

**取得方法：**

1. Uptime Kuma にログイン
2. **Settings** → **API Keys**
3. **Add API Key** をクリック
4. 説明（例：`hatch`）を入力
5. **Save** → 生成されたキーをコピー → **メモ帳に貼付**

```
Uptime Kuma メトリクス API キー: ___________________________
```

---

## 6. Object Storage（バックアップ用 S3）

### ① エンドポイント

**例（Linode の場合）：** `https://us-west-1.linodeobjects.com`

取得方法は Object Storage サービスによって異なります。

```
S3 エンドポイント: ___________________________
```

### ② バケット名

**例：** `hatch-backups`

```
S3 バケット名: ___________________________
```

### ③ アクセスキー・シークレットキー

**取得方法（Linode の場合）：**

1. Linode ダッシュボード → **Object Storage**
2. バケットを作成
3. **Access Keys** で新しいキーを作成
4. **Access Key** と **Secret Key** をコピー → **メモ帳に貼付**

```
S3 アクセスキー: ___________________________
S3 シークレットキー: ___________________________
```

---

## チェックリスト

以下をすべてコピーしたら、セットアップ準備完了です。

### Pterodactyl パネル
- [ ] Application API キー
- [ ] Client API キー（root 管理者用）
- [ ] パネルの URL

### Cloudflare
- [ ] API トークン
- [ ] ゾーン ID

### Tailscale
- [ ] 認証キー（オプション）
- [ ] ホスト名

### Discord
- [ ] Bot トークン
- [ ] クライアント ID
- [ ] クライアント シークレット
- [ ] ギルド ID
- [ ] お知らせチャンネル ID
- [ ] 運営通知チャンネル ID

### Uptime Kuma
- [ ] URL
- [ ] ユーザー名・パスワード
- [ ] メトリクス API キー

### Object Storage
- [ ] エンドポイント
- [ ] バケット名
- [ ] アクセスキー
- [ ] シークレットキー

---

## 注意事項

### 秘密情報の取り扱い
- **API キー・トークン・パスワード** は絶対に GitHub に commit しない
- メモ帳は電子版（パスワードマネージャーなど）で管理
- `hatch-setup` 実行後、設定値は `/etc/hatch/hatch.env` に保存（権限 640）

### 複数環境を構築する場合
- テスト環境（stg）と本番環境（prod）で **別の API キーを作成**
- Cloudflare では同じゾーンを共有可だが、DNS レコード名で区別（`stg-mc{nn}`など）
- Discord では **テスト用の Bot・サーバー・チャンネル** を別に用意

### 情報が不完全な場合
- `hatch-setup` 実行中に接続確認が失敗すれば、その場で修正可能
- 後から変更する場合は、コンテナ内で以下を実行：
  ```bash
  nano /etc/hatch/hatch.env
  systemctl restart hatch.target
  ```

---

## 次のステップ

全ての情報を集めたら、[インストール詳細ガイド](install-guide.html) に進んでください。
