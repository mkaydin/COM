"""Tests for null tolerance in the model-authored schemas.

Found in production: the Moderator returned `"answer": null` on a plurality
outcome — a field the prompt did not make mandatory — and a plain `str` field
rejected it as a validation error. The whole synthesis fell back to the
deterministic stub, so a topic that had deliberated properly ended with
positions that merely pointed back at the thread.
"""

import pytest

from agents.registry import (
    CharacterStatement,
    Dissent,
    ForumSynthesis,
    Position,
    TopicOpening,
)


ALL_NULL = {
    "CharacterStatement": CharacterStatement,
    "TopicOpening": TopicOpening,
    "ForumSynthesis": ForumSynthesis,
    "Position": Position,
    "Dissent": Dissent,
}


class TestNullTolerance:
    @pytest.mark.parametrize("name", sorted(ALL_NULL))
    def test_fully_null_payload_parses(self, name):
        """The worst case: every field null."""
        model = ALL_NULL[name]
        assert model(**{f: None for f in model.model_fields}) is not None

    def test_the_shape_that_broke_it(self):
        """The exact payload from the failed Moderator call."""
        payload = {
            "mode": "plurality",
            "answer": None,
            "positions": [{"label": "Abandon", "position": None, "backers": None}],
            "fault_line": None,
            "dissent": None,
        }
        s = ForumSynthesis(**payload)
        assert s.mode == "plurality"
        assert s.answer == ""
        assert s.positions[0].backers == []
        assert s.positions[0].position == ""
        assert s.dissent == []

    def test_strings_coerce_to_empty_not_a_list(self):
        """The coercion branch has to pick the right empty value per field.

        `stance` becomes "" rather than a valid stance — coercion restores the
        *type*, not the semantics. `forum_characters` validates the stance
        against the character's allowed set and falls back to its default, so
        inventing a stance here would hide the model's actual output.
        """
        c = CharacterStatement(verdict=None, stance=None, claims=None, reply_to=None)
        assert c.verdict == ""
        assert c.stance == ""
        assert c.claims == []
        assert c.reply_to == 0

    def test_reply_to_null_becomes_zero_which_means_no_target(self):
        """`forum_characters` reads 0 as 'no post to reply to'."""
        assert CharacterStatement(reply_to=None).reply_to == 0

    def test_real_values_are_untouched(self):
        s = ForumSynthesis(
            mode="consensus",
            answer="the answer",
            positions=[{"label": "A", "position": "p", "backers": ["oracle"]}],
        )
        assert s.answer == "the answer"
        assert s.positions[0].backers == ["oracle"]

    def test_partial_nulls_do_not_disturb_the_rest(self):
        c = CharacterStatement(confidence=0.8, verdict="real", claims=None, gaps=None)
        assert c.verdict == "real"
        assert c.confidence == 0.8
        assert c.claims == []
        assert c.gaps == []


class TestTheCoercionIsNarrow:
    """It must fix nulls, not silently swallow real errors."""

    def test_a_wrongly_typed_value_still_raises(self):
        with pytest.raises(Exception):
            ForumSynthesis(positions="not a list of positions")

    def test_confidence_is_not_coerced(self):
        """`confidence` is a float with no null branch; a bad value must fail
        rather than silently become 0.0 and look like low confidence."""
        with pytest.raises(Exception):
            CharacterStatement(confidence="very high")

    def test_a_missing_field_keeps_its_default(self):
        assert ForumSynthesis().mode == "plurality"
        assert CharacterStatement().stance == "propose"

class TestListItemCoercion:
    """Models return list fields as objects or as bare scalars, interchangeably.

    Found in production: the Analyst returned `"claims": ["...", "..."]` and
    Pydantic rejected it, so the character silently posted *nothing* — no
    error surfaced anywhere, the thread just had a hole in it.
    """

    def test_bare_strings_become_claims(self):
        c = CharacterStatement(claims=["first claim", "second claim"])
        assert [x.text for x in c.claims] == ["first claim", "second claim"]

    def test_dicts_still_work(self):
        c = CharacterStatement(claims=[{"text": "a claim", "sources": ["s1"]}])
        assert c.claims[0].text == "a claim"
        assert c.claims[0].sources == ["s1"]

    def test_mixed_shapes_are_accepted(self):
        """The realistic case: half right, half wrong, in one list."""
        c = CharacterStatement(claims=[{"text": "object form"}, "scalar form"])
        assert [x.text for x in c.claims] == ["object form", "scalar form"]

    def test_citations_coerce_too(self):
        c = CharacterStatement(citations=["A Paper"])
        assert c.citations[0].text == "A Paper"

    def test_all_list_fields_coerce(self):
        """Each list field wraps a bare scalar under its *own* natural key.

        `claims` want `text`, but a causal chain wants `chain` and a hidden
        link wants `relation` — wrapping everything as `{"text": ...}` would
        produce an object missing its required fields.
        """
        c = CharacterStatement(
            claims=["c"], citations=["s"], analogies=["a"],
            causal_chains=["A -> B -> C"], hidden_links=["related_to"],
            prior_conclusions=["prior"],
        )
        assert c.claims[0].text == "c"
        assert c.citations[0].text == "s"
        assert c.analogies[0].text == "a"
        assert c.causal_chains[0].chain == ["A", "B", "C"]
        assert c.hidden_links[0].relation == "related_to"
        assert c.prior_conclusions[0].text == "prior"

    def test_backers_accept_bare_strings(self):
        assert ForumSynthesis(positions=[{"backers": ["oracle", "sage"]}]).positions[0].backers == [
            "oracle", "sage"
        ]

    def test_a_scalar_wins_over_a_broken_model(self):
        """The point: one odd element must not cost the whole character."""
        c = CharacterStatement(claims=["good", 42, None])
        assert c.claims[0].text == "good"
        assert c.claims[1].text == "42"
