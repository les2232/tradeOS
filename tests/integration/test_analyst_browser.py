"""Real-browser check of the session analyst panel against a recorded PAPER session."""
import os
from threading import Thread

import pytest

pytest.importorskip('playwright.sync_api')
if not os.environ.get('PLAYWRIGHT_BROWSERS_PATH'):
    pytest.skip('Set PLAYWRIGHT_BROWSERS_PATH for optional browser checks', allow_module_level=True)

from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
from tests.unit.analyst.conftest import run_session, supervised  # noqa: F401  (fixture)
from tradeos.web.app import create_app


def test_analyst_panel_explains_a_recorded_session(supervised, tmp_path):
    supervisor, broker = supervised
    run_session(supervisor, cycles=1)
    broker.reset_mock()
    app = create_app(paper_supervisor=supervisor, research_memory=tmp_path / 'none.jsonl')
    server = make_server('127.0.0.1', 0, app)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=['--no-sandbox'])
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{server.server_port}/#analyst')
            expect(page.locator('#analyst-status')).to_contain_text('1 recorded session')
            page.locator('#analyst-kind').select_option('symbol')
            page.locator('#analyst-symbol').fill('aapl')
            page.locator('#analyst-local').click()
            expect(page.locator('#analyst-output')).to_contain_text('Why did AAPL buy, sell, or hold')
            expect(page.locator('#analyst-output')).to_contain_text('Latest AAPL decision: buy')
            expect(page.locator('#analyst-output')).to_contain_text('E')
            expect(page.locator('#analyst-evidence')).to_contain_text('"evidence_version": 1')
            if os.environ.get('TRADEOS_SCREENSHOT'):
                page.locator('#analyst').screenshot(path=os.environ['TRADEOS_SCREENSHOT'])
            assert errors == []
            browser.close()
        # Reading sessions and explaining them never calls the broker.
        assert broker.method_calls == []
    finally:
        server.shutdown()
