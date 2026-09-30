"""Linode Cloud Firewall のルールの組み立て（純粋関数）。docs/IMPLEMENTATION.md 7A.2。

- Hatch のルールはラベルが "hatch-<instance>-" で始まる。それ以外（SSH など手で作ったもの）はそのまま残す。
- 連続するポートは範囲にまとめ、プロトコルごとに、1ルールあたり PORTS_PER_RULE 個までに分ける。
- Linode の上限（1つのファイアウォールにルール MAX_RULES 個）を超えるときは FirewallTooLarge。
"""

from __future__ import annotations

from typing import Any

MAX_RULES = 25  # 受信と送信の合計
PORTS_PER_RULE = 15  # 1ルールに書けるポート・範囲の数（範囲は2つ分として数える：上限の数え方の違いに備えて控えめに）
ANY = {"ipv4": ["0.0.0.0/0"], "ipv6": ["::/0"]}


class FirewallTooLarge(ValueError):
    pass


def label_prefix(instance: str) -> str:
    return f"hatch-{instance}-"


def to_ranges(ports: set[int] | list[int]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for p in sorted(set(ports)):
        if out and p == out[-1][1] + 1:
            out[-1] = (out[-1][0], p)
        else:
            out.append((p, p))
    return out


def _chunks(ranges: list[tuple[int, int]]) -> list[list[tuple[int, int]]]:
    chunks: list[list[tuple[int, int]]] = []
    cur: list[tuple[int, int]] = []
    used = 0
    for a, b in ranges:
        cost = 1 if a == b else 2
        if used + cost > PORTS_PER_RULE:
            chunks.append(cur)
            cur, used = [], 0
        cur.append((a, b))
        used += cost
    if cur:
        chunks.append(cur)
    return chunks


def hatch_rules(instance: str, ports: dict[str, set[int]]) -> list[dict[str, Any]]:
    """開けるポート（{"tcp": {...}, "udp": {...}}）から Hatch の受信ルールを作る。"""
    rules: list[dict[str, Any]] = []
    for proto in ("tcp", "udp"):
        for i, chunk in enumerate(_chunks(to_ranges(ports.get(proto, set()))), start=1):
            rules.append(
                {
                    "label": f"{label_prefix(instance)}{proto}-{i}",
                    "description": "Hatch がゲームサーバーのために自動で管理します（手で編集しないでください）",
                    "action": "ACCEPT",
                    "protocol": proto.upper(),
                    "ports": ",".join(str(a) if a == b else f"{a}-{b}" for a, b in chunk),
                    "addresses": ANY,
                }
            )
    return rules


def merge(current: dict[str, Any], instance: str, ports: dict[str, set[int]]) -> dict[str, Any]:
    """今のルール全体（GET の応答）の Hatch の分だけを差し替えた全体を返す。PUT にそのまま渡す。"""
    prefix = label_prefix(instance)
    inbound = [r for r in current.get("inbound") or [] if not str(r.get("label") or "").startswith(prefix)]
    outbound = list(current.get("outbound") or [])
    new = hatch_rules(instance, ports)
    total = len(inbound) + len(new) + len(outbound)
    if total > MAX_RULES:
        raise FirewallTooLarge(
            f"ルールが{total}個になり、Linode の上限（{MAX_RULES}個）を超えます。"
            "手で作ったルールを減らすか、ポートが連続するようにアドレス枠を見直してください。"
        )
    out = {"inbound": inbound + new, "outbound": outbound}
    for k in ("inbound_policy", "outbound_policy"):
        if k in current:
            out[k] = current[k]
    return out


def same(current: dict[str, Any], wanted: dict[str, Any]) -> bool:
    """変更が無ければ PUT しない（Linode 側の反映を待たせないように）。"""

    def norm(rules):
        return [(r.get("label"), r.get("action"), r.get("protocol"), r.get("ports")) for r in rules or []]

    return norm(current.get("inbound")) == norm(wanted.get("inbound")) and norm(current.get("outbound")) == norm(
        wanted.get("outbound")
    )
