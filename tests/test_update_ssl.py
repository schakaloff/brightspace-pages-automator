"""The updater's TLS trust store.

A frozen macOS build has no usable CA store of its own, so every updater
request failed with CERTIFICATE_VERIFY_FAILED. certifi supplies the bundle —
without ever relaxing verification.
"""
import io
import json
import ssl
import sys

import certifi
import pytest

sys.path.insert(0, "src")

import update_checker


@pytest.fixture(autouse=True)
def _fresh_context(tmp_path, monkeypatch):
    # The context is cached for reuse; each test needs to observe its creation.
    update_checker.ssl_context.cache_clear()
    monkeypatch.setattr(update_checker.tempfile, "gettempdir", lambda: str(tmp_path))
    yield
    update_checker.ssl_context.cache_clear()


def _capture_urlopen(monkeypatch, body=b"{}"):
    """Record the kwargs urlopen is called with, and serve a canned body."""
    seen = {}

    class _Resp:
        def __init__(self):
            self.headers = {"Content-Length": str(len(body))}
            self._data = io.BytesIO(body)

        def read(self, *a):
            return self._data.read(*a)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _urlopen(req, **kwargs):
        seen.update(kwargs)
        seen["url"] = getattr(req, "full_url", req)
        return _Resp()

    monkeypatch.setattr(update_checker.urllib.request, "urlopen", _urlopen)
    return seen


# ── the certifi bundle is used ──────────────────────────────────────────────

def test_context_loads_the_certifi_bundle(monkeypatch):
    loaded = {}
    real = ssl.SSLContext.load_verify_locations

    def _spy(self, cafile=None, capath=None, cadata=None):
        loaded["cafile"] = cafile
        return real(self, cafile=cafile, capath=capath, cadata=cadata)

    monkeypatch.setattr(ssl.SSLContext, "load_verify_locations", _spy)

    update_checker.ssl_context()

    assert loaded["cafile"] == certifi.where()


def test_context_actually_contains_certificates():
    """Proves the bundle parsed, not merely that a path was passed."""
    context = update_checker.ssl_context()

    assert len(context.get_ca_certs()) > 0


def test_certifi_is_added_to_the_platform_store_not_substituted():
    """cafile= on create_default_context would suppress the system store, which
    on a managed Windows machine drops the enterprise roots it needs."""
    system_only = ssl.create_default_context()
    combined = update_checker.ssl_context()

    assert len(combined.get_ca_certs()) >= len(system_only.get_ca_certs())
    certifi_only = ssl.create_default_context(cafile=certifi.where())
    assert len(combined.get_ca_certs()) >= len(certifi_only.get_ca_certs())


def test_missing_certifi_still_yields_a_verified_context(monkeypatch):
    """A broken bundle must fall back to system trust, never to no trust."""
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def _no_certifi(name, *args, **kwargs):
        if name == "certifi":
            raise ImportError("no certifi in this build")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _no_certifi)

    context = update_checker.ssl_context()

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


# ── verification stays on ───────────────────────────────────────────────────

def test_verification_is_required():
    context = update_checker.ssl_context()

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.verify_mode != ssl.CERT_NONE


def test_hostname_checking_is_on():
    assert update_checker.ssl_context().check_hostname is True


def test_insecure_protocols_are_not_enabled():
    context = update_checker.ssl_context()

    # OP_NO_SSLv2 is 0 on modern OpenSSL — SSLv2 is gone, not merely disabled —
    # so the floor is what's worth asserting.
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
    assert context.options & ssl.OP_NO_SSLv3


def test_no_unverified_context_anywhere_in_the_updater():
    """Guards against the tempting one-line "fix"."""
    from pathlib import Path

    source = Path(update_checker.__file__).read_text(encoding="utf-8")

    assert "_create_unverified_context" not in source
    assert "CERT_NONE" not in source
    assert "check_hostname = False" not in source


# ── every updater request uses it ───────────────────────────────────────────

def test_release_check_passes_the_context(monkeypatch):
    seen = _capture_urlopen(monkeypatch, json.dumps({"tag_name": "v0.8.5-103"}).encode())

    update_checker._fetch_latest_release()

    assert seen["context"] is update_checker.ssl_context()


def test_checksum_download_passes_the_context(monkeypatch):
    digest = "a" * 64
    seen = _capture_urlopen(monkeypatch, f"{digest}  Setup.exe".encode())

    update_checker.fetch_expected_sha256("https://example.test/sums", "Setup.exe")

    assert seen["context"] is update_checker.ssl_context()


def test_installer_download_passes_the_context(monkeypatch, tmp_path):
    seen = _capture_urlopen(monkeypatch, b"installer bytes")

    update_checker.download_asset("https://example.test/latest.exe", tmp_path / "Setup.exe")

    assert seen["context"] is update_checker.ssl_context()


def test_the_context_is_reused_rather_than_rebuilt():
    assert update_checker.ssl_context() is update_checker.ssl_context()


# ── normal behaviour is unchanged ───────────────────────────────────────────

def test_a_good_response_still_parses(monkeypatch):
    _capture_urlopen(monkeypatch, json.dumps({"tag_name": "v0.8.5-103"}).encode())

    assert update_checker._fetch_latest_release() == {"tag_name": "v0.8.5-103"}


def test_the_request_url_and_timeout_are_unchanged(monkeypatch):
    seen = _capture_urlopen(monkeypatch, b"{}")

    update_checker._fetch_latest_release()

    assert seen["url"] == update_checker.API_URL
    assert seen["timeout"] == update_checker.API_TIMEOUT_SECONDS


def test_a_download_still_writes_the_file(monkeypatch, tmp_path):
    _capture_urlopen(monkeypatch, b"installer bytes")
    dest = tmp_path / "Setup.exe"

    update_checker.download_asset("https://example.test/latest.exe", dest)

    assert dest.read_bytes() == b"installer bytes"


def test_ssl_failures_are_still_classified_as_ssl(monkeypatch):
    """The error path added earlier keeps working with a context in play."""
    reason = ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] bad chain")
    reason.reason = "CERTIFICATE_VERIFY_FAILED"

    def _urlopen(req, **kwargs):
        raise update_checker.urllib.error.URLError(reason)

    monkeypatch.setattr(update_checker.urllib.request, "urlopen", _urlopen)

    with pytest.raises(update_checker.UpdateFetchError) as e:
        update_checker._fetch_latest_release()
    assert e.value.kind == "ssl"
