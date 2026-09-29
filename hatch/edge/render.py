"""edge の設定（HAProxy と nftables）を生成する。docs/IMPLEMENTATION.md 7章。

同じ入力なら必ず同じ文字列を返す（ポート順に並べる）。内容のハッシュが前回と同じなら新しい版を作らない。
"""

from __future__ import annotations

import hashlib
import ipaddress
from dataclasses import dataclass


@dataclass(frozen=True)
class Target:
    port: int
    backend_ip: str  # Wings ノードの Tailscale IP
    protocol: str  # tcp / udp
    proxy_protocol: bool
    label: str  # コメント用（サーバー名）。設定の構文には使わない


@dataclass(frozen=True)
class Limits:
    per_ip_conn: int = 20
    per_ip_rate: int = 30  # 10秒あたり
    max_conn: int = 500
    udp_per_ip_pps: int = 2000


HEADER = "# Hatch が生成しました。手で編集しないでください(次の更新で上書きされます)。\n"

# ゲームの接続は長時間続くので、配布元の既定（50秒）より長いタイムアウトにする
DEFAULTS = """defaults pd_tcp
    mode tcp
    log global
    option tcplog
    option dontlognull
    timeout connect 5s
    timeout client 2h
    timeout server 2h
    timeout client-fin 30s
    timeout server-fin 30s
"""


def _safe_label(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in s)[:40]


def _check(t: Target) -> None:
    if not (1024 <= t.port <= 65535):
        raise ValueError(f"ポートが範囲外です: {t.port}")
    ipaddress.ip_address(t.backend_ip)  # 不正な値を設定ファイルに入れない
    if t.protocol not in ("tcp", "udp"):
        raise ValueError(f"protocol が不正です: {t.protocol}")


def render_haproxy(targets: list[Target], limits: Limits) -> str:
    parts = [HEADER, DEFAULTS]
    for t in sorted((t for t in targets if t.protocol == "tcp"), key=lambda t: t.port):
        _check(t)
        opts = " send-proxy-v2 check-send-proxy" if t.proxy_protocol else ""
        parts.append(
            f"""
# {_safe_label(t.label)}
frontend fe_{t.port} from pd_tcp
    bind :{t.port}
    maxconn {limits.max_conn}
    stick-table type ip size 100k expire 10m store conn_cur,conn_rate(10s)
    tcp-request connection track-sc0 src
    tcp-request connection reject if {{ sc0_conn_cur gt {limits.per_ip_conn} }} || {{ sc0_conn_rate gt {limits.per_ip_rate} }}
    default_backend be_{t.port}

backend be_{t.port} from pd_tcp
    server s{t.port} {t.backend_ip}:{t.port}{opts} check inter 10s fall 3 rise 2
"""
        )
    return "".join(parts)


def render_nft(targets: list[Target], limits: Limits) -> str:
    """UDP の転送ルール。UDP のゲームが無ければ空文字（エージェントがテーブルを消す）。"""
    udp = sorted((t for t in targets if t.protocol == "udp"), key=lambda t: t.port)
    if not udp:
        return ""
    for t in udp:
        _check(t)
    ports = ", ".join(str(t.port) for t in udp)
    dnat = "\n".join(f"    udp dport {t.port} dnat to {t.backend_ip}:{t.port}  # {_safe_label(t.label)}" for t in udp)
    masq = "\n".join(f"    ip daddr {t.backend_ip} udp dport {t.port} masquerade" for t in udp)
    return f"""{HEADER}table ip hatch {{
  set udp_ports {{
    type inet_service
    elements = {{ {ports} }}
  }}
  set udp_flood {{
    type ipv4_addr
    size 65535
    flags dynamic,timeout
    timeout 60s
  }}
  chain guard {{
    type filter hook prerouting priority -150; policy accept;
    udp dport @udp_ports add @udp_flood {{ ip saddr limit rate over {limits.udp_per_ip_pps}/second }} drop
  }}
  chain prerouting {{
    type nat hook prerouting priority dstnat; policy accept;
{dnat}
  }}
  chain postrouting {{
    type nat hook postrouting priority srcnat; policy accept;
{masq}
  }}
}}
"""


def content_hash(haproxy_cfg: str, nft_rules: str) -> str:
    return hashlib.sha256((haproxy_cfg + "\0" + nft_rules).encode()).hexdigest()
