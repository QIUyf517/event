import type { EventGraph, EventMaterialType, EventSearchResponse, EventSubjectsResponse, ResearchRun } from "./types";

const API_ROOT = "/api/v1";
const KEY_STORAGE_NAME = "event-api-key";

export function getStoredApiKey(): string {
  return sessionStorage.getItem(KEY_STORAGE_NAME) ?? "";
}

export function storeApiKey(value: string): void {
  if (value) sessionStorage.setItem(KEY_STORAGE_NAME, value);
  else sessionStorage.removeItem(KEY_STORAGE_NAME);
}

async function request<T>(path: string, init?: RequestInit, modelCall = false): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body) headers.set("Content-Type", "application/json");
  if (modelCall) {
    const key = getStoredApiKey();
    if (!key) throw new Error("请先填写本机模型调用密钥");
    headers.set("X-API-Key", key);
  }
  const response = await fetch(path, { ...init, headers });
  if (!response.ok) {
    let detail = `请求失败（${response.status}）`;
    try {
      const payload = await response.json();
      if (payload.detail) detail = String(payload.detail);
    } catch {
      // 响应不是 JSON 时，保留基于状态码的备用错误提示。
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export const api = {
  demoCatalog: () => request<{ enabled: boolean; runs: { run_id: string; label: string }[] }>(`${API_ROOT}/event-research/demo`),
  startResearch: (payload: { question: string; subject_code: string; window_start: string; as_of_date: string }) =>
    request<{ run: ResearchRun }>(`${API_ROOT}/event-research/runs`, { method: "POST", body: JSON.stringify(payload) }, true),
  researchRun: (id: string) => request<{ run: ResearchRun }>(`${API_ROOT}/event-research/runs/${encodeURIComponent(id)}`),
  eventSubjects: () => request<EventSubjectsResponse>(`${API_ROOT}/event-research/subjects`),
  eventTimeline: (subjectCode: string) => {
    const params = new URLSearchParams({ subject_code: subjectCode });
    return request<{ graph: EventGraph }>(`${API_ROOT}/event-research/timeline?${params}`);
  },
  buildEventTimeline: (payload: {
    question: string; subject_code: string; window_start: string; as_of_date: string;
  }) => request<{ graph: EventGraph }>(`${API_ROOT}/event-research/timeline/build`,
    { method: "POST", body: JSON.stringify(payload) }, true),
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
  }
};
