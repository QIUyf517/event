from app.data.repositories.sqlite import SQLiteRepository
from app.models.research import ResearchSubject

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

