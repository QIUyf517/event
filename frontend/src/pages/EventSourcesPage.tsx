import { ArrowUpRight, FileSearch, Search } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { api } from "../api";
import type {
  EventMaterialType, EventSearchResponse, EventSubjectsResponse
} from "../types";

const materialLabels: Record<EventMaterialType, string> = {
  announcement: "公告", news: "新闻", financial: "财报／财务资料"
};
const materialTypes: EventMaterialType[] = ["announcement", "news", "financial"];

function safeSourceUrl(value: string): string | null {
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch {
    return null;
  }
}

export function EventSourcesPage() {
  const [catalog, setCatalog] = useState<EventSubjectsResponse | null>(null);
  const [catalogError, setCatalogError] = useState("");
  const [subjectId, setSubjectId] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [sourceTypes, setSourceTypes] = useState<EventMaterialType[]>(materialTypes);
  const [keyword, setKeyword] = useState("");
  const [result, setResult] = useState<EventSearchResponse | null>(null);
  const [searchError, setSearchError] = useState("");
  const [searching, setSearching] = useState(false);
  const searchSequence = useRef(0);

  useEffect(() => {
    api.eventSubjects().then((response) => {
      setCatalog(response);
      setSubjectId(response.subjects[0]?.subject_id ?? "");
      setStartDate(response.available_start ?? "");
      setEndDate(response.available_end ?? "");
    }).catch((reason: Error) => setCatalogError(reason.message));
  }, []);

  function clearResult() {
    searchSequence.current += 1;
    setResult(null);
    setSearchError("");
  }

  function changeType(type: EventMaterialType) {
    setSourceTypes((current) => current.includes(type)
      ? current.filter((item) => item !== type)
      : materialTypes.filter((item) => current.includes(item) || item === type));
    clearResult();
  }

  async function searchMaterials(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const sequence = ++searchSequence.current;
    setSearchError("");
    setResult(null);
    if (!subjectId || !startDate || !endDate || !keyword.trim()) {
      setSearchError("请选择研究对象、日期范围并输入关键词。");
      return;
    }
    if (startDate > endDate) {
      setSearchError("开始日期不能晚于结束日期。");
      return;
    }
    if (!sourceTypes.length) {
      setSearchError("请至少选择一种资料类型。");
      return;
    }
    setSearching(true);
    try {
      const response = await api.searchEventMaterials({
        subjectId, startDate, endDate, query: keyword.trim(), sourceTypes
      });
      if (sequence === searchSequence.current) setResult(response);
    } catch (reason) {
      if (sequence === searchSequence.current) {
        setSearchError(reason instanceof Error ? reason.message : "检索失败，请稍后重试。");
      }
    } finally {
      setSearching(false);
    }
  }

  return (
    <main className="page-shell event-page">
      <header className="page-header event-header">
        <div><h1>从原文查起。</h1></div>
        <p>在已保存的企业公告、新闻和财务资料中检索。结果展示原文片段，方便回到来源核对。</p>
      </header>

      <section className="event-workspace" aria-label="企业资料检索">
        <form className="event-search-form" onSubmit={searchMaterials}>
          <div className="event-form-heading"><FileSearch aria-hidden="true" /><div><h2>设置检索范围</h2><p>只查询本地已保存资料，不会联网采集。</p></div></div>
          {catalogError ? <div className="error-banner" role="alert">{catalogError}</div> : null}
          <label>研究对象
            <select value={subjectId} onChange={(event) => { setSubjectId(event.target.value); clearResult(); }} disabled={!catalog?.subjects.length}>
              {catalog?.subjects.map((subject) => (
                <option key={subject.subject_id} value={subject.subject_id}>{subject.name}（{subject.code}）</option>
              ))}
            </select>
          </label>
          <div className="event-date-fields">
            <label>开始日期<input type="date" value={startDate} onChange={(event) => { setStartDate(event.target.value); clearResult(); }} /></label>
            <label>结束日期<input type="date" value={endDate} onChange={(event) => { setEndDate(event.target.value); clearResult(); }} /></label>
          </div>
          <fieldset className="event-type-fieldset">
            <legend>资料类型</legend>
            <div className="event-type-options">{materialTypes.map((type) => (
              <label key={type} className="event-type-option">
                <input type="checkbox" checked={sourceTypes.includes(type)} onChange={() => changeType(type)} />
                <span>{materialLabels[type]}</span>
              </label>
            ))}</div>
          </fieldset>
          <label>检索关键词
            <input type="search" value={keyword} maxLength={120} placeholder="例如：营业收入、分红、产能" onChange={(event) => { setKeyword(event.target.value); clearResult(); }} />
          </label>
          {searchError ? <div className="error-banner" role="alert">{searchError}</div> : null}
          <button className="primary-button event-search-button" type="submit" disabled={searching || !catalog?.subjects.length}>
            <Search size={18} aria-hidden="true" />{searching ? "正在检索" : "检索已保存资料"}
          </button>
        </form>

        <div className="event-results" aria-live="polite">
          <div className="event-results-heading">
            <div><h2>原文片段</h2><p>标题、发布者与发布日期均来自已保存的资料记录。</p></div>
            {result ? <strong>{result.count} 条片段</strong> : null}
          </div>
          {catalog ? <div className="event-coverage">
            <strong>{catalog.source_kind === "accepted_phase3_sample" ? "阶段三验收样本"
              : catalog.source_kind === "accepted_sample" ? "阶段二验收样本" : "本地事件资料库"}</strong>
            <span>资料发布日期范围：{catalog.available_start && catalog.available_end
              ? `${catalog.available_start} 至 ${catalog.available_end}` : "暂无已保存资料"}</span>
            {catalog.source_kind.startsWith("accepted_") ? <span>当前样本含财务资料和新闻，暂无独立公告样本。</span> : null}
          </div> : null}

          {result?.items.length ? <div className="event-hit-list">{result.items.map((hit) => {
            const sourceUrl = safeSourceUrl(hit.source_url);
            return <article className="event-hit" key={hit.chunk_id}>
              <div className="event-hit-meta"><span>{materialLabels[hit.document_type]}</span><time dateTime={hit.published_at}>{hit.published_at}</time></div>
              <h3>{hit.title}</h3>
              <p className="event-publisher">来源：{hit.publisher || "未标注发布者"}</p>
              <blockquote>{hit.excerpt}</blockquote>
              {sourceUrl ? <a href={sourceUrl} target="_blank" rel="noopener noreferrer">查看原文 <ArrowUpRight size={15} aria-hidden="true" /></a>
                : <span className="event-link-unavailable">原文链接不可用</span>}
            </article>;
          })}</div> : result ? <div className="event-empty">当前条件没有命中已保存资料。可调整关键词或日期范围后重新检索。</div>
            : <div className="event-empty">输入关键词并检索，相关原文片段会显示在这里。</div>}
        </div>
      </section>
    </main>
  );
}
