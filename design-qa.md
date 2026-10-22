# Link review preview design QA

Final result: passed.

## Reference and evidence

- Selected source: `C:/Users/Nick/.codex/generated_images/01a11374-3aed-7b11-98a4-385ac63d11d5/exec-523ad126-2676-4f49-a04e-7db30993b5bf.png` (1487 x 1058).
- Reference was proportionally resized to 1120 x 797 and padded to 1120 x 800: `test-results/link-review-design/reference-1120x800.png`.
- Implementation: native PySide6/Fusion, Segoe UI 10, density 1, light theme, default Choose state at 1120 x 800. The reference includes window chrome; the Qt capture is the client area.
- Full comparison: `test-results/link-review-design/comparison.png` (reference left, implementation right). Focused list comparison: `test-results/link-review-design/comparison-list.png`. Both opened and visually inspected.
- Additional Choose and Review captures: light and dark at 1487 x 1058, 1120 x 800, and 720 x 560; editing dialogs in both themes. Evidence lives in `test-results/link-review-design/` and can be regenerated with `tools/preview_link_review.py`.

## Inspection and fixes

- P2 resolved: narrow windows wrapped the step indicator. Kept the indicator on one line and moved proposed changes below link names on narrow screens.
- P2 resolved: old list headers briefly persisted after switching layouts. Hide removed widgets before scheduling deletion.
- P2 resolved: initially excessive vertical spacing made the table heavier than the reference. Reduced section and row spacing and adjusted the section heading.
- P2 resolved: editing-dialog radio indicators were indistinct under the custom stylesheet. Added explicit indicators and checked colours, then inspected light/dark dialogs.
- P2 resolved: the preview sidebar label was clipped. Retained the existing Check & fix label.
- No outstanding P0, P1, or P2 design issues. The compact list, selection hierarchy, contextual editor, working-links toggle, and pinned review action follow the selected design. Narrow review URLs wrap and the scroll area preserves access to the footer.

## Intentional differences and limits

The existing native app supplies its sidebar, typography, controls, and theme colours. Its row spacing and background are slightly different from the generated reference. The selected workflow step uses plain text rather than bold emphasis (P3). The textual source control uses the named platform rather than adding a new logo asset. The sample-data label and route to the existing Checker disclose the preview scope; the standalone preview hides the Checker route.

This is an interactive local prototype. Course/location URLs provide preview context only. Applying changes updates in-memory sample links; scanning, file upload, authentication, and Moodle/Brightspace writes are not connected. No backend fidelity is claimed.

## Validation

50 tests passed across link data validation, editing/cancel, exact selected-change review, hidden selections, manual links, reset, narrow layouts, panel navigation, and existing Checker/startup/hub regressions. `git diff --check` passed. Native offscreen captures and interaction checks apply here; browser deployment checks do not apply to this desktop preview.

The previous Build & style QA report was preserved in `docs/qa/build-style-design-qa.md`.

---

# Settings and Collector redesign QA — October 9, 2026

final result: passed (Settings and Collector scope).

## Reference and comparison

The approved source is `C:/dev/brightspace-automator/settings-ux-review/settings-concept.png`, 1487×1058. The matching implementation is `test-results/settings-design/dark-1487x1058-accounts.png`, the native Settings widget at the same dimensions, dark theme, density 1, with the same example SSO/Moodle configured state and Kaltura unconfigured state. Source and implementation were placed together and inspected in `comparison-accounts.png` (source left, implementation right). `comparison-form.png` provides a focused, unscaled label/password/action comparison.

Additional inspected evidence covers the app shell in both themes, all four categories, narrow Accounts/Help/Appearance views, Moodle editing, troubleshooting and Collector with its source visible in both disclosure states. Captures use actual PySide6 widgets, explicitly loaded Segoe UI fonts, and in-memory accounts/configuration. No course, login or updater requests were made.

## Findings and fixes

- P2 resolved: Qt interpreted category ampersands as mnemonics. Escaped them so Appearance & files and Help & updates display correctly. Verified final captures.
- P2 resolved: some controls inherited smaller text than the approved direction. Added scoped 14–15px form/body/control rules with 28px/20px page/section hierarchy. Verified full and focused comparisons and narrow captures.
- P2 resolved: the embedded Settings widget initially inherited the older app background instead of painting the modern surface. Enabled its native styled background and recaptured the actual app shell in dark/light themes.
- P2 resolved: Collector's Advanced/Log disclosure labels were too faint in light mode. Added theme-aware text and focus styling and inspected the final expanded light capture.
- No outstanding P0/P1/P2 issues in this scope. Vertical scrolling preserves access to longer account/troubleshooting forms at short heights; category navigation remains visible.

## Required fidelity surfaces

- Typography: Segoe UI, readable body/field sizes, distinct page/section hierarchy and permanent field labels. No missing font glyphs. Native rendering is intentionally more compact than the generated concept's type scale.
- Layout: same category order, Brightspace login choice, one expanded form, compact secondary account rows, local actions and lightweight dividers. Forms have a 1120px maximum width to fit the existing app shell; controls adapt to narrower content and vertically scroll. Native radios replace the concept's large two-option tiles.
- Colours: existing modern dark/light tokens, teal actions/selection, readable secondary text and saved/error states. Settings is scoped so existing workflows retain their styles. Collector's active source remains visible when Advanced is closed.
- Image/asset quality: the mock contains no photographs or other raster content needed in the app. Native controls and text provide selection/status semantics; redundant decorative tab/status pictograms are intentionally omitted rather than approximated with custom art (P3 visual difference).
- Copy: permanent Email/Username/Password labels; saved state is distinguished from authentication success; explicit Remove explains credential deletion. Account Save and automatic AI preference saving are described separately. The unavailable interactive guide has a preparation message rather than an inactive link.

## Interaction and regression checks

47 relevant tests pass, including old UI/startup/sidebar/log tests and new coverage for startup ignoring legacy sources, same-course unit retention, host/course reset, explicit paired Checker copying, Clear remaining clear, enabled-source backend arguments, malformed URLs, login-method routing, password spaces, confirmed/cancelled deletion, credential-store/config errors, separate update/reinstall actions, installation failures remaining visible, and configured/malformed interactive-guide URLs.

The wider UI run exposed the pre-existing Link Review 560px horizontal-overflow test. It also fails in an isolated process which does not load Settings/Collector. This section's passing result is scoped to this implementation and does not supersede that separate issue in the earlier preview.

Screenshots and behaviour checks do not establish screen-reader compliance or an installed-build/live-auth result. Articulate URL integration is implemented as a config hook; no URL has been supplied. No installer was built or deployed. Native widget QA applies; web prototype/server/hosting checks do not apply to this desktop application change.


---

# Check & fix calmer overview QA — October 9, 2026

final result: passed

This result covers the Check & fix redesign only. It does not supersede the older Link Review preview findings or imply installed-build/live-course verification.

Source visual truth: `C:/dev/brightspace-automator/checker-ux-review/checker-concept-v2.png`, the second proposal selected by Nick. Source dimensions: 1487 × 1058. Implementation: `test-results/checker-design/after-dark-1487x1058-results.png`, actual Qt widgets in the app shell, same dimensions, dark theme and scan-complete sample state. Captures use density 1 with explicitly loaded Segoe UI. The existing 160px app sidebar is preserved; the generated concept depicts a wider sidebar. No image scaling was applied to the saved comparison.

Full-view comparison evidence: `test-results/checker-design/comparison-overview.png`, source left and implementation right in one image (2974 × 1058). Focused evidence: `comparison-findings.png`, unscaled crops from source (292, 290, 1148, 464) and implementation (204, 205, 1239, 480) in one image (2387 × 480). Both combined images were opened and inspected. The focused comparison makes row copy, count units, separators and the primary action readable.

Additional inspected evidence includes full-app 900 × 720 and 720 × 560 views in both themes; initial fields, all focused groups, confirmation, partial scans, simulated login wait, stopped and failed-outcome displays. These use in-memory settings, empty credential stubs, suppressed startup tasks and a disabled worker. Native Qt capture applies; web prototype/server/console checks do not apply to this desktop application.

Findings and comparison history:

- P1 resolved: old evidence widgets remained visible briefly after group changes, overlapping new text in the first small-window content capture. Hide and detach them before deferred deletion. Recaptured focused groups show only the current evidence; a regression checks cross-group evidence removal.
- P2 resolved: the first action area was pinned at the bottom of a tall window, creating a large gap from the overview. The scroll view now supplies the current content's natural height, with spare space below the action area. The primary action stays outside the scroll content and remains reachable in short windows. The revised combined overview comparison confirms the gap is removed.
- P1 resolved: a short-to-long result transition in the actual app shell could compress the wrapped outcome label, hiding verification/error text. Use the body's preferred height and refit after Qt propagates layout changes. The final `after-light-outcome.png` shows every paragraph, and both themes have a long-result geometry regression. Rechecked small-window content and confirmation; scrolling preserves longer content without hiding the action.
- P2 resolved: the entire finding row is now a native button with keyboard activation and a visible focus outline, instead of restricting opening to the count control.
- No outstanding actionable P0/P1/P2 findings in this scope.

Required fidelity surfaces:

- Fonts/typography: Segoe UI, 28px title, 22px section headings, 16px finding titles and 14–15px body/control text. Native type is intentionally more compact than the generated mock's oversized text, consistent with the existing Settings and app shell. Wrapping was checked at narrow widths; long result text receives its full required height.
- Spacing/layout: compact source → destination summary, three lightweight finding rows, thin dividers, no expanded findings or operation wall on the overview. Focused views expose at most three related choices. The native action area appears directly beneath short content, remains outside the scroll at smaller heights, and stacks its hint/actions when narrow. Other options/activity details appear before that action area to retain their access within the existing content scroll; this is an intentional native layout adaptation.
- Colours/tokens: existing modern dark/light backgrounds, readable secondary text, teal actions and focus outlines. Flat surfaces replace the mock's subtle generated shading. No additional colour system was introduced.
- Assets/icons: reuse the existing native icon library and sidebar assets; no new raster assets or dependencies are needed. The conceptual document/folder/Book icons are replaced with the app's existing check/collect glyphs rather than bespoke approximations. This is an acceptable P3 difference.
- Copy/content: same three group names and focused hero use case; count units are singular/plural appropriately. Books are described as optional conversion. A failed link check displays Not checked rather than zero. The read-only hint says This scan made no changes to Brightspace, which remains accurate after a previous apply. Full course URLs and explicit operations appear at confirmation; the result distinguishes recorded outcomes from full verification.

Interactions and validation: 95 relevant tests pass. Coverage includes responsive reachability, cross-group selection retention, explicit apply dispatch, Books/order-only routing, changed-course invalidation, rejection of late reports, failed link coverage, Moodle section scope, malformed input, keyboard row activation, cancellation at browser/login/confirmation waits, deferred worker completion and wrapped outcome text. Existing GUI/startup/sidebar/log, file-repair, Books, activities and prior Settings/Collector tests also pass. `git diff --check` passes.

Evidence limits: no live authentication, real course writes, screen-reader behavior or installed executable was exercised. Applying still rescans and can prompt for individual files/uncertain matches. The backend does not expose a complete per-item outcome for every operation; the UI deliberately requests a verification scan instead of declaring everything fixed. No installer was built or deployed.

Follow-up polish (P3): matching the generated icon shapes and course-ID bold styling more closely could be a later cosmetic pass. No additional implementation is required for the selected workflow.

## Duplicate content — final QA, 2026-10-09

Final result: **passed** for the approved native-app implementation.

Approved reference: `C:/Users/Nick/.codex/generated_images/01a121d6-eea0-7441-a648-158e61340d99/exec-4bcc8a5b-5cc2-443e-b4a4-848a5f7cb9aa.png`.
Implemented evidence: `duplicate-preview/dark-desktop.png`, `light-desktop.png`,
`dark-narrow.png`, `dark-narrow-settings.png`, `light-narrow.png`, and
`light-narrow-settings.png`. The screenshots are rendered from the actual PySide6
panel by `tools/preview_duplicate.py`, with example content and no network requests.

Comparison: `duplicate-preview/comparison.png` contains the reference main-content
crop and the implemented panel side by side. Both halves are 1213 × 1058 pixels,
without stretching or resampling. The reference's conceptual navigation rail is
excluded because the existing application navigation is retained. The selected
Physical Health and Safety section, expanded contents, one copy, Course home
destination, and resulting structure match in both captures. Full-size inspection
covered the fields, tree rows, footer and activity-link note; additional focus crops
were unnecessary because these regions are readable in the full comparison.

Fidelity review:

- Layout: source tree at left, settings and resulting structure at right, persistent
  footer with Review copies. Narrow viewports use a vertically scrollable workspace.
- Typography: native Segoe UI with a clear title/section/body hierarchy. The preview
  uses denser rows than the generated image to keep the complete hierarchy visible.
- Color: near-black surfaces, restrained dividers, teal selection and mint primary
  action. Light-theme selection uses dark text to maintain legibility.
- Icons and controls: licensed Tabler outline icons and native keyboard-accessible
  Qt trees, checkboxes, fields and selectors. Icon silhouettes differ slightly from
  the generated reference; this is cosmetic P3 polish.
- Content and state: original titles, nesting and item counts remain visible. Activity
  shortcuts explicitly reuse an existing activity. New content starts hidden, and
  the final confirmation identifies the course before any write.

Comparison history: corrected a clipped final preview row, insufficient selection
space in the narrow layout, and low-contrast selected text in light mode. Also fixed
custom-name resets after excluding children, including restored drafts, and disabled
the single-name field when multiple source roots are selected. No P0/P1/P2 visual or
interaction findings remain open within the tested scope.

Validation covers independent repeated files, nested/partial selection, naming,
hidden content, restricted additive writes, lost-response recovery, stopping and
resuming, completed read-only reruns, final confirmation, restored drafts, navigation,
and responsive reachability at widths 560, 760 and 1200. The new adapter also loaded
128 Content objects from Nick Usatenco EA105 Sandbox using GET requests only and
built a section-shell proposal without applying it.

Evidence limits: no live creation/uploads, installed-executable packaging, or
screen-reader session was exercised. Backend mutations are covered with the
in-memory API. This is a native Qt panel, so browser-console checks do not apply.
Existing Claude Structured Transfer changes and unrelated checker/settings work
are preserved.

Final checks: 41 duplication/panel/navigation tests and 73 existing Structured
Transfer tests pass (114 selected tests total). `git diff --check` passes.

## Structured Transfer — option 3, final QA, 2026-10-13

final result: passed

Source visual truth:
`C:/Users/Nick/.codex/generated_images/01a121d6-eea0-7441-a648-158e61340d99/exec-21cbf1d6-b172-4114-93fb-ff8da1b177e3.png`.
Implementation screenshot: `transfer-investigation/ui/dark-2-review-comparison.png`.
Both source and implementation are 1487 × 1058 pixels. The native logical viewport
is 1487 × 1058 at density 1; CSS sizing is not applicable to this PySide6 application.
No stretching or resampling was used to make the comparison artifacts.

State: Review, Anxiety in the Workplace selected, its source page expanded into
parts, and the pre-work part selected in both source and target trees. The app
renders the saved EA113/EA105 inventories; these captures do not claim to be a fresh
live course scan. Authored titles and source order supersede the mock's shortened
example titles. Both retain the five required destination sections in order.

Full-view evidence: `transfer-investigation/ui/structured-transfer-comparison.png`
places the approved mock and actual native capture side by side, source first.
Focused evidence: `transfer-investigation/ui/structured-transfer-comparison-detail.png`
places equal-size crops of the destination tree side by side, preserving each
capture's natural layout. These were inspected together for hierarchy, wrapping,
selection, indentation, icons and the final assignment row.

**Findings and comparison history**

- Fixed P1: the mock's rubric notice implied an incomplete assignment could be
  created. The implemented notice requires copying assignments with their rubrics
  in Brightspace first, then checking them. Existing backend gating is retained.
- Fixed P2: native tree text initially appeared beneath custom wrapping labels.
  A delegate now draws selection and icons while the labels draw the text once;
  accessibility/search text stays on the native item.
- Fixed P2: the final destination row clipped. Measured wrapping rows, compact
  section headers and a horizontal rubric notice now keep the complete selected
  workshop visible at the reference viewport, including the pending assignment.
- Fixed P2: narrow layouts inherited minimum widths from the header, pending-item
  banner and footer. They now stack where needed, and More holds secondary actions.
  The primary action remains visible at widths 560 and 760.
- Fixed P2: generated introduction/session rows had an unclear authoring action.
  Selecting them now exposes Add / edit text, with activation also supported.
- Fixed P2: selection synchronization could recurse when selecting generated rows.
  Programmatic restoration is guarded, with a regression test for the actual action.

Post-fix evidence includes `dark-2-review-comparison.png` and its light counterpart,
`dark-5-narrow-comparison.png`, `dark-5-narrow-result.png`, `dark-5-small-result.png`,
`dark-5-small-create.png` and `dark-5-small-confirm.png`, with matching light captures.
The latter confirms the target identity and both confirmation statements remain
readable at 560 × 760. Longer content stays reachable through native scroll areas.
There are no remaining actionable P0/P1/P2 findings in the checked scope.

**Required fidelity surfaces**

- Fonts/typography: Segoe UI; 32 px title, 26 px workshop heading, 19 px pane headings,
  15 px content titles and 12–14 px supporting text. Section weight distinguishes
  the five destinations. Longer authored names wrap instead of being discarded.
- Spacing/layout: workshop navigation at left, original and resulting trees beside
  one another, copy notice below, persistent primary action in the footer. Minimal
  dividers and restrained borders preserve the approved flat composition. The app
  keeps useful item/decision counts and Workshop options from the existing workflow.
- Colors/tokens: near-black background, slate text, teal selected rows and mint CTA;
  light mode uses dark selected text on pale teal. Warning and disabled states remain
  distinct. Native theme colors replace the image's faint generated shading.
- Assets/icons: bundled, licensed Tabler outline files and native tree disclosure
  controls remain crisp. The mock contains no required raster artwork. Course/header
  icon silhouettes and the selection-edge treatment differ slightly (P3).
- Copy/content: actual course names, source part labels and assignment titles replace
  illustrative mock text. Both sides use the same row IDs. The saved proposal remains
  editable until application; rubric/unsupported-item blockers remain explicit.

**Interactions and limitations**

Coverage includes selecting matching source/target rows, changing or skipping a
placement without altering the source inventory, distinct split-part IDs, authoring
text from generated rows, locked/busy plans, changed-course invalidation, responsive
actions, existing create confirmations, and saved-plan recovery behavior. The six
Build & style navigation checks also passed in the preceding 89-test run.

This QA pass uses the real native widgets and saved course data. No course writes,
fresh authentication, installed executable packaging, or screen-reader session was
performed. Browser-console checks do not apply to this native application. The
latest transfer/splitting backend still requires its separate end-to-end live
sandbox validation; passing design QA does not claim the nine-workshop transfer
has been executed.

**Follow-up polish**

P3 only: closer header/course icons and the mock's slender selection-edge accent.
Full course identities, the real assignment title and useful attention counts are
intentional product differences, not placeholders to replace with mock text.

**Implementation checklist**

- Selected comparison layout implemented with functional proposal controls.
- Desktop/light/dark and narrow review/confirmation captures inspected.
- Prior clipping, recursion and width findings resolved.
- Existing transfer safety gates and unrelated local work preserved.
- Usage guide updated; final screenshot ready to inspect.

Final regression run (2026-10-13): **83 passed in 9.82 seconds** across the comparison,
panel, structured-transfer, guided-transfer and custom-structure suites. The run
used normal local filesystem access and a dedicated ignored workspace temporary
directory after Windows sandbox temporary-file restrictions blocked the first run.
Python compilation and `git diff --check` also pass. This is selected-suite coverage,
not a claim that the full repository suite was run.

### Follow-up: actionable guidance after Nick's screenshot

final result: passed

The source/result layout and theme remain those of option 3. Nick's actual screen
showed an unexplained red unscored-task message and number badges. This scoped
follow-up replaces that task message with Copy without a grade / Leave out of this
transfer, identifies the attention count in words, and adds Show next item. The
sidebar explains its numbers; the comparison hint explains left/right or above/below.
Native-copy actions are now above the trees so they remain readily discoverable.

Latest comparison evidence remains `structured-transfer-comparison.png` and
`structured-transfer-comparison-detail.png` under `transfer-investigation/ui/`.
Both full captures are 1487 × 1058 at density 1. The equal-size focused crops follow
the actual destination pane positions after the intentional guidance movement;
neither crop is scaled. The full and focused comparisons were inspected together.
Dark/light desktop and 560-pixel review states were inspected. The selected Anxiety
workshop's five destinations and final assignment stay readable; larger workshops
remain scrollable. The unscored-task choice is directly visible in
`light-2-review.png`. No actionable P0/P1/P2 finding remains in this refinement.

Typography, colors, icon assets and tree hierarchy retain the preceding QA verdict.
The added amber notices use plain language and direct actions. This also fixes a
behavioral problem: resolving one unscored task no longer requires disabling the
whole gradebook. The explicit per-task choice survives refresh/save, is included in
the execution signature, and is verified on readback. It cannot silently remove a
score or existing grade association. Existing saved plans retain their prior default.

87 selected tests passed in 6.47 seconds, including an offline mixed transfer with
an ungraded task, another graded assignment, unchanged 70/30 categories, no source
writes, and a completed rerun without new writes. Tests also cover malformed choices,
protection of scored tasks, direct next-item navigation and persisted mapping refresh.
No live Brightspace writes or changes to Nick's actual saved proposal were made.

## Sidebar header: selected option 1 (2026-10-21)

Source visual truth: `C:/Users/Nick/.codex/generated_images/01a15f57-7d40-72c2-bff2-db97e2a04afb/exec-f1dbf3a0-e000-4532-919b-11701e22dc69.png` (1448 x 1086, generated comparison board showing open and collapsed states).

Implementation evidence: `test-results/sidebar/light-expanded.png`, `light-collapsed.png`, `dark-expanded.png`, and `dark-collapsed.png`. Native PySide6/Fusion, Segoe UI, 1120 x 800 logical and physical pixels, density 1. Existing app navigation and empty Checker state. This is a native application change, not a website prototype.

Combined comparison evidence: `test-results/sidebar/option-1-comparison.png`. The source board is scaled proportionally to 720 x 540; native app regions remain at density 1. Focused header crops below show both open and collapsed states; native crops enlarged proportionally for inspection. The generated board has no reliable CSS size or device density, so it is a structural reference, not a pixel-exact viewport specification. Existing 160px sidebar width and app content layout are intentionally retained.

Findings: no actionable P0/P1/P2 mismatches in the scoped header. The two-line brand is replaced with a contiguous single-line wordmark, the header is reduced to 56px, and the panel glyph is replaced with the selected bordered directional chevron. The collapsed control stays centered and reverses direction. No changes to the unrelated Checker content are claimed.

Required fidelity surfaces checked:
- Typography: native Segoe UI, matching size for both words, muted medium-weight MTB and bold Writer. No wrapping or clipping.
- Spacing and layout: common vertical center for wordmark and 28px toggle, 14px outer padding, compact header, consistent rail alignment.
- Colors: existing light/dark theme tokens retained; active navigation and Settings remain intact.
- Asset quality: existing QPainter icon library supplies sharp directional chevrons; no raster logos, decorative assets, or placeholders are required by the selected concept.
- Copy: MTB Writer, Check & fix, Media, Build & style, Settings, and accessible Collapse/Expand sidebar labels checked.

Validation: 30 GUI tests passed, covering mouse and keyboard toggling, navigation and Settings while collapsed, animation reversal, startup preference restoration, and saved user changes. Both themes rendered and inspected. Production credentials and course data were not used by the render harness. Git diff whitespace check passed.

Comparison history: first post-change comparison passed; no P0/P1/P2 fixes were required after comparison. Native application rendering and existing width are intentional adaptations of the generated concept.

Implementation checklist: complete. Follow-up polish: none required for this scope.

final result: passed

## Media chooser: selected option 1 (2026-10-21)

Source visual truth: `C:/Users/Nick/.codex/generated_images/01a15f57-7d40-72c2-bff2-db97e2a04afb/exec-34f09a49-446b-4ec8-b38a-1a7a258b72a2.png` (1774 x 887).

Implementation evidence: `test-results/media-hub/light-hover.png`, `light-focus.png`, `light-expanded.png`, `dark-expanded.png`, and `dark-narrow.png`. Native PySide6/Fusion at density 1 with Segoe UI. Desktop viewport 1120 x 800 logical/physical pixels; narrow viewport 720 x 560. State: Media chooser, three tools, no transfer running. Render harness disables credentials, config writes and startup network work.

Combined full-view and focused-row comparison: `test-results/media-hub/option-1-comparison.png`. Source is proportionally normalized to 960 x 480. Implementation uses the same 960 x 480 Media content crop, removing the existing 160px sidebar and unused lower canvas. Focused first-row crops preserve scale. Generated mockup has no reliable CSS/device density; existing native desktop layout is the implementation constraint.

Comparison history:
- Initial review found a P2 hierarchy difference: 16px tool titles and 56px icon tiles were smaller than the selected visual direction. Increased titles to 18px, descriptions to 15px, icon tiles to 64px, minimum row height to 96px, and screen title to 28px. Captured again and inspected the combined comparison and narrow layout.
- Post-fix comparison: no outstanding actionable P0/P1/P2 issues. The new rows reproduce the left icon tile, grouped text, full-width navigation target, right chevron and teal hover treatment.

Required fidelity surfaces:
- Typography: Segoe UI; 28px page title, 18px semibold tool names and 15px descriptions. Descriptions wrap without clipping at narrow widths.
- Layout: 32px page gutters, 12px row gaps, 12px rounded corners, 64px icon tiles and full-row hit targets. Narrow layouts scroll vertically when required, with no horizontal scroll.
- Colors: existing native theme tokens, neutral icon tiles, white/light surfaces and teal hover/focus border. Dark theme icons and surfaces refresh with the application theme.
- Assets: unmodified puzzle, package and movie outline SVGs from the same pinned Tabler v3.41.0 release already bundled by the application. Existing chevron asset. The official movie glyph is a filmstrip without the mockup's play triangle; this is an accepted P3 asset variation, preserving the existing icon family and license. No placeholder images or decorative generated assets are needed.
- Copy: Media, H5P activities, SCORM packages, Kaltura videos and their descriptions retained. Course-URL hint follows the selected concept. Separate Open buttons removed.

Behavior: each row is one native accessible button. Qt window-level event tests click icons, titles and whitespace on all three tools and confirm one correct navigation event each. Tab, Enter and Space work without nested focus targets. Existing tool routes and course-URL propagation are retained. Opening a tool does not start a transfer.

Validation: 29 existing GUI/startup/hub checks passed, 17 focused navigation/integration checks passed, and 6 final row-layout/SCORM integration checks passed after the sizing refinement (overlapping suites). New SVGs render in both themes and are included by the existing assets packaging rules. Git diff whitespace check passed.

Implementation checklist complete. Residual P3: native Tabler filmstrip differs slightly from the illustrative video icon in the generated mockup. No live Moodle or Brightspace actions were needed.

final result: passed

## Side-by-side window sizing (2026-10-21)

Implemented the resize fix in the native application shell. Minimum window size is now 560 x 480 logical pixels. Below 760px width the sidebar automatically becomes an icon rail; above 820px it restores the user's preferred state. Automatic changes never overwrite the saved preference, and the user can still manually expand navigation while narrow.

Only the active page contributes its minimum size to the content viewport. Dense legacy pages can scroll inside that viewport without forcing the entire window wider. Media, Checker, H5P and Settings were visually checked at 624 x 640 logical pixels with 150% scale, in both themes. Evidence: `test-results/window-layout/light-media-150.png`, `light-checker-150.png`, `light-h5p-150.png`, `light-settings-150.png`, corresponding dark captures, minimum-size and restored-wide captures. Captures are native Qt client areas; Windows title bars/borders are not included.

Removed the startup override that set QT_SCALE_FACTOR=1.5 and relaunched. Qt 6 already follows Windows' per-monitor scale, and a global factor multiplies the native factor: https://doc.qt.io/qt-6/highdpi.html#qt-scale-factor . Explicit environment settings supplied by the user remain untouched.

Validation: 19 window/startup/sidebar/media tests plus 76 existing GUI/workflow regressions passed (95 total). Cases cover minimum/half-screen widths, keyboard/pointer navigation, manual preference restoration, narrow primary actions, and scrolling a dense page then returning to an ordinary page without overflow. Manual Qt scale checks at 100%, 125%, 150% and 200% retain a 624px logical window and render at 624, 780, 936 and 1248 physical pixels respectively. The 936px client area leaves room in the user's approximately 960px half-screen at 150%; native Windows snapping itself was not driven by this test harness.

No source/course content, credentials or real preferences were changed by the verification harness. Text size remains governed by the normal platform/theme settings. Dense legacy pages use scrolling rather than a full UI redesign. Visual inspection found no blocked primary controls at the tested sizes.

final result: passed

## H5P Back navigation (2026-10-21)

Added a persistent Back button at the left of the H5P footer, keeping Stop & save on the right. The saved-progress hint now sits above the actions so the buttons remain reachable in narrow windows. Back from an active scan, review or transfer cancels the worker, waits for its save/browser cleanup, then returns to editable course setup. Back from results returns to setup; Back from idle setup returns to Media. URLs stay populated and saved transfers remain available. Stop & save still opens the progress/results view.

Native Qt screenshots inspected in light and dark themes: `test-results/h5p-design/app/light-624-connect-waiting.png` and `dark-1120-connect-waiting.png`. Back, Stop & save and the disabled scan action fit the narrow footer. Browser-course mutations were not needed for this UI check.
