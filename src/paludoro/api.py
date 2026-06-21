import base64
import json
import logging
import time
from collections import deque

import httpx

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
    payload = {
        "model": model,
        "messages": messages,
    }

    target_url = api_url
    if not target_url.endswith("/chat/completions"):
        if not target_url.endswith("/"):
            target_url += "/"
        target_url += "chat/completions"

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

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
            span.set_attribute("messages", json.dumps(messages))

        t0 = time.time()
        async with httpx.AsyncClient(timeout=None) as client:
            for attempt in range(5):
                try:
                    resp = await client.post(target_url, json=payload, headers=headers)
                    resp.raise_for_status()
                    res_data = resp.json()
                    msg = res_data["choices"][0]["message"]
                    content = msg.get("content")
                    reasoning = msg.get("reasoning") or msg.get("reasoning_content")
                    if content is None:
                        _log_request(
                            model=model,
                            agent_name=agent_name,
                            messages=messages,
                            response=None,
                            error="response content was None",
                            duration=time.time() - t0,
                            reasoning=reasoning,
                        )
                        return ""
                    if record_content:
                        span.set_attribute("response", content.strip())
                    _log_request(
                        model=model,
                        agent_name=agent_name,
                        messages=messages,
                        response=content.strip(),
                        error=None,
                        duration=time.time() - t0,
                        reasoning=reasoning,
                    )
                    return content.strip()
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 429:
                        if attempt == 4:
                            logger.error("Rate limited on final attempt. Giving up.")
                            _log_request(
                                model=model,
                                agent_name=agent_name,
                                messages=messages,
                                response=None,
                                error="Rate limited (429) on all attempts",
                                duration=time.time() - t0,
                            )
                            break
                        sleep_time = 32 if attempt < 2 else 64
                        logger.warning(
                            f"Rate limited (429) on attempt {attempt + 1}. Retrying in {sleep_time}s..."
                        )
                        import asyncio

                        await asyncio.sleep(sleep_time)
                        continue
                    else:
                        resp_body = e.response.text
                        logger.error(
                            f"API call failed with HTTP error {e.response.status_code}: {e}\nResponse body: {resp_body}"
                        )
                        span.set_attribute("error", True)
                        span.set_attribute("error.message", resp_body)
                        _log_request(
                            model=model,
                            agent_name=agent_name,
                            messages=messages,
                            response=None,
                            error=f"HTTP {e.response.status_code}: {resp_body[:500]}",
                            duration=time.time() - t0,
                        )
                        break
                except Exception as e:
                    logger.error(f"API call failed: {e}")
                    _log_request(
                        model=model,
                        agent_name=agent_name,
                        messages=messages,
                        response=None,
                        error=str(e),
                        duration=time.time() - t0,
                    )
                    break
        return ""


async def call_image_api(
    model,
    prompt,
    api_url,
    api_key=None,
    record_content=False,
    agent_name="unknown",
):
    payload = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "response_format": "b64_json",
    }

    target_url = api_url or ""
    if not target_url.endswith("/images/generations"):
        if not target_url.endswith("/"):
            target_url += "/"
        target_url += "images/generations"

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

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
        async with httpx.AsyncClient(timeout=None) as client:
            for attempt in range(5):
                try:
                    resp = await client.post(target_url, json=payload, headers=headers)
                    resp.raise_for_status()
                    res_data = resp.json()
                    if "data" in res_data and len(res_data["data"]) > 0:
                        item = res_data["data"][0]
                        if item.get("b64_json"):
                            result = f"data:image/png;base64,{item['b64_json']}"
                        elif item.get("url"):
                            img_resp = await client.get(item["url"], timeout=None)
                            img_resp.raise_for_status()
                            b64_img = base64.b64encode(img_resp.content).decode("utf-8")
                            result = f"data:image/png;base64,{b64_img}"
                        else:
                            result = None
                        _log_request(
                            model=model,
                            agent_name=agent_name,
                            messages=img_messages,
                            response="(image generated)" if result else None,
                            error=None,
                            duration=time.time() - t0,
                            kind="image",
                        )
                        return result
                    _log_request(
                        model=model,
                        agent_name=agent_name,
                        messages=img_messages,
                        response=None,
                        error="No image data in response",
                        duration=time.time() - t0,
                        kind="image",
                    )
                    return None
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 429:
                        if attempt == 4:
                            logger.error("Image rate limited on final attempt.")
                            _log_request(
                                model=model,
                                agent_name=agent_name,
                                messages=img_messages,
                                response=None,
                                error="Image rate limited (429) on all attempts",
                                duration=time.time() - t0,
                                kind="image",
                            )
                            break
                        sleep_time = 32 if attempt < 2 else 64
                        logger.warning(
                            f"Image rate limited (429) on attempt {attempt + 1}. Retrying in {sleep_time}s..."
                        )
                        import asyncio

                        await asyncio.sleep(sleep_time)
                        continue
                    else:
                        resp_content = e.response.text
                        logger.error(
                            f"Image API call failed with HTTP error {e.response.status_code}: {e}\nResponse: {resp_content}"
                        )
                        _log_request(
                            model=model,
                            agent_name=agent_name,
                            messages=img_messages,
                            response=None,
                            error=f"HTTP {e.response.status_code}: {resp_content[:500]}",
                            duration=time.time() - t0,
                            kind="image",
                        )
                        break
                except Exception as e:
                    logger.error(f"Failed image API call: {e}")
                    _log_request(
                        model=model,
                        agent_name=agent_name,
                        messages=img_messages,
                        response=None,
                        error=str(e),
                        duration=time.time() - t0,
                        kind="image",
                    )
                    break
        return None
