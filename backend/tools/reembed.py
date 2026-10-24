#!/usr/bin/env python3
"""Re-embed stored chunks under the currently configured embedding model.

Changing `EMBEDDING_MODEL` (or switching backend) does not invalidate stored
vectors by itself — it just makes them incomparable. Cosine distance between
vectors from two different models is meaningless rather than merely imprecise,
so retrieval degrades silently. This command is how you resolve that.

    python backend/tools/reembed.py --status      # what is in the store
    python backend/tools/reembed.py --dry-run     # what would be rebuilt
    python backend/tools/reembed.py               # rebuild the stale ones
    python backend/tools/reembed.py --all         # rebuild everything

"Stale" means the chunk has no embedding, or was embedded by a different
`backend:model` than the one configured now. `--all` is for the case where the
model keeps its name but its weights changed (an Ollama tag that was re-pulled).
"""

import argparse
import asyncio
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, update  # noqa: E402

from db.models import RagChunk  # noqa: E402
from db.postgres import async_session  # noqa: E402
from llm import embeddings  # noqa: E402


async def _load() -> list[tuple]:
    async with async_session() as db:
        return (
            await db.execute(
                select(RagChunk.id, RagChunk.chunk_text, RagChunk.embedding_model)
            )
        ).all()


def _batched(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--status", action="store_true", help="report and exit")
    ap.add_argument("--dry-run", action="store_true", help="report what would change")
    ap.add_argument("--all", action="store_true", help="re-embed even matching chunks")
    ap.add_argument("--batch-size", type=int, default=32)
    args = ap.parse_args()

    active = embeddings.identity()
    rows = await _load()

    counts = Counter((r.embedding_model or "(unknown)") for r in rows)
    stale = [r for r in rows if args.all or r.embedding_model != active]

    print(f"configured embedder : {active}")
    print(f"chunks in store     : {len(rows)}")
    for model, n in counts.most_common():
        mark = "  <- active" if model == active else ""
        print(f"  {n:>6}  {model}{mark}")
    print(f"to re-embed         : {len(stale)}")

    if args.status or not rows:
        return 0
    if args.dry_run:
        print("\ndry run — nothing written")
        return 0
    if not stale:
        print("\nnothing to do; every chunk already matches the active embedder")
        return 0

    from llm.embeddings import embed_sync

    started = time.time()
    done = 0
    for batch in _batched(stale, max(1, args.batch_size)):
        texts = [r.chunk_text or "" for r in batch]
        vectors = await asyncio.to_thread(embed_sync, texts)

        if len(vectors) != len(batch):
            print(
                f"embedder returned {len(vectors)} vectors for {len(batch)} chunks; "
                "aborting without writing",
                file=sys.stderr,
            )
            return 1
        widths = {len(v) for v in vectors}
        if widths != {embeddings.VECTOR_DIM}:
            print(
                f"embedder produced width(s) {sorted(widths)}, but the store holds "
                f"{embeddings.VECTOR_DIM}-dimensional vectors. Change the store "
                "first, or pick a compatible model.",
                file=sys.stderr,
            )
            return 1

        async with async_session() as db:
            for row, vector in zip(batch, vectors):
                await db.execute(
                    update(RagChunk)
                    .where(RagChunk.id == row.id)
                    .values(embedding=vector, embedding_model=active)
                )
            await db.commit()

        done += len(batch)
        print(f"  re-embedded {done}/{len(stale)}", end="\r", flush=True)

    elapsed = time.time() - started
    rate = done / elapsed if elapsed else 0
    print(f"\nre-embedded {done} chunk(s) in {elapsed:.1f}s ({rate:.0f}/s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))