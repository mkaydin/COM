import asyncio
import time
from functools import wraps
from logging_config import get_logger

logger = get_logger(__name__)


class LLMResult:
    """Wrapper that preserves the LiteLLM response and captures reasoning content."""
    def __init__(self, response):
        self.response = response
        self.reasoning = ""
        try:
            msg = response.choices[0].message
            self.reasoning = getattr(msg, 'reasoning_content', None) or ""
        except Exception:
            pass

    @property
    def choices(self):
        return self.response.choices


async def llm_call_with_retry(completion_fn, max_retries=3, base_delay=1.0, backoff=2.0, **kwargs):
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            if attempt > 0:
                delay = base_delay * (backoff ** (attempt - 1))
                logger.info("llm_retry", attempt=attempt, delay=delay, model=kwargs.get("model", ""))
                await asyncio.sleep(delay)
            result = await asyncio.to_thread(completion_fn, **kwargs)
            return LLMResult(result)
        except Exception as e:
            last_error = e
            status = getattr(e, "status_code", 0) or getattr(getattr(e, "response", None), "status_code", 0)
            if status == 429:
                logger.warning("llm_rate_limited", attempt=attempt, error=str(e))
            elif 500 <= status < 600:
                logger.warning("llm_server_error", attempt=attempt, error=str(e))
            else:
                logger.warning("llm_error", attempt=attempt, error=str(e))
                if attempt >= max_retries:
                    raise
    raise last_error
