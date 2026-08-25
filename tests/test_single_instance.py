"""The mutex has to *detect* a running copy, not merely exist."""
import sys
import uuid

import pytest

sys.path.insert(0, "src")

from single_instance import claim_single_instance

windows_only = pytest.mark.skipif(
    sys.platform != "win32", reason="named mutexes are a Windows API"
)


@windows_only
def test_first_claim_reports_no_other_instance():
    handle, already_running = claim_single_instance(f"BPA.test.{uuid.uuid4().hex}")

    assert handle
    assert already_running is False


@windows_only
def test_second_claim_of_the_same_name_reports_already_running():
    """CreateMutexW succeeds for the second caller too — only ERROR_ALREADY_EXISTS
    tells them apart, which is what the app was previously failing to check."""
    name = f"BPA.test.{uuid.uuid4().hex}"
    first_handle, first_flag = claim_single_instance(name)
    second_handle, second_flag = claim_single_instance(name)

    assert first_flag is False
    assert second_handle  # the call still succeeds...
    assert second_flag is True  # ...but the app now knows to bow out


@windows_only
def test_different_names_do_not_collide():
    claim_single_instance(f"BPA.test.{uuid.uuid4().hex}")
    _, already_running = claim_single_instance(f"BPA.test.{uuid.uuid4().hex}")

    assert already_running is False


def test_guard_never_blocks_startup_when_the_api_is_unavailable(monkeypatch):
    """A broken guard must fail open: worst case is the old behaviour."""
    monkeypatch.setattr(sys, "platform", "darwin")

    assert claim_single_instance("anything") == (None, False)
