"""Run GraphRAG extraction on a batch of PDFs."""
import sys
sys.path.insert(0, "/home/mka/Code/COM/backend")

import asyncio
import glob
import os
import time
from pathlib import Path

def log(msg):
    print(msg, flush=True)

async def main():
    start = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 4

    from ingestion.parser import _parse_pdf
    from ingestion.graph_extractor import extract_entities_relationships, store_in_neo4j
    import uuid

    pdfs = sorted(glob.glob("/home/mka/Code/COM/raw_data/*.pdf"))
    batch = pdfs[start:start + count]
    log(f"Batch: PDFs {start+1}-{start+len(batch)} of {len(pdfs)}")

    total_e = 0
    total_r = 0

    for i, path in enumerate(batch):
        idx = start + i + 1
        fname = os.path.basename(path)
        t0 = time.time()

        try:
            pages = _parse_pdf(Path(path))
            text = "\n\n".join(p["text"] for p in pages) if pages else ""
        except Exception as e:
            log(f"[{idx:2d}] PARSE FAIL: {str(e)[:60]}")
            continue

        if not text or len(text) < 200:
            log(f"[{idx:2d}] SKIP (short)")
            continue

        sample = text[:4000].replace("\x00", "")
        doc_id = str(uuid.uuid4())

        log(f"[{idx:2d}] Extracting: {fname[:55]}...")
        try:
            extraction = await extract_entities_relationships(sample)
            entities = extraction.get("entities", [])
            relationships = extraction.get("relationships", [])
        except Exception as e:
            log(f"[{idx:2d}] EXTRACT FAIL: {str(e)[:60]}")
            continue

        if not entities:
            log(f"[{idx:2d}] NO ENTITIES")
            continue

        stored = store_in_neo4j(doc_id, fname, extraction)
        elapsed = time.time() - t0
        total_e += len(entities)
        total_r += len(relationships)
        log(f"[{idx:2d}] OK ({len(entities)}e/{len(relationships)}r, {elapsed:.0f}s)")

    log(f"\nBatch done: +{total_e} entities, +{total_r} relationships")


if __name__ == "__main__":
    asyncio.run(main())
