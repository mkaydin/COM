"""Tests for provider error diagnosis.

The failure this exists for is quiet: a key that is *valid*, that passes every
configuration check, and that cannot serve a single completion. A user seeing
"OPENCODE_API_KEY is not set" when it is set — and is set correctly — would be
sent looking in the wrong place entirely.
"""

import pytest

from llm import NOT_CALLABLE_HINTS, diagnose_call_error


FREE_TIER = (
    "litellm.APIError: APIError: OpenAIException - OpenCode's free tier can only "
    "be used from within OpenCode. Download it at https://opencode.ai and keep building."
)
UNFUNDED = "litellm.APIError: APIError: OpenAIException - Upstream request failed: Insufficient account funds"
DISABLED = "litellm.APIError: APIError: OpenAIException - Upstream request failed: Model access is disabled"
GONE = "litellm.APIError: APIError: OpenAIException - Upstream request failed: Model is unavailable."


class TestDiagnose:
    def test_free_tier_lock_is_explained(self):
        """The headline case: valid key, zero usable models, no obvious cause."""
        d = diagnose_call_error(FREE_TIER)
        assert d is not None
        assert "locked to the OpenCode client" in d
        # It must offer both ways forward, not just report the problem.
        assert "CLI" in d or "cli" in d
        assert "billing" in d

    def test_unfunded_account_is_explained(self):
        d = diagnose_call_error(UNFUNDED)
        assert d is not None
        assert "no billing" in d

    def test_disabled_access_is_explained(self):
        d = diagnose_call_error(DISABLED)
        assert d is not None
        assert "does not have access" in d

    def test_unavailable_model_is_explained(self):
        d = diagnose_call_error(GONE)
        assert d is not None
        assert "unavailable" in d

    @pytest.mark.parametrize("message", [
        FREE_TIER, UNFUNDED, DISABLED, GONE,
        "prefix " + FREE_TIER + " suffix",
    ])
    def test_survives_wrapping_and_surrounding_text(self, message):
        """LiteLLM prefixes everything with its own noise; the substring must still match."""
        assert diagnose_call_error(message) is not None

    def test_case_insensitive(self):
        assert diagnose_call_error(FREE_TIER.upper()) is not None

    def test_unknown_error_returns_none(self):
        """No diagnosis is better than a wrong one."""
        assert diagnose_call_error("some other failure") is None

    def test_empty_error_returns_none(self):
        assert diagnose_call_error("") is None
        assert diagnose_call_error(None) is None


class TestHintCoverage:
    def test_every_known_phrase_is_diagnosed(self):
        """A phrase listed as a known failure must actually produce advice."""
        for phrase in NOT_CALLABLE_HINTS:
            assert diagnose_call_error(f"boom: {phrase}") is not None, (
                f"{phrase!r} is listed as a known failure but has no diagnosis"
            )