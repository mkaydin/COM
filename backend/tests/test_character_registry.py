"""Tests for the character registry.

The cast is data now. These tests pin the invariants that make it usable as a
forum: every character is complete enough to run, every one has a model role
that actually exists, and nothing is hardcoded elsewhere that could drift.
"""



import pytest

from agents import graph, moderator, registry, critique
from llm import roles


class TestCastIntegrity:
    def test_five_characters(self):
        """Oracle, Scholar, Sage, Analyst, Historian."""
        assert set(registry.keys()) == {
            "oracle", "scholar", "sage", "analyst", "historian"
        }

    def test_every_character_has_a_model_role_that_exists(self):
        """A typo'd role would resolve to a silent fallback model at runtime."""
        for c in registry.all_characters():
            assert c.model_role in roles.KNOWN_ROLES, (
                f"{c.key} names unknown model role {c.model_role!r}"
            )

    def test_every_character_is_fully_specified(self):
        for c in registry.all_characters():
            assert c.display_name, f"{c.key} has no display name"
            assert c.archetype, f"{c.key} has no archetype"
            assert c.color and c.icon, f"{c.key} has no UI identity"
            assert c.system_prompt.strip(), f"{c.key} has no prompt"
            assert c.voice_rules, f"{c.key} has no voice rules"
            assert c.knowledge, f"{c.key} has no knowledge source"
            assert c.response_model is not None, f"{c.key} has no schema"

    def test_every_character_has_a_stance(self):
        for c in registry.all_characters():
            assert "propose" in c.stances or c.stances

    def test_models_are_distinct_per_character(self):
        """Five characters on one model would be one voice five times."""
        model_roles = [c.model_role for c in registry.all_characters()]
        assert len(set(model_roles)) == len(model_roles)


class TestVoice:
    def test_prompts_are_substantially_different(self):
        """The failure mode the whole design exists to avoid.

        `old_docs/character_persona.md`: "3 ChatGPT responses with different
        tones" is the system failing. Distinct knowledge + distinct prompt is
        the defence.
        """
        prompts = {c.key: c.system_prompt for c in registry.all_characters()}
        keys = list(prompts)
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                assert prompts[a] != prompts[b], f"{a} and {b} share a prompt"
                # Shared boilerplate is fine; require real divergence.
                overlap = len(set(prompts[a].split()) & set(prompts[b].split()))
                union = len(set(prompts[a].split()) | set(prompts[b].split()))
                assert (overlap / union) < 0.85, f"{a} and {b} prompts are too similar"

    def test_every_prompt_demands_strict_json(self):
        for c in registry.all_characters():
            assert "ONLY this JSON" in c.system_prompt or "ONLY JSON" in c.system_prompt, (
                f"{c.key}'s prompt does not anchor on a JSON template"
            )

    def test_every_prompt_tells_the_character_to_engage_with_the_thread(self):
        for c in registry.all_characters():
            assert "reply_to" in c.system_prompt, (
                f"{c.key} is not told it must reply to a specific post"
            )

    def test_colors_and_icons_are_unique(self):
        colors = [c.color for c in registry.all_characters()]
        icons = [c.icon for c in registry.all_characters()]
        assert len(set(colors)) == len(colors)
        assert len(set(icons)) == len(icons)


class TestCastOperations:
    def test_ordered_sorts_canonically(self):
        assert registry.ordered(["sage", "oracle"]) == ["oracle", "sage"]
        assert registry.ordered(["sage", "oracle", "historian"]) == [
            "oracle", "historian", "sage"
        ]

    def test_ordered_drops_unknown_keys(self):
        assert registry.ordered(["oracle", "nope"]) == ["oracle"]

    def test_validate_cast_rejects_unknown(self):
        """A typo in the Moderator's cast must be visible, not silently dropped."""
        with pytest.raises(KeyError, match="Unknown character"):
            registry.validate_cast(["oracle", "nonexistent"])

    def test_validate_cast_rejects_empty(self):
        with pytest.raises(ValueError):
            registry.validate_cast([])

    def test_validate_cast_caps_the_cast_size(self):
        with pytest.raises(ValueError):
            registry.validate_cast(["oracle"] * 6)

    def test_default_cast_is_the_full_order(self):
        assert registry.default_cast() == list(registry.ORDER)

    def test_require_returns_the_character(self):
        assert registry.require("oracle").key == "oracle"

    def test_require_raises_for_unknown(self):
        with pytest.raises(KeyError):
            registry.require("nobody")

    def test_display_name_falls_back_to_key(self):
        assert registry.display_name("oracle") == "The Oracle"
        assert registry.display_name("nobody") == "nobody"

    def test_briefs_are_serialisable(self):
        for card in registry.briefs():
            assert {"name", "display_name", "color", "icon", "model_role"} <= set(card)


class TestNoHardcodedCastsRemain:
    """The original bug was a literal tuple repeated in three modules."""

    def test_moderator_still_has_the_legacy_three(self):
        """The chamber path is unchanged: it runs Oracle, Scholar and Sage only."""
        assert set(moderator.COUNCIL_AGENTS) == set(registry.COUNCIL_AGENTS)

    def test_chamber_graph_unchanged(self):
        """The old graph keeps its three deliberating agents."""
        assert {"router", "oracle", "rag", "sage", "moderator", "critique"} <= set(
            graph.build_workflow().nodes
        )

    def test_legacy_keys_map_onto_real_characters(self):
        """`rag` is the chamber's name for the Scholar."""
        assert registry.resolve_key("rag") == "scholar"
        assert registry.get("rag") is registry.require("scholar")

    def test_resolve_key_passes_through_current_keys(self):
        for key in registry.keys():
            assert registry.resolve_key(key) == key

    def test_legacy_council_agents_alias_resolves(self):
        """Every legacy key must resolve, or the chamber breaks at runtime."""
        for key in registry.COUNCIL_AGENTS:
            assert registry.get(key) is not None, f"legacy key {key!r} has no character"


class TestSchemas:
    def test_character_statement_has_the_forum_fields(self):
        fields = set(registry.CharacterStatement.model_fields)
        assert {"confidence", "stance", "reply_to", "verdict", "claims"} <= fields

    def test_statement_tolerates_an_empty_payload(self):
        """A character that returns junk must not take down the thread."""
        s = registry.CharacterStatement()
        assert s.verdict == ""
        assert s.claims == []

    def test_opening_schema_has_cast_and_mode(self):
        fields = set(registry.TopicOpening.model_fields)
        assert {"title", "framing", "cast", "mode"} <= fields

    def test_synthesis_schema_supports_both_outcomes(self):
        fields = set(registry.ForumSynthesis.model_fields)
        assert {"mode", "answer", "positions", "fault_line", "dissent"} <= fields

    def test_claim_tolerates_both_shapes(self):
        """Analyst/Historian reuse ForumClaim for things that are not claims."""
        c = registry.ForumClaim(text="x", sources=["a"], trust_tier="preprint")
        assert c.text == "x"
        assert registry.ForumClaim().text == ""