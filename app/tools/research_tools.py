from datetime import date
from app.config import ALLOWED_ETFS
from app.data.repositories.sqlite import SQLiteRepository
from app.analysis.signals import calculate_signal

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

