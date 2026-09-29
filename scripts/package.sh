#!/usr/bin/env bash
# リリース用の配布ファイルを作ります:  scripts/package.sh 0.2.0
set -Eeuo pipefail
VER="${1:?バージョンを指定してください（例 0.2.0）}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/dist"; STAGE="$(mktemp -d)/hatch-${VER}"
mkdir -p "$STAGE" "$OUT"
cp -r "$ROOT"/{hatch,web,db,deploy,requirements.txt} "$STAGE"/
echo "$VER" > "$STAGE/VERSION"
find "$STAGE" -name '__pycache__' -prune -exec rm -rf {} +
tar -C "$(dirname "$STAGE")" --owner=0 --group=0 -czf "$OUT/hatch-${VER}.tar.gz" "hatch-${VER}"
(cd "$OUT" && sha256sum "hatch-${VER}.tar.gz" > "hatch-${VER}.tar.gz.sha256")
echo "$OUT/hatch-${VER}.tar.gz"
