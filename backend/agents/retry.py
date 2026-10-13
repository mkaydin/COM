import asyncio
import inspect
import time
from functools import wraps
from logging_config import get_logger

logger = get_logger(__name__)


class LLMResult:
    """Wrapper that preserves the provider response and captures reasoning.

    LiteLLM returns an object graph; the CLI transport returns a plain dict
    with the same shape. Both are accepted so callers can keep using
    `.choices[0].message.content` without knowing which transport ran.
    """

    def __init__(self, response):
        self.response = response
        self.reasoning = ""
        try:
            msg = self._first_message()
            self.reasoning = getattr(msg, "reasoning_content", None) or ""
        except Exception:
            pass

    @property
    def _choices(self):
        """The response's choices, whether it is an object or a dict."""
        raw = self.response
        if isinstance(raw, dict):
            return raw.get("choices") or []
        return getattr(raw, "choices", []) or []

    @property
    def choices(self):
        return self._choices

    @property
    def content(self) -> str:
        """The first choice's text, regardless of transport.

        The single accessor every caller should use: it works whether the
        provider returned LiteLLM's object graph or the CLI transport's dict.
        """
        msg = self._first_message()
        return (getattr(msg, "content", "") or "") if msg is not None else ""

    def _first_message(self):
        """The first choice's message, as an object with `.content`."""
        choices = self._choices
        if not choices:
            return None
        first = choices[0]
        if isinstance(first, dict):
            return _DictMessage(first.get("message") or {})
        return getattr(first, "message", None)


class _DictMessage:
    """Attribute access over a dict-shaped message."""

    __slots__ = ("_data",)

    def __init__(self, data: dict):
        self._data = data or {}

    @property
    def content(self) -> str:
        return self._data.get("content") or ""

    @property
    def reasoning_content(self):
        return self._data.get("reasoning_content") or ""

    def __getattr__(self, name):
        try:
            return self._data[name]
        except KeyError as e:
            raise AttributeError(name) from e


def _status_of(e: Exception) -> int:
    """Best-effort HTTP status from a provider exception."""
    status = getattr(e, "status_code", 0) or getattr(getattr(e, "response", None), "status_code", 0)
    try:
        return int(status)
    except (TypeError, ValueError):
        return 0


#: Statuses that will never succeed on a retry. Retrying them just multiplies
#: the wait: a missing API key costs 1s + 2s + 4s of backoff on every call,
#: and a forum run makes one call per character per round — so a misconfigured
#: key turned into a ~50 second thread that never starts. Fail fast instead.
NON_RETRYABLE_STATUS = {400, 401, 403, 404}


def is_retryable(e: Exception) -> bool:
    """Whether another attempt could plausibly succeed.

    Rate limits (429) and server errors (5xx) are worth waiting out. Auth and
    bad-request errors are not — the same call will fail identically, only
    later. Connection errors have no status and are worth one more try.
    """
    if _status_of(e) in NON_RETRYABLE_STATUS:
        return False
    name = type(e).__name__
    if name in ("AuthenticationError", "PermissionDeniedError", "NotFoundError",
                "BadRequestError", "UnprocessableEntityError"):
        return False
    return True


async def llm_call_with_retry(completion_fn, max_retries=3, base_delay=1.0, backoff=2.0, **kwargs):
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            if attempt > 0:
                delay = base_delay * (backoff ** (attempt - 1))
                logger.info("llm_retry", attempt=attempt, delay=delay, model=kwargs.get("model", ""))
                await asyncio.sleep(delay)
            result = await asyncio.to_thread(completion_fn, **kwargs)
            # LiteLLM's `completion` is synchronous, so `to_thread` returns the
            # response directly. The CLI transport is async and `to_thread`
            # hands back the un-awaited coroutine instead — awaiting here keeps
            # both kinds of transport working behind one call site.
            if inspect.isawaitable(result):
                result = await result
            return LLMResult(result)
        except Exception as e:
            last_error = e
            status = _status_of(e)

            if status == 429:
                logger.warning("llm_rate_limited", attempt=attempt, error=str(e))
            elif 500 <= status < 600:
                logger.warning("llm_server_error", attempt=attempt, error=str(e))
            else:
                logger.warning("llm_error", attempt=attempt, error=str(e))

            if not is_retryable(e):
                logger.warning(
                    "llm_error_not_retryable",
                    attempt=attempt,
                    error_type=type(e).__name__,
                    status=status,
                    error=str(e)[:200],
                )
                raise

            if attempt >= max_retries:
                raise

    raise last_error