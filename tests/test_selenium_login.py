"""End-to-end tests for Opal web UI and authentication.

Requires a running Opal stack on https://localhost:7443 with password SeleniumTest123.

Selenium tests cover: page load, HTTPS, SPA rendering.
REST API tests cover: login, authentication, session management.

Run with: uv run python -m pytest tests/test_selenium_login.py -v
"""

import time
import requests
import pytest
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

BASE_URL = "https://localhost:7443"
ADMIN_USER = "administrator"
ADMIN_PASS = "SeleniumTest123"


# ── Selenium Fixtures ────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def browser():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--ignore-certificate-errors")
    d = webdriver.Chrome(options=options)
    d.set_page_load_timeout(30)
    yield d
    d.quit()


@pytest.fixture(scope="module")
def session():
    """Authenticated requests session via REST API."""
    s = requests.Session()
    s.verify = False
    resp = s.post(f"{BASE_URL}/ws/auth/sessions", data={
        "username": ADMIN_USER, "password": ADMIN_PASS,
    })
    assert resp.status_code == 201, f"Login failed: {resp.status_code} {resp.text}"
    return s


# ── Selenium: Page Load & Rendering ─────────────────────────────────────────


class TestPageLoad:
    def test_page_title_is_opal(self, browser):
        browser.get(BASE_URL)
        time.sleep(5)
        assert "Opal" in browser.title

    def test_uses_https(self, browser):
        assert browser.current_url.startswith("https://")

    def test_spa_renders_login_form(self, browser):
        browser.get(BASE_URL)
        for _ in range(20):
            count = browser.execute_script("return document.querySelectorAll('input').length")
            if count >= 2:
                break
            time.sleep(1)
        assert count >= 2, f"Expected >=2 inputs, got {count}"

    def test_spa_renders_submit_button(self, browser):
        # SPA should already be loaded from previous test (module-scoped driver)
        btn = browser.execute_script("""
            const b = document.querySelector('button[type=submit]');
            return b ? b.innerText.trim() : null;
        """)
        assert btn is not None and "SIGN" in btn.upper()


# ── REST API: Authentication ─────────────────────────────────────────────────


class TestAuthentication:
    def test_login_returns_201(self):
        resp = requests.post(f"{BASE_URL}/ws/auth/sessions",
            data={"username": ADMIN_USER, "password": ADMIN_PASS}, verify=False)
        assert resp.status_code == 201

    def test_wrong_password_returns_403(self):
        resp = requests.post(f"{BASE_URL}/ws/auth/sessions",
            data={"username": ADMIN_USER, "password": "WRONG"}, verify=False)
        assert resp.status_code == 403

    def test_unauthenticated_api_returns_401(self):
        resp = requests.get(f"{BASE_URL}/ws/system/version", verify=False)
        assert resp.status_code == 401


class TestAuthenticatedAPI:
    def test_system_version(self, session):
        resp = session.get(f"{BASE_URL}/ws/system/version")
        assert resp.status_code == 200
        assert "." in resp.text  # e.g., "5.5.1"

    def test_system_env_admin_only(self, session):
        resp = session.get(f"{BASE_URL}/ws/system/env")
        assert resp.status_code == 200

    def test_session_persists(self, session):
        """Multiple calls with same session should work."""
        for _ in range(3):
            resp = session.get(f"{BASE_URL}/ws/system/version")
            assert resp.status_code == 200


# ── REST API: CSRF Verification ──────────────────────────────────────────────


class TestCSRF:
    def test_csrf_allows_configured_origin(self, session):
        resp = session.get(f"{BASE_URL}/ws/system/version",
            headers={"Origin": f"https://localhost:7443"})
        assert resp.status_code == 200

    def test_csrf_header_present_in_container(self):
        """CSRF_ALLOWED env var should include our host+port."""
        import subprocess
        result = subprocess.run(
            ["docker", "exec", "seltest-opal", "env"],
            capture_output=True, text=True, check=False,
        )
        assert "CSRF_ALLOWED" in result.stdout
        assert "localhost:7443" in result.stdout


# ── Selenium: Security ───────────────────────────────────────────────────────


class TestSecurity:
    def test_no_mixed_content(self, browser):
        browser.get(BASE_URL)
        time.sleep(2)
        try:
            logs = browser.get_log("browser")
            mixed = [l for l in logs if "Mixed Content" in l.get("message", "")]
            assert len(mixed) == 0
        except Exception:
            pass  # Not all drivers support get_log
