#!/usr/bin/env bash
# pterodeploy - LXC コンテナ内のインストール処理（ct/pterodeploy.sh から呼ばれます）
# 何度実行しても同じ結果になるように書いています（途中で失敗しても再実行可能）。
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive

PD_REPO="${PD_REPO:?PD_REPO が未設定です}"
PD_BRANCH="${PD_BRANCH:-main}"
PD_CHANNEL="${PD_CHANNEL:-stable}"
PD_HOME=/opt/pterodeploy
PD_ETC=/etc/pterodeploy
ENV_FILE="${PD_ETC}/pterodeploy.env"

say() { printf '   %s\n' "$1"; }

say "パッケージを更新しています"
apt-get update -qq
apt-get -y -qq -o Dpkg::Options::=--force-confold upgrade >/dev/null
apt-get install -y -qq curl ca-certificates jq postgresql python3 python3-venv \
  tar openssl sudo locales gnupg whiptail >/dev/null

# 日本語ロケール（ログ・メッセージ用）
if ! locale -a 2>/dev/null | grep -qi '^ja_JP.utf8$'; then
  sed -i 's/^# *ja_JP.UTF-8/ja_JP.UTF-8/' /etc/locale.gen && locale-gen >/dev/null
fi

say "Tailscale をインストールしています"
if ! command -v tailscale >/dev/null; then
  curl -fsSL https://tailscale.com/install.sh | sh >/dev/null
fi
if [[ ! -c /dev/net/tun ]]; then
  say "警告: /dev/net/tun がありません。Tailscale は使えません（ホスト側の LXC 設定を確認してください）"
elif [[ -n "${TS_AUTHKEY:-}" ]]; then
  tailscale up --authkey "$TS_AUTHKEY" --hostname "$(hostname)" >/dev/null || say "警告: Tailscale への接続に失敗しました。後で tailscale up を実行してください"
fi

say "ユーザーとディレクトリを作成しています"
id pterodeploy >/dev/null 2>&1 || useradd --system --home "$PD_HOME" --shell /usr/sbin/nologin pterodeploy
install -d -m 755 "$PD_HOME" "$PD_HOME/releases"
install -d -m 750 -o pterodeploy -g pterodeploy "$PD_HOME/shared"
install -d -m 700 "$PD_HOME/backups"
install -d -m 750 -o pterodeploy -g pterodeploy /var/lib/pterodeploy /var/lib/pterodeploy/uploads
install -d -m 750 -o root -g pterodeploy "$PD_ETC"

say "データベースを準備しています"
systemctl enable --now postgresql >/dev/null
if ! runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='pterodeploy'" | grep -q 1; then
  DB_PASS="$(openssl rand -hex 24)"
  runuser -u postgres -- psql -v ON_ERROR_STOP=1 -q -c "CREATE ROLE pterodeploy LOGIN PASSWORD '${DB_PASS}';"
  runuser -u postgres -- psql -v ON_ERROR_STOP=1 -q -c "CREATE DATABASE pterodeploy OWNER pterodeploy ENCODING 'UTF8' TEMPLATE template0;"
else
  DB_PASS=""
fi

if [[ ! -f "$ENV_FILE" ]]; then
  [[ -n "$DB_PASS" ]] || { say "エラー: DB ユーザーは存在しますが設定ファイルがありません。手動で復旧してください"; exit 1; }
  umask 027
  cat > "$ENV_FILE" << ENV
# pterodeploy 設定ファイル
# 編集後: systemctl restart pterodeploy.target
# 対話式で設定する場合: pterodeploy-setup

# ---- 基本 ----
PD_REPO=${PD_REPO}
PD_CHANNEL=${PD_CHANNEL}
PD_HOST=0.0.0.0
PD_PORT=8080
PD_PUBLIC_URL=http://$(hostname -I | awk '{print $1}'):8080
PD_INTERNAL_URL=http://$(hostname -I | awk '{print $1}'):8080
PD_TIMEZONE=Asia/Tokyo
# 本番は prod、テスト環境は stg など。DNS のコメントと監視名に入り、同じ Cloudflare ゾーンや Kuma を共有しても互いに消し合わない
PD_INSTANCE=prod
PD_DATA_DIR=/var/lib/pterodeploy
# 自己監視（/api/health/full）用。Uptime Kuma の HTTP 監視のヘッダーに設定する
PD_HEALTH_TOKEN=$(openssl rand -hex 24)
PD_SECRET_KEY=$(openssl rand -hex 32)
DATABASE_URL=postgresql://pterodeploy:${DB_PASS}@127.0.0.1:5432/pterodeploy

# ---- パネル（Pterodactyl / Pelican） ----
PANEL_KIND=pterodactyl
PANEL_URL=
PANEL_APP_KEY=
PANEL_CLIENT_KEY=
PANEL_PUBLIC_URL=

# ---- Cloudflare（DNS 編集のみの API トークン） ----
CF_API_TOKEN=
# ドメイン（ゾーン）は画面の「管理 → ドメイン」から登録します

# ---- Uptime Kuma ----
KUMA_URL=
KUMA_USERNAME=
KUMA_PASSWORD=
KUMA_METRICS_KEY=
KUMA_WEBHOOK_SECRET=$(openssl rand -hex 24)

# ---- Discord ----
DISCORD_BOT_TOKEN=
DISCORD_CLIENT_ID=
DISCORD_CLIENT_SECRET=
DISCORD_GUILD_ID=
DISCORD_CHANNEL_ANNOUNCE=
DISCORD_CHANNEL_OPS=

# ---- edge エージェント ----
EDGE_AGENT_TOKEN=$(openssl rand -hex 32)
HAPROXY_PER_IP_CONN=20
HAPROXY_PER_IP_RATE=30
HAPROXY_MAX_CONN=500

# ---- バックアップ保存先（S3 互換） ----
S3_ENDPOINT=
S3_BUCKET=
S3_ACCESS_KEY=
S3_SECRET_KEY=
ENV
  chown root:pterodeploy "$ENV_FILE"
  chmod 640 "$ENV_FILE"
fi

say "update コマンドを登録しています"
cat > /usr/bin/update << STUB
#!/usr/bin/env bash
# pterodeploy を更新します。  update --help で使い方を表示
set -euo pipefail
REPO="\$(sed -n 's/^PD_REPO=//p' ${ENV_FILE} 2>/dev/null || true)"
REPO="\${REPO:-${PD_REPO}}"
exec bash -c "\$(curl -fsSL "https://raw.githubusercontent.com/\${REPO}/${PD_BRANCH}/misc/update.sh")" update "\$@"
STUB
chmod 755 /usr/bin/update

say "最新のリリースを配置しています"
/usr/bin/update --first-install --yes

say "起動を確認しています"
ln -sf "$PD_HOME/current/deploy/bin/pterodeploy-setup" /usr/local/bin/pterodeploy-setup
if [[ ! -f "$PD_ETC/games.yml" ]]; then
  say "ゲームの定義（games.yml）の見本を置いています。パネルの nest・egg の ID に合わせて編集してください"
  install -m 640 -o root -g pterodeploy "$PD_HOME/current/deploy/games.example.yml" "$PD_ETC/games.yml"
fi
