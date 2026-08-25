"""Why an update check failed must survive as far as the user.

Every failure used to collapse into "check your internet connection", with
nothing in the log to tell a rate limit apart from a certificate failure.
"""
import io
import json
import socket
import ssl
import sys
import urllib.error

import pytest

sys.path.insert(0, "src")

import update_checker
from update_checker import UpdateFetchError


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(update_checker.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(update_checker, "get_my_build_tag", lambda: "v0.8.5-102")


def _raise_from_urlopen(monkeypatch, exc):
    def _urlopen(req, **kwargs):
        raise exc
    monkeypatch.setattr(update_checker.urllib.request, "urlopen", _urlopen)


def _serve(monkeypatch, body: bytes):
    class _Resp:
        def read(self):
            return body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(update_checker.urllib.request, "urlopen",
                        lambda req, **kwargs: _Resp())


def _http_error(code, reason="Forbidden", headers=None):
    return urllib.error.HTTPError(
        update_checker.API_URL, code, reason, headers or {}, io.BytesIO(b"{}")
    )


def _fetch_error(monkeypatch, exc) -> UpdateFetchError:
    _raise_from_urlopen(monkeypatch, exc)
    with pytest.raises(UpdateFetchError) as e:
        update_checker._fetch_latest_release()
    return e.value


# ── classification ──────────────────────────────────────────────────────────

def test_rate_limited_403_is_classified_as_a_rate_limit(monkeypatch):
    error = _fetch_error(monkeypatch, _http_error(
        403, "rate limit exceeded",
        {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1700000000"},
    ))

    assert error.kind == "rate_limit"
    assert error.status == 403
    assert "rate limit exceeded" in error.detail
    assert "X-RateLimit-Remaining=0" in error.detail
    # It must not send the user off to check their connection...
    assert "check your internet connection" not in error.user_message.lower()
    # ...it should rule that out for them instead.
    assert "Your internet connection is fine" in error.user_message


def test_rate_limit_names_the_reset_time(monkeypatch):
    error = _fetch_error(monkeypatch, _http_error(
        403, "rate limit exceeded",
        {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1700000000"},
    ))

    expected = update_checker.datetime.fromtimestamp(1700000000).strftime("%H:%M:%S")
    assert expected in error.user_message
    assert expected in error.detail


def test_rate_limit_without_a_usable_reset_header_still_works(monkeypatch):
    error = _fetch_error(monkeypatch, _http_error(
        403, "rate limit exceeded",
        {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "not-a-number"},
    ))

    assert error.kind == "rate_limit"
    assert "Try again later" in error.user_message


def test_429_with_no_quota_left_is_also_a_rate_limit(monkeypatch):
    error = _fetch_error(monkeypatch, _http_error(
        429, "Too Many Requests", {"X-RateLimit-Remaining": "0"}))

    assert error.kind == "rate_limit"
    assert error.status == 429


def test_a_403_with_quota_remaining_is_a_plain_http_failure(monkeypatch):
    """403 alone doesn't mean rate limiting — it can be a blocked request."""
    error = _fetch_error(monkeypatch, _http_error(
        403, "Forbidden", {"X-RateLimit-Remaining": "57"}))

    assert error.kind == "http"
    assert error.status == 403
    assert "HTTP 403" in error.user_message


def test_server_error_keeps_its_status(monkeypatch):
    error = _fetch_error(monkeypatch, _http_error(500, "Internal Server Error"))

    assert error.kind == "http"
    assert error.status == 500
    assert error.detail == "HTTP 500: Internal Server Error"


def test_ssl_verification_failure_is_not_reported_as_a_network_problem(monkeypatch):
    reason = ssl.SSLCertVerificationError(
        1, "[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate"
    )
    reason.reason = "CERTIFICATE_VERIFY_FAILED"
    error = _fetch_error(monkeypatch, urllib.error.URLError(reason))

    assert error.kind == "ssl"
    assert error.status is None
    assert "CERTIFICATE_VERIFY_FAILED" in error.detail
    assert "CERTIFICATE_VERIFY_FAILED" in error.user_message
    assert "certificate problem" in error.user_message


def test_timeout_wrapped_in_urlerror_is_a_timeout(monkeypatch):
    error = _fetch_error(monkeypatch, urllib.error.URLError(socket.timeout("timed out")))

    assert error.kind == "timeout"
    assert f"{update_checker.API_TIMEOUT_SECONDS}s" in error.detail
    assert "timed out" in error.user_message


def test_bare_timeout_is_a_timeout(monkeypatch):
    error = _fetch_error(monkeypatch, TimeoutError("timed out"))

    assert error.kind == "timeout"


def test_dns_failure_is_a_network_problem(monkeypatch):
    error = _fetch_error(monkeypatch, urllib.error.URLError(
        socket.gaierror(8, "nodename nor servname provided")))

    assert error.kind == "network"
    assert "gaierror" in error.detail
    # Here the connection wording is the correct advice.
    assert "internet connection" in error.user_message


def test_connection_refused_without_a_wrapper_is_a_network_problem(monkeypatch):
    error = _fetch_error(monkeypatch, ConnectionResetError("connection reset by peer"))

    assert error.kind == "network"
    assert "ConnectionResetError" in error.detail


def test_malformed_json_is_reported_as_an_unreadable_reply(monkeypatch):
    _serve(monkeypatch, b"<html>Sign in to the campus network</html>")

    with pytest.raises(UpdateFetchError) as e:
        update_checker._fetch_latest_release()

    assert e.value.kind == "malformed"
    assert "JSONDecodeError" in e.value.detail
    assert "web filter" in e.value.user_message


def test_undecodable_body_is_also_malformed(monkeypatch):
    _serve(monkeypatch, b"\xff\xfe\x00not utf-8")

    with pytest.raises(UpdateFetchError) as e:
        update_checker._fetch_latest_release()

    assert e.value.kind == "malformed"


def test_a_good_response_still_parses(monkeypatch):
    _serve(monkeypatch, json.dumps({"tag_name": "v0.8.5-103"}).encode())

    assert update_checker._fetch_latest_release()["tag_name"] == "v0.8.5-103"


# ── the reason reaches diagnostics and the log ──────────────────────────────

@pytest.mark.parametrize("exc,kind,status", [
    (_http_error(403, "rate limit exceeded", {"X-RateLimit-Remaining": "0"}), "rate_limit", 403),
    (urllib.error.URLError(ssl.SSLError("CERTIFICATE_VERIFY_FAILED")), "ssl", ""),
    (urllib.error.URLError(socket.timeout("timed out")), "timeout", ""),
    (urllib.error.URLError(socket.gaierror(8, "no name")), "network", ""),
])
def test_check_for_update_records_the_reason(exc, kind, status, monkeypatch):
    _raise_from_urlopen(monkeypatch, exc)

    assert update_checker.check_for_update() is None

    diagnostics = update_checker.get_update_diagnostics()
    assert diagnostics["last_update_result"] == "Failed"
    assert diagnostics["fetch_error_kind"] == kind
    assert diagnostics["fetch_http_status"] == status
    assert diagnostics["fetch_user_message"]
    assert diagnostics["last_update_detail"] not in ("", "Could not fetch latest GitHub release.")


def test_the_updater_log_names_the_failure(monkeypatch):
    _raise_from_urlopen(monkeypatch, _http_error(
        403, "rate limit exceeded", {"X-RateLimit-Remaining": "0"}))

    update_checker.check_for_update()

    log = update_checker.update_log_path().read_text(encoding="utf-8")
    assert "kind=rate_limit" in log
    assert "status=403" in log
    assert update_checker.API_URL in log


def test_a_later_success_clears_the_error_fields(monkeypatch):
    _raise_from_urlopen(monkeypatch, _http_error(500, "Internal Server Error"))
    update_checker.check_for_update()
    assert update_checker.get_update_diagnostics()["fetch_error_kind"] == "http"

    monkeypatch.setattr(update_checker, "_fetch_latest_release",
                        lambda: {"tag_name": "v0.8.5-102", "assets": []})
    update_checker.check_for_update()

    diagnostics = update_checker.get_update_diagnostics()
    assert diagnostics["last_update_result"] == "Up to date"
    assert diagnostics["fetch_error_kind"] == ""
    assert diagnostics["fetch_user_message"] == ""


def test_a_stubbed_none_still_reports_the_generic_failure(monkeypatch):
    """The old contract: anything returning None means an unexplained failure."""
    monkeypatch.setattr(update_checker, "_fetch_latest_release", lambda: None)

    assert update_checker.check_for_update() is None

    diagnostics = update_checker.get_update_diagnostics()
    assert diagnostics["last_update_detail"] == "Could not fetch latest GitHub release."
    assert diagnostics["fetch_error_kind"] == ""


def test_forced_check_also_records_the_reason(monkeypatch):
    """The badge's manual check is where the misleading dialog appeared."""
    _raise_from_urlopen(monkeypatch, _http_error(
        403, "rate limit exceeded", {"X-RateLimit-Remaining": "0"}))

    assert update_checker.check_for_update(force_install=True) is None
    assert update_checker.get_update_diagnostics()["fetch_error_kind"] == "rate_limit"
