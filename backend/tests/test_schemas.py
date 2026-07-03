import pytest
from agents.schemas import (
    QueryClassification, ScoutResponse, ScoutSignal, HistorianClaim, HistorianResponse,
    DetectiveResponse, CausalChain, HiddenLink,
    SageResponse, SageAnalogy, ModeratorResponse, DecisionOption, Conflict, Agreement,
    CompanionResponse, TrustGuidance,
)


class TestQueryClassification:
    def test_valid_factual(self):
        q = QueryClassification(query_type="factual", confidence=0.9, reasoning="test")
        assert q.query_type == "factual"

    def test_valid_relational(self):
        q = QueryClassification(query_type="relational", confidence=0.8, reasoning="test")
        assert q.query_type == "relational"

    def test_valid_strategic(self):
        q = QueryClassification(query_type="strategic", confidence=0.85, reasoning="test")
        assert q.query_type == "strategic"

    def test_valid_general(self):
        q = QueryClassification(query_type="general", confidence=0.5, reasoning="test")
        assert q.query_type == "general"


class TestScoutResponse:
    def test_empty(self):
        r = ScoutResponse(confidence=0.0, key_signals=[], consensus="", topic="", source_diversity="low", source_reliability="low")
        assert r.confidence == 0.0
        assert r.key_signals == []

    def test_with_signals(self):
        signal = ScoutSignal(signal="test signal", strength="strong", sources=3)
        r = ScoutResponse(
            confidence=0.85, key_signals=[signal], consensus="Consensus found",
            topic="AI", source_diversity="high", source_reliability="medium",
        )
        assert r.confidence == 0.85
        assert len(r.key_signals) == 1
        assert r.key_signals[0].signal == "test signal"


class TestHistorianResponse:
    def test_empty(self):
        r = HistorianResponse(confidence=0.0, claims=[], verdict="")
        assert r.verdict == ""

    def test_with_claims(self):
        claim = HistorianClaim(text="Claim text", sources=["E1", "E2"])
        r = HistorianResponse(confidence=0.9, claims=[claim], verdict="Verdict")
        assert len(r.claims) == 1
        assert r.claims[0].text == "Claim text"
        assert r.claims[0].sources == ["E1", "E2"]


class TestDetectiveResponse:
    def test_empty(self):
        r = DetectiveResponse(confidence=0.0, causal_chains=[], hidden_links=[], inconsistencies=[], verdict="")
        assert r.verdict == ""

    def test_with_data(self):
        chain = CausalChain(chain=["A", "B", "C"], evidence_strength=0.8)
        link = HiddenLink(entity_a="X", entity_b="Y", relation="linked")
        r = DetectiveResponse(
            confidence=0.75, causal_chains=[chain], hidden_links=[link],
            inconsistencies=["Issue 1"], verdict="Analysis complete",
        )
        assert len(r.causal_chains) == 1
        assert r.causal_chains[0].chain == ["A", "B", "C"]
        assert r.inconsistencies == ["Issue 1"]


class TestSageResponse:
    def test_empty(self):
        r = SageResponse(confidence=0.0, synthesis="", speculation_warning=False)
        assert r.synthesis == ""

    def test_with_content(self):
        analogy = SageAnalogy(domain="physics", concept="entropy", application="information theory")
        r = SageResponse(confidence=0.6, synthesis="Deep insight", speculation_warning=True, analogies=[analogy])
        assert r.synthesis == "Deep insight"
        assert r.speculation_warning is True
        assert len(r.analogies) == 1


class TestModeratorResponse:
    def test_empty(self):
        r = ModeratorResponse()
        assert r.comparison == {}

    def test_with_conflicts(self):
        conflict = Conflict(topic="Test", status="unresolved", scout="A", historian="B")
        r = ModeratorResponse(conflicts=[conflict])
        assert len(r.conflicts) == 1
        assert r.conflicts[0].topic == "Test"


class TestCompanionResponse:
    def test_empty(self):
        c = CompanionResponse(translation="", summary="", recommended_agent="", recommendation_reason="")
        assert c.translation == ""

    def test_with_data(self):
        guidance = TrustGuidance(agent="sage", trust_level="high", reason="good")
        c = CompanionResponse(
            translation="Simple", summary="Brief", recommended_agent="sage",
            recommendation_reason="good", trust_guidance=[guidance],
            clarifying_questions=["What is X?"],
        )
        assert len(c.clarifying_questions) == 1
        assert c.recommended_agent == "sage"


class TestGLMCoercion:
    """Tests that schemas tolerate real GLM output quirks."""

    def test_conflict_float_scout(self):
        c = Conflict(topic="t", scout=0.85)
        assert c.scout == "0.85"

    def test_agreement_missing_agents(self):
        a = Agreement(topic="t")
        assert a.agents == []

    def test_decision_option_defaults(self):
        d = DecisionOption()
        assert d.label == "Strategic"
        assert d.risk == "medium"

    def test_decision_option_from_dict(self):
        d = DecisionOption(**{"label": "X", "action": "Y", "risk": "low"})
        assert d.action == "Y"

    def test_trust_guidance_no_agent(self):
        t = TrustGuidance(trust_level="high", reason="good")
        assert t.agent == ""

    def test_moderator_decision_space_dict(self):
        m = ModeratorResponse(decision_space={"conservative": {"action": "do A", "risk": "low"}})
        assert len(m.decision_space) == 1
        assert m.decision_space[0].label == "conservative"
