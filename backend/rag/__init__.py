"""RAG layer — raw/growing knowledge: ingest, chunk, embed, retrieve.

This is the third knowledge system (per knowledge-system-3-rag-architecture.md):
  - Continuous ingestion of PDFs, web scrapes, web search results.
  - Structure-aware chunking with parent-child relationships.
  - Dedupe via content hash + versioning via supersedes.
  - Trust-tier-weighted retrieval that feeds (and is fed by) OKF + Wiki.
"""
