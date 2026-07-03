# Third Knowledge System: Continuous Ingestion RAG Layer

## Purpose

This system is the **raw/growing knowledge layer**, sitting alongside your two existing systems:

| System | Role | Content shape | Growth |
|---|---|---|---|
| LLM-Wiki | Curated core knowledge | Hand-written markdown concepts | Slow, manual |
| OKF | Portable/exchangeable knowledge | Markdown + frontmatter concepts | Slow-medium, semi-manual |
| **RAG Layer (this doc)** | Raw ingest at scale | Parsed PDFs, scraped web pages, summaries | Continuous, automatic |

The RAG layer does **not** replace the other two — it feeds them. Distilled/summarized content produced by this pipeline should periodically graduate into an OKF concept file. Raw source material stays here permanently as the provenance record.

---

## 1. Architecture Overview

```
                         ┌─────────────────────────┐
                         │      INGESTION           │
                         │  - PDF upload            │
                         │  - Web scraper            │
                         │  - Web search (API)       │
                         └───────────┬───────────────┘
                                     │
                         ┌───────────▼───────────────┐
                         │       PARSING              │
                         │  PDF → structured text      │
                         │  HTML → cleaned text         │
                         └───────────┬───────────────┘
                                     │
                         ┌───────────▼───────────────┐
                         │   DEDUPE / VERSIONING       │
                         │  hash check, supersede logic │
                         └───────────┬───────────────┘
                                     │
                         ┌───────────▼───────────────┐
                         │   CHUNKING (structure-aware) │
                         └───────────┬───────────────┘
                                     │
                    ┌────────────────┼────────────────┐
                    │                                  │
          ┌─────────▼─────────┐            ┌───────────▼───────────┐
          │   EMBEDDING         │            │   SUMMARIZATION         │
          │   → Vector Store     │            │   → candidate for OKF   │
          └─────────┬─────────┘            └───────────┬───────────┘
                    │                                  │
          ┌─────────▼─────────┐            ┌───────────▼───────────┐
          │  Raw chunk store    │            │  Promotion queue         │
          │  (with metadata)     │            │  (human/agent review)    │
          └─────────┬─────────┘            └───────────┬───────────┘
                    │                                  │
                    │                        ┌──────────▼──────────┐
                    │                        │  OKF / LLM-Wiki       │
                    │                        │  (existing systems)   │
                    │                        └──────────────────────┘
                    │
          ┌─────────▼──────────────────────────────┐
          │            RETRIEVAL LAYER               │
          │  1. Query OKF/Wiki first (curated)         │
          │  2. Fall back to vector search (raw)        │
          │  3. Merge + rank + cite                       │
          └────────────────────────────────────────┘
```

---

## 2. Components in Detail

### 2.1 Ingestion sources

- **PDF upload** — user-supplied science surveys
- **Web scraper** — full-page raw text
- **Web search** — both the summarized answer *and* the raw source text should be stored separately (never store only the summary — you lose the ability to re-verify or re-summarize later)

### 2.2 Parsing

- PDFs: use a layout-aware parser (not naive `pdftotext`) so section headers, figures, tables, and references are identifiable — this preserves structure for chunking in step 2.4.
- Web pages: strip nav/ads/boilerplate, keep main content + byline + publish date.

### 2.3 Dedupe / versioning

- Compute a content hash per document on ingest. Exact duplicate → skip, just update `last_seen`.
- Near-duplicate (e.g., updated preprint vs published version) → keep both, link with `supersedes` / `superseded_by` metadata field. Never silently overwrite — science content revisions matter.

### 2.4 Chunking (structure-aware)

- Chunk along section boundaries (Abstract, Methods, Results, Discussion, References) where the parser can detect them.
- Store a **parent-child relationship**: small chunk (~300-500 tokens) for retrieval precision, linked to its parent section (~1500-2000 tokens) for context expansion at answer time.
- Never chunk across a section boundary if avoidable — mixing "Methods" and "Results" text in one chunk degrades retrieval precision for science content specifically.

### 2.5 Embedding + vector store

- Embed each chunk; store chunk text + embedding + metadata together.
- Vector store choice is secondary to metadata schema (below) — any of pgvector, Qdrant, Weaviate, or a managed option works; pick based on your existing infra rather than raw benchmark numbers.

### 2.6 Summarization → promotion queue

- Any chunk or document that gets summarized (by your pipeline or by an agent during use) is a **candidate OKF concept file**, not just a cached answer.
- Push candidates to a review queue (human approval, or agent-approval with a confidence threshold) before writing into the OKF/Wiki layer, to avoid polluting your curated knowledge with low-quality auto-summaries.

---

## 3. Metadata Schema (attach to every chunk/document)

```yaml
source_type: pdf | web_scrape | websearch_summary | websearch_raw
source_url: <url or DOI>
title: <string>
authors: <list, if available>
publish_date: <date, if available>
ingested_date: <date>
content_hash: <sha256>
supersedes: <id, optional>
superseded_by: <id, optional>
trust_tier: peer_reviewed | preprint | web_primary | web_secondary | ai_summary
tags: [list]
parent_chunk_id: <id, optional — for parent-child chunking>
promoted_to_okf: true | false
okf_concept_ref: <path, if promoted>
```

`trust_tier` is the field that matters most for science content — it lets retrieval weight or filter by source reliability, and lets your agent cite appropriately ("per a peer-reviewed survey" vs "per an AI-generated summary of a web page").

---

## 4. Retrieval Flow (instructions for the agent/pipeline)

1. **Query OKF/Wiki first.** If a curated concept file directly answers the query, use it — it's cheaper, higher-precision, and already vetted.
2. **Fall back to vector search** over the raw chunk store if no curated concept covers the query, or if the query needs primary-source detail the summary doesn't have.
3. **Merge results**, preferring higher `trust_tier` when chunks conflict.
4. **Always cite** `source_url`/DOI and `trust_tier` in the final answer — never present an `ai_summary`-tier chunk with the same confidence as a `peer_reviewed` one.
5. **If a gap is found repeatedly** (same query hits vector fallback 3+ times with no curated match), flag it for promotion review — this is your signal to grow the OKF layer.

---

## 5. Operational Instructions

**On new PDF upload:**
1. Parse → hash → dedupe check → chunk → embed → store with metadata.
2. Generate a section-level summary automatically; add to promotion queue.

**On web scrape / web search:**
1. Store raw text as `websearch_raw` / `web_scrape`.
2. Store any generated summary separately as `websearch_summary`, linked via `parent_chunk_id` or a `derived_from` field to its raw source — never store a summary without a traceable link back to raw text.

**Weekly/periodic maintenance:**
1. Review promotion queue → approve or discard candidate OKF concepts.
2. Check `superseded_by` chains for orphaned old versions still being served in retrieval — suppress superseded content from default retrieval unless explicitly requested ("what did the earlier version say").

**Never:**
- Overwrite raw source text once ingested (append/version instead).
- Promote an `ai_summary`-tier item to OKF without a review step.
- Mix trust tiers in a single chunk.

---

## 6. Why this is a third system, not a merge

- LLM-Wiki and OKF assume a human or reviewed agent authored the file — that doesn't scale to continuous PDF/web ingestion.
- This layer assumes the opposite: everything in is unreviewed until promoted.
- The **promotion queue** is the seam between them — it's the only place unreviewed content becomes curated content, keeping your OKF/Wiki layer trustworthy even as ingestion volume grows unbounded.
