"""Tests for provider resolution and per-role model binding.

The forum runs five characters, each potentially on a different model. This is
the layer that makes that possible, so its resolution order and its guarantees
about the defaults are both pinned here.
"""

import pytest

from llm import build_kwargs, catalogue, providers, roles
from config import Settings, get_settings


@pytest.fixture(autouse=True)
def _clean_overrides():
    """Runtime overrides are process-global; never let one test leak into another."""
    roles._reset_cache()
    yield
    roles._reset_cache()


class TestProviderResolution:
    def test_default_provider_is_the_cli(self):
        """The API refuses free models for standalone clients, so the CLI is
        the only route to them — that makes it the default."""
        p = providers.get()
        assert p.id == "opencode-cli"
        assert p.transport == "cli_provider"

    def test_ollama_is_available_and_optional(self):
        p = providers.get("ollama")
        assert p.id == "ollama"
        assert p.base_url.endswith("/v1")
        # Ollama ignores the key value; only its presence matters to LiteLLM.
        assert p.api_key

    def test_unknown_provider_raises(self):
        """A typo in .env must fail loudly, not silently hit the wrong endpoint."""
        with pytest.raises(providers.ProviderError, match="Unknown LLM provider"):
            providers.get("not-a-provider")

    def test_provider_list_includes_all_three(self):
        assert set(providers.available()) == {"opencode-cli", "opencode", "ollama"}

    def test_model_id_gets_the_litellm_prefix(self):
        p = providers.get("opencode")
        assert p.model_id("big-pickle") == "openai/big-pickle"

    def test_cli_provider_takes_the_bare_tag(self):
        """`openai/big-pickle` is not a model the CLI recognises — it does its
        own provider/ prefixing."""
        assert providers.get("opencode-cli").model_id("big-pickle") == "big-pickle"

    def test_model_id_is_idempotent(self):
        p = providers.get("opencode")
        assert p.model_id("openai/big-pickle") == "openai/big-pickle"

    def test_require_credential_fails_without_a_key(self):
        bare = providers.Provider(id="x", label="x", base_url="http://x", api_key="",
                                 requires_key=True)
        with pytest.raises(providers.MissingCredential):
            providers.require_credential(bare)

    def test_credentialed_provider_passes_without_check(self):
        ok = providers.Provider(id="x", label="x", base_url="http://x", api_key="k",
                                requires_key=False)
        providers.require_credential(ok)  # must not raise


class TestRoleBinding:
    def test_every_known_role_resolves(self):
        for role in roles.KNOWN_ROLES:
            assert roles.model_for(role), f"{role} resolved to nothing"

    def test_defaults_come_from_the_provider(self):
        p = providers.get()
        assert roles.model_for("MODERATOR", p) == "big-pickle"
        assert roles.model_for("ORACLE", p) == "longcat-2.5-preview-free"

    def test_each_character_gets_a_distinct_model(self):
        """Spreading across models keeps free-tier rate limits off one endpoint."""
        p = providers.get()
        models = [roles.model_for(r, p) for r in roles.KNOWN_ROLES]
        assert len(set(models)) == len(models)

    def test_unknown_role_falls_back_rather_than_raising(self):
        """A new character with no default must still get a usable model."""
        assert roles.model_for("BRAND_NEW_CHARACTER")

    def test_role_lookup_is_case_insensitive(self):
        p = providers.get()
        assert roles.model_for("oracle", p) == roles.model_for("ORACLE", p) == roles.model_for("Oracle", p)

    def test_env_override_wins_over_default(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "llm_model_oracle", "custom-model-x", raising=False)
        assert roles.model_for("ORACLE") == "custom-model-x"

    def test_env_override_reports_its_source(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "llm_model_sage", "custom-y", raising=False)
        assert roles.routing_for("SAGE")["source"] == "env"

    def test_default_reports_its_source(self):
        assert roles.routing_for("MODERATOR")["source"] == "default"

    def test_runtime_override_wins_over_env(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "llm_model_oracle", "from-env", raising=False)
        roles._overrides["ORACLE"] = "from-runtime"
        assert roles.model_for("ORACLE") == "from-runtime"
        assert roles.routing_for("ORACLE")["source"] == "runtime"

    def test_unknown_role_source_is_fallback(self):
        assert roles.routing_for("NEWCOMER")["source"] == "fallback"


class TestFreeModelGuarantee:
    """The one promise that would quietly cost the user money."""

    def test_all_defaults_are_free(self):
        for model_id in catalogue.OPENCODE_ROLE_DEFAULTS.values():
            assert model_id in catalogue.OPENCODE_FREE_MODELS, (
                f"{model_id} is not a verified free model"
            )

    def test_ollama_defaults_do_not_leak_remote_models(self):
        """Switching to local must not leave a character pointing at Zen."""
        for role in catalogue.OLLAMA_ROLE_DEFAULTS:
            assert catalogue.OLLAMA_ROLE_DEFAULTS[role] == catalogue.OLLAMA_FALLBACK_MODEL

    def test_free_catalogue_entries_are_all_chat_completions(self):
        """Responses-API and systemone models are excluded from the default set."""
        unsupported = set(catalogue.OPENCODE_FREE_UNSUPPORTED)
        assert not (set(catalogue.OPENCODE_FREE_MODELS) & unsupported)

    def test_free_models_list_is_non_empty_and_marked_free(self):
        models = catalogue.free_models_for("opencode")
        assert models
        assert all(m["free"] for m in models)

    def test_unsupported_free_models_explain_themselves(self):
        """The UI must be able to say *why* a free model is not offered."""
        for entry in catalogue.unsupported_free_models():
            assert entry["available"] is False
            assert entry["reason"]

    def test_paid_models_are_never_defaults(self):
        paid = set(catalogue.OPENCODE_PAID_SUGGESTIONS)
        assert not (paid & set(catalogue.OPENCODE_ROLE_DEFAULTS.values()))

    def test_free_models_for_ollama_is_empty(self):
        assert catalogue.free_models_for("ollama") == []


class TestCompletionKwargs:
    def test_kwargs_are_explicit_not_environmental(self):
        """api_base/api_key must be explicit so a stray OPENAI_* cannot redirect."""
        kwargs = build_kwargs(role="MODERATOR", messages=[{"role": "user", "content": "hi"}],
                              provider=providers.get("opencode"))
        assert kwargs["api_base"] == "https://opencode.ai/zen/v1"
        assert kwargs["api_key"]
        assert kwargs["model"] == "openai/big-pickle"

    def test_kwargs_use_the_roles_model(self):
        kwargs = build_kwargs(role="SAGE", messages=[], provider=providers.get("opencode"))
        assert kwargs["model"] == "openai/mimo-v2.6-flash-free"

    def test_explicit_model_overrides_the_role(self):
        kwargs = build_kwargs(role="SAGE", messages=[], model="some-other-model",
                              provider=providers.get("opencode"))
        assert kwargs["model"] == "openai/some-other-model"

    def test_cli_kwargs_carry_no_http_fields(self):
        """The CLI transport has no endpoint and no key to send."""
        kwargs = build_kwargs(role="MODERATOR", messages=[], provider=providers.get("opencode-cli"))
        assert "api_base" not in kwargs
        assert "api_key" not in kwargs
        assert kwargs["model"] == "big-pickle"

    def test_overrides_pass_through(self):
        kwargs = build_kwargs(role="MODERATOR", messages=[], temperature=0.9, max_tokens=99,
                              provider=providers.get("opencode"))
        assert kwargs["temperature"] == 0.9
        assert kwargs["max_tokens"] == 99

    def test_missing_credential_is_refused_before_the_call(self, monkeypatch):
        """Better a clear error than five characters silently failing."""
        monkeypatch.setattr(get_settings(), "opencode_api_key", "", raising=False)
        with pytest.raises(providers.MissingCredential):
            build_kwargs(role="MODERATOR", messages=[], provider=providers.get("opencode"))

    def test_cli_provider_needs_no_credential(self, monkeypatch):
        """The CLI uses its own credential store, so an empty key is not an error."""
        monkeypatch.setattr(get_settings(), "opencode_api_key", "", raising=False)
        kwargs = build_kwargs(role="MODERATOR", messages=[], provider=providers.get("opencode-cli"))
        assert kwargs["model"]

    def test_json_mode_only_when_configured(self, monkeypatch):
        p = providers.get("opencode")
        monkeypatch.setattr(get_settings(), "supports_json_mode", False, raising=False)
        assert "response_format" not in build_kwargs(role="MODERATOR", messages=[], provider=p)

        monkeypatch.setattr(get_settings(), "supports_json_mode", True, raising=False)
        assert build_kwargs(role="MODERATOR", messages=[], provider=p)["response_format"] == {
            "type": "json_object"
        }

    def test_json_mode_never_sent_to_the_cli(self, monkeypatch):
        """`opencode run` has no such flag; sending one would fail the call."""
        monkeypatch.setattr(get_settings(), "supports_json_mode", True, raising=False)
        kwargs = build_kwargs(role="MODERATOR", messages=[], provider=providers.get("opencode-cli"))
        assert "response_format" not in kwargs


class TestLegacyConfig:
    def test_legacy_llm_model_used_when_no_new_default(self):
        """An existing .env from the Ollama-only setup must keep working."""
        s = Settings(_env_file=None, llm_model="openai/gemma4:e4b")
        # llm_model_default was not explicitly passed, so the legacy key wins.
        assert "llm_model_default" not in s.model_fields_set
        assert s.default_model == "gemma4:e4b"
        assert s.bare_llm_model == "gemma4:e4b"

    def test_explicit_default_beats_legacy(self):
        s = Settings(_env_file=None, llm_model="legacy-model",
                     llm_model_default="big-pickle")
        assert "llm_model_default" in s.model_fields_set
        assert s.default_model == "big-pickle"

    def test_prefix_is_stripped_from_legacy_model(self):
        """The old Ollama config carried an `openai/` prefix."""
        assert Settings(_env_file=None, llm_model="openai/gemma4:e4b").default_model == "gemma4:e4b"