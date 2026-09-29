#!/usr/bin/env bash
# =============================================================================
#  Hatch 更新スクリプト(コンテナ内の `update` コマンドから呼ばれます)
#
#    update                  最新版に更新
#    update --check          更新があるか確認だけ（更新あり: 終了コード 10）
#    update --version v0.3.0 指定したバージョンにする
#    update --channel beta   ベータ版を含めて最新を探す（以後も beta を使う場合は設定ファイルを変更）
#    update --rollback       1つ前のバージョンに戻す
#    update --restore-db     --rollback と一緒に使うと、更新直前の DB バックアップも戻す
#    update --yes            確認を省略
#
#  流れ: 取得 → 検証(sha256) → 展開 → 依存導入 → DB退避 → 移行 → 切替 → 再起動 → 死活確認
#        死活確認に失敗したら自動で前のバージョンに戻します。
# =============================================================================
set -Eeuo pipefail

PD_HOME=/opt/hatch
ENV_FILE=/etc/hatch/hatch.env
LOCK=/run/hatch-update.lock
KEEP_RELEASES=3
KEEP_DB_BACKUPS=10

C_OK=$'\e[32m'; C_ERR=$'\e[31m'; C_INF=$'\e[36m'; C_WRN=$'\e[33m'; C_RST=$'\e[0m'
[[ -t 1 ]] || { C_OK=""; C_ERR=""; C_INF=""; C_WRN=""; C_RST=""; }
info() { printf ' %s…%s %s\n' "$C_INF" "$C_RST" "$1"; }
ok()   { printf ' %s✓%s %s\n' "$C_OK" "$C_RST" "$1"; }
warn() { printf ' %s!%s %s\n' "$C_WRN" "$C_RST" "$1"; }
die()  { printf ' %s✗%s %s\n' "$C_ERR" "$C_RST" "$1" >&2; exit 1; }

usage() {
  cat << 'HELP'
 使い方: update [オプション]
   (なし)              最新版に更新
   --check             更新があるか確認だけ（更新あり: 終了コード 10）
   --version v0.3.0    指定したバージョンにする
   --channel beta      ベータ版を含めて最新を探す
   --rollback          1つ前のバージョンに戻す
   --restore-db        --rollback と一緒に使うと DB も更新直前に戻す
   --yes               確認を省略
HELP
  exit 0
}

# ---- 引数 --------------------------------------------------------------------
MODE=update; WANT_VERSION=""; CHANNEL=""; YES=0; FIRST=0; RESTORE_DB=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) MODE=check ;;
    --rollback) MODE=rollback ;;
    --restore-db) RESTORE_DB=1 ;;
    --version) WANT_VERSION="${2:?--version にはバージョンを指定してください}"; shift ;;
    --channel) CHANNEL="${2:?--channel には stable か beta を指定してください}"; shift ;;
    --first-install) FIRST=1; YES=1 ;;
    --yes|-y) YES=1 ;;
    --help|-h) usage ;;
    *) die "不明なオプション: $1（update --help で使い方を表示）" ;;
  esac
  shift
done

[[ $EUID -eq 0 ]] || die "root で実行してください。"
[[ -f "$ENV_FILE" ]] || die "設定ファイル ${ENV_FILE} がありません。"
envget() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -1; }
PD_REPO="$(envget PD_REPO)"; [[ -n "$PD_REPO" ]] || die "PD_REPO が設定ファイルにありません。"
CHANNEL="${CHANNEL:-$(envget PD_CHANNEL)}"; CHANNEL="${CHANNEL:-stable}"
[[ "$CHANNEL" == stable || "$CHANNEL" == beta ]] || die "チャンネルは stable か beta です。"
PORT="$(envget PD_PORT)"; PORT="${PORT:-8080}"

exec 9>"$LOCK"
flock -n 9 || die "別の更新が実行中です。"

current_ver() { cat "$PD_HOME/current/VERSION" 2>/dev/null || echo "なし"; }
services() { cat "$PD_HOME/current/deploy/services.txt" 2>/dev/null | grep -v '^\s*#' | grep -v '^\s*$' || true; }

health_ok() {
  for _ in $(seq 1 40); do
    if curl -fsS -m 3 "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then return 0; fi
    sleep 1.5
  done
  return 1
}

install_units() { # install_units <リリースディレクトリ>
  local rel="$1"
  install -m 644 "$rel"/deploy/systemd/*.service "$rel"/deploy/systemd/*.target /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable hatch.target >/dev/null 2>&1
  while read -r svc; do systemctl enable "$svc" >/dev/null 2>&1; done < <(grep -v '^\s*#' "$rel/deploy/services.txt" | grep -v '^\s*$')
}

switch_to() { # switch_to <リリースディレクトリ>
  local rel="$1"
  if [[ -L "$PD_HOME/current" ]]; then ln -sfn "$(readlink -f "$PD_HOME/current")" "$PD_HOME/previous"; fi
  ln -sfn "$rel" "$PD_HOME/current.new"
  mv -Tf "$PD_HOME/current.new" "$PD_HOME/current"
}

restart_all() { systemctl restart hatch.target; }

run_as_app() { # 設定ファイルを読み込んでアプリユーザーで実行
  runuser -u hatch -- bash -c "set -a; . '$ENV_FILE'; set +a; exec \"\$@\"" _ "$@"
}

# ---- ロールバック ------------------------------------------------------------
if [[ "$MODE" == rollback ]]; then
  [[ -L "$PD_HOME/previous" && -d "$(readlink -f "$PD_HOME/previous")" ]] || die "戻せる前のバージョンがありません。"
  PREV="$(readlink -f "$PD_HOME/previous")"
  info "$(current_ver) → $(cat "$PREV/VERSION") に戻します"
  if [[ $RESTORE_DB -eq 1 ]]; then
    DUMP="$(ls -1t "$PD_HOME"/backups/pre-*.sql.gz 2>/dev/null | head -1 || true)"
    [[ -n "$DUMP" ]] || die "DB のバックアップが見つかりません。"
    [[ $YES -eq 1 ]] || { read -rp " DB を ${DUMP##*/} の時点に戻します。よろしいですか？ [y/N] " a; [[ "$a" == [yY] ]] || exit 1; }
    systemctl stop hatch.target
    runuser -u postgres -- dropdb --if-exists hatch
    runuser -u postgres -- createdb -O hatch -E UTF8 -T template0 hatch
    gunzip -c "$DUMP" | runuser -u postgres -- psql -q -v ON_ERROR_STOP=1 hatch >/dev/null
    ok "DB を戻しました"
  fi
  install_units "$PREV"; switch_to "$PREV"; restart_all
  health_ok && ok "$(current_ver) に戻しました" || die "戻した後も起動を確認できません。journalctl -u hatch-api -n 80 を確認してください。"
  exit 0
fi

# ---- リリース情報の取得 --------------------------------------------------------
API="https://api.github.com/repos/${PD_REPO}"
AUTH=(); [[ -n "${GITHUB_TOKEN:-}" ]] && AUTH=(-H "Authorization: Bearer ${GITHUB_TOKEN}")
gh() { curl -fsSL -m 20 -H "Accept: application/vnd.github+json" "${AUTH[@]}" "$1"; }

info "リリース情報を確認しています（${CHANNEL}）"
if [[ -n "$WANT_VERSION" ]]; then
  REL_JSON="$(gh "${API}/releases/tags/${WANT_VERSION}")" || die "バージョン ${WANT_VERSION} が見つかりません。"
elif [[ "$CHANNEL" == beta ]]; then
  REL_JSON="$(gh "${API}/releases?per_page=15" | jq '[.[] | select(.draft|not)][0]')" || die "リリース情報を取得できません。"
else
  REL_JSON="$(gh "${API}/releases/latest")" || die "リリース情報を取得できません（リリースが1つもない可能性があります）。"
fi
[[ "$REL_JSON" != "null" && -n "$REL_JSON" ]] || die "リリースが見つかりません。"
TAG="$(jq -r .tag_name <<<"$REL_JSON")"
VER="${TAG#v}"
TAR_URL="$(jq -r --arg n "hatch-${VER}.tar.gz" '.assets[] | select(.name==$n) | .browser_download_url' <<<"$REL_JSON")"
SUM_URL="$(jq -r --arg n "hatch-${VER}.tar.gz.sha256" '.assets[] | select(.name==$n) | .browser_download_url' <<<"$REL_JSON")"
[[ -n "$TAR_URL" && -n "$SUM_URL" ]] || die "リリース ${TAG} に配布ファイルがありません。"

CUR="$(current_ver)"
if [[ "$MODE" == check ]]; then
  if [[ "$CUR" == "$VER" ]]; then ok "最新です（${CUR}）"; exit 0; fi
  info "更新があります: ${CUR} → ${VER}"
  info "変更内容: $(jq -r .html_url <<<"$REL_JSON")"
  exit 10
fi
if [[ "$CUR" == "$VER" && $FIRST -eq 0 ]]; then ok "最新です（${CUR}）"; exit 0; fi

if [[ $YES -eq 0 && -t 0 ]]; then
  echo; echo " ${CUR} → ${VER}"; echo " 変更内容: $(jq -r .html_url <<<"$REL_JSON")"; echo
  read -rp " 更新しますか？ 更新中はパネルとBotが数十秒止まります。 [y/N] " a
  [[ "$a" == [yY] ]] || { warn "中止しました"; exit 1; }
fi

# ---- 取得と検証 ----------------------------------------------------------------
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
info "ダウンロードしています"
curl -fsSL -m 300 -o "$TMP/pd.tar.gz" "$TAR_URL"
curl -fsSL -m 30  -o "$TMP/pd.sha256" "$SUM_URL"
( cd "$TMP" && echo "$(awk '{print $1}' pd.sha256)  pd.tar.gz" | sha256sum -c --quiet - ) || die "ファイルの検証（sha256）に失敗しました。"
ok "検証しました"

REL="$PD_HOME/releases/${VER}"
if [[ -d "$REL" && "$(readlink -f "$PD_HOME/current" 2>/dev/null)" != "$REL" ]]; then rm -rf "$REL"; fi
install -d "$REL"
tar -xzf "$TMP/pd.tar.gz" -C "$REL" --strip-components=1 --no-same-owner
[[ -f "$REL/VERSION" && -f "$REL/deploy/services.txt" ]] || die "配布ファイルの中身が不正です。"

info "依存パッケージを導入しています"
python3 -m venv "$REL/.venv"
"$REL/.venv/bin/pip" install -q --disable-pip-version-check --no-cache-dir -r "$REL/requirements.txt"
ok "依存パッケージを導入しました"

# ---- DB 退避と移行 --------------------------------------------------------------
if [[ $FIRST -eq 0 ]]; then
  info "データベースを退避しています"
  STAMP="$(date +%Y%m%d-%H%M%S)"
  runuser -u postgres -- pg_dump hatch | gzip > "$PD_HOME/backups/pre-${VER}-${STAMP}.sql.gz"
  chmod 600 "$PD_HOME/backups/pre-${VER}-${STAMP}.sql.gz"
  ls -1t "$PD_HOME"/backups/pre-*.sql.gz | tail -n +$((KEEP_DB_BACKUPS + 1)) | xargs -r rm -f
  ok "退避しました（backups/pre-${VER}-${STAMP}.sql.gz）"
fi

info "データベースを移行しています"
(cd "$REL" && run_as_app "$REL/.venv/bin/python" -m hatch.migrate) || die "データベースの移行に失敗しました。現在のバージョンのまま動作しています。"
ok "移行しました"

# ---- 切り替えと起動確認 --------------------------------------------------------
info "新しいバージョンに切り替えています"
install_units "$REL"
switch_to "$REL"
restart_all

if health_ok; then
  ok "${VER} で起動しました"
else
  warn "${VER} の起動を確認できませんでした。前のバージョンに戻します"
  if [[ -L "$PD_HOME/previous" ]]; then
    PREV="$(readlink -f "$PD_HOME/previous")"
    install_units "$PREV"
    ln -sfn "$PREV" "$PD_HOME/current.new" && mv -Tf "$PD_HOME/current.new" "$PD_HOME/current"
    restart_all
    health_ok && warn "$(current_ver) に戻しました。原因: journalctl -u hatch-api -n 80" \
              || die "戻した後も起動を確認できません。journalctl -u hatch-api -n 80 を確認してください。"
    exit 1
  fi
  die "起動を確認できません。journalctl -u hatch-api -n 80 を確認してください。"
fi

# ---- 古いリリースの整理 --------------------------------------------------------
KEEP_CUR="$(readlink -f "$PD_HOME/current")"; KEEP_PREV="$(readlink -f "$PD_HOME/previous" 2>/dev/null || true)"
ls -1dt "$PD_HOME"/releases/*/ 2>/dev/null | sed 's#/$##' | while read -r d; do
  [[ "$d" == "$KEEP_CUR" || "$d" == "$KEEP_PREV" ]] && continue
  echo "$d"
done | tail -n +$((KEEP_RELEASES)) | xargs -r rm -rf

ok "完了しました（${CUR} → ${VER}）"
