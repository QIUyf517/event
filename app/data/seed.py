from datetime import date
from app.config import ALLOWED_ETFS
from app.data.fetchers import catalog_profile, demo_documents, demo_market, demo_shares
from app.data.repositories.sqlite import SQLiteRepository
from app.rag.chunker import chunk_document

def seed_demo(repo: SQLiteRepository, as_of: date | None = None):
    as_of = as_of or date.today()
    for etf_code in ALLOWED_ETFS:
        repo.upsert_profile(catalog_profile(etf_code))
        repo.upsert_market(demo_market(as_of, etf_code=etf_code))
        repo.upsert_shares(demo_shares(as_of, etf_code=etf_code))
        for doc in demo_documents(as_of, etf_code):
            repo.upsert_document(doc, chunk_document(doc))

def seed_profile(repo: SQLiteRepository, etf_code: str):
    repo.upsert_profile(catalog_profile(etf_code))

