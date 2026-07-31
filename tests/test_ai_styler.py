import sys

import httpx
import pytest

sys.path.insert(0, "src")

import anthropic

import ai_styler


SOURCE_HTML = "<p>hello world</p>"
STYLED_HTML = "<p class='themed'>hello world, restyled for the theme</p>"


class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Usage:
    input_tokens = 100
    output_tokens = 200


class _Response:
    stop_reason = "end_turn"
    usage = _Usage()

    def __init__(self, text):
        self.content = [_Block(text)]


class _Stream:
    def __init__(self, text):
        self._text = text

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return _Response(self._text)


class _Messages:
    """Raises a connection error for the first `fail_times` calls, then succeeds."""

    def __init__(self, fail_times):
        self.fail_times = fail_times
        self.calls = 0

    def stream(self, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise anthropic.APIConnectionError(
                request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
            )
        return _Stream(STYLED_HTML)


class _FakeClient:
    def __init__(self, fail_times):
        self.messages = _Messages(fail_times)


@pytest.fixture(autouse=True)
def _no_sleep_and_stub_prompt(monkeypatch):
    monkeypatch.setattr(ai_styler.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        ai_styler, "_load_prompt", lambda theme: "{source_html}|{style_reference_html}"
    )


def _run(monkeypatch, fail_times):
    client = _FakeClient(fail_times)
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: client)
    result, usage = ai_styler.apply_style(
        source_html=SOURCE_HTML,
        style_reference_html="",
        theme_name="lake",
        api_key="test-key",
        model="claude-opus-5",
    )
    return client.messages.calls, result, usage


def test_connection_error_is_retried_then_succeeds(monkeypatch):
    """A transient connection error used to abandon the page after one attempt —
    APIConnectionError is not an APIStatusError, so it fell to the catch-all."""
    calls, result, usage = _run(monkeypatch, fail_times=1)

    assert calls == 2, "should have retried after the connection error"
    assert result == STYLED_HTML
    assert usage["input_tokens"] == 100


def test_connection_error_gives_up_after_max_retries(monkeypatch):
    calls, result, usage = _run(monkeypatch, fail_times=ai_styler._MAX_RETRIES)

    assert calls == ai_styler._MAX_RETRIES
    assert result is None
    assert usage is None


def test_non_connection_error_still_fails_fast(monkeypatch):
    """Unexpected errors should not burn retries — only network blips do."""

    class _Boom:
        calls = 0

        def stream(self, **kwargs):
            type(self).calls += 1
            raise ValueError("bad prompt")

    client = type("C", (), {"messages": _Boom()})()
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: client)

    result, usage = ai_styler.apply_style(
        source_html=SOURCE_HTML,
        style_reference_html="",
        theme_name="lake",
        api_key="test-key",
    )

    assert _Boom.calls == 1
    assert result is None and usage is None
