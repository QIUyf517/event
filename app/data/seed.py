from datetime import date
from app.config import ALLOWED_ETFS
from app.data.fetchers import catalog_profile, demo_documents, demo_market, demo_shares
from app.data.repositories.sqlite import SQLiteRepository
from app.models.research import ResearchSubject
from app.rag.chunker import chunk_document

EVENT_RESEARCH_SUBJECTS = (
    ResearchSubject(subject_id="600519", subject_type="company", code="600519", name="贵州茅台",
                    aliases=["贵州茅台酒股份有限公司", "茅台"], market="SSE"),
    ResearchSubject(subject_id="601318", subject_type="company", code="601318", name="中国平安",
                    aliases=["中国平安保险（集团）股份有限公司", "平安"], market="SSE"),
    ResearchSubject(subject_id="688981", subject_type="company", code="688981", name="中芯国际",
                    aliases=["中芯国际集成电路制造有限公司", "SMIC"], market="SSE"),
)

def seed_event_research_subjects(repo: SQLiteRepository) -> None:
    for subject in EVENT_RESEARCH_SUBJECTS:
        repo.upsert_research_subject(subject)

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

