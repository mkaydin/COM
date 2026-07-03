"""OKF conformance tests — validate backend/okf against the SPEC.md contract.

These mirror the upstream OKF test suite (google-okf/okf/tests/), ported to
our vendored package paths. They pin the format contract from SPEC.md §3-§9.
"""
import asyncio
import pytest
from pathlib import Path
from textwrap import dedent

from okf.document import OKFDocument, OKFDocumentError, REQUIRED_FRONTMATTER_KEYS
from okf.paths import concept_id_to_path, parse_concept_id, path_to_concept_id
from okf.context import set_context, set_web_state, clear_web_state, is_web_pass
from okf.bundle_tools import write_concept, read_concept
from okf.index import regenerate_indexes
from okf.viewer import generate_visualization, _walk_concepts, _build_graph


# --- §4 Concept Documents: frontmatter + body -------------------------------

class TestOKFDocument:
    def test_roundtrip_preserves_frontmatter_and_body(self):
        src = (
            "---\n"
            "type: BigQuery Table\n"
            "title: Sample\n"
            "description: A sample table.\n"
            "tags: [a, b]\n"
            "timestamp: 2026-05-27T00:00:00+00:00\n"
            "---\n\n# Sample\n\nBody text.\n"
        )
        doc = OKFDocument.parse(src)
        assert doc.frontmatter["type"] == "BigQuery Table"
        assert doc.frontmatter["tags"] == ["a", "b"]
        assert doc.body.startswith("# Sample")
        # Round-trip.
        rt = OKFDocument.parse(doc.serialize())
        assert rt.frontmatter == doc.frontmatter
        assert rt.body.strip() == doc.body.strip()

    def test_parse_no_frontmatter_treats_all_as_body(self):
        doc = OKFDocument.parse("# Hello\n\nNo frontmatter here.\n")
        assert doc.frontmatter == {}
        assert "Hello" in doc.body

    def test_unterminated_frontmatter_raises(self):
        with pytest.raises(OKFDocumentError):
            OKFDocument.parse("---\ntype: X\nstill in frontmatter\n")

    def test_validate_rejects_missing_required_keys(self):
        doc = OKFDocument(frontmatter={"type": "X", "title": "Y"})
        with pytest.raises(OKFDocumentError) as exc:
            doc.validate()
        assert "description" in str(exc.value)
        assert "timestamp" in str(exc.value)

    def test_validate_accepts_full_frontmatter(self):
        fm = {k: "v" for k in REQUIRED_FRONTMATTER_KEYS}
        OKFDocument(frontmatter=fm).validate()  # no raise

    def test_required_keys_match_spec(self):
        # SPEC §4.1 + §9 conformance: type is required; tests require all four.
        assert REQUIRED_FRONTMATTER_KEYS == ("type", "title", "description", "timestamp")


# --- §3 Bundle structure: paths --------------------------------------------

class TestPaths:
    def test_concept_id_roundtrip(self, tmp_path):
        cid = ("tables", "events_")
        p = concept_id_to_path(tmp_path, cid)
        assert p.name == "events_.md"
        assert path_to_concept_id(tmp_path, p) == cid

    def test_parse_concept_id(self):
        assert parse_concept_id("tables/users") == ("tables", "users")
        assert parse_concept_id("/a/b/") == ("a", "b")

    def test_invalid_segment_rejected(self):
        with pytest.raises(ValueError):
            parse_concept_id("tables/ev ents")


# --- §6 Index files: regenerate_indexes ------------------------------------

def _write_doc(path, type_, title, description):
    path.write_text(
        "---\n"
        f"type: {type_}\n"
        f"title: {title}\n"
        f"description: {description}\n"
        "timestamp: 2026-05-27T00:00:00+00:00\n"
        "---\n\n"
        f"# {title}\n\n{description}\n",
        encoding="utf-8",
    )


class TestIndexRegeneration:
    def test_regenerate_groups_by_type_and_links_relative(self, tmp_path):
        (tmp_path / "datasets").mkdir()
        (tmp_path / "tables").mkdir()
        _write_doc(tmp_path / "datasets" / "ga4.md", "BigQuery Dataset", "GA4", "GA4 obfuscated ecommerce sample.")
        _write_doc(tmp_path / "tables" / "events_.md", "BigQuery Table", "events_*", "Daily-sharded GA4 event tables.")
        _write_doc(tmp_path / "tables" / "users.md", "BigQuery Table", "users", "Users table.")

        async def _stub(rel, children, *, model):
            return f"stub: {len(children)} items"

        written = asyncio.run(regenerate_indexes(tmp_path, model="stub", synthesize=_stub))
        assert written

        tables_index = (tmp_path / "tables" / "index.md").read_text()
        assert tables_index.startswith("# BigQuery Table")
        assert "[events_*](events_.md)" in tables_index
        assert "[users](users.md)" in tables_index
        assert "Daily-sharded GA4 event tables." in tables_index

        root_index = (tmp_path / "index.md").read_text()
        assert "# Subdirectories" in root_index
        assert "(datasets/index.md) - GA4 obfuscated ecommerce sample."
        assert "(tables/index.md) - stub: 2 items"

    def test_regenerate_skips_empty_directories(self, tmp_path):
        (tmp_path / "empty_dir").mkdir()
        written = asyncio.run(regenerate_indexes(tmp_path, model="stub", synthesize=lambda **k: ""))
        assert written == []
        assert not (tmp_path / "empty_dir" / "index.md").exists()

    def test_single_child_reuses_description_no_synth(self, tmp_path):
        (tmp_path / "datasets").mkdir()
        _write_doc(tmp_path / "datasets" / "only.md", "BigQuery Dataset", "Only", "The only dataset in this bundle.")
        calls = []
        def _counting(rel, children, *, model):
            calls.append(rel)
            return "x"
        asyncio.run(regenerate_indexes(tmp_path, model="stub", synthesize=_counting))
        assert calls == []
        assert "(datasets/index.md) - The only dataset in this bundle." in (tmp_path / "index.md").read_text()


# --- §5 Cross-linking + viewer graph ---------------------------------------

class TestViewer:
    def _make_bundle(self, root):
        (root / "datasets").mkdir(parents=True)
        (root / "tables").mkdir()
        (root / "references").mkdir(parents=True)
        (root / "references" / "metrics").mkdir()
        _write_doc(root / "datasets" / "my_dataset.md", "BigQuery Dataset", "DS", "A dataset.")
        (root / "datasets" / "my_dataset.md").write_text(
            (root / "datasets" / "my_dataset.md").read_text()
            + "See [users](../tables/users.md)\n"
        )
        _write_doc(root / "tables" / "users.md", "BigQuery Table", "Users", "Users table.")
        (root / "tables" / "users.md").write_text(
            (root / "tables" / "users.md").read_text()
            + "[events](events.md) [DAU](../references/metrics/dau.md)\n"
        )
        _write_doc(root / "tables" / "events.md", "BigQuery Table", "Events", "Events table.")
        (root / "tables" / "events.md").write_text(
            (root / "tables" / "events.md").read_text()
            + "[users](users.md)\n"
        )
        _write_doc(root / "references" / "metrics" / "dau.md", "Reference", "DAU", "Daily active users.")
        (root / "index.md").write_text("# hand-written index\n", encoding="utf-8")

    def test_generate_visualization_writes_html(self, tmp_path):
        self._make_bundle(tmp_path)
        out = tmp_path / "viz.html"
        stats = generate_visualization(tmp_path, out, bundle_name="My Bundle")
        assert out.exists()
        assert stats["concepts"] == 4
        assert stats["bytes"] > 0
        html = out.read_text()
        assert "<title>OKF Bundle Viewer</title>" in html
        assert "cytoscape" in html.lower()
        assert "marked" in html.lower()
        assert '"My Bundle"' in html

    def test_index_md_is_not_a_concept(self, tmp_path):
        self._make_bundle(tmp_path)
        concepts = _walk_concepts(tmp_path)
        ids = {c.id for c in concepts}
        assert ids == {"datasets/my_dataset", "tables/users", "tables/events", "references/metrics/dau"}
        assert "index" not in ids

    def test_cross_links_become_edges(self, tmp_path):
        self._make_bundle(tmp_path)
        graph = _build_graph(_walk_concepts(tmp_path))
        edges = {(e["data"]["source"], e["data"]["target"]) for e in graph["edges"]}
        assert ("datasets/my_dataset", "tables/users") in edges
        assert ("tables/users", "tables/events") in edges
        assert ("tables/users", "references/metrics/dau") in edges
        assert ("tables/events", "tables/users") in edges

    def test_missing_link_targets_skipped(self, tmp_path):
        (tmp_path / "tables").mkdir(parents=True)
        _write_doc(tmp_path / "tables" / "lonely.md", "BigQuery Table", "Lonely", "Alone.")
        (tmp_path / "tables" / "lonely.md").write_text(
            (tmp_path / "tables" / "lonely.md").read_text() + "[missing](missing.md)\n"
        )
        graph = _build_graph(_walk_concepts(tmp_path))
        assert graph["edges"] == []
        assert len(graph["nodes"]) == 1

    def test_node_colors_match_palette(self, tmp_path):
        self._make_bundle(tmp_path)
        graph = _build_graph(_walk_concepts(tmp_path))
        colors = {n["data"]["id"]: n["data"]["color"] for n in graph["nodes"]}
        assert colors["datasets/my_dataset"] == "#8b5cf6"
        assert colors["tables/users"] == "#3b82f6"
        assert colors["references/metrics/dau"] == "#10b981"

    def test_raises_when_bundle_missing(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            generate_visualization(tmp_path / "nope", tmp_path / "v.html")


# --- §4.2 write_concept augmentation guard ---------------------------------

class TestWriteConceptGuard:
    @pytest.fixture(autouse=True)
    def _cleanup(self):
        yield
        clear_web_state()

    def _set_ctx(self, tmp_path):
        set_context(tmp_path)

    def _good_fm(self, **overrides):
        fm = {
            "type": "BigQuery Table",
            "title": "Users",
            "description": "A table of users.",
            "resource": "https://example.com/users",
            "tags": ["users"],
        }
        fm.update(overrides)
        return fm

    def _bq_body(self, fields, citations=1):
        schema = "\n".join(f"- `{f}` STRING: desc" for f in fields)
        cites = "\n".join(f"[{i}] cite{i}" for i in range(1, citations + 1))
        return f"Prose.\n\n# Schema\n{schema}\n\n# Citations\n{cites}\n"

    def test_write_succeeds_when_no_existing_doc(self, tmp_path):
        self._set_ctx(tmp_path)
        res = write_concept("tables/users", self._good_fm(), self._bq_body(["id", "name"]))
        assert "error" not in res
        assert (tmp_path / "tables" / "users.md").exists()

    def test_bq_pass_can_shrink_schema_when_no_web_state(self, tmp_path):
        self._set_ctx(tmp_path)
        write_concept("tables/users", self._good_fm(), self._bq_body(["id", "name", "legacy_col"]))
        res = write_concept("tables/users", self._good_fm(), self._bq_body(["id", "name"]))
        assert "error" not in res

    def test_web_pass_rejects_schema_shrinkage(self, tmp_path):
        self._set_ctx(tmp_path)
        write_concept("tables/users", self._good_fm(), self._bq_body(["id", "name", "email", "created_at"]))
        set_web_state({"docs.example.com"}, 10, seeds=["https://docs.example.com/x"])
        res = write_concept("tables/users", self._good_fm(), self._bq_body(["id", "name"]))
        assert "error" in res
        assert "missing 2" in res["error"]
        assert "`email`" in res["error"]
        assert "`created_at`" in res["error"]

    def test_web_pass_rejects_citation_shrinkage(self, tmp_path):
        self._set_ctx(tmp_path)
        write_concept("tables/users", self._good_fm(), self._bq_body(["id", "name"], citations=2))
        set_web_state({"docs.example.com"}, 10, seeds=["https://docs.example.com/x"])
        res = write_concept("tables/users", self._good_fm(), self._bq_body(["id", "name"], citations=1))
        assert "error" in res
        assert "had 2 entries" in res["error"]

    def test_web_pass_allows_augmentation_with_new_section(self, tmp_path):
        self._set_ctx(tmp_path)
        write_concept("tables/users", self._good_fm(), self._bq_body(["id", "name"], citations=1) + "\n# Metrics\n- dau\n")
        set_web_state({"docs.example.com"}, 10, seeds=["https://docs.example.com/x"])
        # keep id+name, keep the citation, add a 2nd citation + new section.
        res = write_concept("tables/users", self._good_fm(), self._bq_body(["id", "name"], citations=2))
        assert "error" not in res

    def test_web_pass_skips_guard_for_non_bigquery_table_types(self, tmp_path):
        self._set_ctx(tmp_path)
        write_concept("refs/x", {**self._good_fm(), "type": "Reference"}, "Prose `field_a` `field_b`\n\n# Citations\n[1] c\n")
        set_web_state({"docs.example.com"}, 10, seeds=["https://docs.example.com/x"])
        res = write_concept("refs/x", {**self._good_fm(), "type": "Reference"}, "Prose only\n\n# Citations\n[1] c\n")
        assert "error" not in res


# --- Oracle bundle helpers --------------------------------------------------

class TestOracleBundle:
    def test_list_concepts_empty(self, tmp_path):
        from okf.bundle import list_concepts
        assert list_concepts(tmp_path) == []

    def test_file_and_read_deliberation(self, tmp_path):
        from okf.bundle import file_deliberation, list_concepts, load_concept_body
        set_context(tmp_path)
        res = file_deliberation(
            deliberation_id="d1",
            query="Is X better than Y?",
            verdict="X wins",
            agent_summaries={"scout": "found sources", "sage": "X is robust"},
            bundle_root=tmp_path,
        )
        assert "error" not in res
        concepts = list_concepts(tmp_path)
        assert len(concepts) == 1
        assert concepts[0]["type"] == "Deliberation"
        body = load_concept_body(concepts[0]["id"], tmp_path)
        assert "X wins" in body
        assert "found sources" in body

    def test_retrieve_relevant_keyword_fallback(self, tmp_path):
        from okf.bundle import retrieve_relevant, file_reference
        set_context(tmp_path)
        file_reference(title="Neural Networks Primer", description="neural networks deep learning",
                       body="NN content", bundle_root=tmp_path)
        file_reference(title="Cooking Pasta", description="how to boil pasta",
                       body="pasta content", bundle_root=tmp_path)
        results = retrieve_relevant("tell me about neural networks", tmp_path, top_k=2)
        assert results
        assert "neural" in results[0]["title"].lower() or "neural" in results[0]["description"].lower()
