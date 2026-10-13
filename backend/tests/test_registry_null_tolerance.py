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