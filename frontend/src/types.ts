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

export interface TimelineSource {
  document_id: string;
  source_id: string;
  source_type: EventMaterialType;
  title: string;
  publisher: string;
  source_url: string;
  published_at: string;
  reprint_of: string | null;
}

export interface TimelineCitation {
  citation_id: string;
  document_id: string;
  chunk_id: string;
  quote: string;
  page: number | null;
  location: string | null;
}

export interface TimelineClaim {
  claim_id: string;
  event_id: string;
  citation_id: string;
  attribute: string;
  value_text: string | null;
  qualifier?: string | null;
  unit_context?: string | null;
  numeric_status: "not_applicable" | "format_checked" | "calculation_mismatch";
  calculation_note?: string | null;
  text: string;
}

export interface TimelineConflict {
  conflict_id: string;
  event_id: string;
  kind: "number" | "occurred_date" | "calculation";
  attribute: string;
  variants: string[];
  claim_ids: string[];
}

export interface TimelineRelation {
  relation_id: string;
  event_id: string;
  left_claim_id: string;
  right_claim_id: string;
  kind: "agreement" | "conflict" | "reprint" | "unresolved";
}

export interface TimelineEvent {
  event_id: string;
  title: string;
  occurred_at: string | null;
  date_status: "known" | "unknown" | "conflict";
  reported_at: string;
  importance: "high" | "medium" | "low";
  claim_ids: string[];
  document_ids: string[];
  conflict_ids: string[];
}

export interface EventGraph {
  run_id: string;
  question: string;
  subject_code: string;
  subject_name: string;
  window_start: string;
  as_of_date: string;
  status: "extracted" | "partial" | "insufficient_evidence";
  model: string;
  planning_tokens: number;
  extraction_tokens: number;
  estimated_cost_usd: number;
  sources: TimelineSource[];
  citations: TimelineCitation[];
  claims: TimelineClaim[];
  events: TimelineEvent[];
  conflicts: TimelineConflict[];
  relations: TimelineRelation[];
  warnings: string[];
}

export interface ResearchRun {
  demo?: boolean;
  run_id: string; question: string; subject_code: string; window_start: string; as_of_date: string;
  status: string; elapsed_seconds: number; total_tokens: number; estimated_cost_usd: number; stop_reason: string;
  stages: { key: string; label: string; status: string; elapsed_seconds: number }[];
  planning: null | {
    model: string; stop_reason: string;
    plan: { steps: { step_id: string; objective: string; tool: string; depends_on: string[]; selection_reason: string }[] };
    execution_trace: { step_id: string; status: string; started_at: string; finished_at: string;
      tool_calls: string[]; evidence_ids: string[]; stop_reason: string; error: string | null }[];
  };
  graph: EventGraph | null;
  debate: null | {
    publication_mode: string;
    hypotheses: { hypothesis_id: string; statement: string; interpretation_kind?: "event_candidate" | "industry_factor" | "market_factor" | "evidence_gap" | null;
      support_claim_ids: string[]; counter_claim_ids: string[];
      missing_evidence: string[]; alternative_explanations: string[]; assessment: string }[];
    findings: { hypothesis_id: string; kind: string; detail: string; claim_ids: string[]; hit_chunk_ids: string[] }[];
    search_checks: { query: string; hits: { chunk_id: string; title: string; excerpt: string; published_at: string }[] }[];
    conclusion: { text: string; answer_status: string; limitations: string[] };
    quality_issues: string[];
  };
}
