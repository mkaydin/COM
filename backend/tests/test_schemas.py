import pytest
from agents.schemas import (
    QueryClassification,
    SageResponse, SageAnalogy, ModeratorResponse, DecisionOption, Conflict, Agreement,
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


class TestLegacyAgentFieldCoercion:
    """Older records name Scout/Historian/Detective; those fields still coerce."""

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

    def test_conflict_coerces_numbers_for_live_agents_too(self):
        c = Conflict(topic="t", oracle=0.7, rag=0.6)
        assert c.oracle == "0.7" and c.rag == "0.6"

    def test_moderator_decision_space_dict(self):
        m = ModeratorResponse(decision_space={"conservative": {"action": "do A", "risk": "low"}})
        assert len(m.decision_space) == 1
        assert m.decision_space[0].label == "conservative"
