"""Tests for the Sage wiki path containment.

`/api/wiki/page?path=` takes a user-supplied path and returns file contents.
Before the guard it was a raw `WIKI_DIR / path`, so `?path=../../.env` read
the backend's own environment file — including the LLM configuration.
"""
import pytest
from fastapi import HTTPException

from api.wiki import _resolve_within, WIKI_DIR, BACKEND_DIR


class TestPageTraversal:
    @pytest.mark.parametrize("bad", [
        "../../.env",
        "../../../etc/passwd",
        "..",
        "../../",
        "sub/../../escape.md",
        "wiki/../../backend/.env",
        "....//....//etc/passwd",
    ])
    def test_traversal_rejected(self, bad):
        with pytest.raises(HTTPException) as exc:
            _resolve_within(WIKI_DIR, bad, suffixes={".md", ".txt"})
        assert exc.value.status_code == 400

    @pytest.mark.parametrize("bad", ["/etc/passwd", "/run/media/x/.env"])
    def test_absolute_paths_rejected(self, bad):
        with pytest.raises(HTTPException) as exc:
            _resolve_within(WIKI_DIR, bad, suffixes={".md", ".txt"})
        assert exc.value.status_code == 400

    def test_null_byte_rejected(self):
        with pytest.raises(HTTPException):
            _resolve_within(WIKI_DIR, "page.md\x00.txt", suffixes={".md", ".txt"})

    @pytest.mark.parametrize("good", ["index.md", "sub/page.md", "a/b/c.md"])
    def test_legitimate_paths_allowed(self, good):
        resolved = _resolve_within(WIKI_DIR, good, suffixes={".md", ".txt"})
        assert resolved.is_relative_to(WIKI_DIR)


class TestSuffixRestriction:
    @pytest.mark.parametrize("bad", ["evil.sh", "run.py", "passwd", "app.js"])
    def test_non_markdown_rejected(self, bad):
        with pytest.raises(HTTPException) as exc:
            _resolve_within(WIKI_DIR, bad, suffixes={".md", ".txt"})
        assert exc.value.status_code == 400
        assert "Unsupported" in exc.value.detail

    def test_suffix_check_is_case_insensitive(self):
        assert _resolve_within(WIKI_DIR, "notes.MD", suffixes={".md", ".txt"})


class TestIngestSource:
    def test_ingest_cannot_escape_the_repo(self):
        with pytest.raises(HTTPException):
            _resolve_within(BACKEND_DIR.parent, "../../etc/passwd", suffixes={".md", ".txt"})

    def test_ingest_allows_repo_relative_paths(self):
        resolved = _resolve_within(
            BACKEND_DIR.parent, "sage_wiki/wiki/index.md", suffixes={".md", ".txt"}
        )
        assert resolved.name == "index.md"


class TestResolvedRoots:
    def test_wiki_dir_is_absolute(self):
        assert WIKI_DIR.is_absolute()

    def test_wiki_dir_is_resolved(self):
        """Symlinked checkouts must not leave `..` segments in the root, or
        the containment comparison would never match."""
        assert ".." not in WIKI_DIR.parts
