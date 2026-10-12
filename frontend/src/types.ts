export type DataMode = "demo" | "live";
export type ResearchMethod = "rules" | "ai";
export type EventMaterialType = "announcement" | "news" | "financial";

export interface EventSubject {
  subject_id: string;
  code: string;
  name: string;
}

export interface EventSubjectsResponse {
  subjects: EventSubject[];
  available_start: string | null;
  available_end: string | null;
  source_kind: "active" | "configured" | "accepted_sample" | "accepted_phase3_sample";
}

export interface EventMaterialHit {
  chunk_id: string;
  document_id: string;
  title: string;
  publisher: string;
  source_url: string;
  published_at: string;
  document_type: EventMaterialType;
  excerpt: string;
}

export interface EventSearchResponse {
  subject_id: string;
  start_date: string;
  end_date: string;
  query: string;
  source_types: EventMaterialType[];
  source_kind: EventSubjectsResponse["source_kind"];
  count: number;
  items: EventMaterialHit[];
}

export interface Etf {
  etf_code: string;
  name: string;
}

export interface AgentTrace {
  agent: string;
  status: string;
  summary: string;
  duration_ms: number;
  error?: string | null;
  evidence_refs?: string[];
}

export interface SourceExcerpt {
  citation: string;
  title: string;
  content: string;
  source_url: string;
  published_at: string;
  source_category?: string;
}

export interface TriggerDetail {
  indicator: string;
  value: number | null;
  contribution: number;
  reason: string;
}

export interface ResearchReport {
  question: string;
  answer_status: string;
  direct_answer: string;
  research_method?: string;
  data_mode: DataMode;
  as_of_date: string;
  data_cutoff_date?: string | null;
  etf: { etf_code: string; name: string };
  calculated_results: {
    signal: string;
    evidence_strength: string;
    indicators: Record<string, number | null>;
    source_dates: Record<string, string>;
    trigger_details?: TriggerDetail[];
  };
  source_excerpts: SourceExcerpt[];
  source_quotes?: string[];
  execution_trace: AgentTrace[];
  warnings: string[];
  disclaimer: string;
}

export interface ResearchResponse {
  task_id: string;
  report: ResearchReport;
}

export interface MarketBar {
  trade_date: string;
  close: number;
}

export interface ComparisonRow {
  etf_code: string;
  name: string;
  signal: string;
  indicators: Record<string, number | null>;
  source_dates: Record<string, string>;
  missing_fields: string[];
}

export interface ArchiveItem {
  task_id: string;
  created_at: string;
  etf_code?: string;
  etf_name?: string;
  as_of_date?: string;
  data_cutoff_date?: string;
  question: string;
  answer_status: string;
}

export interface MonitorRun {
  run_id: string;
  as_of_date: string;
  started_at: string;
  finished_at: string;
  status: string;
  etf_results: unknown[];
  warnings: string[];
}

export interface SyncRun {
  sync_id: string;
  etf_code: string;
  start_date: string;
  end_date: string;
  market_status: string;
  share_status: string;
  market_rows: number;
  share_rows: number;
  warnings: string[];
}

export interface AlertRow {
  alert_id: string;
  etf_code: string;
  as_of_date: string;
  severity: string;
  title: string;
  message: string;
}

export interface SignalHistoryItem {
  as_of_date: string;
  signal: string;
  source_dates: Record<string, string>;
  missing_fields: string[];
}
