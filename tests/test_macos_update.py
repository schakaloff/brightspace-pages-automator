"""Mac update preparation, handoff, recovery and native filesystem integration."""
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, 'src')
import macos_updater as mac
import update_checker


def bundle(path, marker='new', native=False):
    contents = path / 'Contents'
    (contents / 'MacOS').mkdir(parents=True)
    (contents / 'Info.plist').write_bytes(plistlib.dumps(dict(
        CFBundleIdentifier=mac.BUNDLE_ID, CFBundleExecutable='BrightspacePagesAutomator',
        CFBundlePackageType='APPL', CFBundleName='MTB Writer', CFBundleVersion='1')))
    executable = contents / 'MacOS' / 'BrightspacePagesAutomator'
    if native:
        shutil.copy2('/usr/bin/true', executable)
        subprocess.run(['/usr/bin/codesign', '--force', '--deep', '--sign', '-', str(path)], check=True)
    else:
        executable.write_text('fake executable')
    # A resource marker lets us distinguish old/new bundles after replacement.
    (contents / 'marker').write_text(marker)
    if native:
        subprocess.run(['/usr/bin/codesign', '--force', '--deep', '--sign', '-', str(path)], check=True)
    return path


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(mac.tempfile, 'gettempdir', lambda: str(tmp_path))
    monkeypatch.setattr(mac, 'update_log_path', lambda: tmp_path / 'update.log')
    monkeypatch.setattr(mac, 'update_result_path', lambda: tmp_path / 'result.txt')
    app = bundle(tmp_path / "Apps with spaces & quotes'" / mac.APP_NAME, 'old')
    directory = tmp_path / (mac.UPDATE_DIR_PREFIX + 'download')
    directory.mkdir()
    image = directory / 'update.dmg'
    image.write_bytes(b'verified image')
    return app, image


@pytest.mark.parametrize('failure', [None, 'attach', 'ditto', 'codesign', 'detach', 'wrong_bundle'])
def test_preparation_never_changes_the_installed_app_and_reports_failures(setup, monkeypatch, failure):
    app, image = setup
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        operation = args[1] if args[0].endswith('hdiutil') else Path(args[0]).name
        if operation == failure:
            raise RuntimeError('simulated ' + failure + ' failure')
        if operation == 'attach':
            source = bundle(Path(args[args.index('-mountpoint') + 1]) / mac.APP_NAME)
            if failure == 'wrong_bundle':
                (source / 'Contents' / 'Info.plist').write_bytes(plistlib.dumps(dict(CFBundleIdentifier='other.app')))
        elif operation == 'ditto':
            shutil.copytree(args[1], args[2])
        elif operation == 'detach':
            # Simulate unmount leaving an empty mount point.
            shutil.rmtree(Path(args[2]) / mac.APP_NAME)

    monkeypatch.setattr(mac, '_run', run)
    if failure:
        with pytest.raises(RuntimeError):
            mac.prepare_update(image, app)
        assert not list(app.parent.glob('.' + mac.UPDATE_DIR_PREFIX + '*'))
    else:
        staged = mac.prepare_update(image, app)
        assert (staged / 'Contents' / 'marker').read_text() == 'new'
        assert any(command[0] == '/usr/bin/codesign' for command in calls)
    assert (app / 'Contents' / 'marker').read_text() == 'old'
    assert image.is_file()
    attach = next(command for command in calls if command[1] == 'attach')
    assert '-readonly' in attach and '-nobrowse' in attach


def test_detached_helper_failure_keeps_original_and_cleans_staging(setup, monkeypatch):
    app, image = setup
    staged = bundle(app.parent / ('.' + mac.UPDATE_DIR_PREFIX + 'stage') / app.name)
    monkeypatch.setattr(mac, 'installed_bundle', lambda: app)
    monkeypatch.setattr(mac, 'prepare_update', lambda *_: staged)
    monkeypatch.setattr(mac.subprocess, 'Popen', Mock(side_effect=OSError('launch failed')))
    with pytest.raises(OSError, match='launch failed'):
        mac.launch_update(image)
    assert app.is_dir() and not staged.parent.exists()


def test_helper_is_detached_and_does_not_require_python(setup, monkeypatch):
    app, image = setup
    staged = bundle(app.parent / ('.' + mac.UPDATE_DIR_PREFIX + 'stage') / app.name)
    monkeypatch.setattr(mac, 'installed_bundle', lambda: app)
    monkeypatch.setattr(mac, 'prepare_update', lambda *_: staged)
    popen = Mock()
    monkeypatch.setattr(mac.subprocess, 'Popen', popen)
    mac.launch_update(image)
    command = popen.call_args.args[0]
    assert command[0] == '/bin/sh'
    assert popen.call_args.kwargs['start_new_session'] is True
    assert b'\r\n' not in Path(command[1]).read_bytes()
    assert (app / 'Contents' / 'marker').read_text() == 'old'


def test_non_app_and_readonly_locations_fail_before_quitting(setup, monkeypatch):
    app, image = setup
    with pytest.raises(RuntimeError, match='installed .app'):
        mac.installed_bundle(image)
    monkeypatch.setattr(mac.os, 'access', lambda *_: False)
    with pytest.raises(RuntimeError, match='not writable'):
        mac.installed_bundle(app / 'Contents' / 'MacOS' / 'BrightspacePagesAutomator')


def test_helper_refuses_to_remove_unowned_directories(setup):
    app, image = setup
    with pytest.raises(ValueError, match='not owned'):
        mac.install_script(app, app.parent / 'unowned' / app.name, image.parent, 123)


@pytest.mark.parametrize('machine,expected', [('arm64', 'arm64'), ('x86_64', 'x86_64')])
def test_mac_selects_its_architecture_even_when_the_other_image_is_first(monkeypatch, machine, expected):
    monkeypatch.setattr(update_checker.sys, 'platform', 'darwin')
    monkeypatch.setattr(update_checker.platform, 'machine', lambda: machine)
    assets = [dict(name=f'app-{arch}.dmg') for arch in ('x86_64', 'arm64')]
    assert update_checker._pick_asset(assets)['name'] == f'app-{expected}.dmg'
    other = 'x86_64' if expected == 'arm64' else 'arm64'
    assert update_checker._pick_asset([dict(name=f'app-{other}.dmg')]) is None


@pytest.mark.parametrize('system', ['win32', 'darwin'])
def test_update_button_enters_download_flow_instead_of_opening_github(qtbot, monkeypatch, system):
    import gui_dialogs
    import webbrowser
    monkeypatch.setattr(gui_dialogs.sys, 'platform', system)
    browser = Mock()
    monkeypatch.setattr(webbrowser, 'open', browser)
    threads = []
    dialog = gui_dialogs.UpdateDialog(dict(tag='v1', asset_url='https://example.test/update', asset_name='update.dmg'))
    qtbot.addWidget(dialog)
    monkeypatch.setattr(gui_dialogs.threading, 'Thread', lambda **kwargs: Mock(start=lambda: threads.append(kwargs['target'])))
    dialog._update_btn.click()
    assert len(threads) == 1 and not dialog._update_btn.isEnabled()
    browser.assert_not_called()


@pytest.mark.parametrize('system', ['win32', 'darwin'])
def test_missing_asset_keeps_app_open_with_an_explanation(qtbot, monkeypatch, system):
    import gui_dialogs
    monkeypatch.setattr(gui_dialogs.sys, 'platform', system)
    dialog = gui_dialogs.UpdateDialog(dict(tag='v1'))
    qtbot.addWidget(dialog)
    assert not dialog._update_btn.isEnabled()
    assert 'compatible installer' in dialog._status_lbl.text()
    assert not dialog.install_started()


def test_packaged_preferences_migrate_outside_bundle_and_preserve_saved_values(tmp_path, monkeypatch):
    import config
    legacy = tmp_path / 'bundle' / 'user_config.json'
    legacy.parent.mkdir()
    legacy.write_text('{"theme":"light","h5p_moodle_url":"saved course"}')
    userdata = tmp_path / 'userdata'
    userdata.mkdir()
    monkeypatch.setattr(config, 'USERDATA_DIR', userdata)
    monkeypatch.setattr(config.sys, 'frozen', True, raising=False)
    target = config.gui_config_path(legacy)
    assert target.parent == userdata and 'saved course' in target.read_text()
    target.write_text('{"theme":"dark"}')
    assert config.gui_config_path(legacy).read_text() == '{"theme":"dark"}'
    monkeypatch.setattr(config.sys, 'frozen', False)
    assert config.gui_config_path(legacy) == legacy


@pytest.mark.skipif(sys.platform != 'darwin', reason='Exercises native hdiutil, ditto and codesign on both Mac CI runners')
@pytest.mark.parametrize('fail_relaunch', [False, True])
def test_native_dmg_replacement_and_rollback(tmp_path, monkeypatch, fail_relaunch):
    """Build a tiny signed app/image and execute the real detached shell logic."""
    app = bundle(tmp_path / 'Applications' / mac.APP_NAME, 'old', native=True)
    source = bundle(tmp_path / 'image-source' / mac.APP_NAME, 'new', native=True)
    directory = tmp_path / (mac.UPDATE_DIR_PREFIX + 'native')
    directory.mkdir()
    image = directory / 'update.dmg'
    subprocess.run(['/usr/bin/hdiutil', 'create', '-srcfolder', str(source),
                    '-ov', '-format', 'UDZO', str(image)], check=True, capture_output=True)
    monkeypatch.setattr(mac, 'update_result_path', lambda: tmp_path / 'result')
    monkeypatch.setattr(mac, 'update_log_path', lambda: tmp_path / 'log')
    staged = mac.prepare_update(image, app)
    # Stub only LaunchServices to avoid opening GUI apps in CI. Native image,
    # signature, copy, rename, result reporting and rollback remain real.
    script = mac.install_script(app, staged, directory, 99999999)
    script = script.replace('/usr/bin/open -a "$APP"', 'false' if fail_relaunch else 'true')
    helper = directory / 'install.sh'
    helper.write_text(script)
    subprocess.run(['/bin/sh', str(helper)], timeout=30, check=not fail_relaunch)
    assert (app / 'Contents' / 'marker').read_text() == ('old' if fail_relaunch else 'new')
    assert (tmp_path / 'result').read_text().strip() == ('exit=1' if fail_relaunch else 'exit=0')
    assert not directory.exists() and not staged.parent.exists()
