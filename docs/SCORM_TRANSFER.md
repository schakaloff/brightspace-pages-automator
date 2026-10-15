# SCORM packages in Media

Open **Media → SCORM packages** and enter the Brightspace and Moodle course URLs.
Choose **1. Download & review**. If prompted, log in to Moodle in the browser and
click **Ready — scan Moodle** in the app.

Each original package is downloaded through **SCORM activity → Settings → Package
file → click its ZIP filename → Download**. The app does not save Moodle settings,
enable student downloads, or reconstruct the ZIP from the player. It checks the
archive, its CRCs, and its root SCORM manifest before marking a download ready.
External packages and activities without edit permissions are reported individually.

Review each destination unit. An exact, unique section name selects a unit
automatically; other names require a choice. Check the packages to transfer.
**Transfer first selected** processes one package; **2. Transfer selected** processes
all selected packages. Existing SCORM topics in the selected unit are skipped when
their title exactly matches the activity, package filename or manifest title.
Same-named HTML pages and Moodle links are not treated as transferred SCORM.

Brightspace uses **Add Existing → SCORM/xAPI Object**. The app reuses an exact
filename match in the package library, otherwise uploads the ZIP. It selects **No**
for a grade item and **Display this version even if a new version is added**.
Existing library packages are never updated or deleted. A transfer succeeds only
after the destination Content API returns a matching SCORM topic.

Progress and original ZIPs are kept under the app's `scorm-runs` data directory,
isolated by Moodle and Brightspace course IDs. Running Download & review again
reuses validated cached downloads. Select the fresh-download option if the Moodle
source package changed.

If a network error or Stop leaves an upload/Add with an unknown result, the app
stops the batch and does not automatically repeat that write. Inspect the SCORM
library or destination unit before recovery. A subsequent review recognizes a
successfully saved topic. An unresolved pending write requires inspection before
its saved pending status can be cleared with **Allow retry after checking an
uncertain result…**. Do not delete cached ZIPs to force a retry.

Live verification on course pair Moodle 166972 → Brightspace 23840 downloaded and
validated all nine original ZIPs, including Radiography (1,474,946,973 bytes).
The app added the two missing SCORM topics using packages already in the library:
Alginate (topic 1000011) and Radiography (topic 1000015). Both actual course title
screens and Start Course controls loaded in the saved Brightspace topics. The final
read-only review recognized all nine destination SCORM activities and reused all
nine cached ZIPs. The cache was preserved in the app's normal data directory.

The workflow opens the live package-service picker URL directly with the selected
course and unit IDs. This avoids the smart-curriculum tile's transient rendering
and stacked modal frames. Its radio/button controls retain verified DOM-click
fallbacks for Brightspace's pointer-interception behavior.

New ZIP uploads are implemented and tested against browser fixtures, including
processing completion and Save/Next flows. The real Bulk Upload file input was
inspected, but no new library upload was performed live because this course's
packages were already in the library. Verify that branch on a genuinely new package
before treating new-library uploads as live-proven.
