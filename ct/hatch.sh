#!/usr/bin/env bash
# =============================================================================
#  Hatch - Proxmox VE 用インストーラー（Proxmox VE Helper-Scripts 方式）
#
#  Proxmox VE ホストのシェルで実行:
#    bash -c "$(curl -fsSL https://raw.githubusercontent.com/mesamaru/hatch/main/ct/hatch.sh)"
#
#  インストール済みのコンテナ内で実行すると、更新として動作します。
#  非対話で実行する場合は PD_YES=1 と var_* 変数を指定します（README 参照）。
# =============================================================================
set -Eeuo pipefail

PD_REPO="${PD_REPO:-mesamaru/hatch}"
PD_BRANCH="${PD_BRANCH:-main}"
PD_CHANNEL="${PD_CHANNEL:-stable}"
RAW="https://raw.githubusercontent.com/${PD_REPO}/${PD_BRANCH}"

# ---- 表示 --------------------------------------------------------------------
if [[ -t 1 ]]; then
  C_OK=$'\e[32m'; C_ERR=$'\e[31m'; C_INF=$'\e[36m'; C_WRN=$'\e[33m'; C_DIM=$'\e[2m'; C_RST=$'\e[0m'
else
  C_OK=""; C_ERR=""; C_INF=""; C_WRN=""; C_DIM=""; C_RST=""
fi
msg_info() { printf ' %s…%s %s\n' "$C_INF" "$C_RST" "$1"; }
msg_ok()   { printf ' %s✓%s %s\n' "$C_OK" "$C_RST" "$1"; }
msg_warn() { printf ' %s!%s %s\n' "$C_WRN" "$C_RST" "$1"; }
msg_err()  { printf ' %s✗%s %s\n' "$C_ERR" "$C_RST" "$1" >&2; }
die()      { msg_err "$1"; exit 1; }

# ---- コンテナ内で実行された場合は更新に切り替え ------------------------------
if ! command -v pct >/dev/null 2>&1; then
  if [[ -d /opt/hatch ]]; then
    exec bash -c "$(curl -fsSL "${RAW}/misc/update.sh")" update "$@"
  fi
  die "Proxmox VE ホストのシェルで実行してください。"
fi

# ---- 事前チェック --------------------------------------------------------------
[[ $EUID -eq 0 ]] || die "root で実行してください。"
[[ "$PD_REPO" != mesamaru/* ]] || die "PD_REPO が未設定です。README の手順でリポジトリ名を置き換えてください。"
PVE_MAJOR="$(pveversion | sed -n 's#^pve-manager/\([0-9]*\).*#\1#p')"
[[ "${PVE_MAJOR:-0}" -ge 8 ]] || die "Proxmox VE 8 以降が必要です（検出: $(pveversion)）。"
[[ "$(dpkg --print-architecture)" == "amd64" ]] || die "amd64 のみ対応しています。"

cat << 'BANNER'

   hatch ─ Pterodactyl 自動デプロイ基盤のインストール

BANNER

# ---- 既定値（環境変数で上書き可能） ------------------------------------------------
CTID="${var_ctid:-$(pvesh get /cluster/nextid)}"
HN="${var_hostname:-hatch}"
CPU="${var_cpu:-2}"
RAM="${var_ram:-2048}"
DISK="${var_disk:-10}"
BRIDGE="${var_bridge:-vmbr0}"
NET="${var_net:-dhcp}"            # dhcp または 192.168.1.50/24
GW="${var_gateway:-}"
VLAN="${var_vlan:-}"
STORAGE="${var_storage:-$(pvesm status -content rootdir 2>/dev/null | awk 'NR>1 && $3=="active"{print $1; exit}')}"
TSTORE="${var_template_storage:-$(pvesm status -content vztmpl 2>/dev/null | awk 'NR>1 && $3=="active"{print $1; exit}')}"
TS_AUTHKEY="${var_ts_authkey:-}"
TZ_HOST="$(timedatectl show -p Timezone --value 2>/dev/null || echo Asia/Tokyo)"

[[ -n "$STORAGE" ]] || die "コンテナ用のストレージ（rootdir）が見つかりません。"
[[ -n "$TSTORE" ]]  || die "テンプレート用のストレージ（vztmpl）が見つかりません。"

ask() { whiptail --title "hatch" --inputbox "$1" 10 66 "$2" 3>&1 1>&2 2>&3 || die "キャンセルしました。"; }

if [[ "${PD_YES:-0}" != "1" && -t 0 ]] && command -v whiptail >/dev/null; then
  MODE=$(whiptail --title "hatch" --menu "設定方法を選んでください" 13 70 2 \
    "1" "標準設定（CPU ${CPU} / メモリ ${RAM}MB / ディスク ${DISK}GB / DHCP）" \
    "2" "詳細設定" 3>&1 1>&2 2>&3) || die "キャンセルしました。"
  if [[ "$MODE" == "2" ]]; then
    CTID=$(ask "コンテナID" "$CTID")
    HN=$(ask "ホスト名" "$HN")
    CPU=$(ask "CPUコア数" "$CPU")
    RAM=$(ask "メモリ (MB)" "$RAM")
    DISK=$(ask "ディスク (GB)" "$DISK")
    STORAGE=$(ask "コンテナのストレージ" "$STORAGE")
    BRIDGE=$(ask "ネットワークブリッジ" "$BRIDGE")
    NET=$(ask "IPアドレス（dhcp または 192.168.1.50/24）" "$NET")
    if [[ "$NET" != "dhcp" ]]; then GW=$(ask "ゲートウェイ" "$GW"); fi
    VLAN=$(ask "VLAN タグ（空欄でなし）" "$VLAN")
    TS_AUTHKEY=$(ask "Tailscale 認証キー（空欄なら後で手動で接続）" "$TS_AUTHKEY")
  fi
fi

# ---- 入力チェック --------------------------------------------------------------
[[ "$CTID" =~ ^[0-9]+$ ]] || die "コンテナIDが不正です: $CTID"
pct status "$CTID" >/dev/null 2>&1 && die "コンテナID $CTID は既に使われています。"
[[ "$HN" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]] || die "ホスト名が不正です: $HN"
[[ "$CPU" =~ ^[0-9]+$ && "$RAM" =~ ^[0-9]+$ && "$DISK" =~ ^[0-9]+$ ]] || die "CPU・メモリ・ディスクは数値で指定してください。"
(( RAM >= 1024 )) || die "メモリは 1024MB 以上にしてください。"
(( DISK >= 6 ))   || die "ディスクは 6GB 以上にしてください。"
if [[ "$NET" != "dhcp" ]]; then
  [[ "$NET" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}/[0-9]{1,2}$ ]] || die "IPアドレスは 192.168.1.50/24 の形式で指定してください。"
  [[ -n "$GW" ]] || die "固定IPの場合はゲートウェイが必要です。"
fi
[[ -z "$VLAN" || "$VLAN" =~ ^[0-9]+$ ]] || die "VLAN タグは数値で指定してください。"

NET0="name=eth0,bridge=${BRIDGE},ip=${NET}"
[[ -n "$GW" ]]   && NET0+=",gw=${GW}"
[[ -n "$VLAN" ]] && NET0+=",tag=${VLAN}"

# ---- 失敗時の後片付け ----------------------------------------------------------
CREATED=0
on_error() {
  msg_err "$1 行目でエラーが発生しました。"
  if [[ $CREATED -eq 1 && "${PD_KEEP_ON_FAIL:-0}" != "1" ]]; then
    msg_warn "作成途中のコンテナ ${CTID} を削除します（調査のため残す場合は PD_KEEP_ON_FAIL=1）。"
    pct stop "$CTID" >/dev/null 2>&1 || true
    pct destroy "$CTID" --purge >/dev/null 2>&1 || true
  fi
}
trap 'on_error $LINENO' ERR

# ---- テンプレート ------------------------------------------------------------
msg_info "Debian テンプレートを確認しています"
pveam update >/dev/null 2>&1 || msg_warn "テンプレート一覧の更新に失敗しました（手元の一覧を使います）"
TEMPLATE="$(pveam available --section system | awk '/debian-13-standard/ {print $2}' | sort -V | tail -1)"
[[ -n "$TEMPLATE" ]] || TEMPLATE="$(pveam available --section system | awk '/debian-12-standard/ {print $2}' | sort -V | tail -1)"
[[ -n "$TEMPLATE" ]] || die "Debian テンプレートが見つかりません。"
pveam list "$TSTORE" | grep -qF "$TEMPLATE" || pveam download "$TSTORE" "$TEMPLATE" >/dev/null
msg_ok "テンプレート: $TEMPLATE"

# ---- コンテナ作成 ------------------------------------------------------------
msg_info "コンテナ ${CTID} を作成しています"
pct create "$CTID" "${TSTORE}:vztmpl/${TEMPLATE}" \
  --hostname "$HN" --cores "$CPU" --memory "$RAM" --swap 512 \
  --rootfs "${STORAGE}:${DISK}" --net0 "$NET0" \
  --unprivileged 1 --features nesting=1 --onboot 1 \
  --ostype debian --tags hatch --timezone "$TZ_HOST" >/dev/null
CREATED=1

# Tailscale 用に /dev/net/tun をコンテナへ渡す（非特権コンテナで必要）
cat >> "/etc/pve/lxc/${CTID}.conf" << CONF
lxc.cgroup2.devices.allow: c 10:200 rwm
lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file
CONF
msg_ok "コンテナ ${CTID} を作成しました"

msg_info "コンテナを起動しています"
pct start "$CTID"
for _ in $(seq 1 60); do
  pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null 2>&1 && break
  sleep 1
done
pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null 2>&1 \
  || die "コンテナからインターネットに接続できません。ブリッジ・IP・ゲートウェイを確認してください。"
msg_ok "ネットワークに接続しました"

msg_info "インストールしています（数分かかります）"
pct exec "$CTID" -- bash -c "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl ca-certificates >/dev/null"
pct exec "$CTID" -- env PD_REPO="$PD_REPO" PD_BRANCH="$PD_BRANCH" PD_CHANNEL="$PD_CHANNEL" TS_AUTHKEY="$TS_AUTHKEY" \
  bash -c "curl -fsSL '${RAW}/install/hatch-install.sh' | bash"
msg_ok "インストールが完了しました"

IP="$(pct exec "$CTID" -- hostname -I | awk '{print $1}')"
pct set "$CTID" --description "hatch — http://${IP}:8080 — 更新はコンテナ内で update" >/dev/null
trap - ERR

cat << DONE

${C_OK}Hatch の準備ができました。${C_RST}

   パネル  http://${IP}:8080
   初期設定 pct enter ${CTID} → hatch-setup
   更新    pct enter ${CTID} → update
   ${C_DIM}Tailscale を後から接続する場合: pct enter ${CTID} → tailscale up${C_RST}

DONE
