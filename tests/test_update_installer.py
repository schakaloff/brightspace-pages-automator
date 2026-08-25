import sys
from pathlib import Path

sys.path.insert(0, "src")

import update_checker
from update_installer import (
    _removable_update_dir,
    installer_args,
    wait_then_install_script,
)

APP = Path(r"C:\Apps\BrightspacePagesAutomator\BrightspacePagesAutomator.exe")


def _managed_dir(name="Setup.exe") -> Path:
    """A path shaped like the mkdtemp'd directory the updater really uses."""
    return Path(r"C:\Temp") / f"{update_checker.UPDATE_DIR_PREFIX}abc123" / name


def test_installer_args_are_silent():
    args = installer_args()

    assert "/SILENT" in args
    assert "/SUPPRESSMSGBOXES" in args
    assert "/NORESTART" in args


def test_installer_no_longer_asks_setup_to_relaunch():
    """Setup relaunching the app raced the helper doing the same thing, which
    is how two windows could appear after an update. The helper owns it now."""
    assert not any("RELAUNCH" in arg.upper() for arg in installer_args())


def test_wait_script_waits_for_app_before_starting_installer():
    script = wait_then_install_script(Path(r"C:\Temp\Setup File.exe"), 12345)

    assert "BrightspacePagesAutomator-update.log" in script
    assert "BrightspacePagesAutomator-setup.log" in script
    assert "Wait-Process -Id 12345 -Timeout 120" in script
    assert "App exited, continuing" in script
    assert '"%INSTALLER%" /SILENT /SUPPRESSMSGBOXES /NORESTART /LOG="%SETUPLOG%"' in script
    assert "Last update result: Installer exited %SETUP_EXIT%" in script


def test_wait_script_relaunches_app_after_installer_finishes():
    script = wait_then_install_script(Path(r"C:\Temp\Setup.exe"), 12345, APP)

    assert 'tasklist /FI "IMAGENAME eq %APPNAME%"' in script
    assert 'taskkill /F /IM "%APPNAME%"' in script
    assert script.index('taskkill /F /IM "%APPNAME%"') < script.index('"%INSTALLER%"')
    assert 'if exist "%APP%"' in script
    assert 'Restart command start "" /D "%APPDIR%" "%APP%"' in script
    assert 'start "" /D "%APPDIR%" "%APP%"' in script


# ── Setup exit code is reported back ────────────────────────────────────────

def test_wait_script_writes_the_setup_exit_code_to_the_result_file():
    script = wait_then_install_script(Path(r"C:\Temp\Setup.exe"), 12345, APP)

    assert update_checker.UPDATE_RESULT_NAME in script
    assert '> "%RESULT%" echo exit=%SETUP_EXIT%' in script
    # Captured before anything else can clobber ERRORLEVEL.
    assert script.index('set "SETUP_EXIT=%ERRORLEVEL%"') < script.index('> "%RESULT%"')


def test_wait_script_distinguishes_success_from_failure():
    script = wait_then_install_script(Path(r"C:\Temp\Setup.exe"), 12345, APP)

    assert 'if "%SETUP_EXIT%"=="0" (' in script
    assert "Last update result: Update installed" in script
    assert "Update FAILED: Setup did not apply the update" in script
    # The success branch must not claim an install that didn't happen.
    success_at = script.index("Last update result: Update installed")
    failure_at = script.index("Update FAILED")
    assert success_at < failure_at


# ── per-update directory cleanup ────────────────────────────────────────────

def test_managed_update_dir_is_removed_after_the_install():
    installer = _managed_dir()
    script = wait_then_install_script(installer, 12345, APP)

    assert f'set "UPDATEDIR={installer.parent}"' in script
    assert 'del /f /q "%INSTALLER%"' in script
    assert 'rd /s /q "%CLEANUPDIR%"' in script
    # Nothing may follow the rd: it deletes this script, and cmd reads the file
    # as it goes.
    assert script.rstrip("\r\n").endswith('rd /s /q "%CLEANUPDIR%"')


def test_cleanup_happens_after_the_installer_has_finished():
    installer = _managed_dir()
    script = wait_then_install_script(installer, 12345, APP)

    assert script.index('"%INSTALLER%" /SILENT') < script.index('del /f /q "%INSTALLER%"')
    assert script.index('> "%RESULT%"') < script.index('rd /s /q "%CLEANUPDIR%"')
    assert script.index('start "" /D "%APPDIR%" "%APP%"') < script.index('rd /s /q')


def test_unmanaged_directory_is_never_deleted_recursively():
    """A path outside our mkdtemp'd directories must only lose the script."""
    script = wait_then_install_script(Path(r"C:\Users\Nick\Downloads\Setup.exe"), 1, APP)

    assert "rd /s /q" not in script
    assert 'del "%~f0"' in script
    assert 'set "UPDATEDIR="' in script


def test_removable_update_dir_only_matches_our_own_directories():
    assert _removable_update_dir(_managed_dir()) == _managed_dir().parent
    assert _removable_update_dir(Path(r"C:\Temp\Setup.exe")) is None
    assert _removable_update_dir(Path(r"C:\Users\Nick\Downloads\Setup.exe")) is None
