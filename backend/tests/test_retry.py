import pytest
from unittest.mock import MagicMock
from agents.retry import (
    LLMResult,
    is_retryable,
    llm_call_with_retry,
    NON_RETRYABLE_STATUS,
)


"""Provider exceptions shaped like the ones LiteLLM raises.

`litellm` is not imported here — the retry layer only ever inspects the class
name and the `status_code` attribute, so plain stand-ins reproduce it exactly.
"""


class AuthenticationError(Exception):
    status_code = 401


class PermissionDeniedError(Exception):
    status_code = 403


class BadRequestError(Exception):
    status_code = 400


class NotFoundError(Exception):
    status_code = 404


class UnprocessableEntityError(Exception):
    pass


class RateLimitError(Exception):
    status_code = 429


class ServiceUnavailableError(Exception):
    status_code = 503


def err(cls_name, status=0):
    """An exception of the named class, optionally with an overridden status."""
    e = type(cls_name, (Exception,), {})("boom")
    if status:
        e.status_code = status
    return e


class TestLlmCallWithRetry:
    @pytest.mark.asyncio
    async def test_success_first_attempt(self):
        mock_fn = MagicMock(return_value="success")
        result = await llm_call_with_retry(mock_fn, max_retries=2, base_delay=0.01)
        assert isinstance(result, LLMResult)
        assert result.response == "success"
        assert mock_fn.call_count == 1

    @pytest.mark.asyncio
    async def test_success_after_retry(self):
        mock_fn = MagicMock(side_effect=[Exception("temporary"), "success"])
        result = await llm_call_with_retry(mock_fn, max_retries=2, base_delay=0.01, backoff=1.0)
        assert isinstance(result, LLMResult)
        assert result.response == "success"
        assert mock_fn.call_count == 2

    @pytest.mark.asyncio
    async def test_max_retries_exceeded(self):
        mock_fn = MagicMock(side_effect=Exception("persistent"))
        with pytest.raises(Exception, match="persistent"):
            await llm_call_with_retry(mock_fn, max_retries=2, base_delay=0.01, backoff=1.0)
        assert mock_fn.call_count == 3


class TestIsRetryable:
    def test_rate_limits_are_retryable(self):
        assert is_retryable(err("RateLimitError", 429))

    def test_server_errors_are_retryable(self):
        assert is_retryable(err("ServiceUnavailableError", 503))

    def test_connection_errors_are_retryable(self):
        """No status at all — usually transient, worth another try."""
        assert is_retryable(Exception("connection reset"))

    def test_auth_errors_are_not_retryable(self):
        """A missing API key fails identically on every attempt.

        Retrying it costs 1s + 2s + 4s per call, and a forum run makes one
        call per character per round — so this turned a misconfiguration into
        a ~50 second thread that never starts.
        """
        assert not is_retryable(err("AuthenticationError", 401))
        assert not is_retryable(err("PermissionDeniedError", 403))

    def test_bad_request_is_not_retryable(self):
        assert not is_retryable(err("BadRequestError", 400))
        assert not is_retryable(err("NotFoundError", 404))

    def test_known_non_retryable_class_names_without_status(self):
        assert not is_retryable(err("AuthenticationError"))
        assert not is_retryable(err("UnprocessableEntityError"))

    def test_non_retryable_statuses_are_the_documented_set(self):
        assert NON_RETRYABLE_STATUS == {400, 401, 403, 404}


class TestFailFast:
    @pytest.mark.asyncio
    async def test_auth_error_is_not_retried(self):
        """The whole point: one attempt, then raise."""
        mock_fn = MagicMock(side_effect=err("AuthenticationError", 401))
        with pytest.raises(Exception, match="boom"):
            await llm_call_with_retry(mock_fn, max_retries=3, base_delay=0.01)
        assert mock_fn.call_count == 1, "an auth error should not be retried"

    @pytest.mark.asyncio
    async def test_rate_limit_still_retries(self):
        mock_fn = MagicMock(side_effect=[err("RateLimitError", 429), "success"])
        result = await llm_call_with_retry(mock_fn, max_retries=3, base_delay=0.01, backoff=1.0)
        assert result.response == "success"
        assert mock_fn.call_count == 2

    @pytest.mark.asyncio
    async def test_server_error_still_retries(self):
        mock_fn = MagicMock(side_effect=[err("ServiceUnavailableError", 500), "success"])
        result = await llm_call_with_retry(mock_fn, max_retries=3, base_delay=0.01, backoff=1.0)
        assert result.response == "success"
        assert mock_fn.call_count == 2

    @pytest.mark.asyncio
    async def test_non_retryable_wins_over_remaining_budget(self):
        """Even on the final attempt, an auth error raises immediately."""
        mock_fn = MagicMock(side_effect=err("AuthenticationError", 401))
        with pytest.raises(Exception):
            await llm_call_with_retry(mock_fn, max_retries=0, base_delay=0.01)
        assert mock_fn.call_count == 1