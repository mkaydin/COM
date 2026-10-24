"""Contract tests for the routers that had none.

`api/forum.py` was the only router with unit coverage. The other ten had 52
endpoints between them and nothing asserting their error paths, which is the
category of code that turns a bad request into a 500 and nobody notices until a
client sends one.

These are contract tests: no Postgres, no LLM. They pin the response code for
malformed and missing input, and the shape of the few pure responses. They are
deliberately not exhaustive — the point is that every router has *something*
that fails when its error handling regresses.

The first bug this found: `GET /api/verdicts/{id}` called `UUID()` on a raw
string while `POST /api/verdicts` validated it, so the same malformed id was a
400 on one route and a 500 on the other.
"""

import uuid

import pytest
from fastapi import HTTPException

from api import agents, deliberations, documents, llm as llm_api, okf, rag
from api import divination, sessions, verdicts, wiki


# ======================================================================================
# A minimal async-session stand-in.
# ======================================================================================


class _Result:
    def __init__(self, *, one=None, rows=None, scalar=None):
        self._one = one
        self._rows = rows or []
        self._scalar = scalar

    def scalar_one_or_none(self):
        return self._one

    def scalar(self):
        return self._scalar

    def one(self):
        return self._one

    def all(self):
        return self._rows

    def scalars(self):
        return self

    def unique(self):
        return self


class _DB:
    def __init__(self, result=None):
        self.result = result or _Result()
        self.added = []
        self.deleted = []
        self.commits = 0

    async def execute(self, *args, **kwargs):
        return self.result

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        pass

    async def flush(self):
        pass


async def _raises(coro, status: int):
    with pytest.raises(HTTPException) as exc:
        await coro
    assert exc.value.status_code == status, (
        f"expected {status}, got {exc.value.status_code}: {exc.value.detail}"
    )
    return exc.value


# ======================================================================================
# agents
# ======================================================================================


class TestAgents:
    async def test_unknown_agent_is_404(self):
        await _raises(agents.get_agent("nobody"), 404)

    async def test_cards_include_the_moderator(self):
        cards = await agents.list_agents()
        assert any(c["name"] == "moderator" for c in cards)
        assert all("display_name" in c for c in cards)

    async def test_every_card_reports_a_model(self):
        """The profile page shows the model; an empty string looks like a bug."""
        for card in await agents.list_agents():
            assert "model" in card


# ======================================================================================
# deliberations
# ======================================================================================


class TestDeliberations:
    async def test_missing_deliberation_is_404(self):
        await _raises(
            deliberations.get_deliberation(uuid.uuid4(), db=_DB()), 404
        )

    async def test_missing_status_is_404(self):
        await _raises(
            deliberations.get_deliberation_status(uuid.uuid4(), db=_DB()), 404
        )

    async def test_deleting_a_missing_deliberation_is_404(self):
        await _raises(
            deliberations.delete_deliberation(uuid.uuid4(), db=_DB()), 404
        )


# ======================================================================================
# documents
# ======================================================================================


class TestDocuments:
    async def test_missing_document_is_404(self):
        await _raises(documents.get_document(uuid.uuid4(), db=_DB()), 404)

    async def test_scout_report_keeps_sources_and_texts(self):
        body = documents.ScoutReportSave(
            topic="Ion thrusters",
            summarized_text="A summary.",
            original_urls=["https://example.org/a"],
            original_texts="The original passage.",
            key_signals="efficiency",
            confidence=0.7,
        )
        db = _DB()
        result = await documents.save_scout_report(body, db=db)

        assert result["status"] == "saved"
        doc = db.added[0]
        assert doc.source_type == "scout_report"
        # The raw material has to survive: a summary with no provenance cannot
        # be checked later, which defeats the point of saving the report.
        assert "https://example.org/a" in doc.content
        assert "The original passage." in doc.content
        assert doc.metadata_json["source_urls"] == ["https://example.org/a"]


# ======================================================================================
# llm
# ======================================================================================


class TestLLMEndpoints:
    async def test_unknown_provider_in_set_role_is_400(self):
        async def boom(*a, **k):
            from llm import providers
            raise providers.ProviderError("Unknown LLM provider 'nope'")

        original = llm_api.roles.set_override
        llm_api.roles.set_override = boom
        try:
            await _raises(
                llm_api.set_role(
                    llm_api.RoleSet(role="ORACLE", model_id="x", provider="nope")
                ),
                400,
            )
        finally:
            llm_api.roles.set_override = original

    async def test_unknown_provider_in_models_is_400(self):
        await _raises(llm_api.models("nope"), 400)

    async def test_unknown_embedding_backend_is_400(self):
        await _raises(
            llm_api.embedding_select(
                llm_api.EmbeddingSet(backend="nope", model="whatever")
            ),
            400,
        )

    def test_embedding_selection_requires_a_model(self):
        """Rejected by the schema, before the endpoint is reached."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            llm_api.EmbeddingSet(backend="ollama", model="")


# ======================================================================================
# okf
# ======================================================================================


class TestOkf:
    async def test_traversal_is_400(self):
        await _raises(okf.get_concept("../../etc/passwd"), 400)

    async def test_absolute_path_cannot_escape_the_bundle(self):
        """`/etc/passwd` is split into bundle-relative segments, not honoured.

        `parse_concept_id` drops empty parts, so this becomes the concept
        `etc/passwd` inside BUNDLE_ROOT — hence 404 rather than 400. What
        matters is that it does not read outside the tree.
        """
        await _raises(okf.get_concept("/etc/passwd"), 404)

    async def test_missing_concept_is_404(self):
        await _raises(okf.get_concept("no_such_concept_anywhere"), 404)

    async def test_listing_returns_the_bundle(self):
        out = await okf.list_concepts()
        assert "bundle" in out
        assert isinstance(out["concepts"], list)


# ======================================================================================
# rag
# ======================================================================================


class TestRag:
    async def test_unknown_promote_action_is_400(self):
        await _raises(
            rag.promote_chunk(
                rag.PromoteRequest(candidate_id=str(uuid.uuid4()), action="maybe"),
                db=_DB(),
            ),
            400,
        )


# ======================================================================================
# sessions
# ======================================================================================


class TestSessions:
    async def test_missing_session_is_404(self):
        await _raises(sessions.get_session(uuid.uuid4(), db=_DB()), 404)

    async def test_archiving_a_missing_session_is_404(self):
        await _raises(sessions.archive_session(uuid.uuid4(), db=_DB()), 404)


# ======================================================================================
# verdicts
# ======================================================================================


class TestVerdicts:
    async def test_malformed_id_is_400_not_500(self):
        """The GET validated nothing while the POST did.

        A raw `UUID()` on user input raises ValueError, which FastAPI turns into
        a 500 — the caller's mistake reported as a server fault.
        """
        await _raises(verdicts.get_verdict("not-a-uuid", db=_DB()), 400)

    async def test_missing_verdict_is_404(self):
        await _raises(verdicts.get_verdict(str(uuid.uuid4()), db=_DB()), 404)

    async def test_malformed_id_on_submit_is_400(self):
        await _raises(
            verdicts.submit_verdict(
                verdicts.VerdictCreate(deliberation_id="not-a-uuid"),
                background_tasks=None, db=_DB(),
            ),
            400,
        )


# ======================================================================================
# wiki
# ======================================================================================


class TestWiki:
    async def test_traversal_is_400(self):
        await _raises(wiki.get_wiki_page("../../etc/passwd"), 400)

    async def test_absolute_path_is_400(self):
        await _raises(wiki.get_wiki_page("/etc/passwd"), 400)

    async def test_null_byte_is_400(self):
        """A null byte raises ValueError from the OS layer — a 500 without this."""
        await _raises(wiki.get_wiki_page("page\x00.md"), 400)

    async def test_missing_page_is_404(self):
        await _raises(wiki.get_wiki_page("no_such_page_anywhere.md"), 404)


# ======================================================================================
# divination
# ======================================================================================


class TestDivination:
    async def test_search_returns_every_stage(self, monkeypatch):
        """Expand -> gather -> synthesise, and all three survive into the result.

        The sources are the part that was silently empty for months; a shape
        assertion here would have caught that as soon as it regressed.
        """
        async def terms(q):
            return ["alpha", "beta"]

        async def gather(search_terms):
            return (
                [{"title": "Source A", "url": "https://example.org/a"}],
                [{"source": "Source A", "text": "a summary"}],
                ["a summary"],
            )

        async def synth(query, search_terms, summaries):
            return "the synthesis"

        monkeypatch.setattr(divination, "_expand_search_terms", terms)
        monkeypatch.setattr(divination, "_gather_intelligence", gather)
        monkeypatch.setattr(divination, "_synthesize", synth)

        out = await divination.divination_search(
            divination.DivinationRequest(query="what is a vector index")
        )
        assert out.original_query == "what is a vector index"
        assert out.search_terms == ["alpha", "beta"]
        assert out.sources and out.sources[0]["url"] == "https://example.org/a"
        assert out.synthesis == "the synthesis"

    async def test_save_files_a_document_and_schedules_ingestion(self, monkeypatch):
        from fastapi import BackgroundTasks
        import db.postgres as pg

        class FakeSession:
            def __init__(self):
                self.added = []
                self.commits = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def add(self, obj):
                self.added.append(obj)

            async def commit(self):
                self.commits += 1

        fake = FakeSession()
        monkeypatch.setattr(pg, "async_session", lambda: fake)

        bg = BackgroundTasks()
        out = await divination.divination_save(
            divination.DivinationSaveRequest(
                title="Vector index report",
                synthesis="The synthesis.",
                sources=[{"title": "Source A", "url": "https://example.org/a"}],
                summaries=[{"source": "Source A", "text": "a summary"}],
            ),
            background_tasks=bg,
        )

        assert out["status"] == "saved"
        assert fake.commits == 1
        doc = fake.added[0]
        assert doc.source_type == "divination"
        # The sources are the point of a research report; losing them turns it
        # into an opinion piece with no provenance.
        assert "https://example.org/a" in doc.content
        assert "a summary" in doc.content
        assert len(bg.tasks) == 1, "ingestion was not scheduled"
