"""画面：管理 → はじめの設定（docs/TASKS.md T48）の通しテスト。

サーバー一覧の案内から開き、ドメイン → アドレス枠 → DNS を、手順のボタンだけで順に済ませる。
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
    expect(main).to_contain_text("登録済み：edge-1（203.0.113.10）")
    expect(main).to_contain_text("ドメインを登録次にやる")
    assert _no_horizontal_scroll(page)

    # ドメイン（下の大きいボタンが、次の手順のシートを開く）
    page.get_by_role("button", name="ドメインを追加").last.click()
    page.fill("#d-name", "nuids.jp")
    page.fill("#d-zone", ZONE)
    page.click("#simple-ok")
    expect(main).to_contain_text("登録済み：nuids.jp", timeout=20000)

    # アドレス枠（保存してもこの画面に残る）
    page.get_by_role("button", name="アドレス枠を作る").last.click()
    page.fill("#r-name", "共有枠")
    expect(page.locator("#rule-save")).to_be_enabled()
    page.click("#rule-save")
    expect(main).to_contain_text("登録済み：共有枠")
    expect(page).to_have_url(f"{base}/admin/start")

    # DNS
    page.get_by_role("button", name="DNS を作成").last.click()
    expect(main).to_contain_text("はじめの設定はすべて済みました", timeout=20000)

    # 済むと、管理トップとサーバー一覧の案内は消える
    page.goto(f"{base}/admin")
    expect(main).to_contain_text("はじめの設定")
    expect(main).not_to_contain_text("はじめの設定が残っています")
    page.goto(f"{base}/servers")
    expect(main).to_contain_text("サーバー")
    expect(main).not_to_contain_text("はじめの設定が残っています")
    assert not errors, errors
    ctx.close()
