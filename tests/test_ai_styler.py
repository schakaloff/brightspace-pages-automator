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
        sent_source = prompt.split("SOURCE_START\n", 1)[1].split("\nSOURCE_END", 1)[0]
        return _Stream(sent_source.replace("<p", "<p class='themed'", 1))


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
async def test_empty_exception_message_still_identifies_the_error_type(monkeypatch):
    class _BlankError(Exception):
        pass

    class _Boom:
        def stream(self, **kwargs):
            return _AsyncStreamContextManager(self._stream_impl(**kwargs))

        def _stream_impl(self, **kwargs):
            raise _BlankError()

    logs = []
    client = type("C", (), {"messages": _Boom()})()
    monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda **kwargs: client)

    result, usage = await ai_styler.apply_style(
        source_html=SOURCE_HTML,
        style_reference_html="",
        theme_name="lake",
        api_key="test-key",
        log_callback=lambda message, level: logs.append(message),
    )

    assert result is None and usage is None
    assert any("_BlankError" in message for message in logs)


@pytest.mark.asyncio
async def test_response_without_text_is_reported_without_saving(monkeypatch):
    class _NonTextBlock:
        type = "tool_use"

    class _NoTextResponse:
        stop_reason = "end_turn"
        usage = _Usage()
        content = [_NonTextBlock()]

    class _NoTextStream:
        async def get_final_message(self):
            return _NoTextResponse()

    class _MessagesWithoutText:
        def stream(self, **kwargs):
            return _AsyncStreamContextManager(_NoTextStream())

    logs = []
    client = type("C", (), {"messages": _MessagesWithoutText()})()
    monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda **kwargs: client)

    result, usage = await ai_styler.apply_style(
        source_html=SOURCE_HTML,
        style_reference_html="",
        theme_name="lake",
        api_key="test-key",
        log_callback=lambda message, level: logs.append(message),
    )

    assert result is None and usage is None
    assert any("returned no HTML text" in message for message in logs)


@pytest.mark.asyncio
async def test_refusal_retries_once_with_default_model_and_preserves_content(monkeypatch):
    class _RefusalResponse:
        stop_reason = "refusal"
        stop_details = {"category": "safety"}
        usage = _Usage()
        content = []

    class _RefusalStream:
        async def get_final_message(self):
            return _RefusalResponse()

    class _FallbackMessages:
        def __init__(self):
            self.models = []

        def stream(self, **kwargs):
            self.models.append(kwargs["model"])
            if kwargs["model"] == "claude-opus-5":
                return _AsyncStreamContextManager(_RefusalStream())
            prompt = kwargs["messages"][0]["content"]
            sent_source = prompt.split("SOURCE_START\n", 1)[1].split("\nSOURCE_END", 1)[0]
            return _AsyncStreamContextManager(
                _Stream(sent_source.replace("<p", "<p class='themed'", 1))
            )

    logs = []
    messages = _FallbackMessages()
    client = type("C", (), {"messages": messages})()
    monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda **kwargs: client)

    result, usage = await ai_styler.apply_style(
        source_html=SOURCE_HTML,
        style_reference_html="",
        theme_name="lake",
        api_key="test-key",
        model="claude-opus-5",
        log_callback=lambda message, level: logs.append(message),
    )

    assert messages.models == ["claude-opus-5", ai_styler.DEFAULT_MODEL]
    assert "hello world" in result
    assert "themed" in result
    assert usage["cost_cad"] == ai_styler._cost_cad(
        ai_styler.DEFAULT_MODEL, _Usage.input_tokens, _Usage.output_tokens
    )
    assert any("Retrying once with claude-sonnet-5" in message for message in logs)


@pytest.mark.asyncio
async def test_default_model_refusal_does_not_retry_or_save(monkeypatch):
    class _RefusalResponse:
        stop_reason = "refusal"
        usage = _Usage()
        content = []

    class _RefusalStream:
        async def get_final_message(self):
            return _RefusalResponse()

    class _RefusalMessages:
        calls = 0

        def stream(self, **kwargs):
            self.calls += 1
            return _AsyncStreamContextManager(_RefusalStream())

    logs = []
    messages = _RefusalMessages()
    client = type("C", (), {"messages": messages})()
    monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda **kwargs: client)

    result, usage = await ai_styler.apply_style(
        source_html=SOURCE_HTML,
        style_reference_html="",
        theme_name="lake",
        api_key="test-key",
        model=ai_styler.DEFAULT_MODEL,
        log_callback=lambda message, level: logs.append(message),
    )

    assert messages.calls == 1
    assert result is None and usage is None
    assert any("refused this formatting request" in message for message in logs)


CONTENT_BLOCKS = [
    "<h2>Weekly resources</h2>",
    "<p>Bring your course notes to class on Monday. Read the assigned chapter before the practice activity.</p>",
    '<p><a href="/notes.pdf">Download the lecture notes</a></p>',
    '<img src="/diagram.png" alt="Course diagram">',
    '<iframe src="https://example.test/practice/embed"></iframe>',
]
COMPACT_HTML = "".join(CONTENT_BLOCKS)
# Styling can eliminate thousands of characters of redundant wrapper markup.
BLOATED_HTML = "".join(
    f'<div class="{"legacy-formatting-" * 500}">{block}</div>'
    for block in CONTENT_BLOCKS
)


def sequence_client(monkeypatch, responses):
    class Messages:
        def __init__(self):
            self.prompts = []

        def stream(self, **kwargs):
            self.prompts.append(kwargs["messages"][0]["content"])
            response = responses[len(self.prompts) - 1]
            if isinstance(response, Exception):
                raise response
            return _AsyncStreamContextManager(_Stream(response))

    client = type("C", (), {"messages": Messages()})()
    monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda **kwargs: client)
    return client.messages


async def run_compact(logs):
    return await ai_styler.apply_style(
        BLOATED_HTML, "", "lake", "test-key", log_callback=lambda message, level: logs.append(message)
    )


@pytest.mark.asyncio
async def test_short_complete_output_is_accepted_without_another_request(monkeypatch):
    messages = sequence_client(monkeypatch, [COMPACT_HTML])
    logs = []
    result, usage = await run_compact(logs)
    assert len(result) < len(ai_styler._clean_html(BLOATED_HTML)) * 0.5
    assert "lecture notes" in result
    assert len(messages.prompts) == 1
    assert usage["input_tokens"] == 100
    assert any("Styled content verified" in message for message in logs)
    assert not any("suspiciously short" in message for message in logs)


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", CONTENT_BLOCKS[1:])
async def test_short_missing_content_retries_from_original_and_accepts_repair(monkeypatch, missing):
    incomplete = COMPACT_HTML.replace(missing, "")
    messages = sequence_client(monkeypatch, [incomplete, COMPACT_HTML])
    result, usage = await run_compact([])
    assert result
    assert len(messages.prompts) == 2
    assert ai_styler._clean_html(BLOATED_HTML) in messages.prompts[1]
    assert "CONTENT REPAIR" in messages.prompts[1]
    assert usage["input_tokens"] == 200
    assert usage["output_tokens"] == 400
    assert usage["cost_cad"] == ai_styler._cost_cad(ai_styler.DEFAULT_MODEL, 200, 400)


@pytest.mark.asyncio
async def test_incomplete_repair_is_rejected_even_if_it_has_more_markup(monkeypatch):
    incomplete = COMPACT_HTML.replace(CONTENT_BLOCKS[1], "")
    padded = "<style>/*" + "padding " * 8000 + "*/</style>" + incomplete
    assert len(padded) > len(ai_styler._clean_html(BLOATED_HTML)) * 0.5
    messages = sequence_client(monkeypatch, [incomplete, padded])
    logs = []
    assert await run_compact(logs) == (None, None)
    assert len(messages.prompts) == 2
    assert any("still has missing content" in message and "Bring your course notes" in message for message in logs)


@pytest.mark.asyncio
async def test_content_retry_is_available_after_network_retries(monkeypatch):
    failure = anthropic.APIConnectionError(
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    )
    incomplete = COMPACT_HTML.replace(CONTENT_BLOCKS[2], "")
    messages = sequence_client(monkeypatch, [failure, failure, incomplete, COMPACT_HTML])
    result, usage = await run_compact([])
    assert result
    assert len(messages.prompts) == 4
    assert usage["input_tokens"] == 200


@pytest.mark.asyncio
async def test_content_retry_does_not_create_an_unbounded_network_retry(monkeypatch):
    failure = anthropic.APIConnectionError(
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    )
    incomplete = COMPACT_HTML.replace(CONTENT_BLOCKS[2], "")
    messages = sequence_client(monkeypatch, [incomplete, failure, failure, failure])
    assert await run_compact([]) == (None, None)
    assert len(messages.prompts) == 4


@pytest.mark.asyncio
async def test_empty_code_fence_is_not_a_usable_result(monkeypatch):
    messages = sequence_client(monkeypatch, ["```html\n```"])
    assert await run_compact([]) == (None, None)
    assert len(messages.prompts) == 1
