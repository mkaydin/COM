import pytest
from unittest.mock import MagicMock
from agents.retry import llm_call_with_retry, LLMResult


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
