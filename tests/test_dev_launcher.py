"""Exercise the watcher with a disposable app, without opening the real GUI."""
import importlib.util
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


DEV_SCRIPT = Path(__file__).resolve().parents[1] / "dev.py"
spec = importlib.util.spec_from_file_location("dev_launcher", DEV_SCRIPT)
dev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dev)


def wait_until(predicate, proc, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        if proc.poll() is not None:
            pytest.fail(f"Watcher exited early: {proc.communicate()[0]}")
        time.sleep(0.05)
    pytest.fail("Disposable app did not start/restart in time")


def close_app(root, exitcode=0):
    # Publish the request only after its contents have been written completely.
    pending = root / "close-pending.txt"
    pending.write_text(str(exitcode))
    pending.replace(root / "close.txt")


@pytest.fixture
def watched_app(tmp_path):
    shutil.copyfile(DEV_SCRIPT, tmp_path / "dev.py")
    gui = tmp_path / "gui.py"
    gui.write_text(
        "import os, sys, time\n"
        "from pathlib import Path\n"
        "with Path('launches.txt').open('a') as log:\n"
        "    log.write(str(os.getpid()) + '\\n')\n"
        "while not Path('close.txt').exists():\n"
        "    time.sleep(0.05)\n"
        "sys.exit(int(Path('close.txt').read_text()))\n",
        encoding="utf-8",
    )
    proc = subprocess.Popen(
        [sys.executable, "-u", str(tmp_path / "dev.py")],
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        wait_until(lambda: (tmp_path / "launches.txt").exists(), proc)
        yield tmp_path, proc
    finally:
        close_app(tmp_path)
        dev.stop_app(proc)
        if proc.stdout is not None:
            proc.stdout.close()


@pytest.mark.parametrize("exitcode", [0, 7])
def test_app_exit_stops_watcher_and_preserves_exit_code(watched_app, exitcode):
    root, proc = watched_app
    close_app(root, exitcode)

    output, _ = proc.communicate(timeout=8)

    assert proc.returncode == exitcode, output
    assert "Stopped watching." in output
    assert len((root / "launches.txt").read_text().splitlines()) == 1


def test_saved_edit_restarts_app_then_closing_it_stops_watcher(watched_app):
    root, proc = watched_app
    gui = root / "gui.py"
    first_pid = (root / "launches.txt").read_text().strip()
    original_mtime = gui.stat().st_mtime
    with gui.open("a") as script:
        script.write("\n# A saved code edit.\n")
    os.utime(gui, (original_mtime + 2, original_mtime + 2))
    wait_until(
        lambda: len((root / "launches.txt").read_text().splitlines()) == 2,
        proc,
    )
    assert (root / "launches.txt").read_text().splitlines()[1] != first_pid
    close_app(root)

    output, _ = proc.communicate(timeout=8)

    assert proc.returncode == 0
    assert "restarting gui.py" in output
    assert "Stopped watching." in output


def test_ctrl_c_cleans_up_app(monkeypatch, watched_app):
    _, proc = watched_app
    # Use a real owned process so the cleanup includes Windows venv children.
    original_wait = proc.wait

    def interrupted_wait(*args, **kwargs):
        monkeypatch.setattr(proc, "wait", original_wait)
        raise KeyboardInterrupt

    monkeypatch.setattr(proc, "wait", interrupted_wait)
    monkeypatch.setattr(dev, "get_mtimes", lambda: {})
    monkeypatch.setattr(dev, "subprocess", SimpleNamespace(
        Popen=lambda *args, **kwargs: proc,
        TimeoutExpired=subprocess.TimeoutExpired,
        run=subprocess.run,
        DEVNULL=subprocess.DEVNULL,
    ))

    assert dev.main() == 0
    assert proc.poll() is not None
    # Descendants inherit this pipe, so this also catches an orphaned app.
    proc.communicate(timeout=8)
