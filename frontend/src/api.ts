import type {
  AlertRow, ArchiveItem, ComparisonRow, DataMode, Etf, EventMaterialType,
  EventSearchResponse, EventSubjectsResponse, MarketBar,
  MonitorRun, ResearchMethod, ResearchResponse, SignalHistoryItem, SyncRun
} from "./types";

const API_ROOT = "/api/v1";

export function getStoredApiKey(): string {
  return sessionStorage.getItem("etf-api-key") ?? "";
}

export function storeApiKey(value: string): void {
  if (value) sessionStorage.setItem("etf-api-key", value);
  else sessionStorage.removeItem("etf-api-key");
}

async function request<T>(path: string, init?: RequestInit, write = false): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body) headers.set("Content-Type", "application/json");
  if (write) {
    const key = getStoredApiKey();
    if (!key) throw new Error("请先填写本机API写入密钥");
    headers.set("X-API-Key", key);
  }
  const response = await fetch(path, { ...init, headers });
  if (!response.ok) {
    let detail = `请求失败（${response.status}）`;
    try {
      const payload = await response.json();
      if (payload.detail) detail = String(payload.detail);
    } catch {
      // Keep the status based fallback when the response is not JSON.
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export const api = {
  etfs: () => request<Etf[]>(`${API_ROOT}/etfs`),
  eventSubjects: () => request<EventSubjectsResponse>(`${API_ROOT}/event-research/subjects`),
  searchEventMaterials: (filters: {
    subjectId: string; startDate: string; endDate: string; query: string;
    sourceTypes: EventMaterialType[];
  }) => {
    const params = new URLSearchParams({
      subject_id: filters.subjectId, start_date: filters.startDate,
      end_date: filters.endDate, query: filters.query
    });
    filters.sourceTypes.forEach((item) => params.append("source_types", item));
    return request<EventSearchResponse>(`${API_ROOT}/event-research/search?${params}`);
  },
  settings: () => request<{ default_etf: string; default_mode: DataMode; default_question: string }>(`${API_ROOT}/settings`),
  research: (payload: { question: string; etf_code: string; as_of_date: string; mode: DataMode; method: ResearchMethod }) =>
    request<ResearchResponse>(`${API_ROOT}/research`, { method: "POST", body: JSON.stringify(payload) }, true),
  market: (etf: string, mode: DataMode, end: string) =>
    request<{ items: MarketBar[] }>(`${API_ROOT}/market/${etf}?mode=${mode}&end=${end}&limit=90`),
  reports: (mode: DataMode) =>
    request<{ items: ArchiveItem[] }>(`${API_ROOT}/reports?mode=${mode}&limit=12`),
  comparison: (mode: DataMode, asOf: string) =>
    request<{ rows: ComparisonRow[]; warnings: string[] }>(`${API_ROOT}/comparison?mode=${mode}&as_of_date=${asOf}`),
  signals: (etf: string, mode: DataMode) =>
    request<{ items: SignalHistoryItem[] }>(`${API_ROOT}/signals/${etf}?mode=${mode}&limit=30`),
  alerts: (mode: DataMode) =>
    request<{ items: AlertRow[] }>(`${API_ROOT}/monitor/alerts?mode=${mode}&limit=20`),
  monitorRuns: (mode: DataMode) =>
    request<{ items: MonitorRun[] }>(`${API_ROOT}/monitor/runs?mode=${mode}&limit=20`),
  syncRuns: (mode: DataMode) =>
    request<{ items: SyncRun[] }>(`${API_ROOT}/monitor/sync?mode=${mode}&limit=30`),
  runMonitor: (mode: DataMode, asOf: string) =>
    request<MonitorRun>(`${API_ROOT}/monitor/run`, {
      method: "POST", body: JSON.stringify({ as_of_date: asOf, mode, sync_live: mode === "live" })
    }, true),
  syncEtf: (etf: string, start: string, end: string) =>
    request<SyncRun>(`${API_ROOT}/sync/${etf}`, {
      method: "POST", body: JSON.stringify({ start_date: start, end_date: end })
    }, true),
  pdfUrl: (taskId: string, mode: DataMode) => `${API_ROOT}/reports/${taskId}/pdf?mode=${mode}`
};
