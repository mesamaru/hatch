"""Linode Cloud Firewall のルールの組み立て（docs/IMPLEMENTATION.md 7A.2）。"""

import pytest

from hatch.domain.firewall import FirewallTooLarge, hatch_rules, merge, same, to_ranges

MANUAL = {"label": "ssh", "action": "ACCEPT", "protocol": "TCP", "ports": "22", "addresses": {}}


def test_consecutive_ports_become_ranges():
    assert to_ranges({25563, 25561, 25562, 30000, 25565}) == [(25561, 25563), (25565, 25565), (30000, 30000)]
    assert to_ranges(set()) == []


def test_rules_per_protocol_and_label():
    rules = hatch_rules("prod", {"tcp": {25561, 25562, 25563}, "udp": {30000}})
    assert [(r["label"], r["protocol"], r["ports"]) for r in rules] == [
        ("hatch-prod-tcp-1", "TCP", "25561-25563"),
        ("hatch-prod-udp-1", "UDP", "30000"),
    ]
    assert all(r["action"] == "ACCEPT" for r in rules)


def test_many_scattered_ports_are_split_into_rules_of_15():
    ports = set(range(20000, 20060, 2))  # 30個の飛び飛びのポート
    rules = hatch_rules("prod", {"tcp": ports})
    assert [len(r["ports"].split(",")) for r in rules] == [15, 15]


def test_ranges_count_double_to_stay_under_the_limit():
    ports = {p for start in range(20000, 20100, 10) for p in (start, start + 1)}  # 10個の範囲
    rules = hatch_rules("prod", {"tcp": ports})
    assert [len(r["ports"].split(",")) for r in rules] == [7, 3]


def test_merge_keeps_manual_rules_and_other_instances():
    other = {"label": "hatch-stg-tcp-1", "action": "ACCEPT", "protocol": "TCP", "ports": "40000", "addresses": {}}
    old = {"label": "hatch-prod-tcp-1", "action": "ACCEPT", "protocol": "TCP", "ports": "1", "addresses": {}}
    current = {"inbound": [MANUAL, other, old], "outbound": [], "inbound_policy": "DROP", "outbound_policy": "ACCEPT"}
    out = merge(current, "prod", {"tcp": {25561}})
    assert [r["label"] for r in out["inbound"]] == ["ssh", "hatch-stg-tcp-1", "hatch-prod-tcp-1"]
    assert out["inbound"][-1]["ports"] == "25561"
    assert out["inbound_policy"] == "DROP" and out["outbound_policy"] == "ACCEPT"
    # 全部締めると Hatch の分だけが消える
    assert [r["label"] for r in merge(out, "prod", {})["inbound"]] == ["ssh", "hatch-stg-tcp-1"]


def test_too_many_rules_is_refused():
    manual = [dict(MANUAL, label=f"m{i}") for i in range(24)]
    with pytest.raises(FirewallTooLarge):
        merge({"inbound": manual, "outbound": []}, "prod", {"tcp": {25561}, "udp": {30000}})


def test_same_ignores_other_fields():
    a = {"inbound": hatch_rules("prod", {"tcp": {1}}), "outbound": []}
    b = {"inbound": [dict(r, description="x", addresses={}) for r in a["inbound"]], "outbound": []}
    assert same(a, b)
    assert not same(a, {"inbound": [], "outbound": []})
