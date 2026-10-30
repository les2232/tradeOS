"""Optional real-browser smoke test, with a mocked broker and real Flask routes.

PLAYWRIGHT_BROWSERS_PATH must point to installed Playwright Chromium browsers.
"""
import os
from threading import Thread

import pytest

pytest.importorskip('playwright.sync_api')
if not os.environ.get('PLAYWRIGHT_BROWSERS_PATH'):
    pytest.skip('Set PLAYWRIGHT_BROWSERS_PATH for optional browser checks', allow_module_level=True)

from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
from tests.unit.test_paper_supervisor import paper
from tradeos.web.app import create_app


def test_supervised_browser_workflow(paper):
    supervisor, broker, _ = paper
    server = make_server('127.0.0.1', 0, create_app(paper_supervisor=supervisor))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=['--no-sandbox'])
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{server.server_port}')
            expect(page.locator('#paper-summary')).to_contain_text('idle')
            broker.get_account.assert_not_called()
            page.locator('[data-paper="evaluate"]').click()
            expect(page.locator('#paper-decisions')).to_contain_text('buy')
            broker.submit_order.assert_not_called()
            page.locator('[data-paper="start"]').click()
            expect(page.locator('#paper-message')).to_contain_text('Confirm PAPER')
            page.locator('#paper-confirm').check()
            page.locator('[data-paper="start"]').click()
            expect(page.locator('#paper-message')).to_contain_text('requested')
            page.wait_for_function("document.querySelector('#paper-state').textContent.includes('broker-1')")
            assert broker.submit_order.call_count == 1
            page.locator('[data-paper="stop"]').click()
            supervisor.thread.join(timeout=3)
            assert not supervisor.active
            assert page.request.get(f'http://127.0.0.1:{server.server_port}/api/paper/history').json()
            page.locator('#run').click()
            expect(page.locator('#trade-count')).not_to_have_text('—', timeout=15000)
            assert not errors
            browser.close()
    finally:
        supervisor.stop()
        server.shutdown()
        thread.join(timeout=3)
