"""Non-Anthropic live `ModelAdapter`s: OpenAI chat completions and Gemini generateContent.

Cross-provider extension (sol.md §16). Stdlib only (`urllib.request`): no new dependency. Every
adapter takes an injectable `transport(url, headers, body_dict) -> (status, body_dict)`, so tests
never touch the network.

Both adapters keep the runtime's message history in Anthropic shape (user strings; assistant
`text`/`tool_use` blocks; user `tool_result`/`text` blocks) and translate it per request. Their
responses come back as Anthropic-shaped blocks (`{"type": "text"}`, `{"type": "tool_use", "id",
"name", "input"}`) with the stop reason mapped to `tool_use` / `end_turn` / `max_tokens`, so the
publisher loop, the chain roles and scoring are unchanged.

Spend and errors mirror `AnthropicAdapter`:
- reserve through the ledger before dispatch, priced by this adapter's model (`model=`);
- settle to actual usage after a success, release on any failure;
- HTTP 429/5xx and connection errors -> `InfraError` (the only retried failure);
- other 4xx -> `RuntimeError` (a configuration bug: abort loudly, never retried).
Error text carries the status and the provider's error message, never headers or the API key
(`infra.error.detail` is written to `events.jsonl`).
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from aiv.contract import InfraError, ModelResponse, canonical_json

Transport = Callable[[str, Mapping[str, str], Mapping[str, Any]], tuple[int, Any]]

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_TIMEOUT_S = 120.0

_NETWORK_ERRORS = (
    urllib.error.URLError,
    ConnectionError,
    TimeoutError,
    socket.timeout,
    http.client.HTTPException,
)


def _parse_body(raw: bytes) -> Any:
    text = raw.decode("utf-8", errors="replace") if raw else ""
    if not text:
        return {}
    try:
        return json.loads(text)
    except ValueError:
        return {"raw": text[:2000]}


def default_transport(
    url: str,
    headers: Mapping[str, str],
    body: Mapping[str, Any],
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> tuple[int, Any]:
    """POST `body` as JSON. HTTP error statuses are returned, not raised, so the status mapping
    lives in one place (`_HttpAdapter.complete`). Network failures raise `InfraError`."""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={**headers, "Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status), _parse_body(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read()
        except Exception:
            raw = b""
        return int(exc.code), _parse_body(raw)
    except _NETWORK_ERRORS as exc:
        reason = getattr(exc, "reason", None) or exc
        raise InfraError(f"network error: {type(exc).__name__}: {reason}") from None


def _error_message(data: Any) -> str:
    if isinstance(data, Mapping):
        err = data.get("error")
        if isinstance(err, Mapping):
            return str(err.get("message") or err.get("status") or err)[:500]
        if err:
            return str(err)[:500]
        if "raw" in data:
            return str(data["raw"])[:500]
    return str(data)[:500]


def _tool_result_text(content: Any) -> str:
    """A `tool_result.content` (a JSON string from the loop, or a list of text blocks) as text."""
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence):
        parts = [
            str(b.get("text", ""))
            for b in content
            if isinstance(b, Mapping) and b.get("type") == "text"
        ]
        return "\n".join(parts)
    return canonical_json(content)


def _blocks(content: Any) -> list[Mapping[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in (content or []) if isinstance(b, Mapping)]


class _HttpAdapter:
    """Shared reserve -> dispatch -> map status -> settle/release logic."""

    provider = "http"

    def __init__(
        self,
        model: str,
        *,
        api_key_env: str,
        episode_id: str,
        ledger: Any = None,
        transport: Transport | None = None,
        env: Mapping[str, str] | None = None,
    ):
        if not model:
            raise ValueError(f"{self.provider}: a model id is required")
        self.requested_model = model
        self.api_key_env = api_key_env
        self._episode_id = episode_id
        self._ledger = ledger
        self._transport = transport or default_transport
        self._env = env

    # ---------------------------------------------------------------- provider hooks

    def _request(
        self,
        *,
        system: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        max_tokens: int,
        temperature: float,
        api_key: str,
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        raise NotImplementedError

    def _parse(self, data: Any) -> tuple[list[dict[str, Any]], str, str, int | None, int | None]:
        """-> (content blocks, stop_reason, returned_model, input_tokens, output_tokens)."""
        raise NotImplementedError

    # ---------------------------------------------------------------- shared flow

    def _api_key(self) -> str:
        env = os.environ if self._env is None else self._env
        key = (env.get(self.api_key_env) or "").strip()
        if not key:
            raise RuntimeError(f"{self.provider}: {self.api_key_env} is not set")
        return key

    def _release(self, reservation: Any) -> None:
        if self._ledger is not None and reservation is not None:
            self._ledger.release(reservation)

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        max_tokens: int,
        temperature: float,
    ) -> ModelResponse:
        # Configuration problems (no key, untranslatable history) fail before any reservation.
        url, headers, body = self._request(
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            api_key=self._api_key(),
        )
        input_est = max(1, len(canonical_json(body)) // 4)
        reservation = None
        if self._ledger is not None:
            reservation = self._ledger.reserve(
                input_est, max_tokens, episode_id=self._episode_id, model=self.requested_model
            )
        t0 = time.monotonic()
        try:
            status, data = self._transport(url, headers, body)
        except InfraError:
            self._release(reservation)
            raise
        except _NETWORK_ERRORS as exc:
            self._release(reservation)
            raise InfraError(f"{self.provider} network error: {type(exc).__name__}") from None
        except BaseException:
            self._release(reservation)
            raise
        latency_ms = int((time.monotonic() - t0) * 1000)

        if status == 429 or status >= 500:
            self._release(reservation)
            raise InfraError(f"{self.provider} HTTP {status}: {_error_message(data)}")
        if not 200 <= status < 300:
            self._release(reservation)
            raise RuntimeError(f"{self.provider} HTTP {status}: {_error_message(data)}")
        try:
            content, stop_reason, returned_model, in_tok, out_tok = self._parse(data)
        except BaseException:
            self._release(reservation)
            raise
        # Missing usage: charge the reservation's own (conservative) estimate, never zero.
        in_tok = input_est if in_tok is None else int(in_tok)
        out_tok = max_tokens if out_tok is None else int(out_tok)
        if self._ledger is not None and reservation is not None:
            self._ledger.settle(reservation, in_tok, out_tok)
        return ModelResponse(
            content=tuple(content),
            stop_reason=stop_reason,
            returned_model=returned_model or self.requested_model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            latency_ms=latency_ms,
        )


def _parse_tool_args(raw: Any) -> Any:
    """Tool-call arguments as a dict. Malformed JSON is kept raw (a non-dict), so the publisher
    loop ends the episode as `invalid_tool` instead of this adapter raising."""
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str):
        try:
            return json.loads(raw) if raw.strip() else {}
        except ValueError:
            return raw
    return raw


# ---------------------------------------------------------------------------------------------
# OpenAI chat completions
# ---------------------------------------------------------------------------------------------

_OPENAI_STOP = {"tool_calls": "tool_use", "stop": "end_turn", "length": "max_tokens"}


def openai_messages(system: str, messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if system:
        out.append({"role": "system", "content": system})
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        blocks = _blocks(content)
        if role == "assistant":
            texts = [str(b["text"]) for b in blocks if b.get("type") == "text" and b.get("text")]
            calls = [
                {
                    "id": b.get("id"),
                    "type": "function",
                    "function": {
                        "name": b.get("name"),
                        "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False),
                    },
                }
                for b in blocks
                if b.get("type") == "tool_use"
            ]
            msg: dict[str, Any] = {"role": "assistant", "content": "\n".join(texts) or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
            continue
        # user turn: tool results first (they must follow the assistant tool_calls directly),
        # then any text (e.g. the T13 coordinator follow-up) as a separate user message.
        for b in blocks:
            if b.get("type") == "tool_result":
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": b.get("tool_use_id"),
                        "content": _tool_result_text(b.get("content")),
                    }
                )
        texts = [str(b["text"]) for b in blocks if b.get("type") == "text" and b.get("text")]
        if texts:
            out.append({"role": "user", "content": "\n".join(texts)})
    return out


def openai_tools(tools: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
            },
        }
        for t in tools
    ]


class OpenAIAdapter(_HttpAdapter):
    """Live adapter over `POST /v1/chat/completions`. Never called in tests (fake transport)."""

    provider = "openai"

    def __init__(
        self,
        model: str,
        *,
        api_key_env: str = "OPENAI_API_KEY",
        episode_id: str,
        ledger: Any = None,
        transport: Transport | None = None,
        env: Mapping[str, str] | None = None,
    ):
        super().__init__(
            model,
            api_key_env=api_key_env,
            episode_id=episode_id,
            ledger=ledger,
            transport=transport,
            env=env,
        )

    def _request(self, *, system, messages, tools, max_tokens, temperature, api_key):
        body: dict[str, Any] = {
            "model": self.requested_model,
            "messages": openai_messages(system, messages),
            "max_completion_tokens": max_tokens,
        }
        # GPT-5.x reasoning models reject a non-default temperature. 1.0 is the API default, so
        # it is omitted (same rule as AnthropicAdapter); any other value is sent unchanged.
        if temperature != 1.0:
            body["temperature"] = temperature
        if tools:
            body["tools"] = openai_tools(tools)
        headers = {"Authorization": f"Bearer {api_key}"}
        return OPENAI_URL, headers, body

    def _parse(self, data):
        choices = data.get("choices") if isinstance(data, Mapping) else None
        if not choices:
            raise RuntimeError(f"openai: response has no choices: {_error_message(data)}")
        choice = choices[0]
        msg = choice.get("message") or {}
        content: list[dict[str, Any]] = []
        text = msg.get("content")
        if isinstance(text, str) and text:
            content.append({"type": "text", "text": text})
        elif not msg.get("tool_calls") and isinstance(msg.get("refusal"), str) and msg["refusal"]:
            content.append({"type": "text", "text": msg["refusal"]})
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            content.append(
                {
                    "type": "tool_use",
                    "id": tc.get("id"),
                    "name": fn.get("name"),
                    "input": _parse_tool_args(fn.get("arguments")),
                }
            )
        finish = str(choice.get("finish_reason") or "")
        if any(b["type"] == "tool_use" for b in content):
            stop = "tool_use"
        else:
            stop = _OPENAI_STOP.get(finish, finish or "end_turn")
        usage = data.get("usage") or {}
        return (
            content,
            stop,
            data.get("model"),
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),  # includes reasoning tokens (billed as output)
        )


# ---------------------------------------------------------------------------------------------
# Gemini generateContent
# ---------------------------------------------------------------------------------------------

# Block keys only this adapter reads (other adapters never see a Gemini history).
GEMINI_SIGNATURE_KEY = "_gemini_thought_signature"
GEMINI_CALL_ID_KEY = "_gemini_call_id"
GEMINI_ID_PREFIX = "gm_"  # synthesized tool_use ids for model-generated calls
# Gemini 3 models expect the `thoughtSignature` of each model-generated functionCall back in the
# history. A tool_use the model never produced (the injected T13 lookup) has none; Google's
# guidance for injected/transferred calls is a documented placeholder value. This was not
# verified offline: check it on the first live smoke episode (pass `None` to send nothing).
GEMINI_INJECTED_SIGNATURE = "skip_thought_signature_validator"
_SCHEMA_KEYS = ("type", "description", "properties", "required", "items", "enum")


def gemini_schema(schema: Any) -> Any:
    """JSON schema -> Gemini `parameters`: keep type/description/properties/required/items/enum
    (recursively), drop everything else."""
    if not isinstance(schema, Mapping):
        return schema
    out: dict[str, Any] = {}
    for key in _SCHEMA_KEYS:
        if key not in schema:
            continue
        val = schema[key]
        if key == "properties" and isinstance(val, Mapping):
            out[key] = {name: gemini_schema(sub) for name, sub in val.items()}
        elif key == "items":
            out[key] = gemini_schema(val)
        else:
            out[key] = val
    return out


def gemini_tools(tools: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "functionDeclarations": [
                {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": gemini_schema(t.get("input_schema") or {"type": "object"}),
                }
                for t in tools
            ]
        }
    ]


def _gemini_result(content: Any) -> dict[str, Any]:
    text = _tool_result_text(content)
    try:
        parsed = json.loads(text)
    except ValueError:
        return {"text": text}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def gemini_contents(
    messages: Sequence[Mapping[str, Any]],
    *,
    injected_signature: str | None = GEMINI_INJECTED_SIGNATURE,
) -> list[dict[str, Any]]:
    # tool_use id -> block, from every assistant turn, so each tool_result recovers its name.
    calls: dict[Any, Mapping[str, Any]] = {}
    for m in messages:
        if m.get("role") == "assistant":
            for b in _blocks(m.get("content")):
                if b.get("type") == "tool_use":
                    calls[b.get("id")] = b
    contents: list[dict[str, Any]] = []
    for m in messages:
        role = "model" if m.get("role") == "assistant" else "user"
        parts: list[dict[str, Any]] = []
        for b in _blocks(m.get("content")):
            kind = b.get("type")
            if kind == "text" and b.get("text"):
                parts.append({"text": str(b["text"])})
            elif kind == "tool_use":
                call: dict[str, Any] = {"name": b.get("name"), "args": b.get("input") or {}}
                if b.get(GEMINI_CALL_ID_KEY):
                    call["id"] = b[GEMINI_CALL_ID_KEY]
                part: dict[str, Any] = {"functionCall": call}
                signature = b.get(GEMINI_SIGNATURE_KEY)
                generated = str(b.get("id") or "").startswith(GEMINI_ID_PREFIX)
                if signature is None and not generated:
                    signature = injected_signature  # a call Gemini never made (T13 injection)
                if signature:
                    part["thoughtSignature"] = signature
                parts.append(part)
            elif kind == "tool_result":
                use = calls.get(b.get("tool_use_id"))
                if use is None:
                    raise ValueError(
                        f"gemini: tool_result for unknown tool_use id {b.get('tool_use_id')!r}"
                    )
                resp: dict[str, Any] = {
                    "name": use.get("name"),
                    "response": {"result": _gemini_result(b.get("content"))},
                }
                if use.get(GEMINI_CALL_ID_KEY):
                    resp["id"] = use[GEMINI_CALL_ID_KEY]
                parts.append({"functionResponse": resp})
        if parts:
            contents.append({"role": role, "parts": parts})
    return contents


class GeminiAdapter(_HttpAdapter):
    """Live adapter over `models/{model}:generateContent`. Never called in tests."""

    provider = "gemini"

    def __init__(
        self,
        model: str,
        *,
        api_key_env: str = "GEMINI_API_KEY",
        episode_id: str,
        ledger: Any = None,
        transport: Transport | None = None,
        env: Mapping[str, str] | None = None,
        injected_signature: str | None = GEMINI_INJECTED_SIGNATURE,
    ):
        super().__init__(
            model,
            api_key_env=api_key_env,
            episode_id=episode_id,
            ledger=ledger,
            transport=transport,
            env=env,
        )
        self.injected_signature = injected_signature
        self._n_calls = 0  # per-instance, so synthesized ids stay unique across turns

    def _request(self, *, system, messages, tools, max_tokens, temperature, api_key):
        body: dict[str, Any] = {
            "contents": gemini_contents(messages, injected_signature=self.injected_signature),
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            body["tools"] = gemini_tools(tools)
        headers = {"x-goog-api-key": api_key}
        return GEMINI_URL.format(model=self.requested_model), headers, body

    def _parse(self, data):
        data = data if isinstance(data, Mapping) else {}
        candidates = data.get("candidates") or []
        cand = candidates[0] if candidates else {}
        parts = (cand.get("content") or {}).get("parts") or []
        content: list[dict[str, Any]] = []
        for part in parts:
            if not isinstance(part, Mapping) or part.get("thought"):
                continue  # thought summaries carry no tool-loop information
            if "functionCall" in part:
                fc = part.get("functionCall") or {}
                block: dict[str, Any] = {
                    "type": "tool_use",
                    "id": f"{GEMINI_ID_PREFIX}{self._n_calls}",
                    "name": fc.get("name"),
                    "input": _parse_tool_args(fc.get("args") if "args" in fc else {}),
                }
                self._n_calls += 1
                if part.get("thoughtSignature"):
                    block[GEMINI_SIGNATURE_KEY] = part["thoughtSignature"]
                if fc.get("id"):
                    block[GEMINI_CALL_ID_KEY] = fc["id"]
                content.append(block)
            elif part.get("text"):
                content.append({"type": "text", "text": str(part["text"])})
        finish = str(cand.get("finishReason") or "")
        if any(b["type"] == "tool_use" for b in content):
            stop = "tool_use"
        elif finish == "MAX_TOKENS":
            stop = "max_tokens"
        elif finish in ("STOP", ""):
            stop = "end_turn"
        else:
            stop = finish.lower()  # SAFETY, RECITATION, ... recorded as given
        usage = data.get("usageMetadata") or {}
        out_tok = None
        if "candidatesTokenCount" in usage or "thoughtsTokenCount" in usage:
            # Thinking tokens are billed as output: count them, or the ledger undercounts.
            out_tok = int(usage.get("candidatesTokenCount") or 0) + int(
                usage.get("thoughtsTokenCount") or 0
            )
        in_tok = usage.get("promptTokenCount")
        return content, stop, data.get("modelVersion"), in_tok, out_tok


__all__ = [
    "GEMINI_INJECTED_SIGNATURE",
    "GeminiAdapter",
    "OpenAIAdapter",
    "Transport",
    "default_transport",
    "gemini_contents",
    "gemini_schema",
    "gemini_tools",
    "openai_messages",
    "openai_tools",
]
