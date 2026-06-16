# Release pipeline — manual test checklist

Run through this before merging anything to `main`. Merging to `main` triggers
a real GitHub Release (tag + attached installers) — there's no dry-run mode,
so confirm everything here first.

## 1. App still works unmodified

```
python gui.py
```
Header should show `v0.5.0` exactly as before. No visual change expected.

## 2. First-run Chromium install

Move your Playwright cache out of the way to simulate a fresh machine, then relaunch:

- Windows: rename `%USERPROFILE%\AppData\Local\ms-playwright`
- macOS: rename `~/Library/Caches/ms-playwright`
- Linux: rename `~/.cache/ms-playwright`

```
python gui.py
```

Expect a "Downloading browser engine…" dialog with progress lines, which closes on
success. Then run an automation job (any tab) to confirm Chromium actually launches.

Also test the failure path: disconnect from the internet, relaunch, and confirm an
error dialog appears (not a silent hang) telling you to check your connection and
restart.

Restore your renamed cache folder back afterward if you want to skip re-downloading.

## 3. Icons

```
python tools/build_icon.py
```
Confirm `assets/icon.ico` and `assets/icon.icns` are written and visually match the
in-app "BP" icon (open them in any image viewer / icon previewer).

## 4. Windows PyInstaller build

Requires Windows + `pip install pyinstaller`.

```
pyinstaller installer/brightspace_automator.spec --noconfirm
dist\BrightspacePagesAutomator\BrightspacePagesAutomator.exe
```

Checks:
- App launches and renders with the dark customtkinter theme (not default Tk
  styling — if it looks unstyled, the `collect_all` step in the spec didn't pick up
  customtkinter's theme assets).
- `dist\BrightspacePagesAutomator\templates\style_reference.html` exists on disk.
- Run a job in each of the three tabs (Page Changer, Unit Collector, Style Migrator)
  and confirm the style reference loads correctly (proves `_resource_path` works
  in frozen mode).
- Taskbar/title bar icon shows the "BP" monogram.

## 5. Windows installer (Inno Setup)

Install Inno Setup once: `choco install innosetup` (or download manually).

```
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DMyAppVersion=0.5.0 installer\windows.iss
installer\output\BrightspacePagesAutomator-Setup-0.5.0.exe
```

Checks:
- Installer runs, Start Menu shortcut and (if selected) desktop icon are created.
- Installed app launches correctly from the shortcut.
- Uninstall via "Apps & Features" removes it cleanly.

## 6. macOS build

Requires a Mac + `pip install pyinstaller`.

```
pyinstaller installer/brightspace_automator_mac.spec --noconfirm
open "dist/Brightspace Pages Automator.app"
```

It's unsigned, so the first launch needs right-click → Open to bypass Gatekeeper.
Confirm it launches and the Dock/Finder icon looks correct.

Then wrap and test the DMG:
```
hdiutil create -volname "Brightspace Pages Automator" \
  -srcfolder "dist/Brightspace Pages Automator.app" \
  -ov -format UDZO BrightspacePagesAutomator-0.5.0.dmg
open BrightspacePagesAutomator-0.5.0.dmg
```
Confirm it mounts and drag-installing works.

## 7. CI workflow dry run (no release)

Push to a throwaway branch with the workflow trigger temporarily widened to include
it (edit `branches: [main, dev]` in `.github/workflows/release.yml` to add your
branch name), push, then check the Actions tab:
- `get-version` extracts `0.5.0` correctly.
- `build-windows` and `build-mac` both go green and upload artifacts.
- `release` shows as **skipped** (since the ref isn't `main`).

Revert the trigger change before merging.

## 8. `dev` branch build (still no release)

Merge to `dev` and confirm in Actions that both build jobs run and artifacts are
produced, but no GitHub Release is created.

---

Once all of the above pass, merge/push to `main` to trigger the first real release
(tag `v0.5.0-<run number>`, installer + DMG attached). This last step is intentionally
not automated here — do it yourself when ready.
