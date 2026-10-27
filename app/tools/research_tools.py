from datetime import date
from app.data.repositories.sqlite import SQLiteRepository

EVENT_SUBJECTS = {"600519", "601318", "688981"}

class EventResearchTools:
    """Read-only tools for traceable company event material."""

    def __init__(self, repo: SQLiteRepository):
        self.repo = repo

    def get_subject(self, subject_id: str):
        self._check_subject(subject_id)
        return self.repo.research_subject(subject_id)

    def search_announcements(self, query: str, subject_id: str, start_date: date, end_date: date,
                             limit: int = 10):
        return self._search(query, subject_id, start_date, end_date, ["announcement", "financial"], limit)

    def search_news(self, query: str, subject_id: str, start_date: date, end_date: date,
                    limit: int = 10):
        return self._search(query, subject_id, start_date, end_date, ["news"], limit)

    def search_financial_materials(self, query: str, subject_id: str, start_date: date, end_date: date,
                                   limit: int = 10):
        return self._search(query, subject_id, start_date, end_date, ["financial"], limit)

    def search_all_materials(self, query: str, subject_id: str, start_date: date, end_date: date,
                             limit: int = 10):
        return self._search(query, subject_id, start_date, end_date, None, limit)

    def get_company_market_data(self, query: str, subject_id: str, start_date: date,
                                end_date: date, limit: int = 10):
        """Return saved historical bars only; query is a planner label, never a symbol override."""
        self._check_subject(subject_id)
        return self.repo.company_market(subject_id, start_date, end_date, limit)

    def get_document(self, document_id: str):
        return self.repo.event_document(document_id)

    def _search(self, query: str, subject_id: str, start_date: date, end_date: date,
                source_types: list[str] | None, limit: int):
        self._check_subject(subject_id)
        return self.repo.search_event_chunks(query, subject_id, end_date,
            window_start=start_date, source_types=source_types, limit=limit)

    @staticmethod
    def _check_subject(subject_id: str):
        if subject_id not in EVENT_SUBJECTS:
            raise ValueError(f"事件研究第一版仅支持: {', '.join(sorted(EVENT_SUBJECTS))}")

