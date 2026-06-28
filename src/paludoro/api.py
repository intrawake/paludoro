import logging
import time
from collections import deque
from sxpb_llm import async_call_api as _sxpb_async_call_api
from sxpb_llm import async_call_image_api as _sxpb_async_call_image_api

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory LLM request log
# ---------------------------------------------------------------------------
_llm_request_log: deque = deque(maxlen=10)


def get_llm_request_log() -> list[dict]:
    return list(_llm_request_log)


def clear_llm_request_log() -> None:
    _llm_request_log.clear()


def set_llm_log_maxlen(n: int) -> None:
    global _llm_request_log
    _llm_request_log = deque(_llm_request_log, maxlen=max(1, n))


def _log_request(
    *,
    model: str,
    agent_name: str,
    messages: list[dict],
    response: str | None,
    error: str | None,
    duration: float,
    kind: str = "text",
    reasoning: str | None = None,
) -> None:
    def truncate(s):
        if not isinstance(s, str):
            return s
        if len(s) > 10000:
            if s.startswith("data:image"):
                return s[:50] + "... [TRUNCATED IMAGE BASE64] ..."
            return s[:10000] + "... [TRUNCATED] ..."
        return s

    clean_messages = []
    for m in messages:
        clean_messages.append({"role": m["role"], "content": truncate(m["content"])})

    entry: dict = {
        "timestamp": time.time(),
        "model": model,
        "agent": agent_name,
        "kind": kind,
        "duration": round(duration, 2),
        "request_messages": clean_messages,
        "response": truncate(response),
        "error": error,
        "reasoning": truncate(reasoning) if reasoning else None,
    }
    _llm_request_log.append(entry)


def _get_tracer():
    try:
        from opentelemetry import trace

        return trace.get_tracer(__name__)
    except ImportError:
        return None


async def call_api(
    model,
    messages,
    api_url,
    api_key=None,
    record_content=False,
    agent_name="unknown",
):
    """Thin wrapper around sxpb_llm.async_call_api.

    Keeps paludoro-specific OTel tracing and in-memory request logging.
    """
    tracer = _get_tracer()
    if tracer:
        span_ctx = tracer.start_as_current_span("call_api")
    else:

        class DummySpan:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def set_attribute(self, *args):
                pass

        span_ctx = DummySpan()

    with span_ctx as span:
        span.set_attribute("model", model)
        span.set_attribute("api_url", api_url)
        if record_content:
            import json

            span.set_attribute("messages", json.dumps(messages))

        t0 = time.time()
        content, _payload, response = await _sxpb_async_call_api(
            model,
            messages,
            api_url=api_url,
            api_key=api_key,
            return_full=True,
        )

        if content is None:
            _log_request(
                model=model,
                agent_name=agent_name,
                messages=messages,
                response=None,
                error="API call failed",
                duration=time.time() - t0,
            )
            return ""

        # Extract reasoning for the log (not available from content-only return).
        msg = response.get("choices", [{}])[0].get("message", {})
        reasoning = msg.get("reasoning") or msg.get("reasoning_content")

        if record_content:
            span.set_attribute("response", content)

        _log_request(
            model=model,
            agent_name=agent_name,
            messages=messages,
            response=content,
            error=None,
            duration=time.time() - t0,
            reasoning=reasoning,
        )
        return content


async def call_image_api(
    model,
    prompt,
    api_url,
    api_key=None,
    record_content=False,
    agent_name="unknown",
):
    """Thin wrapper around sxpb_llm.async_call_image_api.

    Keeps paludoro-specific OTel tracing and in-memory request logging.
    Adds ``data:image/png;base64,`` prefix to the raw base64 result.
    """
    tracer = _get_tracer()
    if tracer:
        span_ctx = tracer.start_as_current_span("call_image_api")
    else:

        class DummySpan:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def set_attribute(self, *args):
                pass

        span_ctx = DummySpan()

    with span_ctx as span:
        span.set_attribute("model", model)
        span.set_attribute("api_url", api_url)
        if record_content:
            span.set_attribute("prompt", prompt)

        img_messages = [{"role": "user", "content": prompt}]
        t0 = time.time()
        b64 = await _sxpb_async_call_image_api(
            model,
            prompt,
            api_url=api_url,
            api_key=api_key,
        )

        if b64 is None:
            _log_request(
                model=model,
                agent_name=agent_name,
                messages=img_messages,
                response=None,
                error="Image API call failed",
                duration=time.time() - t0,
                kind="image",
            )
            return None

        result = f"data:image/png;base64,{b64}"
        _log_request(
            model=model,
            agent_name=agent_name,
            messages=img_messages,
            response="(image generated)",
            error=None,
            duration=time.time() - t0,
            kind="image",
        )
        return result
