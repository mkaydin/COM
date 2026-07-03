"""Run GraphRAG extraction on all PDFs in raw_data folder."""
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
    from ingestion.parser import _parse_pdf
    from ingestion.graph_extractor import extract_entities_relationships, store_in_neo4j
    import uuid

    pdfs = sorted(glob.glob("/home/mka/Code/COM/raw_data/*.pdf"))
    log(f"GraphRAG extraction on {len(pdfs)} PDFs...\n")

    total_entities = 0
    total_rels = 0

    for i, path in enumerate(pdfs, 1):
        fname = os.path.basename(path)
        t0 = time.time()

        # Parse PDF
        try:
            pages = _parse_pdf(Path(path))
            text = "\n\n".join(p["text"] for p in pages) if pages else ""
        except Exception as e:
            log(f"[{i:2d}/{len(pdfs)}] PARSE FAIL: {str(e)[:60]}")
            continue

        if not text or len(text) < 200:
            log(f"[{i:2d}/{len(pdfs)}] SKIP (too short): {fname[:50]}")
            continue

        # Take first 4000 chars for entity extraction
        sample = text[:4000].replace("\x00", "")

        doc_id = str(uuid.uuid4())

        # Extract entities + relationships via GLM
        log(f"[{i:2d}/{len(pdfs)}] Extracting from: {fname[:55]}...")
        try:
            extraction = await extract_entities_relationships(sample)
            entities = extraction.get("entities", [])
            relationships = extraction.get("relationships", [])
        except Exception as e:
            log(f"[{i:2d}/{len(pdfs)}] EXTRACT FAIL: {str(e)[:60]}")
            continue

        if not entities:
            log(f"[{i:2d}/{len(pdfs)}] NO ENTITIES")
            continue

        # Store in Neo4j
        stored = store_in_neo4j(doc_id, fname, extraction)

        elapsed = time.time() - t0
        total_entities += len(entities)
        total_rels += len(relationships)
        status = "OK" if stored else "NEO4J_FAIL"
        log(f"[{i:2d}/{len(pdfs)}] {status} ({len(entities):>2}e/{len(relationships):>2}r, {elapsed:.1f}s)")

    log(f"\nDone! {total_entities} total entities, {total_rels} total relationships in Neo4j")


if __name__ == "__main__":
    asyncio.run(main())
