"""Test-wide safety net for the OS credential store.

SettingsPanel saves the Claude API key straight into the real Windows
Credential Manager on a 500 ms debounce, so any test that types into the key
field silently overwrites the developer's actual key — which is exactly what
happened: a run of this suite replaced a live key with the literal string
"new-value", and the failure only surfaced later as an HTTP 401 at the end of
a long collector run.

Nothing in the tests should ever reach the real keyring, so it is replaced for
the whole session with an in-memory stand-in.
"""
import pytest


@pytest.fixture(autouse=True)
def _never_touch_the_real_keyring(monkeypatch):
    import keyring

    store: dict = {}

    def fake_set(service, username, password):
        store[(service, username)] = password

    def fake_get(service, username):
        return store.get((service, username))

    def fake_delete(service, username):
        store.pop((service, username), None)

    monkeypatch.setattr(keyring, "set_password", fake_set)
    monkeypatch.setattr(keyring, "get_password", fake_get)
    monkeypatch.setattr(keyring, "delete_password", fake_delete)
    return store
