"""Tests for the CLI transport adapter.

`opencode run --format json` emits newline-delimited events, has no system
prompt flag, and exposes neither temperature nor max_tokens. Everything here
covers the seams where those differences would otherwise show up as a silently
malformed character post.
"""

import pytest

from llm import cli_provider


class TestModelNaming:
    def test_bare_tag_gets_the_opencode_prefix(self):
        assert cli_provider.to_cli_model("big-pickle") == "opencode/big-pickle"

    def test_already_prefixed_is_left_alone(self):
        assert cli_provider.to_cli_model("opencode/big-pickle") == "opencode/big-pickle"

    def test_a_litellm_prefix_is_stripped(self):
        """`openai/big-pickle` is not a model the CLI knows."""
        assert cli_provider.to_cli_model("openai/big-pickle") == "opencode/big-pickle"

    def test_empty_is_empty(self):
        assert cli_provider.to_cli_model("") == "opencode/"


class TestFlattenMessages:
    def test_user_only_passes_through_cleanly(self):
        """A plain user message must not be wrapped — extra labels would be
        read as part of the question."""
        out = cli_provider.flatten_messages([{"role": "user", "content": "hello"}])
        assert out == "hello"

    def test_system_prompt_is_folded_in_and_labelled(self):
        out = cli_provider.flatten_messages([
            {"role": "system", "content": "You are the Oracle."},
            {"role": "user", "content": "Is this safe?"},
        ])
        assert "[SYSTEM]" in out
        assert "You are the Oracle." in out
        assert "Is this safe?" in out

    def test_role_labels_survive_because_prompts_address_the_reader(self):
        """Several character prompts say "Reply to the claim you are
        answering". Without a role label that instruction reads as quoted
        material rather than as a directive."""
        out = cli_provider.flatten_messages([
            {"role": "system", "content": "Reply to the specific claim you are answering."},
        ])
        assert "[SYSTEM]" in out

    def test_empty_content_is_skipped(self):
        out = cli_provider.flatten_messages([
            {"role": "system", "content": ""},
            {"role": "user", "content": "real"},
        ])
        assert "[SYSTEM]" not in out
        assert out == "real"

    def test_empty_transcript(self):
        assert cli_provider.flatten_messages([]) == ""
        assert cli_provider.flatten_messages(None) == ""

    def test_missing_role_defaults_to_user(self):
        assert cli_provider.flatten_messages([{"content": "x"}]) == "x"


class TestParseEvents:
    def test_single_text_event(self):
        raw = '{"type":"text","part":{"text":"hello"}}'
        assert cli_provider.parse_events(raw) == "hello"

    def test_multiple_text_events_are_concatenated_in_order(self):
        raw = "\n".join([
            '{"type":"text","part":{"text":"one "}}',
            '{"type":"text","part":{"text":"two"}}',
        ])
        assert cli_provider.parse_events(raw) == "one two"

    def test_non_text_events_are_ignored(self):
        """The real stream starts with step_start and carries timing records."""
        raw = "\n".join([
            '{"type":"step_start","part":{"id":"prt_1"}}',
            '{"type":"text","part":{"text":"the answer"}}',
            '{"type":"step_finish","part":{}}',
        ])
        assert cli_provider.parse_events(raw) == "the answer"

    def test_malformed_lines_are_skipped_not_fatal(self):
        raw = "\n".join(["not json at all", '{"type":"text","part":{"text":"kept"}}'])
        assert cli_provider.parse_events(raw) == "kept"

    def test_blank_lines_are_skipped(self):
        assert cli_provider.parse_events("\n\n  \n") == ""

    def test_json_wrapped_text_is_not_double_escaped(self):
        """Characters are asked for JSON, and that JSON arrives as a JSON string
        inside the event — it must survive as real JSON text."""
        raw = '{"type":"text","part":{"text":"{\\"verdict\\":\\"ok\\"}"}}'
        assert cli_provider.parse_events(raw) == '{"verdict":"ok"}'

    def test_non_dict_events_are_ignored(self):
        assert cli_provider.parse_events('["a","b"]') == ""

    def test_event_without_text_yields_empty(self):
        assert cli_provider.parse_events('{"type":"text","part":{}}') == ""

    def test_missing_text_key_yields_empty(self):
        assert cli_provider.parse_events('{"type":"text"}') == ""


class TestBinaryResolution:
    def test_missing_binary_names_the_remedy(self, monkeypatch):
        """Reporting "not on PATH" alone sends the user hunting for a PATH
        problem they do not have."""
        monkeypatch.setattr(cli_provider, "binary_path", lambda: None)
        monkeypatch.setattr(cli_provider, "container_hint", lambda: None)
        import asyncio

        result = asyncio.run(cli_provider.health())
        assert result["ok"] is False
        assert "not on PATH" in result["hint"]
        assert "start.py --no-docker" in result["hint"]

    def test_in_a_container_it_names_the_container_remedy(self, monkeypatch):
        """Inside the backend image the CLI will never be on PATH, because the
        image does not ship one — so "install the CLI" is the wrong advice."""
        monkeypatch.setattr(cli_provider, "binary_path", lambda: None)
        monkeypatch.setattr(
            cli_provider, "container_hint",
            lambda: "use LLM_PROVIDER=ollama (free, local)")
        import asyncio

        hint = asyncio.run(cli_provider.health())["hint"]
        assert "LLM_PROVIDER=ollama" in hint
        assert "start.py --no-docker" not in hint

    def test_container_hint_is_silent_on_a_host(self, monkeypatch):
        monkeypatch.setattr(cli_provider, "_IN_CONTAINER", False)
        assert cli_provider.container_hint() is None

    def test_container_hint_speaks_inside_a_container(self, monkeypatch):
        monkeypatch.setattr(cli_provider, "_IN_CONTAINER", True)
        hint = cli_provider.container_hint()
        assert hint is not None
        assert "does not ship the OpenCode CLI" in hint

    def test_present_binary_reports_ok(self, monkeypatch):
        monkeypatch.setattr(cli_provider, "binary_path", lambda: "/usr/bin/opencode")
        import asyncio

        result = asyncio.run(cli_provider.health())
        assert result["ok"] is True
        assert result["binary"] == "/usr/bin/opencode"
        # The latency caveat is the main thing a user needs to know up front.
        assert "slower" in result["hint"]


class TestErrorClasses:
    def test_not_installed_is_a_provider_error(self):
        assert issubclass(cli_provider.CliNotInstalled, cli_provider.CliProviderError)

    def test_call_failed_is_a_provider_error(self):
        assert issubclass(cli_provider.CliCallFailed, cli_provider.CliProviderError)