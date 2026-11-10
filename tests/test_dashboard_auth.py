# =============================================================
# tests/test_dashboard_auth.py — tests for the dashboard's login
#
# These tests prove the privacy rules of the dashboard:
#   - every page refuses a browser that is not logged in,
#   - passwords are stored hashed, never as plain text,
#   - too many wrong passwords lock the address out,
#   - forms without the secret form token are refused,
#   - changing the password logs old sessions out.
#
# Each test gets its own empty database in a temporary folder and
# a pretend browser (FastAPI's TestClient), so nothing touches the
# real database and no real server is started.
# =============================================================

import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

import src.dashboard.auth as auth
import src.storage.database as db_module
from src.dashboard.app import create_app, is_public_path
from src.storage.database import create_tables
from src.storage.queries import get_video_by_id, save_video
from src.storage.users import get_user


TEST_USERNAME = "tester"
TEST_PASSWORD = "a-long-test-password"

# The lockout settings these tests run with (written to a temporary
# config file, so the tests do not depend on config/dashboard.yaml).
TEST_MAX_ATTEMPTS = 3
TEST_LOCKOUT_MINUTES = 15

# Pages that must never be visible without logging in. The last three
# do not exist — they must be refused the same way, so a visitor cannot
# even find out which pages exist.
PRIVATE_PAGES = ["/", "/channels", "/videos", "/video/1", "/analytics",
                 "/docs", "/openapi.json", "/no-such-page"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    """
    Build a fresh dashboard with one user, and a pretend browser for it.

    Args:
        tmp_path: A temporary folder pytest creates for this test.
        monkeypatch: pytest's tool for temporarily changing settings.

    Returns:
        TestClient: A pretend browser that remembers cookies between
                    requests, like a real one.
    """
    # Use an empty database in the temporary folder.
    monkeypatch.setattr(db_module, "DB_PATH", str(tmp_path / "test.db"))

    # Use known lockout settings.
    config_path = tmp_path / "dashboard.yaml"
    config_path.write_text(
        "login:\n"
        f"  max_failed_attempts: {TEST_MAX_ATTEMPTS}\n"
        f"  lockout_minutes: {TEST_LOCKOUT_MINUTES}\n"
        "  session_days: 7\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(auth, "DASHBOARD_CONFIG_PATH", str(config_path))

    # Real password hashing is slow on purpose. The weakest setting
    # keeps the tests fast while running exactly the same code.
    monkeypatch.setattr(auth, "BCRYPT_ROUNDS", 4)

    monkeypatch.setenv("SESSION_SECRET", "test-secret-that-is-long-enough-0123456789")
    monkeypatch.delenv("SESSION_COOKIE_SECURE", raising=False)

    create_tables()
    auth.set_password(TEST_USERNAME, TEST_PASSWORD)
    return TestClient(create_app())


def log_in(client, username=TEST_USERNAME, password=TEST_PASSWORD):
    """
    Submit the login form.

    Args:
        client (TestClient): The pretend browser.
        username (str): The username to type.
        password (str): The password to type.

    Returns:
        The response to the login attempt (redirects are not followed,
        so a successful login shows up as status 303).
    """
    return client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )


def get_form_token(client):
    """
    Read the hidden form token out of a dashboard page.

    Args:
        client (TestClient): A logged-in pretend browser.

    Returns:
        str: The token every form must send back.
    """
    page = client.get("/").text
    match = re.search(r'name="form_token" value="([^"]+)"', page)
    assert match, "The page has no form token."
    return match.group(1)


def make_video():
    """
    Save one drafted video in the test database.

    Returns:
        int: The new video's ID.
    """
    return save_video(
        channel="news",
        story_title="A test story",
        story_url="https://example.com/story",
        script_path=None,
        cost_usd=0.01,
    )


# ---------------------------------------------------------------------------
# Pages are private
# ---------------------------------------------------------------------------

class TestPagesRequireLogin:
    """Nothing may be seen or changed without logging in."""

    def test_every_page_redirects_to_login_when_logged_out(self, client):
        """Each private page sends a logged-out browser to the login page."""
        for path in PRIVATE_PAGES:
            response = client.get(path, follow_redirects=False)
            assert response.status_code == 303, path
            assert response.headers["location"] == "/login", path

    def test_logged_out_page_contains_no_private_data(self, client):
        """Following the redirect shows only the login form, not video data."""
        make_video()
        response = client.get("/")
        assert "A test story" not in response.text
        assert 'name="password"' in response.text

    def test_changing_data_is_refused_when_logged_out(self, client):
        """A logged-out approve request is refused and changes nothing."""
        video_id = make_video()
        response = client.post(f"/video/{video_id}/approve", follow_redirects=False)

        assert response.status_code == 401
        assert get_video_by_id(video_id)["status"] == "drafted"

    def test_login_page_and_stylesheet_are_public(self, client):
        """Only the login page and the stylesheet work without a login."""
        assert client.get("/login").status_code == 200
        assert client.get("/static/style.css").status_code == 200

    def test_only_login_and_static_are_public_paths(self):
        """The list of public paths is exactly the login page and static files."""
        assert is_public_path("/login") is True
        assert is_public_path("/static/style.css") is True
        assert is_public_path("/") is False
        assert is_public_path("/videos") is False
        assert is_public_path("/login/extra") is False
        assert is_public_path("/staticfile") is False

    def test_forged_session_cookie_is_refused(self, client):
        """A made-up cookie does not count as being logged in."""
        client.cookies.set("shortsengine_session", "not-a-real-session")
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 303

    def test_pages_work_after_login(self, client):
        """After logging in, every real page opens."""
        video_id = make_video()
        assert log_in(client).status_code == 303

        for path in ["/", "/channels", "/videos", f"/video/{video_id}", "/analytics"]:
            assert client.get(path, follow_redirects=False).status_code == 200, path

    def test_private_pages_are_not_cached(self, client):
        """Private pages tell the browser not to keep a copy."""
        log_in(client)
        response = client.get("/")
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-frame-options"] == "DENY"


# ---------------------------------------------------------------------------
# Logging in
# ---------------------------------------------------------------------------

class TestLogin:
    """Only the right username and password get in."""

    def test_correct_password_logs_in(self, client):
        """The right details redirect to the dashboard."""
        response = log_in(client)
        assert response.status_code == 303
        assert response.headers["location"] == "/"

    def test_wrong_password_is_refused(self, client):
        """A wrong password shows an error and does not log in."""
        response = log_in(client, password="definitely-wrong-password")
        assert response.status_code == 401
        assert "Wrong username or password" in response.text
        assert client.get("/", follow_redirects=False).status_code == 303

    def test_unknown_username_gets_the_same_message(self, client):
        """A wrong username looks identical to a wrong password."""
        response = log_in(client, username="nobody")
        assert response.status_code == 401
        assert "Wrong username or password" in response.text

    def test_password_is_stored_hashed(self, client):
        """The database holds a bcrypt hash, never the password itself."""
        stored = get_user(TEST_USERNAME)["password_hash"]
        assert stored != TEST_PASSWORD
        assert TEST_PASSWORD not in stored
        assert stored.startswith("$2")

    def test_same_password_hashes_differently_each_time(self):
        """A random salt means two hashes of one password are different."""
        first = auth.hash_password(TEST_PASSWORD)
        second = auth.hash_password(TEST_PASSWORD)
        assert first != second
        assert auth.password_matches(TEST_PASSWORD, first)
        assert auth.password_matches(TEST_PASSWORD, second)

    def test_session_cookie_is_hidden_from_scripts(self, client):
        """The login cookie is HttpOnly and SameSite=Lax."""
        cookie = log_in(client).headers["set-cookie"].lower()
        assert "shortsengine_session=" in cookie
        assert "httponly" in cookie
        assert "samesite=lax" in cookie

    def test_cookie_is_https_only_when_configured(self, client, monkeypatch):
        """With SESSION_COOKIE_SECURE=true the cookie is marked Secure."""
        monkeypatch.setenv("SESSION_COOKIE_SECURE", "true")
        secure_client = TestClient(create_app(), base_url="https://testserver")
        cookie = log_in(secure_client).headers["set-cookie"].lower()
        assert "secure" in cookie

    def test_no_user_means_nobody_can_log_in(self, tmp_path, monkeypatch):
        """With an empty users table, every login fails and a hint is shown."""
        monkeypatch.setattr(db_module, "DB_PATH", str(tmp_path / "empty.db"))
        monkeypatch.setenv("SESSION_SECRET", "test-secret-that-is-long-enough-0123456789")
        empty_client = TestClient(create_app())

        assert "set_password" in empty_client.get("/login").text
        assert log_in(empty_client).status_code == 401

    def test_app_refuses_to_start_without_session_secret(self, monkeypatch):
        """Without SESSION_SECRET the dashboard will not run at all."""
        monkeypatch.delenv("SESSION_SECRET", raising=False)
        with pytest.raises(RuntimeError):
            create_app()

    def test_app_refuses_a_short_session_secret(self, monkeypatch):
        """A short, guessable secret is rejected."""
        monkeypatch.setenv("SESSION_SECRET", "short")
        with pytest.raises(RuntimeError):
            create_app()


# ---------------------------------------------------------------------------
# Limit on failed login attempts
# ---------------------------------------------------------------------------

class TestLockout:
    """Repeated wrong passwords lock the address out for a while."""

    def fail_login(self, client, times):
        """Enter a wrong password the given number of times."""
        for _attempt in range(times):
            log_in(client, password="definitely-wrong-password")

    def test_locked_out_after_too_many_failures(self, client):
        """After the limit, even the correct password is refused."""
        self.fail_login(client, TEST_MAX_ATTEMPTS)

        response = log_in(client)
        assert response.status_code == 429
        assert "Too many failed attempts" in response.text
        assert client.get("/", follow_redirects=False).status_code == 303

    def test_below_the_limit_still_works(self, client):
        """One fewer failure than the limit does not lock the address."""
        self.fail_login(client, TEST_MAX_ATTEMPTS - 1)
        assert log_in(client).status_code == 303

    def test_lock_ends_after_the_waiting_time(self, client, monkeypatch):
        """Once the lockout time has passed, logging in works again."""
        self.fail_login(client, TEST_MAX_ATTEMPTS)
        assert log_in(client).status_code == 429

        # Pretend the clock has moved past the end of the lockout.
        real_now = auth.current_time()

        def later():
            """Stand-in clock: the real time plus the lockout and a minute."""
            return real_now + timedelta(minutes=TEST_LOCKOUT_MINUTES + 1)

        monkeypatch.setattr(auth, "current_time", later)
        assert log_in(client).status_code == 303

    def test_successful_login_resets_the_count(self, client):
        """Failures before a good login do not count towards a later lock."""
        self.fail_login(client, TEST_MAX_ATTEMPTS - 1)
        assert log_in(client).status_code == 303

        self.fail_login(client, TEST_MAX_ATTEMPTS - 1)
        assert log_in(client).status_code == 303


# ---------------------------------------------------------------------------
# Sessions and forms
# ---------------------------------------------------------------------------

class TestSessionsAndForms:
    """Logging out, password changes, and the hidden form token."""

    def test_logout_ends_the_session(self, client):
        """After logging out, pages redirect to the login page again."""
        log_in(client)
        token = get_form_token(client)

        response = client.post("/logout", data={"form_token": token},
                               follow_redirects=False)
        assert response.status_code == 303
        assert client.get("/", follow_redirects=False).status_code == 303

    def test_form_without_token_is_refused(self, client):
        """A form submitted without the hidden token changes nothing."""
        video_id = make_video()
        log_in(client)

        response = client.post(f"/video/{video_id}/approve", follow_redirects=False)
        assert response.status_code == 403
        assert get_video_by_id(video_id)["status"] == "drafted"

    def test_form_with_wrong_token_is_refused(self, client):
        """A made-up token is refused too."""
        video_id = make_video()
        log_in(client)

        response = client.post(f"/video/{video_id}/approve",
                               data={"form_token": "made-up"}, follow_redirects=False)
        assert response.status_code == 403

    def test_form_with_token_works(self, client):
        """With the real token, approving a video works."""
        video_id = make_video()
        log_in(client)
        token = get_form_token(client)

        response = client.post(f"/video/{video_id}/approve",
                               data={"form_token": token}, follow_redirects=False)
        assert response.status_code == 303
        assert get_video_by_id(video_id)["status"] == "approved"

    def test_changing_password_logs_out_old_sessions(self, client):
        """A browser logged in with the old password is signed out."""
        log_in(client)
        assert client.get("/", follow_redirects=False).status_code == 200

        auth.set_password(TEST_USERNAME, "a-brand-new-password")
        assert client.get("/", follow_redirects=False).status_code == 303

        # The old password no longer works; the new one does.
        assert log_in(client).status_code == 401
        assert log_in(client, password="a-brand-new-password").status_code == 303

    def test_page_text_is_escaped(self, client):
        """A story title containing HTML is shown as text, not run."""
        save_video(channel="news", story_title="<script>alert(1)</script>",
                   story_url="https://example.com", script_path=None, cost_usd=0.0)
        log_in(client)
        page = client.get("/").text
        assert "<script>alert(1)</script>" not in page
        assert "&lt;script&gt;" in page


# ---------------------------------------------------------------------------
# Password rules
# ---------------------------------------------------------------------------

class TestPasswordRules:
    """Rules for choosing a new password."""

    def test_short_password_is_rejected(self):
        """Passwords under 12 characters are refused with a reason."""
        assert "at least 12" in auth.check_password_rules("short")

    def test_too_long_password_is_rejected(self):
        """bcrypt cannot use more than 72 bytes, so longer is refused."""
        assert "at most 72" in auth.check_password_rules("x" * 80)

    def test_good_password_is_accepted(self):
        """A 12+ character password passes."""
        assert auth.check_password_rules("a-long-test-password") == ""

    def test_set_password_refuses_a_weak_password(self, client):
        """set_password raises an error instead of saving a weak password."""
        with pytest.raises(ValueError):
            auth.set_password(TEST_USERNAME, "short")
        # The original password still works.
        assert log_in(client).status_code == 303


# ---------------------------------------------------------------------------
# Session secret set-up
# ---------------------------------------------------------------------------

class TestEnsureSessionSecret:
    """Tests for the set-up helper that adds SESSION_SECRET to .env."""

    def test_adds_a_secret_when_missing(self, tmp_path, monkeypatch):
        """A long random secret is appended and existing lines are kept."""
        from src.dashboard.set_password import ensure_session_secret

        monkeypatch.delenv("SESSION_SECRET", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text("ANTHROPIC_API_KEY=keep-me\n", encoding="utf-8")

        assert ensure_session_secret(str(env_file)) == "added"

        text = env_file.read_text(encoding="utf-8")
        assert "ANTHROPIC_API_KEY=keep-me" in text
        new_secret = ""
        for line in text.split("\n"):
            if line.startswith("SESSION_SECRET="):
                new_secret = line.split("=", 1)[1]
        assert len(new_secret) >= 32

    def test_leaves_an_existing_secret_alone(self, tmp_path, monkeypatch):
        """If a good secret is already set, the file is not touched."""
        from src.dashboard.set_password import ensure_session_secret

        monkeypatch.setenv("SESSION_SECRET", "x" * 40)
        env_file = tmp_path / ".env"
        env_file.write_text("OTHER=1\n", encoding="utf-8")

        assert ensure_session_secret(str(env_file)) == "exists"
        assert env_file.read_text(encoding="utf-8") == "OTHER=1\n"

    def test_reports_missing_when_there_is_no_env_file(self, tmp_path, monkeypatch):
        """With no .env file (e.g. inside Docker) nothing is created."""
        from src.dashboard.set_password import ensure_session_secret

        monkeypatch.delenv("SESSION_SECRET", raising=False)
        env_file = tmp_path / ".env"

        assert ensure_session_secret(str(env_file)) == "missing"
        assert not env_file.exists()
