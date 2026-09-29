"""Cloudflare アダプター（docs/TASKS.md T07）。通信は respx で置き換える。"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from hatch.adapters.dns import CF_API, CloudflareDns, DnsConflict, DnsRecordSpec, Srv, slot_records
from hatch.errors import TransientError, UpstreamError
from tests.fakes.dns import FakeDns

Z = "zone1"


def rec(id_, type_, name, content=None, comment="hatch:prod slot=25565", data=None, proxied=False, ttl=60):
    r = {"id": id_, "type": type_, "name": name, "comment": comment, "proxied": proxied, "ttl": ttl}
    if content:
        r["content"] = content
    if data:
        r["data"] = data
    return r


def ok(result, **info):
    body = {"success": True, "errors": [], "result": result}
    if info:
        body["result_info"] = info
    return httpx.Response(200, json=body)


@pytest.fixture
def cf():
    return CloudflareDns("tok", "prod")


@respx.mock
async def test_create_cname_without_proxy(cf):
    respx.get(f"{CF_API}/zones/{Z}/dns_records", params={"name": "mc05trt.nuids.jp"}).mock(return_value=ok([]))
    post = respx.post(f"{CF_API}/zones/{Z}/dns_records").mock(
        return_value=ok(rec("r1", "CNAME", "mc05trt.nuids.jp", "edge.nuids.jp"))
    )
    r = await cf.upsert(Z, DnsRecordSpec("CNAME", "mc05trt.nuids.jp", "slot=25565", content="edge.nuids.jp"))
    body = json.loads(post.calls[0].request.content)
    assert body == {
        "type": "CNAME",
        "name": "mc05trt.nuids.jp",
        "ttl": 60,
        "comment": "hatch:prod slot=25565",
        "content": "edge.nuids.jp",
        "proxied": False,
    }
    assert post.calls[0].request.headers["Authorization"] == "Bearer tok"
    assert r.id == "r1"


@respx.mock
async def test_create_srv_uses_data(cf):
    name = "_minecraft._tcp.mc05trt.nuids.jp"
    respx.get(f"{CF_API}/zones/{Z}/dns_records", params={"name": name}).mock(return_value=ok([]))
    post = respx.post(f"{CF_API}/zones/{Z}/dns_records").mock(
        return_value=ok(
            rec("r2", "SRV", name, data={"priority": 0, "weight": 5, "port": 25565, "target": "edge.nuids.jp"})
        )
    )
    await cf.upsert(Z, DnsRecordSpec("SRV", name, "slot=25565", srv=Srv(0, 5, 25565, "edge.nuids.jp")))
    body = json.loads(post.calls[0].request.content)
    assert body["data"] == {"priority": 0, "weight": 5, "port": 25565, "target": "edge.nuids.jp"}
    assert "proxied" not in body and "content" not in body


@respx.mock
async def test_unchanged_record_is_not_touched(cf):
    respx.get(f"{CF_API}/zones/{Z}/dns_records").mock(
        return_value=ok([rec("r1", "CNAME", "mc05trt.nuids.jp", "edge.nuids.jp")])
    )
    post = respx.post(f"{CF_API}/zones/{Z}/dns_records")
    patch = respx.patch(f"{CF_API}/zones/{Z}/dns_records/r1")
    await cf.upsert(Z, DnsRecordSpec("CNAME", "mc05trt.nuids.jp", "slot=25565", content="edge.nuids.jp"))
    assert not post.called and not patch.called


@respx.mock
async def test_proxied_record_is_fixed(cf):
    respx.get(f"{CF_API}/zones/{Z}/dns_records").mock(
        return_value=ok([rec("r1", "CNAME", "mc05trt.nuids.jp", "edge.nuids.jp", proxied=True)])
    )
    patch = respx.patch(f"{CF_API}/zones/{Z}/dns_records/r1").mock(
        return_value=ok(rec("r1", "CNAME", "mc05trt.nuids.jp", "edge.nuids.jp"))
    )
    await cf.upsert(Z, DnsRecordSpec("CNAME", "mc05trt.nuids.jp", "slot=25565", content="edge.nuids.jp"))
    assert json.loads(patch.calls[0].request.content)["proxied"] is False


@respx.mock
async def test_manual_record_is_never_overwritten(cf):
    respx.get(f"{CF_API}/zones/{Z}/dns_records").mock(
        return_value=ok([rec("m1", "A", "mc05trt.nuids.jp", "1.2.3.4", comment="手動")])
    )
    with pytest.raises(DnsConflict):
        await cf.upsert(Z, DnsRecordSpec("CNAME", "mc05trt.nuids.jp", "slot=25565", content="edge.nuids.jp"))


@respx.mock
async def test_other_instance_record_is_foreign(cf):
    respx.get(f"{CF_API}/zones/{Z}/dns_records").mock(
        return_value=ok([rec("s1", "CNAME", "mc05trt.nuids.jp", "edge.nuids.jp", comment="hatch:stg slot=25565")])
    )
    with pytest.raises(DnsConflict):
        await cf.upsert(Z, DnsRecordSpec("CNAME", "mc05trt.nuids.jp", "slot=25565", content="edge.nuids.jp"))


@respx.mock
async def test_list_managed_pages_and_filters_instance(cf):
    route = respx.get(f"{CF_API}/zones/{Z}/dns_records")
    route.side_effect = [
        ok(
            [rec("a", "CNAME", "x.nuids.jp", "e"), rec("b", "CNAME", "y.nuids.jp", "e", comment="hatch:prodx z")],
            page=1,
            total_pages=2,
        ),
        ok([rec("c", "A", "edge.nuids.jp", "1.1.1.1", comment="hatch:prod binding=1")], page=2, total_pages=2),
    ]
    got = await cf.list_managed(Z)
    assert [r.id for r in got] == ["a", "c"]  # "hatch:prodx" は別インスタンス
    assert route.calls[0].request.url.params["comment.startswith"] == "hatch:prod"


@respx.mock
async def test_errors_are_classified(cf):
    respx.get(f"{CF_API}/zones/{Z}").mock(return_value=httpx.Response(429))
    with pytest.raises(TransientError):
        await cf.verify_zone(Z)
    respx.get(f"{CF_API}/zones/bad").mock(
        return_value=httpx.Response(403, json={"success": False, "errors": [{"code": 9109, "message": "Unauthorized"}]})
    )
    with pytest.raises(UpstreamError) as e:
        await cf.verify_zone("bad")
    assert "9109" in e.value.message


@respx.mock
async def test_timeout_is_transient(cf):
    respx.get(f"{CF_API}/zones/{Z}").mock(side_effect=httpx.ConnectTimeout("t"))
    with pytest.raises(TransientError):
        await cf.verify_zone(Z)


@respx.mock
async def test_delete_404_is_ok(cf):
    respx.delete(f"{CF_API}/zones/{Z}/dns_records/gone").mock(return_value=httpx.Response(404, json={"success": False}))
    await cf.delete(Z, "gone")


def test_slot_records_shapes():
    recs = slot_records("mc05trt", "nuids.jp", 25565, mode="cname_edge", edge_host="edge", ip=None, srv=True)
    assert [(r.type, r.name) for r in recs] == [
        ("CNAME", "mc05trt.nuids.jp"),
        ("SRV", "_minecraft._tcp.mc05trt.nuids.jp"),
    ]
    assert recs[1].srv.target == "edge.nuids.jp"
    recs = slot_records("mc05trt", "nuids.jp", 25565, mode="a_ip", edge_host="edge", ip="203.0.113.10", srv=True)
    assert recs[0].type == "A" and recs[1].srv.target == "mc05trt.nuids.jp"  # SRV は A を向く
    assert len(slot_records("pal", "nuids.jp", 8211, mode="cname_edge", edge_host="edge", ip=None, srv=False)) == 1


async def test_fake_behaves_like_real():
    f = FakeDns(instance="prod")
    spec = DnsRecordSpec("CNAME", "mc05trt.nuids.jp", "slot=25565", content="edge.nuids.jp")
    await f.upsert("z1", spec)
    await f.upsert("z1", spec)
    assert len(f.records["z1"]) == 1
    # A から CNAME への切り替えは、自分のレコードなら置き換える
    await f.upsert("z1", DnsRecordSpec("A", "mc05trt.nuids.jp", "slot=25565", content="203.0.113.10"))
    assert f.names() == {("A", "mc05trt.nuids.jp")}
    f.add_manual("z1", "TXT", "mc06trt.nuids.jp", "hello")
    with pytest.raises(DnsConflict):
        await f.upsert("z1", DnsRecordSpec("CNAME", "mc06trt.nuids.jp", "slot=25566", content="edge.nuids.jp"))
