from app.models.research import Document, DocumentChunk

def chunk_document(doc: Document, max_chars: int = 500) -> list[DocumentChunk]:
    return [DocumentChunk(chunk_id=f"{doc.document_id}-chunk-{i // max_chars + 1}", document_id=doc.document_id, content=doc.content[i:i + max_chars], title=doc.title, source_url=doc.source_url, published_at=doc.published_at) for i in range(0, len(doc.content), max_chars)]

