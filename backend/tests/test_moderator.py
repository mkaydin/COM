"""Tests for the Moderator's deterministic signal math.

The alignment score the UI shows is *not* the LLM's self-report — it is
computed here from confidence spread and source overlap, then written over the
model's own value. That makes this pure arithmetic the authority on the
Council's headline number, and it had no coverage at all.
"""
import pytest

from agents.moderator import (
    _norm_conf, _jaccard, _tokens, _compute_signals, _agent_brief,
    SPREAD_ALIGN_HIGH, SPREAD_ALIGN_LOW, SPREAD_CRITIQUE,
    COUNCIL_AGENTS,
)


def brief(confidence=None, main="", insufficient=False, claims=None, citations=None):
    return {
        "present": True,
        "confidence": confidence,
        "main": main,
        "insufficient": insufficient,
        "claims": claims or [],
        "citations": citations or [],
    }


class TestNormConf:
    @pytest.mark.parametrize("raw,expected", [
        (0.5, 0.5),
        (1.0, 1.0),
        (0, 0.0),
        (100, 1.0),
        (85, 0.85),
        ("0.75", 0.75),
        (0.0, 0.0),
        (100.0, 1.0),
    ])
    def test_normalizes(self, raw, expected):
        assert _norm_conf(raw) == pytest.approx(expected)

    def test_clamped_out_of_range(self):
        assert _norm_conf(150) == 1.0
        assert _norm_conf(-5) == 0.0

    @pytest.mark.parametrize("raw,expected,note", [
        (1.2, 1.0,  "in (1.0, 1.5]: clamped to full confidence"),
        (1.5, 1.0,  "exactly at the threshold: clamped"),
        (1.6, 0.016, "just past it: read as 1.6 on a 0-100 scale"),
        (2.0, 0.02, "same cliff"),
    ])
    def test_known_cliff_between_1_and_2(self, raw, expected, note):
        """Documents a discontinuity rather than endorsing it.

        _norm_conf treats <=1.5 as a 0..1 value (then clamps) and >1.5 as a
        0..100 value. So a model reporting confidence 1.5 is read as 100%,
        while 1.6 is read as 1.6%. That cliff is real: it can move the
        alignment band on its own. Widening the band, or asking models for
        exactly two decimals, would remove it.
        """
        assert _norm_conf(raw) == pytest.approx(expected), note

    def test_none_and_garbage(self):
        assert _norm_conf(None) is None
        assert _norm_conf("not a number") is None
        assert _norm_conf([]) is None


class TestJaccard:
    def test_identical_sets(self):
        assert _jaccard({"a", "b"}, {"a", "b"}) == 1.0

    def test_disjoint_sets(self):
        assert _jaccard({"a"}, {"b"}) == 0.0

    def test_partial_overlap(self):
        # |A&B| = 1, |A|B| = 3
        assert _jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)

    def test_empty_sets_do_not_divide_by_zero(self):
        assert _jaccard(set(), {"a"}) == 0.0
        assert _jaccard({"a"}, set()) == 0.0
        assert _jaccard(set(), set()) == 0.0


class TestTokens:
    def test_drops_short_and_stopword_like_tokens(self):
        # Tokens must be longer than 3 characters to count.
        assert _tokens("The Cat is a big animal") == {"animal"}
        assert _tokens("a big animal") == {"animal"}

    def test_punctuation_split(self):
        assert _tokens("alpha, beta; gamma-delta") == {"alpha", "beta", "gamma-delta"}

    def test_none_is_safe(self):
        assert _tokens(None) == set()
        assert _tokens("") == set()


class TestComputeSignals:
    def test_no_responses(self):
        s = _compute_signals({})
        assert s["alignment"] == "high"          # nothing to disagree about
        assert s["confidence_spread"] == 0.0
        assert s["mean_confidence"] is None
        assert s["insufficient_agents"] == []

    def test_single_agent_has_zero_spread(self):
        s = _compute_signals({"oracle": brief(0.5)})
        assert s["confidence_spread"] == 0.0
        assert s["mean_confidence"] == pytest.approx(0.5)

    def test_agreement_yields_high_alignment(self):
        s = _compute_signals({
            "oracle": brief(0.80, main="chinchilla husbandry is well documented"),
            "rag": brief(0.78, main="chinchilla husbandry is well documented"),
            "sage": brief(0.80, main="chinchilla husbandry is well documented"),
        })
        assert s["alignment"] == "high"
        assert s["confidence_spread"] < SPREAD_ALIGN_HIGH
        assert s["oracle_rag_overlap"] == pytest.approx(1.0)

    def test_large_spread_yields_low_alignment(self):
        s = _compute_signals({
            "oracle": brief(0.95),
            "rag": brief(0.05),
            "sage": brief(0.5),
        })
        assert s["alignment"] == "low"
        assert s["confidence_spread"] > SPREAD_ALIGN_LOW

    def test_two_insufficient_agents_force_low(self):
        s = _compute_signals({
            "oracle": brief(0.8, insufficient=True),
            "rag": brief(0.8, insufficient=True),
            "sage": brief(0.8),
        })
        assert s["alignment"] == "low"
        assert set(s["insufficient_agents"]) == {"oracle", "rag"}

    def test_high_band_below_0_15_spread(self):
        s = _compute_signals({"oracle": brief(0.5), "rag": brief(0.62), "sage": brief(0.58)})
        assert s["confidence_spread"] == pytest.approx(0.12)
        assert s["alignment"] == "high"

    def test_medium_band(self):
        # Between SPREAD_ALIGN_HIGH (0.15) and SPREAD_ALIGN_LOW (0.40).
        s = _compute_signals({"oracle": brief(0.3), "rag": brief(0.55), "sage": brief(0.4)})
        assert s["confidence_spread"] == pytest.approx(0.25)
        assert s["alignment"] == "medium"

    def test_absent_agents_do_not_affect_spread(self):
        """A silent agent must not look like a confident one."""
        s = _compute_signals({
            "oracle": brief(0.5),
            "rag": {"present": False},
            "sage": brief(0.6),
        })
        assert s["confidence_spread"] == pytest.approx(0.1)

    def test_ignores_missing_confidence(self):
        s = _compute_signals({
            "oracle": brief(None),
            "rag": brief(0.9),
            "sage": brief(0.9),
        })
        assert s["confidence_spread"] == 0.0

    def test_overlap_is_symmetric(self):
        a = {"oracle": brief(main="chinchilla breeding season begins in winter")}
        b = {"rag": brief(main="chinchilla breeding season begins in winter")}
        assert _compute_signals(a | b)["oracle_rag_overlap"] == \
               _compute_signals(b | a)["oracle_rag_overlap"]

    def test_disjoint_topics_give_zero_overlap(self):
        s = _compute_signals({
            "oracle": brief(0.8, main="medieval pottery glazes"),
            "rag": brief(0.8, main="quantum chromodynamics lattice"),
            "sage": brief(0.8),
        })
        assert s["oracle_rag_overlap"] == 0.0


class TestCritiqueThreshold:
    def test_spread_forces_critique(self):
        s = _compute_signals({"oracle": brief(0.9), "rag": brief(0.1), "sage": brief(0.5)})
        assert s["confidence_spread"] > SPREAD_CRITIQUE

    def test_agreement_does_not_force_critique(self):
        s = _compute_signals({
            "oracle": brief(0.8, main="chinchilla husbandry"),
            "rag": brief(0.8, main="chinchilla husbandry"),
            "sage": brief(0.8, main="chinchilla husbandry"),
        })
        assert s["confidence_spread"] <= SPREAD_CRITIQUE


class TestAgentBrief:
    def test_none_response_is_not_present(self):
        assert _agent_brief(None)["present"] is False

    def test_empty_dict_is_not_present(self):
        assert _agent_brief({})["present"] is False

    def test_confidence_is_normalized(self):
        assert _agent_brief({"confidence": 90})["confidence"] == pytest.approx(0.9)

    def test_reads_verdict_field(self):
        b = _agent_brief({"verdict": "the answer", "confidence": 0.5})
        assert b["present"] is True
        assert b["main"] == "the answer"
