from datetime import date
from app.data.fetchers import demo_documents, demo_market, demo_profile, demo_shares
from app.data.repositories.sqlite import SQLiteRepository
from app.rag.chunker import chunk_document

def seed_demo(repo: SQLiteRepository, as_of: date | None = None):
    as_of = as_of or date.today()
    repo.upsert_profile(demo_profile())
    repo.upsert_market(demo_market(as_of))
    repo.upsert_shares(demo_shares(as_of))
    for doc in demo_documents():
        repo.upsert_document(doc, chunk_document(doc))

