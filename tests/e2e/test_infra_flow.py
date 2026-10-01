"""画面：管理 → ノードと edge（docs/TASKS.md T43）の通しテスト。

Linode のアカウントを追加 → ファイアウォールを登録 → edge に付ける → サーバーを作るとファイアウォールに反映される、
を画面から行う。Linode はフェイク（tests/e2e/fake_server.py）。
"""

from __future__ import annotations

import psycopg
import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect

from tests.e2e.fake_server import LINODE_TOKEN
from tests.e2e.test_servers_flow import _no_horizontal_scroll, app_server, browser  # noqa: F401 - fixture


def test_register_linode_firewall_and_edge_from_the_screen(app_server, browser, db_url):  # noqa: F811
    base, token = app_server
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, locale="ja-JP")
    ctx.add_cookies([{"name": "pd_session", "value": token, "url": base}])
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    expect.set_options(timeout=15000)

    page.goto(f"{base}/admin/nodes")
    expect(page.locator("#main")).to_contain_text("edge-1")

    # アカウントを追加（間違ったトークンは断られる）
    page.click('[data-act="linode-add"]')
    page.fill("#ff-label", "個人契約")
    page.fill("#ff-token", "x" * 30)
    page.click("#form-ok")
    expect(page.locator("#form-err")).to_contain_text("トークン")
    page.fill("#ff-token", LINODE_TOKEN)
    page.click("#form-ok")
    expect(page.locator("#main")).to_contain_text("個人契約")
    assert LINODE_TOKEN not in page.content()

    # ファイアウォールを登録
    page.click('[data-act="firewall-add"]')
    page.click('[data-act="form-pick"][data-arg="firewall"]')
    page.get_by_role("option", name="hatch-edge").click()
    page.click("#form-ok")
    expect(page.locator("#main")).to_contain_text("hatch-edge")

    # edge-1 に Linode とファイアウォールを付ける（公開 IP は Linode から自動で入る）
    page.click('[data-act="menu-edge"][data-arg="edge-1"]')
    page.get_by_role("menuitem", name="変更（Linode・ファイアウォール）").click()
    page.click('[data-act="form-pick"][data-arg="account"]')
    page.get_by_role("option", name="個人契約").click()
    page.click('[data-act="form-pick"][data-arg="linode"]')
    page.get_by_role("option", name="edge-1").click()
    expect(page.locator("#ff-public_ip")).to_have_value("45.33.1.10")
    page.click('[data-act="form-pick"][data-arg="firewall"]')
    page.get_by_role("option", name="hatch-edge").click()
    page.click("#form-ok")
    expect(page.locator("#main")).to_contain_text("edge-1 専用")
    expect(page.locator("#toast")).to_contain_text("反映しました", timeout=20000)
    assert _no_horizontal_scroll(page)

    with psycopg.connect(db_url) as c:
        assert c.execute(
            "SELECT count(*) FROM firewalls WHERE synced_at IS NOT NULL AND last_error IS NULL"
        ).fetchone() == (1,)
        assert c.execute("SELECT firewall_id IS NOT NULL, host(public_ip) FROM edges").fetchone() == (
            True,
            "45.33.1.10",
        )
    # アカウントは、使っている edge とファイアウォールがあっても、まとめて外して削除できる
    page.click('[data-act="menu-firewall"]')
    expect(page.get_by_role("menuitem", name="管理をやめる（使っている edge から先に外す）")).to_be_disabled()
    page.keyboard.press("Escape")
    page.click('[data-act="menu-linode"]')
    page.get_by_role("menuitem", name="削除").click()
    expect(page.locator("#scrim")).to_contain_text("「Linode 以外」になります")
    page.click("#cf-ok")
    expect(page.locator("#toast")).to_contain_text("アカウントを削除しました")
    expect(main := page.locator("#main")).not_to_contain_text("個人契約")
    expect(main).to_contain_text("Linode 以外")
    assert not errors, errors
    ctx.close()
