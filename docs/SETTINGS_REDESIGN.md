# Settings and Collector changes

Implemented October 9, 2026 from the approved Settings concept.

Settings now has Accounts, AI, Appearance & files, and Help & updates categories. Accounts has permanent field labels, masked passwords with Show/Hide, saved-state summaries, local Save actions and explicit credential removal. Brightspace's Microsoft SSO / Direct login choice persists as `bs_login_method`; only the selected method's credentials are passed to workflows. When that setting is absent, existing accounts retain the earlier SSO priority behaviour. Account credentials remain in the existing keyring services.

Removing credentials requires confirmation inside the app and deletes the password for the **saved** identity, even if the form contains an unsaved username edit. Saved-state feedback persists. Passwords preserve leading/trailing spaces. Credential-store and account-config write failures produce a visible error instead of reporting success. The API key retains its automatic save behaviour; account forms save explicitly.

The obsolete Full Visual Guide entry is removed from Settings. The standalone guide file remains. To connect the forthcoming Articulate guide, set `interactive_guide_url` to its published HTTP(S) destination in the existing `user_config.json` beside `gui.py`. With no usable URL configured, Help displays a preparation message and no dead launch button. Guide navigation is tested through the same browser-opening mechanism as the previous guide; no Articulate URL was supplied or contacted during this change.

Help displays version/update status and any previous installation outcome. Troubleshooting hides build/path/log details until requested. Check for updates uses the regular update check; finding the latest installer/reinstall is a separate troubleshooting action and does not itself install anything.

Collector's Moodle name recovery is explicitly enabled and starts blank/off on each fresh app session. It never automatically restores `col_moodle_url` or the saved Checker source. Subsequent saves replace the obsolete `col_moodle_url` value with an empty string. The current in-memory source survives unit changes within the same course. A different Brightspace course or host clears and disables it; unrecognized URL forms are conservatively bound to the exact URL. Returning to Collect never refills a cleared source.

Use matching source from Checker is an explicit action. It copies the currently displayed Moodle course only if Checker's Brightspace host/course identity matches the current unit. Moodle and Brightspace IDs are never compared to each other. The selected source remains visible above the run action when Advanced is collapsed, with Change/Clear actions. Only enabled, valid Moodle course URLs reach the backend, and duplicate-section cleanup does not use name recovery.

## Code organization

- `src/panels/settings_panel.py`: saved credentials, selected login method, updater diagnostics and signals.
- `src/panels/settings_layout.py`: category layout, account forms and standard native Qt controls.
- `src/panels/settings_styles.py`: styles scoped to Settings, using existing modern colour tokens.
- `src/course_context.py`: host/course identity and Moodle course URL validation.
- `src/panels/collector_panel.py`: course-bound source, visible summary and explicit Checker reuse.
- `gui.py`: one additional Collector theme-refresh hook; existing unrelated changes were retained.

## Verification

47 tests pass in the changed workflows and existing UI/startup/sidebar/log coverage:

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
.venv/Scripts/python.exe -m pytest tests/test_settings_workflow.py tests/test_gui_panels.py tests/test_gui_startup.py tests/test_gui_styles.py tests/test_gui_sidebar.py tests/test_gui_log.py -q
```

The wider UI run also found the existing Link Review preview's narrow-width test failing at 560px. It reproduces when `tests/test_link_review_panel.py` is run independently, without importing the changed Settings/Collector modules. That separate preview was not modified.

`tools/preview_settings.py` renders screenshots at 1487×1058, 900×720 and 560×640 in dark/light themes and verifies integration in the actual app shell. Its configuration, accounts and passwords are fictitious and kept in memory. It suppresses Chromium setup, login, update checks and real configuration/credential writes. It also creates full-view and focused comparisons with the approved concept if the local reference image exists.

```powershell
.venv/Scripts/python.exe tools/preview_settings.py
```

Screenshots are saved under `test-results/settings-design/`. Visual QA is documented in the Settings section of the root `design-qa.md`. No live course was run and no installer was built or published.
