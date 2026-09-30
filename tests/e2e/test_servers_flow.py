"""画面：サーバーと作成・管理のアドレス（docs/TASKS.md T16）の通しテスト。

デモと同じ手順で、ドメインの追加 → アドレス枠の追加 → DNS の事前作成 → サーバーの作成 →
削除（ゴミ箱）→ 元に戻す → 完全に削除 を画面から行う。外部サービスは tests/e2e/fake_server.py のフェイク。
"""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import psycopg
import pytest

from hatch.auth.crypto import sha256_hex

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _seed(db_url: str) -> str:
    """初期設定済み・edge とノードあり・管理者のセッションを作り、Cookie の値を返す。"""
    token = secrets.token_urlsafe(32)
    with psycopg.connect(db_url) as c:
        c.execute("UPDATE app_settings SET value = to_jsonb(now()) WHERE key = 'setup_completed_at'")
        c.execute(
            "INSERT INTO edges (id, public_ip, tailscale_ip, is_active, applied_version) "
            "VALUES ('edge-1','203.0.113.10','100.64.1.1', true, 1000000000)"
        )
        c.execute(
            "INSERT INTO nodes (id, panel_node_id, tailscale_ip, memory_mb, disk_mb) "
            "VALUES ('node-1', 1, '100.64.0.12', 32768, 400000)"
        )
        uid = c.execute(
            "INSERT INTO users (username, email, role, max_servers) "
            "VALUES ('mesamaru','m@example.com','admin',10) RETURNING id"
        ).fetchone()[0]
        c.execute(
            "INSERT INTO sessions (id, user_id, csrf_token, expires_at) "
            "VALUES (%s,%s,'csrf', now() + interval '1 day')",
            (sha256_hex(token), uid),
        )
    return token


@pytest.fixture
def app_server(db_url, tmp_path):
    token = _seed(db_url)
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "tests.e2e.fake_server", str(port)],
        cwd=ROOT,
        env={**os.environ, "PD_DATA_DIR": str(tmp_path)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(150):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("サーバーが起動しませんでした")
        yield f"http://127.0.0.1:{port}", token
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture
def browser():
    try:
        with sync_playwright() as p:
            try:
                b = p.chromium.launch()
            except Exception as exc:  # ブラウザ本体が入っていない環境など
                pytest.skip(f"Playwright のブラウザを起動できません: {exc}")
            yield b
            b.close()
    except Exception as exc:
        pytest.skip(f"Playwright を起動できません: {exc}")


def _no_horizontal_scroll(page) -> bool:
    return page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")


def test_create_trash_restore_and_purge_from_the_screens(app_server, browser):
    base, token = app_server
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, locale="ja-JP")
    ctx.add_cookies([{"name": "pd_session", "value": token, "url": base}])
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    expect.set_options(timeout=15000)

    # ---- 管理：ドメインを追加 ----
    page.goto(f"{base}/admin")
    page.get_by_role("button", name="ドメイン").first.click()
    page.click('[data-act="domain-add"]')
    page.fill("#d-name", "nuids.jp")
    page.fill("#d-zone", "a" * 32)
    page.click("#simple-ok")
    expect(page.locator("#main")).to_contain_text("nuids.jp")

    # ---- アドレス枠を追加（プレビューが出て、保存できる） ----
    page.goto(f"{base}/admin/slots")
    page.click('[data-act="rule-new"]')
    page.fill("#r-name", "共有枠")
    page.fill("#r-st", "0")
    page.fill("#r-ex", "25560")
    expect(page.locator("#r-preview")).to_contain_text("mc01trt.nuids.jp")
    expect(page.locator("#rule-save")).to_be_enabled()
    page.click("#rule-save")
    expect(page.locator("#main")).to_contain_text("DNS はまだ作成していません")
    assert _no_horizontal_scroll(page)

    # ---- DNS の事前作成（ジョブの完了で表示が変わる） ----
    page.click('[data-act="rule-publish"]')
    expect(page.locator("#main")).to_contain_text("DNS は作成済み")

    # ---- サーバーを作成 ----
    page.goto(f"{base}/servers")
    page.click('.navbar [data-act="wizard"]')
    page.click('[data-act="wz-game"][data-arg="paper"]')
    page.click('.sheet-b [data-act="wz-next"]')
    page.fill("#wz-name", "Bad Name")
    expect(page.locator("#wz-h")).to_have_class("err-t")
    page.fill("#wz-name", "storia")
    expect(page.locator("#wz-addr")).to_have_text("mc01trt.nuids.jp")
    page.click("#wz-next")
    expect(page.locator(".sheet")).to_contain_text("mc01trt.nuids.jp")
    page.click("#wz-run")
    expect(page.locator("#main")).to_contain_text("storia を公開しました", timeout=30000)
    page.click('.banner [data-act="open-server"]')
    expect(page.locator(".hero")).to_contain_text("稼働中")
    expect(page.locator(".hero")).to_contain_text("mc01trt.nuids.jp")
    assert _no_horizontal_scroll(page)

    # ---- 停止と起動 ----
    page.click('.acts [data-act="power"]')
    expect(page.locator(".hero")).to_contain_text("停止中")
    page.click('.acts [data-act="power"]')
    expect(page.locator(".hero")).to_contain_text("稼働中")

    # ---- 削除（ゴミ箱へ）→ 元に戻す ----
    page.click('[data-act="trash"]')
    page.click("#cf-ok")
    expect(page.locator("#toast")).to_contain_text("storia をゴミ箱へ移動しました", timeout=30000)
    page.goto(f"{base}/servers/trash")
    expect(page.locator("#main")).to_contain_text("storia")
    page.click('[data-act="menu-trash"]')
    page.get_by_role("menuitem", name="元に戻す").click()
    expect(page.locator("#main")).to_contain_text("ゴミ箱は空です", timeout=30000)

    # ---- もう一度削除して、完全に削除（名前の入力で確認） ----
    page.goto(f"{base}/servers/storia")
    page.click('[data-act="trash"]')
    page.click("#cf-ok")
    expect(page.locator("#toast")).to_contain_text("storia をゴミ箱へ移動しました", timeout=30000)
    page.goto(f"{base}/servers/trash")
    expect(page.locator('[data-act="menu-trash"]')).to_be_visible(timeout=30000)
    page.click('[data-act="menu-trash"]')
    page.get_by_role("menuitem", name="完全に削除").click()
    expect(page.locator("#cf-ok")).to_be_disabled()
    page.fill("#cf-t", "storia")
    page.click("#cf-ok")
    expect(page.locator("#main")).to_contain_text("ゴミ箱は空です", timeout=30000)

    # アドレスは空きに戻る
    page.goto(f"{base}/admin/slots")
    expect(page.locator("#main")).to_contain_text("空き 9/9")
    assert not errors, errors
    ctx.close()
