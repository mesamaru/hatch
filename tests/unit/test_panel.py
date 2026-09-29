"""ゲームパネルのアダプター（docs/TASKS.md T06）。"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from hatch.adapters.panel import NewPanelServer, NewPanelUser, PanelUserChanges, PterodactylPanel
from hatch.errors import AppError, TransientError, UpstreamError

URL = "https://gp.test"


@pytest.fixture
def panel():
    return PterodactylPanel(URL, "ptla_app", "ptlc_cli")


def user_attrs(**kw):
    a = {
        "id": 12,
        "external_id": "u-1",
        "username": "tanaka",
        "email": "t@x",
        "first_name": "tanaka",
        "last_name": "tanaka",
        "root_admin": False,
        "language": "en",
    }
    a.update(kw)
    return a


def alloc(id_, port, assigned=False, ip="100.64.0.12"):
    return {"object": "allocation", "attributes": {"id": id_, "ip": ip, "port": port, "assigned": assigned}}


def page(items, total_pages=1):
    return {"data": items, "meta": {"pagination": {"total_pages": total_pages}}}


@respx.mock
async def test_find_user_requires_exact_match(panel):
    respx.get(f"{URL}/api/application/users", params={"filter[external_id]": "u-1"}).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [{"attributes": user_attrs(external_id="u-10")}, {"attributes": user_attrs(external_id="u-1")}]
            },
        )
    )
    u = await panel.find_user(external_id="u-1")
    assert u and u.external_id == "u-1"


@respx.mock
async def test_update_user_sends_all_fields(panel):
    respx.get(f"{URL}/api/application/users/12").mock(
        return_value=httpx.Response(200, json={"attributes": user_attrs()})
    )
    patch = respx.patch(f"{URL}/api/application/users/12").mock(
        return_value=httpx.Response(200, json={"attributes": user_attrs()})
    )
    await panel.update_user(12, PanelUserChanges(password="new-password-123"))
    body = json.loads(patch.calls[0].request.content)
    assert body["password"] == "new-password-123"
    for k in ("email", "username", "first_name", "last_name", "root_admin", "external_id"):
        assert k in body, k
    assert body["username"] == "tanaka" and body["root_admin"] is False


@respx.mock
async def test_update_user_without_password_does_not_send_it(panel):
    respx.get(f"{URL}/api/application/users/12").mock(
        return_value=httpx.Response(200, json={"attributes": user_attrs()})
    )
    patch = respx.patch(f"{URL}/api/application/users/12").mock(
        return_value=httpx.Response(200, json={"attributes": user_attrs(root_admin=True)})
    )
    await panel.update_user(12, PanelUserChanges(root_admin=True))
    body = json.loads(patch.calls[0].request.content)
    assert "password" not in body and body["root_admin"] is True


@respx.mock
async def test_create_allocation_refetches_after_204(panel):
    lst = respx.get(f"{URL}/api/application/nodes/1/allocations")
    lst.side_effect = [httpx.Response(200, json=page([])), httpx.Response(200, json=page([alloc(431, 25565)]))]
    post = respx.post(f"{URL}/api/application/nodes/1/allocations").mock(return_value=httpx.Response(204))
    assert await panel.create_allocation(1, "100.64.0.12", 25565) == 431
    assert json.loads(post.calls[0].request.content) == {"ip": "100.64.0.12", "ports": ["25565"]}
    assert lst.calls[0].request.url.params["filter[port]"] == "25565"


@respx.mock
async def test_create_allocation_reuses_free_one(panel):
    respx.get(f"{URL}/api/application/nodes/1/allocations").mock(
        return_value=httpx.Response(200, json=page([alloc(9, 25565, ip="100.64.0.99"), alloc(431, 25565)]))
    )
    post = respx.post(f"{URL}/api/application/nodes/1/allocations")
    assert await panel.create_allocation(1, "100.64.0.12", 25565) == 431
    assert not post.called


@respx.mock
async def test_create_allocation_in_use_is_slot_taken(panel):
    respx.get(f"{URL}/api/application/nodes/1/allocations").mock(
        return_value=httpx.Response(200, json=page([alloc(431, 25565, assigned=True)]))
    )
    with pytest.raises(AppError) as e:
        await panel.create_allocation(1, "100.64.0.12", 25565)
    assert e.value.code == "slot_taken"


@respx.mock
async def test_allocation_filter_fallback(panel):
    lst = respx.get(f"{URL}/api/application/nodes/1/allocations")
    lst.side_effect = [
        httpx.Response(400, json={"errors": [{"detail": "Requested filter(s) `port` are not allowed."}]}),
        httpx.Response(200, json=page([alloc(1, 25560)], total_pages=2)),
        httpx.Response(200, json=page([alloc(431, 25565)], total_pages=2)),
    ]
    respx.post(f"{URL}/api/application/nodes/1/allocations")
    assert await panel.create_allocation(1, "100.64.0.12", 25565) == 431
    assert "filter[port]" not in lst.calls[1].request.url.params
    assert lst.calls[2].request.url.params["page"] == "2"


@respx.mock
async def test_create_server_body(panel):
    post = respx.post(f"{URL}/api/application/servers").mock(
        return_value=httpx.Response(
            201,
            json={
                "attributes": {
                    "id": 55,
                    "uuid": "abcd1234-0000-0000-0000-000000000000",
                    "identifier": "abcd1234",
                    "external_id": "srv-1",
                    "name": "storia",
                    "suspended": False,
                    "allocation": 431,
                    "user": 12,
                }
            },
        )
    )
    s = await panel.create_server(
        NewPanelServer(
            external_id="srv-1",
            name="storia",
            user_id=12,
            egg=15,
            docker_image="img",
            startup="java",
            environment={"A": "1"},
            memory_mb=4096,
            disk_mb=20480,
            cpu_percent=200,
            backups=5,
            allocation_id=431,
        )
    )
    body = json.loads(post.calls[0].request.content)
    assert body["allocation"] == {"default": 431}
    assert body["limits"] == {"memory": 4096, "swap": 0, "disk": 20480, "io": 500, "cpu": 200}
    assert body["feature_limits"] == {"databases": 0, "allocations": 1, "backups": 5}
    assert body["external_id"] == "srv-1" and body["start_on_completion"] is False
    assert s.identifier == "abcd1234" and s.allocation_id == 431


@respx.mock
async def test_find_server_404_is_none(panel):
    respx.get(f"{URL}/api/application/servers/external/srv-x").mock(return_value=httpx.Response(404))
    assert await panel.find_server("srv-x") is None


@respx.mock
async def test_delete_server_falls_back_to_force(panel):
    respx.delete(f"{URL}/api/application/servers/55").mock(return_value=httpx.Response(500, json={"errors": []}))
    force = respx.delete(f"{URL}/api/application/servers/55/force").mock(return_value=httpx.Response(204))
    await panel.delete_server(55)
    assert force.called


@respx.mock
async def test_keys_are_used_for_the_right_api(panel):
    app = respx.get(f"{URL}/api/application/servers/external/s").mock(return_value=httpx.Response(404))
    cli = respx.post(f"{URL}/api/client/servers/abcd1234/power").mock(return_value=httpx.Response(204))
    await panel.find_server("s")
    await panel.power("abcd1234", "start")
    assert app.calls[0].request.headers["Authorization"] == "Bearer ptla_app"
    assert cli.calls[0].request.headers["Authorization"] == "Bearer ptlc_cli"
    assert json.loads(cli.calls[0].request.content) == {"signal": "start"}


@respx.mock
async def test_errors(panel):
    respx.post(f"{URL}/api/application/users").mock(
        return_value=httpx.Response(
            422, json={"errors": [{"code": "ValidationException", "detail": "The username has already been taken."}]}
        )
    )
    with pytest.raises(UpstreamError) as e:
        await panel.create_user(NewPanelUser("u-1", "tanaka", "t@x"))
    assert "already been taken" in e.value.message
    respx.get(f"{URL}/api/client/servers/x/resources").mock(return_value=httpx.Response(503))
    with pytest.raises(TransientError):
        await panel.resources("x")


@respx.mock
async def test_schedule_is_created_once(panel):
    lst = respx.get(f"{URL}/api/client/servers/abcd/schedules")
    lst.side_effect = [
        httpx.Response(200, json={"data": []}),
        httpx.Response(200, json={"data": [{"attributes": {"id": 7, "name": "Hatch 自動バックアップ"}}]}),
    ]
    mk = respx.post(f"{URL}/api/client/servers/abcd/schedules").mock(
        return_value=httpx.Response(200, json={"attributes": {"id": 7}})
    )
    task = respx.post(f"{URL}/api/client/servers/abcd/schedules/7/tasks").mock(
        return_value=httpx.Response(200, json={})
    )
    from hatch.adapters.panel import Cron

    assert await panel.ensure_schedule("abcd", "Hatch 自動バックアップ", Cron()) == 7
    assert await panel.ensure_schedule("abcd", "Hatch 自動バックアップ", Cron()) == 7
    assert mk.call_count == 1 and task.call_count == 1
    assert json.loads(task.calls[0].request.content)["action"] == "backup"


@respx.mock
async def test_write_file_is_raw_text(panel):
    w = respx.post(f"{URL}/api/client/servers/abcd/files/write").mock(return_value=httpx.Response(204))
    await panel.write_file("abcd", "/config/paper-global.yml", "proxies:\n  velocity: {}\n")
    assert w.calls[0].request.content == b"proxies:\n  velocity: {}\n"
    assert w.calls[0].request.url.params["file"] == "/config/paper-global.yml"
