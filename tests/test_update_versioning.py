"""Build-tag ordering: which releases are allowed to be offered as updates."""
import sys

import pytest

sys.path.insert(0, "src")

import update_checker


def _release(tag, assets=None):
    return {
        "tag_name": tag,
        "body": "notes",
        "html_url": "https://example.test/release",
        "assets": assets if assets is not None else [
            {
                "name": update_checker.LATEST_WINDOWS_INSTALLER,
                "browser_download_url": "https://example.test/latest.exe",
            },
            {
                "name": update_checker.CHECKSUM_ASSET_NAME,
                "browser_download_url": "https://example.test/SHA256SUMS.txt",
            },
        ],
    }


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Keep every test's update state and logs out of the real temp dir."""
    monkeypatch.setattr(update_checker.tempfile, "gettempdir", lambda: str(tmp_path))


# ── parsing ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("tag,expected", [
    ("v0.8.5-98", (0, 8, 5, 98)),
    ("v0.8.5-99", (0, 8, 5, 99)),
    ("0.8.5-99", (0, 8, 5, 99)),
    ("v0.8.6-1", (0, 8, 6, 1)),
    ("v0.8.5", (0, 8, 5, 0)),
    ("v0.8", (0, 8, 0, 0)),
    ("v10.20.30-40", (10, 20, 30, 40)),
])
def test_parse_build_tag_orders_version_and_run_number(tag, expected):
    assert update_checker.parse_build_tag(tag) == expected


@pytest.mark.parametrize("tag", [
    None, "", "nightly", "v1.0-beta", "latest", "v", "v1.2.3.4.5", "vX.Y.Z",
])
def test_parse_build_tag_returns_none_for_malformed_tags(tag):
    assert update_checker.parse_build_tag(tag) is None


def test_run_number_orders_within_the_same_version():
    assert update_checker.is_newer_build("v0.8.5-99", "v0.8.5-98") is True
    assert update_checker.is_newer_build("v0.8.5-98", "v0.8.5-99") is False
    assert update_checker.is_newer_build("v0.8.5-99", "v0.8.5-99") is False


def test_version_bump_outranks_run_number():
    assert update_checker.is_newer_build("v0.8.6-1", "v0.8.5-99") is True
    assert update_checker.is_newer_build("v0.8.5-99", "v0.8.6-1") is False


def test_unparseable_tags_are_not_comparable():
    assert update_checker.is_newer_build("nightly", "v0.8.5-99") is None
    assert update_checker.is_newer_build("v0.8.5-99", "nightly") is None


# ── what check_for_update() does with that ordering ─────────────────────────

def _check(monkeypatch, installed, latest, **kwargs):
    monkeypatch.setattr(update_checker, "get_my_build_tag", lambda: installed)
    monkeypatch.setattr(update_checker, "_fetch_latest_release", lambda: _release(latest))
    return update_checker.check_for_update(**kwargs)


def test_newer_run_number_is_offered(monkeypatch):
    info = _check(monkeypatch, "v0.8.5-98", "v0.8.5-99")

    assert info is not None
    assert info["tag"] == "v0.8.5-99"
    assert info["same_build"] is False


def test_older_release_is_not_offered(monkeypatch):
    """The whole point: /releases/latest can go backwards when a release is
    deleted, and a downgrade must never be presented as an update."""
    assert _check(monkeypatch, "v0.8.5-99", "v0.8.5-98") is None

    diagnostics = update_checker.get_update_diagnostics()
    assert diagnostics["last_update_result"] == "Up to date"
    assert "not newer" in diagnostics["last_update_detail"]


def test_identical_tag_is_not_offered(monkeypatch):
    assert _check(monkeypatch, "v0.8.5-99", "v0.8.5-99") is None


def test_version_bump_is_offered(monkeypatch):
    info = _check(monkeypatch, "v0.8.5-99", "v0.8.6-1")

    assert info is not None
    assert info["tag"] == "v0.8.6-1"


def test_malformed_latest_tag_falls_back_to_inequality(monkeypatch):
    """Legacy/hand-made tags can't be ordered. Offering the update is the old
    behaviour and is preferable to crashing or going permanently silent."""
    info = _check(monkeypatch, "v0.8.5-99", "nightly")

    assert info is not None
    assert info["tag"] == "nightly"


def test_malformed_installed_tag_still_matches_itself(monkeypatch):
    assert _check(monkeypatch, "nightly", "nightly") is None


def test_force_install_offers_the_current_build_as_same_build(monkeypatch):
    info = _check(monkeypatch, "v0.8.5-99", "v0.8.5-99", force_install=True)

    assert info is not None
    assert info["same_build"] is True
    assert info["force_install"] is True
    assert update_checker.get_update_diagnostics()["last_update_result"] == "Reinstall available"


def test_force_install_on_an_older_release_is_not_flagged_as_same_build(monkeypatch):
    info = _check(monkeypatch, "v0.8.5-99", "v0.8.5-98", force_install=True)

    assert info is not None
    assert info["same_build"] is False
