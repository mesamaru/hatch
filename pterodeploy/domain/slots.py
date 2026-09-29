"""アドレス枠（ポート範囲 ↔ ホスト名）の展開と検証。docs/IMPLEMENTATION.md 4章。

テンプレートの記号:
    {n}   番号            n = number_start + (port - port_start)
    {nn}  2桁ゼロ埋め     {nnn} 3桁ゼロ埋め
    {port} ポート番号
    {server} サーバー名（単独でのみ使える。DNS の事前作成は不可）

対象外のポートがあっても番号はずれない（ポートと名前の対応を常に一定にするため）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .names import DEFAULT_RESERVED, host_label_problem

MAX_PORTS_PER_RULE = 200
TOKEN_RE = re.compile(r"\{[^}]*\}")
ALLOWED_TOKENS = frozenset({"{n}", "{nn}", "{nnn}", "{port}", "{server}"})
NUMBER_TOKENS = ("{n}", "{nn}", "{nnn}", "{port}")


@dataclass(frozen=True)
class SlotRule:
    id: int | None
    name: str
    domain_id: int
    domain: str  # 例 "nuids.jp"
    port_start: int
    port_end: int
    host_template: str
    number_start: int = 0
    excluded_ports: tuple[int, ...] = ()
    record_mode: str = "cname_edge"  # cname_edge / a_ip
    ip_id: int | None = None
    create_srv: bool = True
    prepublish: bool = True
    assign_to: str = "shared"  # shared / user
    user_id: str | None = None

    @property
    def uses_server_name(self) -> bool:
        return "{server}" in self.host_template

    @property
    def ports(self) -> range:
        return range(self.port_start, self.port_end + 1)


@dataclass(frozen=True)
class SlotPlan:
    port: int
    host: str | None  # None = サーバー名で決まる（{server} の枠）
    excluded: bool

    def fqdn(self, domain: str, server_name: str | None = None) -> str | None:
        h = self.host or server_name
        return f"{h}.{domain}" if h else None


@dataclass(frozen=True)
class Binding:
    """IP の紐付け（A レコード）。ホスト名の重複チェックに使う。"""

    domain_id: int
    host: str


@dataclass
class Validation:
    problems: list[str] = field(default_factory=list)
    slots: list[SlotPlan] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def host_for(rule: SlotRule, port: int, server_name: str | None = None) -> str | None:
    """ポートのホスト名。{server} の枠でサーバー名が未定なら None。"""
    if rule.uses_server_name:
        return server_name
    n = rule.number_start + (port - rule.port_start)
    out = rule.host_template
    # 長い記号から置き換える（{nn} を {n} として置き換えないように）
    out = out.replace("{nnn}", f"{n:03d}").replace("{nn}", f"{n:02d}").replace("{n}", str(n))
    return out.replace("{port}", str(port))


def expand(rule: SlotRule) -> list[SlotPlan]:
    excl = set(rule.excluded_ports)
    return [SlotPlan(p, host_for(rule, p), p in excl) for p in rule.ports]


def _template_problems(tpl: str) -> list[str]:
    out: list[str] = []
    if not tpl:
        return ["ホスト名を入力してください"]
    if tpl != tpl.lower():
        out.append("ホスト名は英小文字で入力してください")
    tokens = TOKEN_RE.findall(tpl)
    bad = [t for t in tokens if t not in ALLOWED_TOKENS]
    if bad:
        out.append(f"使えない記号があります：{' '.join(bad)}")
    if "{" in TOKEN_RE.sub("", tpl) or "}" in TOKEN_RE.sub("", tpl):
        out.append("{ と } の対応が正しくありません")
    if "{server}" in tpl and tpl != "{server}":
        out.append("{server} は単独で使ってください")
    if "{server}" not in tpl and not any(t in tpl for t in NUMBER_TOKENS):
        out.append("番号（{nn} など）か {port} を含めてください（同じ名前が並ぶため）")
    return out


def validate(
    rule: SlotRule,
    others: list[SlotRule],
    bindings: list[Binding],
    reserved: frozenset[str] | set[str] = DEFAULT_RESERVED,
    in_use_ports: set[int] | None = None,
    original: SlotRule | None = None,
) -> Validation:
    """枠の検証。others は自分以外の既存の枠。

    in_use_ports / original は既存の枠を変更するとき：使用中・ゴミ箱のスロットのポートと、変更前の枠。
    """
    v = Validation()
    p = v.problems
    if not rule.name.strip():
        p.append("名前を入力してください")

    ports_ok = True
    if not (1024 <= rule.port_start <= 65535 and 1024 <= rule.port_end <= 65535):
        p.append("ポートは 1024〜65535 にしてください")
        ports_ok = False
    elif rule.port_end < rule.port_start:
        p.append("終了ポートは開始ポート以上にしてください")
        ports_ok = False
    elif rule.port_end - rule.port_start + 1 > MAX_PORTS_PER_RULE:
        p.append(f"1つの枠は{MAX_PORTS_PER_RULE}ポートまでです")
        ports_ok = False
    else:
        for o in others:
            if not (rule.port_end < o.port_start or rule.port_start > o.port_end):
                p.append(f"ポートが「{o.name}」（{o.port_start}–{o.port_end}）と重なっています")
                ports_ok = False
                break
    stray = [x for x in rule.excluded_ports if not (rule.port_start <= x <= rule.port_end)]
    if ports_ok and stray:
        p.append(f"対象外のポート {', '.join(map(str, stray))} は範囲の外です")
    if rule.number_start < 0:
        p.append("開始番号は0以上にしてください")
    if rule.record_mode not in ("cname_edge", "a_ip"):
        p.append("DNS の形が正しくありません")
    if rule.record_mode == "a_ip" and rule.ip_id is None:
        p.append("IP アドレスを選んでください")
    if rule.assign_to not in ("shared", "user"):
        p.append("使える人の指定が正しくありません")
    if rule.assign_to == "user" and not rule.user_id:
        p.append("専用にするユーザーを選んでください")
    if rule.uses_server_name and rule.prepublish:
        p.append("{server} の枠では DNS を先に作成できません")

    tpl_problems = _template_problems(rule.host_template)
    p.extend(tpl_problems)

    if ports_ok and not tpl_problems:
        v.slots = expand(rule)
        if not rule.uses_server_name:
            taken: dict[str, str] = {}
            for o in others:
                if o.domain_id == rule.domain_id and not o.uses_server_name:
                    for s in expand(o):
                        if not s.excluded and s.host:
                            taken[s.host] = o.name
            bound = {b.host for b in bindings if b.domain_id == rule.domain_id}
            seen: set[str] = set()
            for s in v.slots:
                if s.excluded or s.host is None:
                    continue
                prob = host_label_problem(s.host, reserved)
                if prob:
                    p.append(prob)
                    break
                if s.host in seen:
                    p.append(f"「{s.host}」が重複しています")
                    break
                seen.add(s.host)
                if s.host in taken:
                    p.append(f"{s.host}.{rule.domain} は「{taken[s.host]}」で使われています")
                    break
                if s.host in bound:
                    p.append(f"{s.host}.{rule.domain} は IP の紐付けで使われています")
                    break

    if original is not None and in_use_ports:
        lost = sorted(
            x for x in in_use_ports if not (rule.port_start <= x <= rule.port_end) or x in rule.excluded_ports
        )
        if lost:
            p.append(f"使用中のポート（{', '.join(map(str, lost))}）を範囲外・対象外にはできません")
        if (original.host_template, original.domain_id, original.number_start) != (
            rule.host_template,
            rule.domain_id,
            rule.number_start,
        ):
            p.append("使用中のスロットがある間は、ホスト名の決め方・ドメイン・開始番号を変更できません")
    return v
