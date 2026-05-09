"""Tests for the in-memory LLM request log."""

import pytest
from paludoro.api import (
    get_llm_request_log,
    set_llm_log_maxlen,
    clear_llm_request_log,
    _log_request,
)


@pytest.fixture(autouse=True)
def reset_log():
    """Reset the log before each test."""
    clear_llm_request_log()
    set_llm_log_maxlen(10)
    yield


def test_log_starts_empty():
    assert get_llm_request_log() == []


def test_log_records_entry():
    _log_request(
        model="test-model",
        agent_name="main",
        messages=[{"role": "user", "content": "hello"}],
        response="hi there",
        error=None,
        duration=1.23,
    )
    log = get_llm_request_log()
    assert len(log) == 1
    entry = log[0]
    assert entry["model"] == "test-model"
    assert entry["agent"] == "main"
    assert entry["response"] == "hi there"
    assert entry["error"] is None
    assert entry["duration"] == 1.23
    assert entry["kind"] == "text"
    assert "timestamp" in entry


def test_log_records_error():
    _log_request(
        model="test-model",
        agent_name="transcript",
        messages=[{"role": "user", "content": "prompt"}],
        response=None,
        error="HTTP 500: internal error",
        duration=2.5,
    )
    log = get_llm_request_log()
    assert len(log) == 1
    assert log[0]["error"] == "HTTP 500: internal error"
    assert log[0]["response"] is None


def test_log_respects_maxlen():
    set_llm_log_maxlen(3)
    for i in range(5):
        _log_request(
            model="m",
            agent_name="a",
            messages=[],
            response=f"resp-{i}",
            error=None,
            duration=0.1,
        )
    log = get_llm_request_log()
    assert len(log) == 3
    assert log[0]["response"] == "resp-2"
    assert log[2]["response"] == "resp-4"


def test_set_llm_log_maxlen_preserves_existing():
    for i in range(5):
        _log_request(
            model="m",
            agent_name="a",
            messages=[],
            response=f"r{i}",
            error=None,
            duration=0.1,
        )
    assert len(get_llm_request_log()) == 5
    set_llm_log_maxlen(3)
    log = get_llm_request_log()
    assert len(log) == 3
    assert log[0]["response"] == "r2"


def test_set_llm_log_maxlen_minimum_one():
    set_llm_log_maxlen(0)
    _log_request(
        model="m",
        agent_name="a",
        messages=[],
        response="x",
        error=None,
        duration=0.1,
    )
    assert len(get_llm_request_log()) == 1


def test_image_kind():
    _log_request(
        model="flux",
        agent_name="image_agent",
        messages=[{"role": "user", "content": "a cat"}],
        response="(image generated)",
        error=None,
        duration=5.0,
        kind="image",
    )
    log = get_llm_request_log()
    assert log[0]["kind"] == "image"


def test_log_truncation():
    large_content = "A" * 15000
    img_b64 = "data:image/png;base64," + ("B" * 15000)

    _log_request(
        model="m",
        agent_name="a",
        messages=[{"role": "user", "content": large_content}],
        response=img_b64,
        error=None,
        duration=0.1,
    )

    log = get_llm_request_log()
    entry = log[0]

    # Content should be truncated to 10000 + "... [TRUNCATED] ..."
    assert len(entry["request_messages"][0]["content"]) < 15000
    assert "[TRUNCATED]" in entry["request_messages"][0]["content"]

    # Image base64 should be truncated even more aggressively if it starts with data:image
    assert len(entry["response"]) < 100
    assert "[TRUNCATED IMAGE BASE64]" in entry["response"]
