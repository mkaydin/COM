"""
Embedding generation + vector store insertion.
Chunks text, generates embeddings, stores in both pgvector and ChromaDB.
"""
import uuid
from typing import List
from logging_config import get_logger
from config import get_settings

logger = get_logger(__name__)
settings = get_settings()

_st_model = None


def _get_local_model():
    """Legacy compat — returns None (Ollama embeddings are HTTP-based)."""
    return None


def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 200) -> List[str]:
    """Split text into overlapping chunks."""
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        chunks.append(chunk)
        start = end - overlap
    return chunks


def generate_embedding(text: str) -> List[float]:
    """Generate embedding vector using Ollama qwen3-embedding:0.6b."""
    from embedding import embed_text
    try:
        return embed_text(text)
    except Exception as e:
        logger.error("embedding_failed", error=str(e))
        return []


def store_in_chromadb(doc_id: str, title: str, chunks: List[str], collection_name: str = "documents_shared"):
    """Store document chunks in ChromaDB for vector search."""
    try:
        from db.chroma import get_collection
        collection = get_collection(collection_name)
        if not collection:
            logger.warning("chromadb_unavailable")
            return False

        ids = [f"{doc_id}_chunk_{i}" for i in range(len(chunks))]
        metadatas = [{"doc_id": doc_id, "title": title, "chunk_index": i} for i in range(len(chunks))]

        collection.add(
            ids=ids,
            documents=chunks,
            metadatas=metadatas,
        )
        logger.info("chromadb_stored", doc_id=doc_id, chunks=len(chunks))
        return True
    except Exception as e:
        logger.error("chromadb_store_failed", error=str(e))
        return False


async def ingest_document(doc_id: str, title: str, content: str, source_type: str = "user_upload"):
    """
    Full ingestion pipeline for the Historian:
    1. Chunk text
    2. Generate embeddings
    3. Store in ChromaDB (for vector search)
    4. Store in pgvector (for relational+vector queries)
    """
    logger.info("ingesting_document", doc_id=doc_id, title=title[:50])

    chunks = chunk_text(content)
    logger.info("document_chunked", chunks=len(chunks))

    store_in_chromadb(doc_id, title, chunks)

    try:
        from db.postgres import async_session
        from db.models import Document

        async with async_session() as db:
            for i, chunk in enumerate(chunks):
                embedding = generate_embedding(chunk)
                if not embedding:
                    continue

                chunk_doc = Document(
                    id=uuid.uuid4(),
                    title=f"{title} [chunk {i+1}]",
                    content=chunk,
                    source_type=f"{source_type}_chunk",
                    metadata_json={"parent_doc_id": doc_id, "chunk_index": i, "title": title},
                )
                db.add(chunk_doc)
            await db.commit()
            logger.info("pgvector_stored", doc_id=doc_id, chunks=len(chunks))
    except Exception as e:
        logger.error("pgvector_store_failed", error=str(e))

    logger.info("document_ingested", doc_id=doc_id, chunks=len(chunks))
    return {"status": "ingested", "chunks": len(chunks)}
