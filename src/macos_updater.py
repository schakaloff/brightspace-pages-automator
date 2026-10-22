"""Stage a verified Mac app before quitting; replace it using native OS tools."""
from __future__ import annotations

import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from update_checker import (UPDATE_DIR_PREFIX, log_update_event, update_log_path,
                            update_result_path)

APP_NAME = 'Brightspace Pages Automator.app'
BUNDLE_ID = 'com.brightspacepagesautomator.app'


def installed_bundle(executable: Path | None = None) -> Path:
    executable = Path(executable or sys.executable).resolve()
    app = next((path for path in executable.parents if path.suffix == '.app'), None)
    if app is None:
        raise RuntimeError('Automatic Mac updates require the installed .app, not a source checkout.')
    if 'AppTranslocation' in app.parts or app.parts[1:2] == ('Volumes',):
        raise RuntimeError('Move the app to Applications (or your home Applications folder), '
                           'open that copy, then try Update again.')
    if not os.access(app.parent, os.W_OK):
        raise RuntimeError('This app location is not writable. Move the app to your home '
                           'Applications folder and open that copy to update without administrator access.')
    return app


def _validate_bundle(app: Path) -> None:
    with (app / 'Contents' / 'Info.plist').open('rb') as file:
        info = plistlib.load(file)
    if info.get('CFBundleIdentifier') != BUNDLE_ID:
        raise RuntimeError('The update does not contain the expected MTB Writer app.')
    executable = info.get('CFBundleExecutable', '')
    if not executable or Path(executable).name != executable:
        raise RuntimeError('The Mac app has an invalid executable name.')
    if not (app / 'Contents' / 'MacOS' / executable).is_file():
        raise RuntimeError('The Mac app executable is missing.')


def _run(args: list[str], timeout=120) -> None:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                            stdin=subprocess.DEVNULL)
    if result.returncode:
        raise RuntimeError(f'{Path(args[0]).name} failed: {result.stderr.strip()[-500:]}')


def prepare_update(image: Path, app: Path) -> Path:
    """Mount read-only, copy beside the installed app, then verify its signature.

    No installed files change here. Copying before quitting surfaces permission,
    image and signature failures in the still-running update dialog.
    """
    _validate_bundle(app)
    stage = Path(tempfile.mkdtemp(prefix='.' + UPDATE_DIR_PREFIX, dir=app.parent))
    mount_root = Path(tempfile.mkdtemp(prefix=UPDATE_DIR_PREFIX + 'mount-'))
    mount = mount_root / 'volume'
    mount.mkdir()
    mounted = False
    try:
        try:
            _run(['/usr/bin/hdiutil', 'attach', '-readonly', '-nobrowse',
                  '-mountpoint', str(mount), str(image)])
            mounted = True
            source = mount / APP_NAME
            _validate_bundle(source)
            staged = stage / app.name
            _run(['/usr/bin/ditto', str(source), str(staged)])
            _validate_bundle(staged)
            _run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(staged)])
        finally:
            if mounted:
                _run(['/usr/bin/hdiutil', 'detach', str(mount)], timeout=30)
            # Never recursively remove a mount point, even after attach failure.
            if mount.exists():
                mount.rmdir()
            mount_root.rmdir()
        return staged
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def install_script(app: Path, staged: Path, image_dir: Path, pid: int) -> str:
    """A detached shell needs no separately installed Python on the user's Mac."""
    app, staged, image_dir = app.resolve(), staged.resolve(), image_dir.resolve()
    stage = staged.parent
    if (stage.parent != app.parent or not stage.name.startswith('.' + UPDATE_DIR_PREFIX)
            or staged.name != app.name or not image_dir.name.startswith(UPDATE_DIR_PREFIX)):
        raise ValueError('Mac update staging paths are not owned by this update.')
    values = dict(APP=app, NEW=staged, STAGE=stage, BACKUP=stage / 'previous.app',
                  DOWNLOAD=image_dir, LOG=update_log_path(), RESULT=update_result_path())
    assignments = '\n'.join(f'{key}={shlex.quote(str(value))}' for key, value in values.items())
    return '#!/bin/sh\n' + assignments + f'\nPID={int(pid)}\n' + r'''
exec >> "$LOG" 2>&1
status=1
moved=0
installed=0
cleanup() {
    if [ "$status" -ne 0 ]; then
        printf 'exit=%s\n' "$status" > "$RESULT"
    fi
    if [ "$status" -ne 0 ] && [ "$moved" -eq 1 ]; then
        if [ "$installed" -eq 1 ]; then
            /bin/mv "$APP" "$NEW" || return
        fi
        /bin/mv "$BACKUP" "$APP" || return
    fi
    if [ "$status" -ne 0 ]; then
        echo 'Mac update failed; original app retained. See updater log.'
        /usr/bin/open -a "$APP" || true
    fi
    # Both directories were generated and checked before this script was written.
    /bin/rm -rf "$STAGE" "$DOWNLOAD"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
count=0
while kill -0 "$PID" 2>/dev/null; do
    count=$((count + 1))
    if [ "$count" -ge 120 ]; then
        echo 'App did not exit; installed files were not changed.'
        exit 1
    fi
    sleep 1
done
echo 'App exited; replacing Mac app.'
/bin/mv "$APP" "$BACKUP" || exit 1
moved=1
/bin/mv "$NEW" "$APP" || exit 1
installed=1
# Publish the result before launch: startup consumes it immediately.
printf 'exit=0\n' > "$RESULT"
if ! /usr/bin/open -a "$APP"; then
    exit 1
fi
status=0
echo 'Mac app replaced and relaunch requested.'
'''


def launch_update(image: Path) -> None:
    if not image.parent.name.startswith(UPDATE_DIR_PREFIX):
        raise ValueError('Mac installer must be in a private update directory.')
    app = installed_bundle()
    staged = prepare_update(image, app)
    try:
        helper = image.parent / 'install-macos.sh'
        helper.write_text(install_script(app, staged, image.parent, os.getpid()),
                          encoding='utf-8', newline='\n')
        subprocess.Popen(['/bin/sh', str(helper)], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True, close_fds=True, cwd='/')
    except BaseException:
        shutil.rmtree(staged.parent, ignore_errors=True)
        raise
    log_update_event(f'Mac update prepared; detached helper will replace {app}')
