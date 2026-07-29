"""Release notes users actually read.

Real changelogs mean nothing to the people running this tool — they want to know
an update exists, not which selector moved. The GitHub release keeps the honest
notes for whoever is debugging; the app shows one of these instead.

The line is chosen from the release tag rather than at random, so the same update
always shows the same joke. A line that changed every time the dialog opened
would look like a glitch.
"""

import zlib

JOKE_NOTES = [
    "Now 12% more automatic.",
    "Downloaded 10 TB of updates. And some viruses. You're welcome.",
    "We fixed the bug. A new one has taken its place.",
    "Removed one semicolon. Everything works now.",
    "Contains 400 improvements you will never notice.",
    "This update was tested by exactly zero people.",
    "Made the buttons 2 pixels rounder. Worth it.",
    "Taught the app to count past 5.",
    "The intern touched something. Please restart.",
    "Bribed Brightspace to cooperate. It refused. Trying again.",
    "Now with 100% more electrons.",
    "We renamed a variable. It took three hours.",
    "Fixed the thing that broke when we fixed the last thing.",
    "Shadow DOM has been contained. For now.",
    "Deleted 4000 lines nobody was using. Probably.",
    "Fed the hamsters. Speed increased.",
    "Somebody said \"it works on my machine\". We shipped it.",
    "Now compatible with Tuesdays.",
    "Contains no known bugs. Only unknown ones.",
    "Kaltura is still Kaltura. Sorry.",
    "Improved performance by believing in ourselves.",
    "We apologize for the previous update. And for this one.",
    "Rearranged the code alphabetically. No reason.",
    "Playwright is now 4% less dramatic.",
    "This update includes free air.",
    "Added more update messages. That is the update.",
    "Nothing changed. Restart anyway.",
    "Removed the feature nobody liked. You know the one.",
    "Moodle is now slightly less mysterious.",
    "30% fewer mysterious crashes. Statistically speaking.",
]


def note_for(tag: str) -> str:
    """Pick a stable joke for this release tag.

    crc32 rather than hash() — Python randomizes string hashing per process, so
    hash() would hand the same update a different joke on every launch.
    """
    if not tag:
        return JOKE_NOTES[0]
    return JOKE_NOTES[zlib.crc32(tag.encode("utf-8")) % len(JOKE_NOTES)]
