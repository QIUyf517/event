from datetime import date
from app.config import ALLOWED_ETFS
from app.data.repositories.sqlite import SQLiteRepository
from app.analysis.signals import calculate_signal

EVENT_SUBJECTS = {"600519", "601318", "688981"}

class ResearchTools:
    def __init__(self, repo: SQLiteRepository, data_mode: str = "demo"):
        self.repo = repo
        self.data_mode = data_mode

    def get_etf_profile(self, etf_code: str):
        self._check(etf_code); return self.repo.profile(etf_code)

    def get_etf_market_data(self, etf_code: str, start_date: date, end_date: date):
        self._check(etf_code); return [x for x in self.repo.market(etf_code, end_date) if x.trade_date >= start_date]

    def calculate_fund_signal(self, etf_code: str, as_of_date: date):
        self._check(etf_code)
        result = calculate_signal(etf_code, as_of_date, self.repo.market(etf_code, as_of_date), self.repo.shares(etf_code, as_of_date), self.data_mode)
        self.repo.save_signal(result)
        return result

    def search_public_documents(self, query: str, etf_code: str | None = None, date_range=None, document_types=None):
        return self.repo.search_chunks(query, etf_code, date_range=date_range, document_types=document_types)

    def get_document_chunk(self, chunk_id: str):
        return self.repo.get_document_chunk(chunk_id)

    def validate_number(self, value, source: str):
        return {"value": value, "source": source, "valid": value is not None and isinstance(value, (int, float))}

    @staticmethod
    def _check(etf_code):
        if etf_code not in ALLOWED_ETFS:
            raise ValueError(f"第一版仅支持: {', '.join(ALLOWED_ETFS)}")


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

