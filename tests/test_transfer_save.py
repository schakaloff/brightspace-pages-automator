"""Windows journal replacement must survive transient locks without truncation."""
import json
import sys
from pathlib import Path

import pytest
sys.path.insert(0, 'src')
import transfer_runner


def denied():
    error = PermissionError(13, 'Access is denied')
    error.winerror = 5
    return error


def test_save_retries_windows_lock_and_preserves_complete_old_journal_until_success(tmp_path, monkeypatch):
    path = tmp_path / 'plan.json'
    path.write_text('{"journal": "old"}', encoding='utf-8')
    replace = transfer_runner.os.replace
    attempts, delays = [], []
    def transient(source, target):
        attempts.append(True)
        if len(attempts) <= 2:
            assert json.loads(path.read_text()) == {'journal': 'old'}
            raise denied()
        return replace(source, target)
    monkeypatch.setattr(transfer_runner.os, 'replace', transient)
    monkeypatch.setattr(transfer_runner.time, 'sleep', delays.append)
    transfer_runner.save_plan({'journal': 'new'}, path)
    assert len(attempts) == 3 and delays == [0.05, 0.1]
    assert json.loads(path.read_text()) == {'journal': 'new'}


def test_persistent_denial_keeps_old_and_recoverable_temp_journal(tmp_path, monkeypatch):
    path = tmp_path / 'plan.json'
    path.write_text('{"journal": "old"}', encoding='utf-8')
    attempts = []
    def permanent(*args):
        attempts.append(True)
        raise denied()
    monkeypatch.setattr(transfer_runner.os, 'replace', permanent)
    monkeypatch.setattr(transfer_runner.time, 'sleep', lambda delay: None)
    with pytest.raises(PermissionError): transfer_runner.save_plan({'journal': 'new'}, path)
    assert len(attempts) == 6
    assert json.loads(path.read_text()) == {'journal': 'old'}
    assert json.loads(path.with_suffix('.json.tmp').read_text()) == {'journal': 'new'}


def test_other_filesystem_errors_are_not_retried(tmp_path, monkeypatch):
    def missing(*args): raise FileNotFoundError('Destination unavailable')
    delays = []
    monkeypatch.setattr(transfer_runner.os, 'replace', missing)
    monkeypatch.setattr(transfer_runner.time, 'sleep', delays.append)
    with pytest.raises(FileNotFoundError): transfer_runner.save_plan({'journal': 'new'}, tmp_path / 'plan.json')
    assert delays == []
