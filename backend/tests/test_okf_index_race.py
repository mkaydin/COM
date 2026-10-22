"""The OKF concept index is built once, not once per concurrent caller.

`retrieve_relevant` runs inside `asyncio.to_thread`, and at the opening of a
debate all five characters retrieve at the same moment against a cold cache.
The index used to be a module-level dict with a check-then-act guard and no
lock, so each of those threads saw it stale and built its own — five embedding
passes over the whole bundle instead of one.

It is now a frozen snapshot rebound under a lock, which also removes a second
failure mode: because the old code updated that dict key-by-key, a reader could
observe `ids` from one build beside `embeddings` from another. That pairs the
wrong rows with the wrong vectors, so retrieval returns confidently wrong
results instead of failing.
"""

import threading
import time

import pytest

from okf import bundle as bundle_mod


def _write_bundle(root, count=5):
    for i in range(count):
        (root / f"concept_{i}.md").write_text(
            "---\n"
            f"title: Concept {i}\n"
            f"description: a note about subject {i}\n"
            "type: Reference\n"
            "---\n"
            f"body of concept {i}\n",
            encoding="utf-8",
        )


@pytest.fixture()
def fresh_bundle(tmp_path):
    # A subdirectory, so a second bundle can be created as a *sibling* rather
    # than inside the tree being walked.
    root = tmp_path / "bundle"
    root.mkdir()
    _write_bundle(root)
    bundle_mod.invalidate_concept_cache()
    yield root
    bundle_mod.invalidate_concept_cache()


@pytest.fixture()
def counting_embed(monkeypatch):
    """Counts embeds and holds the window open so threads overlap."""
    batches: list[int] = []
    lock = threading.Lock()

    def fake_embed(texts):
        with lock:
            batches.append(len(texts))
        # Without the pause the first caller finishes before the others start
        # and the race never happens — the test would pass on broken code.
        time.sleep(0.2)
        return [[float(len(t) % 7), 1.0, 0.5, 0.25] for t in texts]

    monkeypatch.setattr("llm.embeddings.embed_sync", fake_embed)
    return batches


class TestConcurrentBuild:
    def test_cold_cache_is_built_once(self, fresh_bundle, counting_embed):
        threads = [
            threading.Thread(
                target=bundle_mod.retrieve_relevant,
                args=("subject 1", fresh_bundle, 3),
            )
            for _ in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        # The build embeds every concept (5 texts); each query embeds one.
        builds = [n for n in counting_embed if n > 1]
        queries = [n for n in counting_embed if n == 1]
        assert len(builds) == 1, (
            f"{len(builds)} index builds for 8 concurrent callers — the lock "
            f"is not serialising the build (batches: {counting_embed})"
        )
        assert len(queries) == 8, "every caller must still embed its own query"

    def test_every_caller_gets_results(self, fresh_bundle, counting_embed):
        out: list = []
        errors: list = []
        lock = threading.Lock()

        def worker():
            try:
                r = bundle_mod.retrieve_relevant("subject 2", fresh_bundle, 3)
                with lock:
                    out.append(r)
            except Exception as e:  # noqa: BLE001 - surfaced below
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"threads raised: {errors[:2]}"
        assert len(out) == 8
        for r in out:
            assert len(r) == 3
            assert all("id" in c for c in r)

    def test_a_warm_cache_does_not_rebuild(self, fresh_bundle, counting_embed):
        bundle_mod.retrieve_relevant("subject 1", fresh_bundle, 3)
        builds_after_first = len([n for n in counting_embed if n > 1])

        for _ in range(4):
            bundle_mod.retrieve_relevant("subject 1", fresh_bundle, 3)

        assert len([n for n in counting_embed if n > 1]) == builds_after_first


class TestSnapshotIsAtomic:
    def test_a_published_snapshot_is_never_mutated(self, fresh_bundle, counting_embed):
        """Readers hold a reference; a rebuild must not rewrite it under them."""
        bundle_mod.retrieve_relevant("subject 0", fresh_bundle, 3)
        first = bundle_mod._concept_index
        assert first is not None
        ids_before = list(first.ids)
        shape_before = first.embeddings.shape

        # Add a concept and retrieve again, forcing a rebuild.
        _write_bundle(fresh_bundle, count=6)
        bundle_mod.retrieve_relevant("subject 5", fresh_bundle, 3)

        second = bundle_mod._concept_index
        assert second is not None and second is not first, "the index was not republished"

        assert first.ids == ids_before, "the old snapshot was mutated in place"
        assert first.embeddings.shape == shape_before
        assert len(second.ids) == len(first.ids) + 1


class TestInvalidation:
    def test_invalidate_forces_a_rebuild(self, fresh_bundle, counting_embed):
        bundle_mod.retrieve_relevant("subject 1", fresh_bundle, 3)
        assert len([n for n in counting_embed if n > 1]) == 1

        bundle_mod.invalidate_concept_cache(fresh_bundle)
        bundle_mod.retrieve_relevant("subject 1", fresh_bundle, 3)
        assert len([n for n in counting_embed if n > 1]) == 2

    def test_invalidate_for_another_bundle_keeps_this_one(self, fresh_bundle, tmp_path,
                                                          counting_embed):
        bundle_mod.retrieve_relevant("subject 1", fresh_bundle, 3)
        other = tmp_path / "other"
        other.mkdir()
        _write_bundle(other, count=2)

        bundle_mod.invalidate_concept_cache(other)
        bundle_mod.retrieve_relevant("subject 1", fresh_bundle, 3)

        assert len([n for n in counting_embed if n > 1]) == 1, (
            "invalidating a different bundle dropped the live index"
        )