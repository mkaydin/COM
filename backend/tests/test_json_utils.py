"""Tests for extract_json — the Council's single most failure-prone function.

Runs with SUPPORTS_JSON_MODE=false, so every agent parses its output through
this. A silent {} here is indistinguishable from "the agent had nothing to say".
"""
import pytest

from agents.json_utils import extract_json, _iter_json_candidates, _close_truncated


class TestCleanOutput:
    def test_plain_object(self):
        assert extract_json('{"verdict": "ok", "confidence": 0.8}') == {
            "verdict": "ok", "confidence": 0.8,
        }

    def test_empty_input(self):
        assert extract_json("") == {}
        assert extract_json(None) == {}

    def test_no_json_at_all(self):
        assert extract_json("I am not sure what you mean.") == {}

    def test_non_object_json_is_ignored(self):
        # An array is valid JSON but not a usable agent response.
        assert extract_json('[1, 2, 3]') == {}


class TestFences:
    def test_json_fence(self):
        assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_bare_fence(self):
        assert extract_json('```\n{"a": 1}\n```') == {"a": 1}

    def test_fence_with_prose_around_it(self):
        raw = 'Sure! Here is the result:\n```json\n{"a": 1}\n```\nLet me know if you need more.'
        assert extract_json(raw) == {"a": 1}

    def test_multiple_fences_prefers_the_longest(self):
        raw = '```json\n{"a": 1}\n```\nscratch:\n```json\n{"a": 1, "b": 2, "c": 3}\n```'
        assert extract_json(raw) == {"a": 1, "b": 2, "c": 3}


class TestReasoningBlocks:
    def test_think_block_stripped(self):
        raw = '<think>The user wants a verdict. Let me pick one.</think>\n{"verdict": "yes"}'
        assert extract_json(raw) == {"verdict": "yes"}

    def test_reasoning_object_does_not_win(self):
        """A model that emits a JSON object *inside* its thinking must still
        have the real answer parsed, not the scratch object."""
        raw = (
            '<think>{"plan": {"step": 1, "detail": "consider options"}}</think>\n'
            '{"verdict": "real answer", "confidence": 0.5}'
        )
        assert extract_json(raw) == {"verdict": "real answer", "confidence": 0.5}

    @pytest.mark.parametrize("tag", ["thinking", "analysis", "scratchpad"])
    def test_other_reasoning_tags(self, tag):
        raw = f'<{tag}>internal notes</{tag}>{{"ok": true}}'
        assert extract_json(raw) == {"ok": True}


class TestBraceScanning:
    def test_nested_objects(self):
        raw = '{"outer": {"inner": {"deep": 1}}, "x": 2}'
        assert extract_json(raw) == {"outer": {"inner": {"deep": 1}}, "x": 2}

    def test_brace_inside_string_value(self):
        """A naive regex ends the object at the } inside the string."""
        raw = '{"snippet": "if (x) { return 1; }", "n": 2}'
        assert extract_json(raw) == {"snippet": "if (x) { return 1; }", "n": 2}

    def test_escaped_quote_in_string(self):
        raw = r'{"quote": "he said \"hi\" }", "n": 1}'
        assert extract_json(raw) == {"quote": 'he said "hi" }', "n": 1}

    def test_arrays_of_objects(self):
        raw = '{"conflicts": [{"topic": "a"}, {"topic": "b"}]}'
        assert extract_json(raw) == {"conflicts": [{"topic": "a"}, {"topic": "b"}]}

    def test_trailing_prose_after_object(self):
        assert extract_json('{"a": 1}\n\nThat is my answer.') == {"a": 1}

    def test_two_objects_last_wins(self):
        raw = '{"draft": 1}\n{"final": 2}'
        assert extract_json(raw) == {"final": 2}

    def test_scanner_finds_balanced_candidates(self):
        cands = _iter_json_candidates('x {"a":1} y {"b":2} z')
        # Longest (= last) first.
        assert cands == ['{"b":2}', '{"a":1}']


class TestTruncation:
    def test_truncated_string_value(self):
        assert extract_json('{"verdict": "the answer is cut') == {"verdict": "the answer is cut"}

    def test_truncated_after_nested_object(self):
        assert extract_json('{"outer": {"inner": 1}, "other": [1, 2') == {
            "outer": {"inner": 1}, "other": [1, 2],
        }

    def test_dangling_key_is_dropped(self):
        parsed = extract_json('{"verdict": "ok", "confidence":')
        assert parsed == {"verdict": "ok"}

    def test_trailing_comma_is_dropped(self):
        assert extract_json('{"a": 1, "b": 2,}') == {"a": 1, "b": 2}

    def test_close_truncated_helper(self):
        assert _close_truncated('{"a": [1, 2') == {"a": [1, 2]}


class TestAgentShapes:
    """Shapes the Council's actual schemas produce."""

    def test_moderator_response(self):
        raw = """```json
        {
          "alignment": "medium",
          "conflicts": [{"topic": "scale", "severity": "nuance", "status": "unresolved"}],
          "agreements": [{"topic": "direction", "agents": ["oracle", "sage"]}],
          "decision_space": [{"label": "A", "action": "do it", "risk": "low"}]
        }
        ```"""
        parsed = extract_json(raw)
        assert parsed["alignment"] == "medium"
        assert parsed["conflicts"][0]["severity"] == "nuance"

    def test_router_response(self):
        raw = 'Classifying...\n{"query_type": "factual", "confidence": 0.9, "reasoning": "asks what"}'
        parsed = extract_json(raw)
        assert parsed["query_type"] == "factual"
        from agents.schemas import QueryClassification
        assert QueryClassification(**parsed).query_type == "factual"

    def test_markdown_body_with_inline_braces(self):
        """Scholar answers embed markdown; braces must not end the object early."""
        raw = '```json\n{"answer": "Use a dict {k: v} in Python.", "citations": []}\n```'
        assert extract_json(raw) == {"answer": "Use a dict {k: v} in Python.", "citations": []}
