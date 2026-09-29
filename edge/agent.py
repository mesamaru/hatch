#!/usr/bin/env python3
"""Hatch edge エージェント（Linode などの edge サーバーで動作）

オーケストレーターから HAProxy 設定を「取りに行く」方式です。
  - オーケストレーターが止まっていても、最後に適用した設定のまま動き続けます
  - edge-1 / edge-2 の両方が同じ設定を取るので、待機側も常に最新です
  - 新しい設定は検証してから差し替え、reload に失敗したら元に戻します
標準ライブラリだけで動きます。
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

API = os.environ["PD_API_URL"].rstrip("/")          # 例 http://hatch:8080 （Tailscale 経由）
TOKEN = os.environ["EDGE_AGENT_TOKEN"]
EDGE_ID = os.environ["EDGE_ID"]                      # 例 edge-1
BASE_CFG = os.environ.get("HAPROXY_BASE", "/etc/haproxy/haproxy.cfg")
MANAGED = os.environ.get("HAPROXY_MANAGED", "/etc/haproxy/hatch.cfg")
NFT = os.environ.get("NFT_MANAGED", "/etc/hatch-edge/hatch.nft")
STATE = os.environ.get("STATE_FILE", "/var/lib/hatch-edge/state.json")
INTERVAL = int(os.environ.get("POLL_SECONDS", "10"))


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def request(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{API}{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as res:
        return json.loads(res.read() or b"{}")


def load_state() -> dict:
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"version": 0}


def save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, STATE)


def haproxy_check(candidate: str) -> tuple[bool, str]:
    p = subprocess.run(["haproxy", "-c", "-q", "-f", BASE_CFG, "-f", candidate],
                       capture_output=True, text=True)
    return p.returncode == 0, (p.stderr or p.stdout).strip()[-2000:]


def apply_nft(rules: str) -> tuple[bool, str]:
    """UDP の転送ルール（nftables）。空なら hatch のテーブルを消す。"""
    body = "table ip hatch\ndelete table ip hatch\n" + (rules or "")
    new = NFT + ".new"
    with open(new, "w", encoding="utf-8") as f:
        f.write(body)
    p = subprocess.run(["nft", "-c", "-f", new], capture_output=True, text=True)
    if p.returncode != 0:
        os.remove(new)
        return False, f"nft 検証エラー: {p.stderr.strip()[-1000:]}"
    os.replace(new, NFT)
    p = subprocess.run(["nft", "-f", NFT], capture_output=True, text=True)
    return (p.returncode == 0), (p.stderr.strip()[-500:] if p.returncode else "")


def apply(version: int, cfg: str) -> tuple[bool, str]:
    new = MANAGED + ".new"
    old = MANAGED + ".prev"
    with open(new, "w", encoding="utf-8") as f:
        f.write(cfg)
    ok, out = haproxy_check(new)
    if not ok:
        os.remove(new)
        return False, f"検証エラー: {out}"
    if os.path.exists(MANAGED):
        shutil.copy2(MANAGED, old)
    os.replace(new, MANAGED)                      # 原子的に差し替え
    r = subprocess.run(["systemctl", "reload", "haproxy"], capture_output=True, text=True)
    if r.returncode != 0:
        if os.path.exists(old):
            os.replace(old, MANAGED)
            subprocess.run(["systemctl", "reload", "haproxy"])
        return False, f"reload 失敗のため元に戻しました: {r.stderr.strip()[-500:]}"
    return True, ""


def main() -> int:
    state = load_state()
    log(f"開始 edge={EDGE_ID} 適用済みの版={state['version']}")
    if os.path.exists(NFT):  # 再起動で消えた UDP の転送ルールを戻す
        r = subprocess.run(["nft", "-f", NFT], capture_output=True, text=True)
        log("UDP の転送ルールを復元しました" if r.returncode == 0 else f"UDP ルールの復元に失敗: {r.stderr.strip()[-300:]}")
    while True:
        report = {"edge_id": EDGE_ID, "applied_version": state["version"], "error": None}
        try:
            res = request("GET", f"/api/edge/config?edge={EDGE_ID}&have={state['version']}")
            if res.get("version") and res["version"] != state["version"]:
                ok, err = apply(int(res["version"]), res["haproxy_cfg"])
                if ok and "nft_rules" in res:
                    ok, err = apply_nft(res["nft_rules"])
                if ok:
                    state["version"] = int(res["version"])
                    save_state(state)
                    log(f"版 {state['version']} を適用しました")
                else:
                    log(err)
                report.update(applied_version=state["version"], error=err or None)
            request("POST", "/api/edge/report", report)
        except urllib.error.HTTPError as e:
            log(f"API エラー {e.code}（最後の設定のまま継続）")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            log(f"オーケストレーターに接続できません: {e}（最後の設定のまま継続）")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
