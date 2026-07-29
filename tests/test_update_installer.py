import sys
from pathlib import Path

sys.path.insert(0, "src")

from config import RELAUNCH_SWITCH
from update_installer import installer_args, wait_then_install_script


def test_installer_args_include_silent_relaunch():
    args = installer_args()

    assert "/SILENT" in args
    assert "/SUPPRESSMSGBOXES" in args
    assert "/NORESTART" in args
    assert RELAUNCH_SWITCH in args


def test_wait_script_waits_for_app_before_starting_installer():
    script = wait_then_install_script(Path(r"C:\Temp\Setup File.exe"), 12345)

    assert "Wait-Process -Id 12345" in script
    assert "Start-Sleep -Milliseconds 400" in script
    assert "Start-Process -FilePath 'C:\\Temp\\Setup File.exe'" in script
    assert "-WindowStyle Hidden" in script
    assert RELAUNCH_SWITCH in script
