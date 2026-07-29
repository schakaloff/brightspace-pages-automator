import sys
from pathlib import Path

sys.path.insert(0, "src")

from config import RELAUNCH_SWITCH
from update_installer import installer_args, wait_then_install_script


def test_installer_args_are_silent():
    args = installer_args()

    assert "/SILENT" in args
    assert "/SUPPRESSMSGBOXES" in args
    assert "/NORESTART" in args
    assert RELAUNCH_SWITCH in args


def test_wait_script_waits_for_app_before_starting_installer():
    script = wait_then_install_script(Path(r"C:\Temp\Setup File.exe"), 12345)

    assert "BrightspacePagesAutomator-update.log" in script
    assert 'tasklist /FI "PID eq 12345"' in script
    assert '"%INSTALLER%" /SILENT /SUPPRESSMSGBOXES /NORESTART /RELAUNCH=yes /LOG="%SETUPLOG%"' in script


def test_wait_script_relaunches_app_after_installer_finishes():
    script = wait_then_install_script(
        Path(r"C:\Temp\Setup.exe"),
        12345,
        Path(r"C:\Apps\BrightspacePagesAutomator\BrightspacePagesAutomator.exe"),
    )

    assert 'tasklist /FI "IMAGENAME eq %APPNAME%"' in script
    assert 'if exist "%APP%"' in script
    assert 'start "" /D "%APPDIR%" "%APP%"' in script
