"""Kaltura data adapter for the shared saved-transfer library."""
from types import SimpleNamespace
from panels.h5p_saved_transfers import SavedH5PTransfersDialog
from kaltura_progress import saved_runs, describe_run, set_run_removed


class SavedKalturaTransfersDialog(SavedH5PTransfersDialog):
    def __init__(self, directory=None, parent=None):
        super().__init__(directory, parent, provider=SimpleNamespace(
            title='Saved Kaltura transfers', list_runs=saved_runs, describe=describe_run,
            set_removed=set_run_removed, row_height=112,
            filters=(('All saved', 'all'), ('Needs attention', 'attention'), ('Ready to review', 'ready'),
                     ('Complete', 'complete'), ('No Kaltura videos', 'empty'), ('Removed', 'removed')),
            removal_note='Remove from list hides the entry. Moodle videos, Kaltura media, Brightspace pages and recovery history are kept. Use Undo or restore it from Removed.'))
