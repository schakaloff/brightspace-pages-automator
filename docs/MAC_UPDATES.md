# Updating MTB Writer on Mac

Older Mac builds deliberately open GitHub when you press Update. They cannot
install their own updater fix, so one manual app replacement is required.

1. Quit MTB Writer and download the latest release's `.dmg` from
   https://github.com/schakaloff/brightspace-pages-automator/releases/latest .
   Choose `arm64` for Apple Silicon, or `x86_64` for Intel (Apple menu > About
   This Mac shows the chip or processor).
2. Open the disk image. Drag **Brightspace Pages Automator.app** into the same
   Applications folder as the existing copy and choose **Replace**. If you lack
   permission for the main Applications folder, use `~/Applications`.
3. Eject the image and open the installed copy from Applications. Running from
   the disk image or a read-only App Translocation location cannot self-update.

This replaces the application; it does not require uninstalling Chromium or
deleting saved transfer runs, browser sessions or Keychain credentials. These
live outside the app. Legacy preferences stored inside an older app bundle may
reset on this one-time manual replacement; new builds save preferences in the
existing per-user `~/.local/share/BrightspaceAutomator/user_config.json` folder.
Windows uses `%APPDATA%/BrightspaceAutomator/user_config.json`. Source checkouts
continue using their existing project `user_config.json`.

After this upgrade, **Restart & Update** downloads the matching architecture,
verifies its SHA-256 against the release checksum, mounts the image read-only,
and copies the signed app into a temporary sibling directory. Permission,
copy and signature errors appear before the app closes. A detached OS helper
waits for the app to exit, replaces it and relaunches it. It restores the
original app if replacement or the relaunch command fails. An app location
without write access gives instructions rather than requesting administrator
credentials or silently opening GitHub.

The updater log and install result remain available through Settings.
Distribution still uses PyInstaller's ad-hoc signature; this change does not
add Apple Developer ID signing or notarization.

Release validation runs updater and GUI startup regressions on Windows,
Apple Silicon and Intel runners. Mac-only integration tests create a small
signed app and real disk image, then exercise native mounting, copying,
replacement, result reporting and rollback. Those two tests are skipped when
running the suite on Windows.
