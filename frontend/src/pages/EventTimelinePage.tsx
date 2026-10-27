import { ArrowUpRight, CalendarDays, FileText, Layers3, LoaderCircle, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { api } from "../api";
import { ApiKeyField } from "../components/ApiKeyField";
import type { EventGraph, EventSubjectsResponse, TimelineEvent } from "../types";

function safeUrl(value: string): string | null {
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch {
    return null;
  }
}

function dateLabel(value: string): string {
  return value.slice(0, 10);
}

function eventDate(event: TimelineEvent): string {
  if (event.date_status === "conflict") return "发生日期有冲突";
  return event.occurred_at ? dateLabel(event.occurred_at) : "发生日期未确认";
}

const statusLabels: Record<EventGraph["status"], string> = {
  extracted: "已提取可追溯事件",
  partial: "部分事件已提取，仍有待核对项",
  insufficient_evidence: "尚无足够原文生成事件"
};

export function EventTimelinePage() {
  const [catalog, setCatalog] = useState<EventSubjectsResponse | null>(null);
  const [subjectCode, setSubjectCode] = useState("");
  const [windowStart, setWindowStart] = useState("");
  const [asOfDate, setAsOfDate] = useState("");
  const [question, setQuestion] = useState("梳理最近30天的重要披露和新闻事件，逐条给出原文依据。");
  const [graph, setGraph] = useState<EventGraph | null>(null);
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [building, setBuilding] = useState(false);
  const [error, setError] = useState("");
  const [catalogError, setCatalogError] = useState("");
  const sequence = useRef(0);

  useEffect(() => {
    api.eventSubjects().then((result) => {
      setCatalog(result);
      setSubjectCode(result.subjects[0]?.subject_id ?? "");
      setWindowStart(result.available_start ?? "");
      setAsOfDate(result.available_end ?? "");
    }).catch((reason: Error) => setCatalogError(reason.message));
  }, []);

  useEffect(() => {
    if (!subjectCode) return;
    const current = ++sequence.current;
    setGraph(null);
    setSelectedEventId(null);
    setLoading(true);
    setError("");
    api.eventTimeline(subjectCode).then(({ graph: saved }) => {
      if (current === sequence.current) setGraph(saved);
    }).catch((reason: Error) => {
      if (current === sequence.current && !reason.message.includes("尚无")) setError(reason.message);
    }).finally(() => {
      if (current === sequence.current) setLoading(false);
    });
  }, [subjectCode]);

  const selected = graph?.events.find((event) => event.event_id === selectedEventId) ?? null;
  const sources = useMemo(() => new Map(graph?.sources.map((item) => [item.document_id, item]) ?? []), [graph]);
  const citations = useMemo(() => new Map(graph?.citations.map((item) => [item.citation_id, item]) ?? []), [graph]);
  const claims = useMemo(() => new Map(graph?.claims.map((item) => [item.claim_id, item]) ?? []), [graph]);
  const conflicts = useMemo(() => new Map(graph?.conflicts.map((item) => [item.conflict_id, item]) ?? []), [graph]);
  const dated = graph?.events.filter((event) => event.date_status === "known") ?? [];
  const uncertain = graph?.events.filter((event) => event.date_status !== "known") ?? [];

  async function build(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    if (!subjectCode || !windowStart || !asOfDate || !question.trim()) {
      setError("请选择公司和日期，并填写研究问题。");
      return;
    }
    const days = (Date.parse(asOfDate) - Date.parse(windowStart)) / 86_400_000;
    if (!Number.isFinite(days) || days < 0 || days > 30) {
      setError("研究日期范围不能超过30天，且开始日期不能晚于截止日期。");
      return;
    }
    const current = ++sequence.current;
    setBuilding(true);
    setSelectedEventId(null);
    try {
      const result = await api.buildEventTimeline({
        question: question.trim(), subject_code: subjectCode,
        window_start: windowStart, as_of_date: asOfDate
      });
      if (current === sequence.current) setGraph(result.graph);
    } catch (reason) {
      if (current === sequence.current) setError(reason instanceof Error ? reason.message : "生成时间线失败。");
    } finally {
      if (current === sequence.current) setBuilding(false);
    }
  }

  function eventButton(item: TimelineEvent) {
    return <li className={`timeline-entry ${item.conflict_ids.length ? "has-conflict" : ""}`} key={item.event_id}>
      <button type="button" className={selectedEventId === item.event_id ? "selected" : ""}
        onClick={() => setSelectedEventId(item.event_id)} aria-pressed={selectedEventId === item.event_id}>
        <span className="timeline-date">{eventDate(item)}</span>
        <span className="timeline-title">{item.title}</span>
        <span className="timeline-meta">{item.document_ids.length} 份来源 · 最早报道 {dateLabel(item.reported_at)}
          {item.conflict_ids.length ? ` · ${item.conflict_ids.length} 处冲突` : ""}</span>
      </button>
    </li>;
  }

  return <main className="page-shell timeline-page">
    <header className="page-header event-header">
      <div><h1>按发生时间梳理，逐条回到原文。</h1></div>
      <p>同一事件的报道合并查看。日期或数字说法不一致时并列保留，无法确认的发生日期单独列出。</p>
    </header>

    <section className="timeline-workspace" aria-label="企业事件时间线">
      <form className="event-search-form timeline-form" onSubmit={build}>
        <div className="event-form-heading"><CalendarDays aria-hidden="true" />
          <div><h2>研究范围</h2><p>读取已保存资料，生成操作会调用模型并保存结果。</p></div>
        </div>
        {catalogError ? <div className="error-banner" role="alert">{catalogError}</div> : null}
        <label>研究公司
          <select value={subjectCode} onChange={(event) => setSubjectCode(event.target.value)}
            disabled={building || !catalog?.subjects.length}>
            {catalog?.subjects.map((item) => <option key={item.subject_id} value={item.subject_id}>
              {item.name}（{item.code}）</option>)}
          </select>
        </label>
        <div className="event-date-fields">
          <label>开始日期<input type="date" value={windowStart} onChange={(event) => setWindowStart(event.target.value)} /></label>
          <label>截止日期<input type="date" value={asOfDate} onChange={(event) => setAsOfDate(event.target.value)} /></label>
        </div>
        <label>研究问题<textarea rows={4} maxLength={500} value={question}
          onChange={(event) => setQuestion(event.target.value)} /></label>
        <ApiKeyField />
        {error ? <div className="error-banner" role="alert">{error}</div> : null}
        <button className="primary-button event-search-button" type="submit"
          disabled={building || loading || !catalog?.subjects.length}>
          {building ? <LoaderCircle className="spin" size={18} aria-hidden="true" />
            : <Layers3 size={18} aria-hidden="true" />}
          {building ? "正在核对原文并生成时间线" : "生成事件时间线（调用模型）"}
        </button>
      </form>

      <div className="timeline-results" aria-live="polite">
        <div className="event-results-heading"><div><h2>事件与证据</h2>
          <p>仅展示能定位到已保存原文片段的事实。</p></div></div>
        {loading ? <div className="event-empty">正在读取已保存的时间线。</div> : null}
        {!loading && !graph ? <div className="event-empty">这家公司还没有保存的时间线。设置日期和问题后，可以生成一份。</div> : null}
        {graph ? <>
          <div className="timeline-summary">
            <strong>{statusLabels[graph.status]}</strong>
            <span>{graph.subject_name} · {graph.window_start} 至 {graph.as_of_date}</span>
            <span>{graph.events.length} 个事件 · {graph.sources.length} 份来源 · {graph.conflicts.length} 处冲突</span>
          </div>
          {graph.warnings.length ? <div className="timeline-warnings"><strong>仍需核对</strong>
            <ul>{graph.warnings.map((warning, index) => <li key={`${index}-${warning}`}>{warning}</li>)}</ul>
          </div> : null}
          {dated.length ? <section className="timeline-group"><h3>已确认发生日期</h3>
            <ol className="timeline-list">{dated.map(eventButton)}</ol></section> : null}
          {uncertain.length ? <section className="timeline-group"><h3>日期待确认或存在冲突</h3>
            <ol className="timeline-list">{uncertain.map(eventButton)}</ol></section> : null}
          {!graph.events.length ? <div className="event-empty">本次未取得足够的可定位事实。可换一个问题，或先补充资料。</div> : null}
          <p className="timeline-cost">模型用量：{graph.planning_tokens + graph.extraction_tokens} 个 Token；
            费用估算约 {graph.estimated_cost_usd.toFixed(4)} 美元。实际费用以服务商账单为准。</p>
        </> : null}
      </div>
    </section>

    {selected && graph ? <aside className="timeline-drawer" aria-label="事件证据详情">
      <div className="timeline-drawer-heading"><div><span>原文与主张</span><h2>{selected.title}</h2></div>
        <button type="button" className="text-button" onClick={() => setSelectedEventId(null)}
          aria-label="关闭证据详情"><X size={20} /></button></div>
      <p className="timeline-drawer-date">发生日期：{eventDate(selected)}。最早资料发布日期：{dateLabel(selected.reported_at)}。</p>
      {selected.conflict_ids.map((id) => {
        const conflict = conflicts.get(id);
        return conflict ? <div className="timeline-conflict" key={id}>
          <strong>{conflict.attribute}存在不同说法</strong>
          <p>{conflict.variants.join("；")}</p>
        </div> : null;
      })}
      {graph.relations.filter((item) => item.event_id === selected.event_id).length ?
        <div className="timeline-relations"><strong>跨来源核对</strong>
          {graph.relations.filter((item) => item.event_id === selected.event_id).map((relation) => {
            const left = claims.get(relation.left_claim_id);
            const right = claims.get(relation.right_claim_id);
            const leftSource = left ? sources.get(citations.get(left.citation_id)?.document_id ?? "") : null;
            const rightSource = right ? sources.get(citations.get(right.citation_id)?.document_id ?? "") : null;
            const label = { agreement: "相同数值或表述", conflict: "数值冲突",
              reprint: "转载关联", unresolved: "说法待核对" }[relation.kind];
            return <p key={relation.relation_id}>{leftSource?.title ?? "来源一"} 与 {rightSource?.title ?? "来源二"}：{label}</p>;
          })}
        </div> : null}
      <div className="timeline-evidence-list">{selected.claim_ids.map((id) => {
        const claim = claims.get(id);
        const citation = claim ? citations.get(claim.citation_id) : null;
        const source = citation ? sources.get(citation.document_id) : null;
        if (!claim || !citation || !source) return null;
        const link = safeUrl(source.source_url);
        return <article key={id}>
          <div className="timeline-claim"><FileText size={16} aria-hidden="true" />
            <strong>系统提取：{claim.attribute}{claim.value_text ? `，${claim.qualifier ? `${claim.qualifier} ` : ""}${claim.value_text}` : ""}</strong></div>
          <blockquote>{citation.quote}</blockquote>
          <p className="timeline-source">{source.title} · {source.publisher} · {dateLabel(source.published_at)}
            {citation.page ? ` · 第${citation.page}页` : ""}
            {source.reprint_of ? " · 有转载关联" : ""}</p>
          {claim.numeric_status === "format_checked" ? <p className="timeline-check">数字已在原文中定位并通过格式检查；未做独立来源复核。</p> : null}
          {claim.calculation_note ? <p className="timeline-calculation">{claim.calculation_note}</p> : null}
          {claim.unit_context ? <p className="timeline-check">{claim.unit_context}</p> : null}
          {link ? <a href={link} target="_blank" rel="noopener noreferrer">查看来源 <ArrowUpRight size={15} aria-hidden="true" /></a>
            : <span className="event-link-unavailable">来源链接不可用</span>}
        </article>;
      })}</div>
    </aside> : null}
  </main>;
}
