"""画面の骨組み（T15）の見た目のテスト。

- 実際に uvicorn でサーバーを起動し、Playwright（Chromium）で docs/UI.md 3.2A の全サイズを開く。
- DATABASE_URL が無い、または Playwright のブラウザが入っていない環境ではテストを飛ばす
  （tests/conftest.py の db_url フィクスチャと同じ方針）。
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
from playwright.sync_api import sync_playwright

# docs/UI.md 3.2A で確認するとされている幅×高さ
VIEWPORTS = [
    (320, 640),
    (344, 882),
    (390, 844),
    (430, 932),
    (673, 841),
    (884, 1104),
    (844, 390),
    (1024, 768),
    (1280, 800),
]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(db_url, tmp_path):
    """DATABASE_URL を db_url に向けたまま、実際に uvicorn を起動する。"""
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "tests.e2e.fake_server", str(port)],  # 外部サービスはフェイク
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "PD_DATA_DIR": str(tmp_path)},  # 初期設定コードなどの書き込み先（テストごとに分ける）
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            proc.terminate()
            pytest.fail("サーバーが起動しませんでした")
        yield base_url
    finally:
        proc.terminate()
        proc.wait(timeout=5)


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


def _mark_setup_done(db_url: str) -> None:
    with psycopg.connect(db_url) as c:
        c.execute("UPDATE app_settings SET value = to_jsonb(now()) WHERE key = 'setup_completed_at'")


def test_setup_screen_has_no_horizontal_scroll(server, browser):
    for width, height in VIEWPORTS:
        page = browser.new_page(viewport={"width": width, "height": height})
        try:
            page.goto(server)
            page.wait_for_selector(".authcard.setup", timeout=5000)
            assert _no_horizontal_scroll(page), f"{width}x{height} で横スクロールが出ています"
        finally:
            page.close()


def test_login_screen_has_no_horizontal_scroll(server, browser, db_url):
    _mark_setup_done(db_url)
    for width, height in VIEWPORTS:
        page = browser.new_page(viewport={"width": width, "height": height})
        try:
            page.goto(server)
            page.wait_for_selector(".authcard", timeout=5000)
            assert _no_horizontal_scroll(page), f"{width}x{height} で横スクロールが出ています"
        finally:
            page.close()


def _make_session(db_url: str) -> str:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(16)
    with psycopg.connect(db_url) as c:
        row = c.execute(
            "INSERT INTO users (username, email, role, max_servers, totp_enabled) "
            "VALUES ('e2euser','e2e@example.com','user',3,false) RETURNING id::text"
        ).fetchone()
        c.execute(
            "INSERT INTO sessions (id, user_id, csrf_token, totp_ok, expires_at) "
            "VALUES (%s,%s,%s,true, now() + interval '1 day')",
            (sha256_hex(token), row[0], csrf),
        )
    return token


def test_servers_screen_has_no_horizontal_scroll(server, browser, db_url):
    _mark_setup_done(db_url)
    token = _make_session(db_url)
    for width, height in VIEWPORTS:
        context = browser.new_context(viewport={"width": width, "height": height})
        context.add_cookies([{"name": "pd_session", "value": token, "url": server}])
        page = context.new_page()
        try:
            page.goto(f"{server}/servers")
            page.wait_for_selector("#app:not([hidden])", timeout=5000)
            assert _no_horizontal_scroll(page), f"{width}x{height} で横スクロールが出ています"
        finally:
            context.close()
