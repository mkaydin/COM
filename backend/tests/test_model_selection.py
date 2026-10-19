"""Per-character provider + model selection, and embedding selection.

Two things this file pins:

* A role resolves **both** a provider and a model, and the two must agree. It
  used to resolve only a model, against whichever single provider was globally
  active — so there was no way to say "the Oracle runs on Ollama". The failure
  mode when that wiring is wrong is silent and expensive: the character
  answers on the wrong model and nothing complains.

* Embedding selection is safe to offer, which means the UI can be told the
  truth about width. The vector stores are fixed at 384 dims; an embedder of
  another width makes cosine search return noise instead of raising.
"""

import pytest
from pathlib import Path

# Source files are read relative to this package, not the working
# directory: the suite is run both from backend/ and from the repo root.
BACKEND = Path(__file__).resolve().parents[1]

from config import get_settings
from llm import catalogue, discovery, embeddings, providers, roles
from agents import registry


@pytest.fixture(autouse=True)
def _dummy_credentials(monkeypatch):
    """`opencode` requires a key; these tests exercise routing, not auth.

    Without this, `build_kwargs` tests fail on a clone with no `.env`. The
    tests that are *about* a missing credential set it back to empty.
    """
    monkeypatch.setattr(
        get_settings(), "opencode_api_key", "test-key-not-real", raising=False
    )


# ======================================================================================
# Embedding model classification
# ======================================================================================


class TestEmbeddingClassification:
    @pytest.mark.parametrize("model_id", [
        "openai/text-embedding-3-small",
        "openai/text-embedding-ada-002",
        "nvidia/nv-embed-v1",
        "nvidia/baai/bge-m3",
        "nomic-embed-text",
        "mxbai-embed-large",
        "snowflake-arctic-embed",
    ])
    def test_embedding_models_recognised(self, model_id):
        assert discovery.is_embedding_model(model_id)

    @pytest.mark.parametrize("model_id", [
        "opencode/big-pickle",
        "longcat-2.5-preview-free",
        "nemotron-3.5-lightning-free",
        "llama3.2",
        "qwen3-coder",
    ])
    def test_chat_models_not_mistaken_for_embedders(self, model_id):
        """A chat model in the embedding list is a worse bug than the reverse."""
        assert not discovery.is_embedding_model(model_id)

    def test_prefix_is_ignored(self):
        """Providers prefix their ids; classification must look at the name."""
        assert discovery.is_embedding_model("ollama/nomic-embed-text")
        assert not discovery.is_embedding_model("ollama/llama3.2")

    def test_empty_is_not_an_embedding_model(self):
        assert not discovery.is_embedding_model("")


# ======================================================================================
# Width compatibility — the thing the UI warns about
# ======================================================================================


class TestEmbeddingCompatibility:
    def test_natively_384_models_accepted(self):
        assert embeddings.check_compatible("all-MiniLM-L6-v2")

    def test_foldable_models_accepted(self):
        """`dimensions` is sent, so a wider model still fits the 384 stores."""
        assert embeddings.check_compatible("openai/text-embedding-3-small")
        assert embeddings.check_compatible("openai/text-embedding-3-large")
        assert embeddings.check_compatible("qwen3-embedding:0.6b")

    def test_fixed_width_models_that_cannot_fold_are_flagged(self):
        """bge-m3 and nv-embed are fixed-width; no way to reach 384."""
        assert not embeddings.check_compatible("bge-m3")
        assert not embeddings.check_compatible("nvidia/nv-embed-v1")
        assert not embeddings.check_compatible("openai/text-embedding-ada-002")

    def test_unknown_model_permissive(self):
        """Refusing to let the user pick would be worse than warning them."""
        assert embeddings.check_compatible("some-unlisted-embedder")

    def test_vector_store_width_is_the_documented_384(self):
        assert embeddings.VECTOR_DIM == 384

    def test_backend_validation_rejects_nonsense(self):
        with pytest.raises(ValueError, match="Unknown embedding backend"):
            embeddings.select("not-a-backend", "all-MiniLM-L6-v2")

    def test_backend_validation_requires_a_model(self):
        with pytest.raises(ValueError, match="model id is required"):
            embeddings.select("local", "")


# ======================================================================================
# Per-role provider resolution
# ======================================================================================


@pytest.fixture(autouse=True)
def clean_role_cache():
    """Role overrides are module-global; keep tests independent."""
    roles._reset_cache()
    yield
    roles._reset_cache()


class TestPerRoleProvider:
    def test_defaults_to_the_global_provider(self):
        p = roles.provider_for("ORACLE")
        assert p.id == providers.get().id

    def test_role_override_selects_its_own_provider(self):
        roles._provider_overrides["ORACLE"] = "ollama"
        assert roles.provider_for("ORACLE").id == "ollama"

    def test_roles_are_independent(self):
        """The point of the feature: two characters, two providers."""
        roles._provider_overrides["ORACLE"] = "ollama"
        assert roles.provider_for("ORACLE").id == "ollama"
        assert roles.provider_for("SCHOLAR").id == providers.get().id

    def test_case_insensitive_role_lookup(self):
        roles._provider_overrides["ORACLE"] = "ollama"
        assert roles.provider_for("oracle").id == "ollama"
        assert roles.provider_for(" Oracle ").id == "ollama"

    def test_unknown_provider_raises_rather_than_defaulting(self):
        """Silently falling back would change which models run, invisibly."""
        roles._provider_overrides["ORACLE"] = "typo-provider"
        with pytest.raises(providers.ProviderError, match="Unknown LLM provider"):
            roles.provider_for("ORACLE")

    def test_missing_provider_override_falls_through_to_global(self):
        """A NULL provider column means 'whichever is globally active'."""
        assert "ORACLE" not in roles._provider_overrides
        assert roles.provider_for("ORACLE").id == providers.get().id


class TestRoleRoutingReportsProvider:
    def test_routing_reports_the_provider(self):
        r = roles.routing_for("ORACLE")
        assert r["provider"] == providers.get().id
        assert r["provider_label"]
        assert r["provider_source"] == "global"

    def test_routing_marks_a_pinned_provider(self):
        roles._provider_overrides["ORACLE"] = "ollama"
        r = roles.routing_for("ORACLE")
        assert r["provider"] == "ollama"
        assert r["provider_source"] == "runtime"

    def test_provider_error_is_reported_not_swallowed(self):
        """The UI has to be able to show 'that provider does not exist'."""
        roles._provider_overrides["ORACLE"] = "typo-provider"
        r = roles.routing_for("ORACLE")
        assert r["provider_error"] and "typo-provider" in r["provider_error"]

    def test_model_resolves_against_its_own_provider(self):
        """An Ollama-pinned Oracle gets Ollama's default, not Zen's."""
        roles._provider_overrides["ORACLE"] = "ollama"
        model = roles.model_for("ORACLE")
        assert model == providers.get("ollama").role_defaults["ORACLE"]

    def test_every_character_role_is_known(self):
        """A character added without its role must fail here, not at call time."""
        for c in registry.BY_KEY.values():
            assert c.model_role in roles.KNOWN_ROLES, (
                f"{c.key} uses role {c.model_role!r}, which is not in KNOWN_ROLES"
            )


class TestRoleDefaultsAreWellFormed:
    """The curated snapshot and the roles must not disagree.

    `ling-3.1-flash-free` and `step-5-preview-free` stayed in
    `OPENCODE_ROLE_DEFAULTS` after rotating out of Zen's free tier, so the
    Analyst and Historian were pinned to models that no longer existed. The
    free catalogue is a snapshot and it drifts; these checks catch the drift
    that is checkable without the network.
    """

    def test_every_role_has_a_default(self):
        for role in roles.KNOWN_ROLES:
            assert roles.KNOWN_ROLES
            assert catalogue.OPENCODE_ROLE_DEFAULTS.get(role), f"{role} has no default"

    def test_every_default_is_a_free_catalogue_entry(self):
        free = catalogue.OPENCODE_FREE_MODELS
        for role, model in catalogue.OPENCODE_ROLE_DEFAULTS.items():
            assert model in free, (
                f"{role}'s default {model!r} is not in OPENCODE_FREE_MODELS — "
                "either add it or repoint the role"
            )

    def test_every_default_is_bare(self):
        """Defaults are bare; `to_cli_model` adds the provider prefix."""
        for role, model in catalogue.OPENCODE_ROLE_DEFAULTS.items():
            assert "/" not in model, (
                f"{role}'s default {model!r} carries a provider prefix; the "
                "CLI transport expects a bare tag"
            )

    def test_ollama_defaults_cover_every_role(self):
        for role in roles.KNOWN_ROLES:
            assert role in catalogue.OLLAMA_ROLE_DEFAULTS

    def test_no_retired_model_is_still_a_default(self):
        """A model that left the free tier must not pin a character.

        Scoped to role defaults specifically: that is what actually breaks a
        character. A free model listed but unused is harmless noise, whereas a
        default pointing at nothing means the character cannot answer at all.
        """
        retired = ("ling-3.1-flash-free", "step-5-preview-free", "cat-jam-free")
        stale = set(catalogue.OPENCODE_ROLE_DEFAULTS.values()) & set(retired)
        assert not stale, f"retired models still used as role defaults: {stale}"


class TestCallersDoNotPinTheGlobalProvider:
    """`all_roles(provider)` pins every role to one provider, defeating the lot.

    The resolver was correct and the feature still did nothing, because three
    API handlers passed the globally-active provider in explicitly. Static
    check, because the failure is invisible: the response looks complete and
    every role quietly reports the wrong provider.
    """
    @pytest.mark.parametrize("module_path", ["api/llm.py", "api/agents.py", "api/forum.py"])
    def test_no_caller_pins_a_provider(self, module_path):

        src = (BACKEND / module_path).read_text()
        assert "all_roles(provider)" not in src, (
            f"{module_path} calls all_roles(provider), which pins every role to "
            "the global provider. Pass nothing and let each role resolve its own."
        )
        assert "all_roles()" in src, f"{module_path} should resolve per role"

    def test_health_resolves_per_role(self):
        """`llm.health` already does; pin it so it stays that way."""

        src = (BACKEND / "llm/__init__.py").read_text()
        assert "roles.all_roles()" in src


class TestBuildKwargsUsesRoleProvider:
    def test_kwargs_follow_the_role_not_the_global(self):
        from llm import build_kwargs

        roles._provider_overrides["ORACLE"] = "ollama"
        kwargs = build_kwargs(role="ORACLE", messages=[])
        assert kwargs["api_base"] == providers.get("ollama").base_url
        assert kwargs["model"] == providers.get("ollama").model_id(
            providers.get("ollama").role_defaults["ORACLE"]
        )

    def test_explicit_provider_argument_still_wins(self):
        """Tooling passes a provider deliberately; that must not be overridden."""
        from llm import build_kwargs

        roles._provider_overrides["ORACLE"] = "ollama"
        kwargs = build_kwargs(
            role="ORACLE", provider=providers.get("opencode"), messages=[]
        )
        assert kwargs["api_base"] == providers.get("opencode").base_url

    def test_cli_transport_takes_no_api_base(self):
        from llm import build_kwargs

        kwargs = build_kwargs(
            role="ORACLE", provider=providers.get("opencode-cli"), messages=[]
        )
        assert "api_base" not in kwargs
        assert "api_key" not in kwargs


# ======================================================================================
# Discovery shape — the picker must not have to know provider identities
# ======================================================================================


class TestDiscoveryShape:
    @pytest.mark.asyncio
    async def test_every_provider_reports_the_same_keys(self, monkeypatch):
        """One unreachable provider must not change the page's structure."""

        async def fake_cli():
            return {"models": ["opencode/big-pickle"], "reachable": True, "hint": None}

        async def fake_ollama():
            return {"models": [], "reachable": False, "hint": "down"}

        monkeypatch.setattr(discovery, "list_cli_models", fake_cli)
        monkeypatch.setattr(discovery, "list_ollama_models", fake_ollama)

        result = await discovery.discover()
        for pid in providers.available():
            entry = result["providers"][pid]
            assert {"id", "label", "reachable", "models", "hint"} <= set(entry)

    @pytest.mark.asyncio
    async def test_unreachable_provider_is_empty_not_an_error(self, monkeypatch):
        async def down():
            return {"models": [], "reachable": False, "hint": "unreachable"}

        monkeypatch.setattr(discovery, "list_cli_models", down)
        monkeypatch.setattr(discovery, "list_ollama_models", down)

        result = await discovery.discover()
        assert result["providers"]["ollama"]["models"] == []
        assert result["providers"]["ollama"]["reachable"] is False
        assert result["providers"]["ollama"]["hint"]

    @pytest.mark.asyncio
    async def test_one_broken_provider_does_not_blank_the_page(self, monkeypatch):
        async def boom():
            raise RuntimeError("subprocess exploded")

        async def ok():
            return {"models": ["x/y"], "reachable": True, "hint": None}

        monkeypatch.setattr(discovery, "list_cli_models", boom)
        monkeypatch.setattr(discovery, "list_ollama_models", ok)

        result = await discovery.discover()
        assert result["providers"]["opencode-cli"]["reachable"] is False
        assert "subprocess exploded" in result["providers"]["opencode-cli"]["hint"]
        # The others still resolved.
        assert result["providers"]["ollama"]["models"] == ["x/y"]

    @pytest.mark.asyncio
    async def test_embedding_block_is_always_present(self, monkeypatch):
        async def down():
            return {"models": [], "reachable": False, "hint": "no"}

        monkeypatch.setattr(discovery, "list_cli_models", down)
        monkeypatch.setattr(discovery, "list_ollama_models", down)

        result = await discovery.discover()
        assert result["embedding"]["supported"]
        assert result["embedding"]["dimension"] == 384

    @pytest.mark.asyncio
    async def test_embedding_block_matches_the_embedding_endpoint(self, monkeypatch):
        """One shape, so the page can render from either response.

        These were two different dicts; the picker read `default_model` from the
        discovery copy and rendered "(from )" because the field was only on the
        other one.
        """
        async def down():
            return {"models": [], "reachable": False, "hint": "no"}

        monkeypatch.setattr(discovery, "list_cli_models", down)
        monkeypatch.setattr(discovery, "list_ollama_models", down)

        result = await discovery.discover()
        assert set(result["embedding"]) == set(embeddings.current())


class TestPickerHelpers:
    def test_free_models_are_offered_first(self):
        discovered = {"providers": {"p": {"models": [
            {"id": "paid/model", "embedding": False, "free": False},
            {"id": "a-free", "embedding": False, "free": True},
            {"id": "z-free", "embedding": False, "free": True},
        ]}}}
        assert discovery.pick_default_models(discovered, "p")[0] == "a-free"

    def test_chat_picker_excludes_embedders(self):
        """An embedder in the chat dropdown is the worst state this page has."""
        discovered = {"providers": {"p": {"models": [
            {"id": "chat/model", "embedding": False, "free": True},
            {"id": "nomic-embed-text", "embedding": True, "free": True},
        ]}}}
        assert discovery.pick_default_models(discovered, "p") == ["chat/model"]

    def test_embedding_picker_is_the_complement(self):
        discovered = {"providers": {"p": {"models": [
            {"id": "chat/model", "embedding": False},
            {"id": "nomic-embed-text", "embedding": True},
        ]}}}
        assert discovery.known_embedding_models(discovered, "p") == ["nomic-embed-text"]

    def test_empty_provider_yields_no_defaults(self):
        assert discovery.pick_default_models({"providers": {}}, "nope") == []

    def test_dimension_guess_from_free_text(self):
        assert discovery.guess_dimension("nomic-embed-text (768 dim)") == 768
        assert discovery.guess_dimension("no hint here") == 384