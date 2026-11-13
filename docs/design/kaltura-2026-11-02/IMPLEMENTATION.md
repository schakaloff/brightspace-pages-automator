# Kaltura implementation and validation

Updated 3 November 2026. Implemented in source in the local working tree; no packaged release or deployment performed. The original README records the preimplementation audit and approved proposals.

## Implemented workflow

| Phase | Behavior and user attention |
| --- | --- |
| Connect | Enter the Moodle and Brightspace courses, choose whether to show the existing browser cursor and live messages, and connect. Service-specific login prompts identify where attention is needed. Read course videos and destination inventory before approval. |
| Review | Review grouped source placements, selections, existing pages, destination units and new-unit requests. Unmapped placements require a destination. Partial scans disclose missing coverage. The approval confirms the target and selected changes. |
| Create | Show the current service, item, destination and action. Reuse the existing browser guide, cursor and click effects. Journal pending operations before writes and count verified outcomes. Stop lets an in-flight save finish its verification or retain an uncertain result. |
| Results | Separate verified new pages, existing pages kept, exclusions, failures, unfinished work and results requiring a check. Resume checks current destination content before offering further writes. |

The source scan keeps repeated occurrences and section identity. Kaltura embeds are checked against the exact entry ID. Existing Brightspace pages are checked by content, rather than title alone. A lost save response can be reconciled with a verified page; an unreadable or unexpected newly created page remains blocked for checking instead of being blindly recreated. Only selected placements can request new units. Ambiguous unit names and incomplete destination reads cannot establish that a unit is absent.

Back saves the review draft. Per-transfer and per-destination locks prevent overlapping work. The plan records sanitized course identities, selection, destination intent, timestamps and outcome evidence; browser sessions and credentials remain separate. Recovery relies on current destination verification and does not promise rollback.

## Consistency and reuse

- H5P, Kaltura and Check & Fix share the **Technical Details** control, including its stable label, chevron, keyboard behavior, theme refresh and log visibility on expansion.
- H5P and Kaltura share **Show browser cursor and live messages** and its explanatory tooltip.
- Kaltura uses the neutral Media/H5P surfaces, shared icon system, theme roles, scrollable content and fixed primary actions. Disclosure buttons use the approved chevrons and neutral treatment.
- The Kaltura **Saved transfers** entry is a card below the course inputs, matching H5P: heading, saved count, explanation and full-width **Browse saved transfers**. It is hidden during an active workflow.
- The library reuses H5P's table, search/filter presentation, selection and Undo/Restore behavior through a provider adapter. Kaltura retains its own domain records and outcome descriptions. Dates, transfer IDs, progress and attention states come from saved facts.
- Removing a saved transfer hides its record from the default library. Undo and Removed/Restore retain recovery evidence. Removal does not delete Kaltura media, Moodle content or Brightspace pages.
- Browser-guide infrastructure and atomic JSON persistence are shared. Existing editor and unit helpers remain in use; old panel orchestration was replaced by the journaled worker flow.

## Verification

- Combined fixture-based Kaltura, H5P, shared GUI, startup, browser-guide and Checker regression run: **138 passed**.
- After the final shared-control refinements, the Kaltura workspace, Checker layout and GUI panel suite was rerun: **33 passed**.
- A local runtime check verified the exact Technical Details label, Space-key activation, full log visibility and collapse on H5P, Kaltura and Check & Fix in both themes.
- Production widgets were rendered inside the real app shell with isolated sample journals, mocked configuration/update/credential access and no browsers. Reviewed light/dark layouts at 1040 × 920 and 640 × 720, including review, progress, results and saved-library states.
- Current rendered examples are under `implemented/`. Values shown are illustrative sample records, not live transfer results.

## Remaining acceptance checks

No live course links were followed through a transfer, no live sign-in was completed, and no Kaltura content or Brightspace pages/units were inserted or modified during this work. Fixture tests cannot validate current institutional login pages or the live editor. A controlled live transfer, packaged Windows build, Narrator and high-DPI checks remain unperformed.

## Main source files

- `src/panels/kaltura_workflow.py`: phases, user decisions, progress, recovery actions and Qt worker integration.
- `src/kaltura_runner.py`: browser sessions, destination checks and journaled writes.
- `src/kaltura_progress.py`: durable plan, operation states and saved-history descriptions.
- `src/kaltura_verification.py`: exact media identity and destination reconciliation.
- `src/panels/_shared.py`: common Technical Details control and browser-guide wording.
- `src/panels/kaltura_saved_transfers.py` and `src/panels/h5p_saved_transfers.py`: provider adapter and reusable library presentation.
