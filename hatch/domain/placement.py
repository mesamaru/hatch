"""ノードの選び方。docs/IMPLEMENTATION.md 5.4。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NodeLoad:
    id: str
    memory_mb: int
    disk_mb: int
    overcommit_percent: int
    accepting: bool
    used_memory_mb: int
    used_disk_mb: int


def choose_node(nodes: list[NodeLoad], memory_mb: int, disk_mb: int) -> NodeLoad | None:
    """受け付け中で、割り当て後も上限（overcommit_percent）以内に収まるノードのうち、空きメモリが最大のもの。"""
    ok = []
    for n in nodes:
        if not n.accepting:
            continue
        cap = n.overcommit_percent / 100
        if n.used_memory_mb + memory_mb > n.memory_mb * cap or n.used_disk_mb + disk_mb > n.disk_mb * cap:
            continue
        ok.append(n)
    if not ok:
        return None
    return sorted(ok, key=lambda n: (-(n.memory_mb - n.used_memory_mb), n.id))[0]
