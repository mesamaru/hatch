"""アドレス枠の展開と検証（docs/TASKS.md T03 の受け入れ条件）。"""

from dataclasses import replace

import pytest

from hatch.domain.names import host_label_problem, server_name_problem
from hatch.domain.slots import Binding, SlotRule, expand, host_for, validate

MC = SlotRule(
    id=1,
    name="Minecraft 共有枠",
    domain_id=1,
    domain="nuids.jp",
    port_start=25560,
    port_end=25569,
    host_template="mc{nn}trt",
    number_start=0,
    excluded_ports=(25560,),
)


def hosts(rule):
    return {s.port: (s.host, s.excluded) for s in expand(rule)}


def test_mc_nn_trt_with_excluded_25560():
    h = hosts(MC)
    assert h[25561] == ("mc01trt", False)
    assert h[25569] == ("mc09trt", False)
    assert h[25560][1] is True
    assert h[25565] == ("mc05trt", False)


def test_start_1_shifts_numbers():
    h = hosts(replace(MC, number_start=1, excluded_ports=()))
    assert h[25560] == ("mc01trt", False)
    assert h[25569] == ("mc10trt", False)


def test_excluded_does_not_shift_numbers():
    h = hosts(replace(MC, excluded_ports=(25560, 25563)))
    assert h[25564][0] == "mc04trt"


@pytest.mark.parametrize(
    ("tpl", "port", "want"),
    [
        ("srv{port}", 30010, "srv30010"),
        ("s{nnn}", 25561, "s001"),
        ("s{n}", 25569, "s9"),
        ("{nn}-{port}", 25561, "01-25561"),
    ],
)
def test_tokens(tpl, port, want):
    assert host_for(replace(MC, host_template=tpl), port) == want


def test_server_template():
    r = replace(MC, host_template="{server}", prepublish=False)
    assert host_for(r, 25561) is None
    assert host_for(r, 25561, "storia") == "storia"
    assert validate(r, [], []).ok


def test_valid_rule_ok():
    v = validate(MC, [], [])
    assert v.ok, v.problems
    assert len(v.slots) == 10


@pytest.mark.parametrize(
    ("tpl", "msg"),
    [
        ("mc{nn}{server}", "{server} は単独"),
        ("mc", "番号"),
        ("mc{x}", "使えない記号"),
        ("mc{nn", "対応"),
        ("MC{nn}", "英小文字"),
        ("", "ホスト名を入力"),
    ],
)
def test_template_errors(tpl, msg):
    v = validate(replace(MC, host_template=tpl), [], [])
    assert any(msg in p for p in v.problems), v.problems


def test_overlap_with_other_rule():
    other = replace(MC, id=2, name="他の枠", port_start=25565, port_end=25580, host_template="x{port}")
    v = validate(MC, [other], [])
    assert any("重なっています" in p for p in v.problems)


def test_same_host_in_other_rule_same_domain():
    other = replace(MC, id=2, name="他の枠", port_start=25570, port_end=25579, number_start=0, excluded_ports=())
    # other は mc00trt..mc09trt を持つので、MC の mc01trt と重なる
    v = validate(MC, [other], [])
    assert any("mc01trt.nuids.jp" in p and "他の枠" in p for p in v.problems), v.problems


def test_same_host_other_domain_is_fine():
    other = replace(MC, id=2, name="他", domain_id=2, domain="example.net", port_start=25570, port_end=25579)
    assert validate(MC, [other], []).ok


def test_binding_conflict():
    v = validate(replace(MC, host_template="edge{n}", excluded_ports=()), [], [Binding(1, "edge3")])
    assert any("IP の紐付け" in p for p in v.problems), v.problems


def test_reserved_name():
    r = replace(MC, host_template="edge-{n}", number_start=1, excluded_ports=(), port_end=25561)
    v = validate(r, [], [])
    assert any("システムで使う" in p for p in v.problems), v.problems


def test_too_long_host():
    r = replace(MC, host_template="a" * 62 + "{nn}")
    assert any("長すぎ" in p for p in validate(r, [], []).problems)


def test_port_range_limits():
    assert any(
        "200ポート" in p for p in validate(replace(MC, port_end=25560 + 200, excluded_ports=()), [], []).problems
    )
    assert any(
        "1024" in p for p in validate(replace(MC, port_start=80, port_end=90, excluded_ports=()), [], []).problems
    )
    assert any(
        "以上" in p for p in validate(replace(MC, port_start=25570, port_end=25560, excluded_ports=()), [], []).problems
    )


def test_excluded_outside_range():
    assert any("範囲の外" in p for p in validate(replace(MC, excluded_ports=(30000,)), [], []).problems)


def test_a_ip_needs_ip_and_user_needs_user():
    v = validate(replace(MC, record_mode="a_ip", assign_to="user"), [], [])
    assert any("IP アドレス" in p for p in v.problems)
    assert any("ユーザー" in p for p in v.problems)


def test_change_while_in_use():
    changed = replace(MC, host_template="x{nn}", port_end=25562)
    v = validate(changed, [], [], in_use_ports={25561, 25565}, original=MC)
    assert any("25565" in p and "範囲外" in p for p in v.problems)
    assert any("変更できません" in p for p in v.problems)
    # 使用中のポートを残して範囲だけ広げるのは可
    assert validate(replace(MC, port_end=25580), [], [], in_use_ports={25561}, original=MC).ok


@pytest.mark.parametrize(
    ("name", "ok"),
    [
        ("storia", True),
        ("a1", False),
        ("ab", False),
        ("abc", True),
        ("a", False),
        ("-ab", False),
        ("ab-", False),
        ("a--b", False),
        ("edge", False),
        ("Storia", False),
        ("a" * 32, True),
        ("a" * 33, False),
    ],
)
def test_server_names(name, ok):
    assert (server_name_problem(name) is None) is ok


def test_host_label():
    assert host_label_problem("mc01trt") is None
    assert host_label_problem("api") is not None
