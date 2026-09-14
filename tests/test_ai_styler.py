import sys

import httpx
import pytest

sys.path.insert(0, "src")

import anthropic

import ai_styler


SOURCE_HTML = "<p>hello world</p>"


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



class _AsyncStreamContextManager:
    def __init__(self, stream):
        self.stream = stream

    async def __aenter__(self):
        return self.stream

    async def __aexit__(self, *exc):
        return False

class _Stream:
    def __init__(self, text):
        self._text = text

    async def get_final_message(self):
        return _Response(self._text)


class _Messages:
    """Raises a connection error for the first `fail_times` calls, then succeeds."""

    def __init__(self, fail_times):
        self.fail_times = fail_times
        self.calls = 0

    def stream(self, **kwargs):
        return _AsyncStreamContextManager(self._stream_impl(**kwargs))

    def _stream_impl(self, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise anthropic.APIConnectionError(
                request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
            )
        prompt = kwargs["messages"][0]["content"]
        protected_source = prompt.split("SOURCE_START\n", 1)[1].split("\nSOURCE_END", 1)[0]
        return _Stream(protected_source.replace("<p", "<p class='themed'", 1))


class _FakeClient:
    def __init__(self, fail_times):
        self.messages = _Messages(fail_times)


async def _mock_sleep(_s):
    pass

@pytest.fixture(autouse=True)
def _no_sleep_and_stub_prompt(monkeypatch):
    monkeypatch.setattr(ai_styler.asyncio, "sleep", _mock_sleep)
    monkeypatch.setattr(
        ai_styler,
        "_load_prompt",
        lambda theme: "SOURCE_START\n{source_html}\nSOURCE_END\n{style_reference_html}",
    )


async def _run(monkeypatch, fail_times):
    client = _FakeClient(fail_times)
    monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda **kwargs: client)
    result, usage = await ai_styler.apply_style(
        source_html=SOURCE_HTML,
        style_reference_html="",
        theme_name="lake",
        api_key="test-key",
        model="claude-opus-5",
    )
    return client.messages.calls, result, usage


@pytest.mark.asyncio
async def test_connection_error_is_retried_then_succeeds(monkeypatch):
    """A transient connection error used to abandon the page after one attempt —
    APIConnectionError is not an APIStatusError, so it fell to the catch-all."""
    calls, result, usage = await _run(monkeypatch, fail_times=1)

    assert calls == 2, "should have retried after the connection error"
    assert "hello world" in result
    assert "themed" in result
    assert usage["input_tokens"] == 100


@pytest.mark.asyncio
async def test_connection_error_gives_up_after_max_retries(monkeypatch):
    calls, result, usage = await _run(monkeypatch, fail_times=ai_styler._MAX_RETRIES)

    assert calls == ai_styler._MAX_RETRIES
    assert result is None
    assert usage is None


@pytest.mark.asyncio
async def test_non_connection_error_still_fails_fast(monkeypatch):
    """Unexpected errors should not burn retries — only network blips do."""

    class _Boom:
        calls = 0

        def stream(self, **kwargs):
            return _AsyncStreamContextManager(self._stream_impl(**kwargs))

        def _stream_impl(self, **kwargs):
            type(self).calls += 1
            raise ValueError("bad prompt")

    client = type("C", (), {"messages": _Boom()})()
    monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda **kwargs: client)

    result, usage = await ai_styler.apply_style(
        source_html=SOURCE_HTML,
        style_reference_html="",
        theme_name="lake",
        api_key="test-key",
    )

    assert _Boom.calls == 1
    assert result is None and usage is None


@pytest.mark.asyncio
async def test_ai_result_that_drops_placeholders_is_rejected(monkeypatch):
    class _UnsafeMessages:
        def stream(self, **kwargs):
            return _AsyncStreamContextManager(_Stream("<p>AI-rewritten words</p>"))

    client = type("C", (), {"messages": _UnsafeMessages()})()
    monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda **kwargs: client)
    logs = []

    result, usage = await ai_styler.apply_style(
        source_html=SOURCE_HTML,
        style_reference_html="",
        theme_name="lake",
        api_key="test-key",
        log_callback=lambda message, level: logs.append(message),
    )

    assert result is None and usage is None
    assert any("Content-integrity check failed" in message for message in logs)


def test_existing_presentation_css_and_generic_scripts_can_still_be_cleaned():
    from content_preservation import protect_html

    source = (
        '<style>.old { color: red; }</style>'
        '<script src="/ordinary-widget.js">setupWidget()</script>'
        '<p>Authored words remain.</p>'
    )
    protection = protect_html(source)
    cleaned = ai_styler._clean_html(protection.protected_html)
    restored = protection.restore_and_validate(cleaned)

    assert "Authored words remain." in restored
    assert "ordinary-widget.js" not in restored
