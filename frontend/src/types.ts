export type DataMode = "demo" | "live";
export type ResearchMethod = "rules" | "ai";

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
