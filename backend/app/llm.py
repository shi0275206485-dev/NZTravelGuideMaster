"""LLM client with structured-output validation.

Two failure modes, deliberately handled differently — Phase 1 benchmarking
showed both, and conflating them wastes either tokens or time:

* **Transport failures** (429, 503, timeouts) mean the request never
  reached the model. Retrying the identical request after a backoff is
  correct; there is nothing to fix in the prompt.

* **Validation failures** mean the model answered, but the answer did not
  satisfy the schema. Retrying the identical prompt mostly reproduces the
  same mistake, so the error text is fed back so the model can see what it
  got wrong.

The client is model-agnostic: it speaks the OpenAI-compatible protocol and
takes the model name from configuration, so switching between the primary
model and the fallback is a config change rather than a code change.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Optional, Type, TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from .config import get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Status codes and error fragments that mean "try again", not "you asked wrong".
TRANSIENT_MARKERS = ("429", "500", "502", "503", "504", "timeout",
                     "overloaded", "ResourceExhausted", "rate limit")

MAX_TRANSPORT_RETRIES = 3
MAX_VALIDATION_RETRIES = 1

# Qwen3 reasons before answering unless told not to, and those tokens are
# billed and timed as output while being invisible in the response. Measured
# on a "pick 3 ids from a list" call: 3,938 completion tokens and 20.6s with
# reasoning on, 32 tokens and 2.1s with it off — the same answer either way,
# because selecting from a list is recall, not deduction.
#
# Off by default, per call rather than globally: itinerary planning is a
# constraint-satisfaction problem where reasoning may genuinely pay for
# itself, and that call can ask for it.
THINKING_DISABLED = {"enable_thinking": False}


class LLMError(RuntimeError):
    """Raised when the model could not produce usable output."""


def _is_transient(error: Exception) -> bool:
    text = repr(error).lower()
    return any(marker.lower() in text for marker in TRANSIENT_MARKERS)


def strip_fences(text: str) -> str:
    """Remove markdown code fences the model was asked not to emit.

    Models comply with "no fences" most of the time; stripping them costs
    nothing and turns an occasional avoidable failure into a success.
    """
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _extract_json_object(text: str) -> str:
    """Pull the outermost JSON object out of a response with stray prose."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return text
    return text[start : end + 1]


def parse_model_output(raw: str, schema: Type[T]) -> T:
    """Validate raw model output against a Pydantic schema.

    Tries progressively more forgiving reads before giving up: as written,
    then without fences, then the outermost JSON object, then via
    json-repair if it is installed. Raises ValidationError if none work, so
    the caller can feed the message back to the model.
    """
    candidates = [raw, strip_fences(raw), _extract_json_object(strip_fences(raw))]

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            return schema.model_validate_json(candidate)
        except (ValidationError, ValueError) as exc:
            last_error = exc

    try:
        from json_repair import repair_json

        repaired = repair_json(_extract_json_object(strip_fences(raw)))
        return schema.model_validate(json.loads(repaired))
    except ImportError:
        pass
    except (ValidationError, ValueError) as exc:
        last_error = exc

    raise last_error if last_error else ValueError("empty model output")


def summarise_validation_error(error: Exception, limit: int = 500) -> str:
    """Condense a validation error into something worth spending tokens on.

    Pydantic errors are verbose and repeat the whole input; only the field
    paths and messages help the model correct itself.
    """
    if isinstance(error, ValidationError):
        lines = []
        for err in error.errors()[:6]:
            location = ".".join(str(p) for p in err["loc"])
            lines.append(f"- {location}: {err['msg']}")
        return "\n".join(lines)[:limit]
    return str(error)[:limit]


class LLMClient:
    """Thin wrapper over an OpenAI-compatible chat completions endpoint."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout_s: Optional[float] = None,
    ):
        settings = get_settings()
        self.model = model or settings.llm_model
        self.timeout_s = timeout_s or settings.llm_timeout_s
        key = api_key or settings.llm_api_key
        if not key:
            # Surfaced as LLMError rather than the SDK's own exception so
            # that a missing key degrades exactly like an unreachable
            # service. A misconfigured deployment should still return an
            # itinerary, not a stack trace.
            raise LLMError(
                "No LLM API key configured. Set LLM_API_KEY in backend/.env"
            )
        self._client = OpenAI(
            api_key=key,
            base_url=base_url or settings.llm_base_url,
            timeout=self.timeout_s,
        )
        self.last_usage: dict[str, int] = {}

    # -- transport ---------------------------------------------------------

    def _complete(self, messages: list[dict], max_tokens: int,
                   temperature: float, thinking: bool = False) -> str:
        """One completion, retrying transport failures with backoff."""
        last_error: Exception | None = None
        extra_body = {} if thinking else dict(THINKING_DISABLED)

        for attempt in range(MAX_TRANSPORT_RETRIES):
            try:
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    extra_body=extra_body,
                )
                usage = response.usage
                if usage:
                    self.last_usage = {
                        "prompt_tokens": usage.prompt_tokens,
                        "completion_tokens": usage.completion_tokens,
                    }
                return response.choices[0].message.content or ""
            except Exception as exc:
                last_error = exc
                if not _is_transient(exc) or attempt == MAX_TRANSPORT_RETRIES - 1:
                    raise LLMError(f"LLM request failed: {exc}") from exc
                wait = 2**attempt * 2  # 2s, 4s
                logger.warning(
                    "transient LLM error (attempt %d/%d), retrying in %ds: %s",
                    attempt + 1, MAX_TRANSPORT_RETRIES, wait, exc,
                )
                time.sleep(wait)

        raise LLMError(f"LLM request failed: {last_error}")

    # -- public API --------------------------------------------------------

    def complete_text(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 1000,
        temperature: float = 0.6,
        thinking: bool = False,
    ) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return self._complete(messages, max_tokens, temperature, thinking)

    def complete_structured(
        self,
        prompt: str,
        schema: Type[T],
        system: Optional[str] = None,
        max_tokens: int = 2500,
        temperature: float = 0.4,
        thinking: bool = False,
    ) -> T:
        """Get output validated against `schema`, retrying once on a bad shape.

        Temperature defaults lower than for prose: the task is to follow a
        structure exactly, and there is no upside to variety in the shape.
        """
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        raw = self._complete(messages, max_tokens, temperature, thinking)

        for attempt in range(MAX_VALIDATION_RETRIES + 1):
            try:
                return parse_model_output(raw, schema)
            except (ValidationError, ValueError) as exc:
                if attempt == MAX_VALIDATION_RETRIES:
                    logger.error("schema validation failed after retry: %s", exc)
                    raise LLMError(
                        f"Model output did not match {schema.__name__}: "
                        f"{summarise_validation_error(exc)}"
                    ) from exc

                logger.warning("schema validation failed, retrying with feedback")
                messages.append({"role": "assistant", "content": raw})
                messages.append({
                    "role": "user",
                    "content": (
                        "That output failed validation:\n"
                        f"{summarise_validation_error(exc)}\n\n"
                        "Return the corrected JSON object only — no fences, no "
                        "commentary. Keep everything that was already valid."
                    ),
                })
                raw = self._complete(messages, max_tokens, temperature, thinking)

        raise LLMError("unreachable")


_client: LLMClient | None = None


def get_llm() -> LLMClient:
    """Process-wide client, so the HTTP connection pool is shared."""
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
