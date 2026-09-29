#!/usr/bin/env bash
# pterodeploy edge のセットアップ（Linode などの Debian / Ubuntu で root 実行）
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/mesamaru/pterodeploy/main/edge/install-edge.sh)"
# 再実行すると、エージェントだけ最新に入れ替えます。
set -Eeuo pipefail
PD_REPO="${PD_REPO:-mesamaru/pterodeploy}"
PD_BRANCH="${PD_BRANCH:-main}"
RAW="https://raw.githubusercontent.com/${PD_REPO}/${PD_BRANCH}"
[[ $EUID -eq 0 ]] || { echo "root で実行してください"; exit 1; }
[[ "$PD_REPO" != mesamaru/* ]] || { echo "PD_REPO を設定してください"; exit 1; }
export DEBIAN_FRONTEND=noninteractive

apt-get update -qq
apt-get install -y -qq haproxy nftables python3 curl ca-certificates >/dev/null
# UDP 転送のため IP 転送を有効化
echo 'net.ipv4.ip_forward=1' > /etc/sysctl.d/90-pterodeploy.conf && sysctl -q -p /etc/sysctl.d/90-pterodeploy.conf
command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh >/dev/null

install -d -m 755 /opt/pterodeploy-edge /var/lib/pterodeploy-edge
install -d -m 750 /etc/pterodeploy-edge
curl -fsSL "${RAW}/edge/agent.py" -o /opt/pterodeploy-edge/agent.py
chmod 755 /opt/pterodeploy-edge/agent.py

if [[ ! -f /etc/pterodeploy-edge/agent.env ]]; then
  read -rp " edge の名前（edge-1 / edge-2）: " EDGE_ID
  read -rp " オーケストレーターの URL（Tailscale 経由, 例 http://pterodeploy:8080）: " API
  read -rsp " EDGE_AGENT_TOKEN（オーケストレーターの設定ファイルにあります）: " TOKEN; echo
  umask 077
  printf 'EDGE_ID=%s\nPD_API_URL=%s\nEDGE_AGENT_TOKEN=%s\n' "$EDGE_ID" "$API" "$TOKEN" > /etc/pterodeploy-edge/agent.env
fi

# 管理対象の設定ファイルを HAProxy に読み込ませる（本体の haproxy.cfg は手で管理可能なまま）
[[ -f /etc/haproxy/pterodeploy.cfg ]] || printf '# pterodeploy が管理します。手で編集しないでください。\n' > /etc/haproxy/pterodeploy.cfg
install -d /etc/systemd/system/haproxy.service.d
cat > /etc/systemd/system/haproxy.service.d/pterodeploy.conf << 'CONF'
[Service]
Environment="EXTRAOPTS=-S /run/haproxy-master.sock -f /etc/haproxy/pterodeploy.cfg"
CONF

cat > /etc/systemd/system/pterodeploy-edge.service << 'UNIT'
[Unit]
Description=pterodeploy edge エージェント
After=network-online.target tailscaled.service haproxy.service
Wants=network-online.target

[Service]
EnvironmentFile=/etc/pterodeploy-edge/agent.env
ExecStart=/usr/bin/python3 /opt/pterodeploy-edge/agent.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
haproxy -c -q -f /etc/haproxy/haproxy.cfg -f /etc/haproxy/pterodeploy.cfg
systemctl restart haproxy
systemctl enable --now pterodeploy-edge >/dev/null
echo " edge エージェントを起動しました。 状態: journalctl -u pterodeploy-edge -f"
tailscale status >/dev/null 2>&1 || echo " Tailscale が未接続です: tailscale up を実行してください"
