"""画面：管理 → はじめの設定（docs/TASKS.md T48）の通しテスト。

サーバー一覧の案内から一覧を開き、手順ごとの画面でドメイン → アドレス枠 → DNS を順に済ませる。
済んだ手順も、その手順の画面で内容を確かめて、削除・作り直しができること。
外部サービスはフェイク（tests/e2e/fake_server.py）。
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect

from tests.e2e.fake_server import ZONE
from tests.e2e.test_servers_flow import _no_horizontal_scroll, app_server, browser  # noqa: F401 - fixture


def test_finish_getting_started_from_the_checklist(app_server, browser):  # noqa: F811
    base, token = app_server
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, locale="ja-JP")
    ctx.add_cookies([{"name": "pd_session", "value": token, "url": base}])
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    expect.set_options(timeout=15000)
    main = page.locator("#main")

    # 管理者のサーバー一覧に案内が出る（edge は登録済みなので 1 / 4）
    page.goto(f"{base}/servers")
    expect(main).to_contain_text("はじめの設定が残っています（1 / 4）")
    page.click('[data-act="gs-open"]')
    expect(page).to_have_url(f"{base}/admin/start")
    expect(main).to_contain_text("ドメインを登録次にやる")
    assert _no_horizontal_scroll(page)

    # 済んだ手順を押すと、その手順の画面で登録した内容が見える（ノードと edge の画面には移らない）
    page.click('[data-act="gs-step"][data-arg="edge"]')
    expect(page).to_have_url(f"{base}/admin/start/edge")
    expect(main).to_contain_text("この手順は済んでいます")
    expect(main).to_contain_text("公開 IP 203.0.113.10")
    page.click('[data-act="menu-edge"][data-arg="edge-1"]')
    expect(page.get_by_role("menuitem", name="変更（Linode・ファイアウォール）")).to_be_visible()
    page.keyboard.press("Escape")

    # 次の手順へ：ドメイン
    page.get_by_role("button", name="次へ：ドメインを登録").click()
    expect(page).to_have_url(f"{base}/admin/start/domain")
    page.get_by_role("button", name="ドメインを追加").click()
    page.fill("#d-name", "nuids.jp")
    page.fill("#d-zone", ZONE)
    page.click("#simple-ok")
    expect(main).to_contain_text("edge.nuids.jp 反映済み", timeout=20000)
    expect(main).to_contain_text("この手順は済んでいます")

    # アドレス枠（保存しても、この手順の画面に残る）
    page.get_by_role("button", name="次へ：アドレス枠を作る").click()
    page.get_by_role("button", name="アドレス枠を作る").click()
    page.fill("#r-name", "共有枠")
    expect(page.locator("#rule-save")).to_be_enabled()
    page.click("#rule-save")
    expect(main).to_contain_text("ポート 25560〜25569")
    expect(page).to_have_url(f"{base}/admin/start/rule")

    # DNS
    page.get_by_role("button", name="次へ：アドレス枠の DNS を作る").click()
    page.get_by_role("button", name="DNS を作成").click()
    expect(main).to_contain_text("作成済み", timeout=20000)

    # 済んだ手順もやり直せる（DNS を削除すると未作成に戻り、また作れる）
    page.click('[data-act="gs-menu-dns"]')
    page.get_by_role("menuitem", name="DNS を削除").click()
    page.click("#cf-ok")
    expect(main).to_contain_text("未作成", timeout=20000)
    page.get_by_role("button", name="DNS を作成").click()
    expect(main).to_contain_text("作成済み", timeout=20000)
    assert _no_horizontal_scroll(page)

    page.get_by_role("button", name="はじめの設定の一覧へ").click()
    expect(main).to_contain_text("はじめの設定はすべて済みました")

    # 済むと、管理トップとサーバー一覧の案内は消える
    page.goto(f"{base}/admin")
    expect(main).to_contain_text("はじめの設定")
    expect(main).not_to_contain_text("はじめの設定が残っています")
    page.goto(f"{base}/servers")
    expect(main).to_contain_text("サーバー")
    expect(main).not_to_contain_text("はじめの設定が残っています")
    assert not errors, errors
    ctx.close()
