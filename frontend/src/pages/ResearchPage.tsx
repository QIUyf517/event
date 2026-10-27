import { useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import { ApiKeyField } from "../components/ApiKeyField";
import type { EventSubjectsResponse, ResearchRun } from "../types";

const labels: Record<string, string> = { pending: "等待", queued: "等待执行", running: "执行中", completed: "完成",
  partial: "部分回答 · 解释未验证", failed: "失败", interrupted: "已中断", success: "成功", skipped: "跳过" };
function safeUrl(value: string) {
  try { const url = new URL(value); return ["https:", "http:"].includes(url.protocol) ? url.href : undefined; }
  catch { return undefined; }
}

export function ResearchPage() {
  const [params, setParams] = useSearchParams();
  const id = params.get("run");
  const [catalog, setCatalog] = useState<EventSubjectsResponse | null>(null);
  const [subject, setSubject] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [question, setQuestion] = useState("梳理重要事件及可能关联，列出支持、反对与缺失证据。");
  const [run, setRun] = useState<ResearchRun | null>(null);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [demos, setDemos] = useState<{ run_id: string; label: string }[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const busy = submitting || !!run && ["queued", "running"].includes(run.status);

  useEffect(() => {
    let active = true;
    api.eventSubjects().then(result => {
      if (!active) return;
      setCatalog(result); setSubject(result.subjects[0]?.subject_id ?? "");
      const last = result.available_end?.slice(0, 10) ?? "";
      setEnd(last);
      setStart(last ? new Date(Date.parse(last) - 30 * 86400000).toISOString().slice(0, 10) : "");
    }).catch(reason => { if (active) setError(reason.message); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    let active = true;
    api.demoCatalog().then(result => { if (active && result.enabled) setDemos(result.runs); }).catch(() => {});
    return () => { active = false; };
  }, []);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    setRun(null); setSelected(null);
    if (!id) return;
    async function poll() {
      try {
        const result = await api.researchRun(id!);
        if (!active) return;
        setRun(result.run); setError("");
        if (["queued", "running"].includes(result.run.status)) timer = setTimeout(poll, 1500);
      } catch (reason) {
        if (active) {
          setError(`${reason instanceof Error ? reason.message : "读取失败"}。可刷新页面重新读取，本次不会重新调用模型。`);
          timer = setTimeout(poll, 5000);
        }
      }
    }
    void poll();
    return () => { active = false; clearTimeout(timer); };
  }, [id]);

  async function submit(event: FormEvent) {
    event.preventDefault(); setError("");
    const days = (Date.parse(end) - Date.parse(start)) / 86400000;
    if (!subject || !question.trim() || !Number.isFinite(days) || days < 0 || days > 30) {
      setError("请选择公司、填写问题，并设置不超过30天的日期窗口。"); return;
    }
    setSubmitting(true);
    try {
      const result = await api.startResearch({ question: question.trim(), subject_code: subject, window_start: start, as_of_date: end });
      setParams({ run: result.run.run_id });
    } catch (reason) { setError(reason instanceof Error ? reason.message : "发起研究失败"); }
    finally { setSubmitting(false); }
  }

  const graph = run?.graph;
  function evidence(ids: string[]) {
    if (!ids.length) return <p className="research-muted">模型未选择此类证据；这不表示不存在。</p>;
    return ids.map(claimId => {
      const claim = graph?.claims.find(item => item.claim_id === claimId);
      const cite = graph?.citations.find(item => item.citation_id === claim?.citation_id);
      return <button type="button" className="evidence-chip" key={claimId} onClick={() => setSelected(claimId)}>
        {claim?.attribute ?? claimId}：{cite?.quote.slice(0, 70) ?? "读取原文"}</button>;
    });
  }
  function evidenceCard(claimId: string, role: "support" | "counter") {
    const item = graph?.claims.find(claim => claim.claim_id === claimId);
    const quote = graph?.citations.find(citation => citation.citation_id === item?.citation_id);
    const document = graph?.sources.find(source => source.document_id === quote?.document_id);
    const relatedEvent = graph?.events.find(event => event.event_id === item?.event_id);
    const hasConflict = graph?.conflicts.some(conflict => conflict.claim_ids.includes(claimId));
    if (!item || !quote || !document) return <div className="research-evidence-row missing" key={claimId}>
      <strong>引用无法读取</strong><p>这条模型选择的证据缺少可展示的原文，请核对保存记录。</p></div>;
    return <article className={`research-evidence-row ${role}`} key={claimId}>
      <div className="research-evidence-row-main">
        <span className="research-evidence-kicker">原文摘录 · {document.source_type === "announcement" ? "公告" : document.source_type === "financial" ? "财务资料" : "新闻"}</span>
        <blockquote>{quote.quote}</blockquote>
        <p className="research-evidence-source">{document.title} · {document.publisher}</p>
        <p className="research-evidence-dates">资料发布日期：{document.published_at}；图谱发生日：{relatedEvent?.date_status === "known" ? relatedEvent.occurred_at : relatedEvent?.date_status === "conflict" ? "有冲突" : "未确认"}。</p>
        <button className="text-button" type="button" onClick={() => setSelected(claimId)}>查看定位与模型提取</button>
      </div>
      <div className="research-evidence-row-check">
        <strong>{role === "support" ? "支持关系未验证" : "反对关系未验证"}</strong>
        <p>{role === "support" ? "原文可定位，不等于它已证明这个解释。" : "原文可定位，不等于它已推翻这个解释。"}</p>
        {(document.reprint_of || hasConflict || item.numeric_status === "calculation_mismatch") && <ul>
          {document.reprint_of && <li>转载或同源，不能重复计证。</li>}
          {hasConflict && <li>图谱记录了相关冲突，需核对原文口径。</li>}
          {item.numeric_status === "calculation_mismatch" && <li>数字重算存在差异。</li>}
        </ul>}
      </div>
    </article>;
  }
  function evidenceCards(ids: string[], role: "support" | "counter") {
    const unique = [...new Set(ids)];
    if (!unique.length) return <p className="research-evidence-empty">模型未选取这类原文；不能据此认定相关材料不存在。</p>;
    return <>
      {unique.slice(0, 2).map(id => evidenceCard(id, role))}
      {unique.length > 2 && <details className="research-more-evidence"><summary>展开其余 {unique.length - 2} 条原文</summary>
        {unique.slice(2).map(id => evidenceCard(id, role))}</details>}
    </>;
  }
  const claim = graph?.claims.find(item => item.claim_id === selected);
  const citation = graph?.citations.find(item => item.citation_id === claim?.citation_id);
  const source = graph?.sources.find(item => item.document_id === citation?.document_id);
  const event = graph?.events.find(item => item.event_id === claim?.event_id);

  return <main className="page-shell research-page">
    <header className="research-heading"><h1>让每一步判断，都有据可查。</h1>
      <p>从研究问题到原文，再到候选解释。保留分歧，也保留无法确认的部分。</p></header>
    {demos.length > 0 && <section className="research-report"><h2>固定历史样本演示</h2><p>仅回放保存结果，不联网、不调用模型。解释关系未经独立验证。</p>
      {demos.map(item => <button type="button" className="text-button" key={item.run_id} onClick={() => setParams({ run: item.run_id })}>{item.label}</button>)}</section>}
    {!demos.length && <form className="research-question" onSubmit={submit}>
      <fieldset disabled={busy}>
        <label>研究问题<textarea rows={2} required maxLength={500} value={question} onChange={e => setQuestion(e.target.value)} /></label>
        <div className="research-scope">
          <label>研究公司<select value={subject} onChange={e => setSubject(e.target.value)} required>
            {catalog?.subjects.map(item => <option key={item.subject_id} value={item.subject_id}>{item.name}（{item.code}）</option>)}
          </select></label>
          <label>开始日期<input type="date" required value={start} onChange={e => setStart(e.target.value)} /></label>
          <label>截止日期<input type="date" required value={end} onChange={e => setEnd(e.target.value)} /></label>
        </div>
        <ApiKeyField />
        <button className="primary-button" disabled={!catalog?.subjects.length || demos.length > 0} type="submit">{busy ? "研究正在执行" : "开始研究（调用模型）"}</button>
        <small>只读取已保存资料；会产生模型费用。刷新页面可继续查看当前运行。</small>
      </fieldset>
    </form>}
    {error && <div className="error-banner" role="alert">{error}</div>}
    {!id && <p className="event-empty">{demos.length ? "选择上方公司，查看固定历史研究记录。" : "设置研究范围后开始。也可从企业时间线查看已有原文。"}</p>}
    {id && !run && <p role="status">正在读取研究记录……</p>}
    {run && <div aria-live="polite">
      <div className="research-run-heading"><h2>{labels[run.status] ?? run.status}</h2><span>{run.window_start} 至 {run.as_of_date}</span></div>
      <p>{run.question}</p>
      <ol className="research-process" aria-label="研究流程">{run.stages.map((stage, index) => <li key={stage.key} data-status={stage.status}>
        <span>{index + 1}</span><strong>{stage.label}</strong><small>{labels[stage.status] ?? stage.status} · {stage.elapsed_seconds.toFixed(1)} 秒</small>
      </li>)}</ol>
      <dl className="research-metrics"><div><dt>模型用量</dt><dd>{run.total_tokens} Token</dd></div>
        <div><dt>费用估算</dt><dd>${run.estimated_cost_usd.toFixed(5)}</dd></div>
        <div><dt>已记录耗时</dt><dd>{run.demo ? "历史记录未保存耗时" : `${run.elapsed_seconds.toFixed(1)} 秒`}</dd></div>
        <div><dt>停止原因</dt><dd>{run.stop_reason}</dd></div></dl>
      <p className="research-muted">用量按完成阶段汇总；失败阶段的用量与费用可能未计入。费用按配置单价估算，以账单为准。</p>
      {run.planning && <section className="research-section"><h2>模型计划与实际工具调用</h2><p>模型：{run.planning.model}。无依赖的取证节点并行执行。</p>
        <div className="research-table"><table><thead><tr><th>目标与选择理由</th><th>工具 / 依赖</th><th>执行结果</th></tr></thead><tbody>
          {run.planning.plan.steps.map(step => {
            const trace = run.planning!.execution_trace.find(item => item.step_id === step.step_id);
            return <tr key={step.step_id}><td><strong>{step.objective}</strong><p>{step.selection_reason}</p></td>
              <td>{step.tool}<p>依赖：{step.depends_on.join("、") || "无"}</p></td>
              <td>{trace ? labels[trace.status] ?? trace.status : "未执行"}<p>{trace?.evidence_ids.length ?? 0} 条证据</p>
                {trace && <small>{trace.started_at} 至 {trace.finished_at}<br />{trace.stop_reason}{trace.error}</small>}</td></tr>;
          })}</tbody></table></div></section>}
      {graph && <section className="research-section"><h2>原文、提取结果与冲突</h2>
        <p>{graph.events.length} 个事件，{graph.sources.length} 份资料，{graph.conflicts.length} 处冲突。提取结果属于模型处理，原文仍需核对。</p>
        {graph.warnings.map((warning, index) => <p className="timeline-warnings" key={index}>{warning}</p>)}
        {graph.events.map(item => <details key={item.event_id} className="research-event"><summary>{item.occurred_at ?? "发生日期未确认"}　{item.title}</summary>
          <p>最早资料发布日期：{item.reported_at}；{item.date_status === "conflict" ? "日期有冲突" : "发布日期不替代发生日"}。</p>
          {evidence(item.claim_ids)}
          {graph.conflicts.filter(conflict => conflict.event_id === item.event_id).map(conflict => <p className="timeline-conflict" key={conflict.conflict_id}>
            程序记录的冲突：{conflict.attribute}：{conflict.variants.join("；")}</p>)}
        </details>)}</section>}
      {run.debate && <>
        <section className="research-section"><h2>候选解释与证据卡</h2>
          <p>{run.debate.publication_mode === "typed_evidence_pending_review"
            ? "模型选择解释类型与证据引用；日期和资料发布日期由图谱字段生成。支持关系与解释未经独立验证。"
            : "以下仅展示模型选择的证据组合。旧版自由文本解释未经独立验证。"}</p>
          <p className="research-muted">多条摘录可能来自同一资料，不能按条数累加为解释的支持力度。</p>
          {run.debate.hypotheses.map((hypothesis, index) => <article className="research-hypothesis" key={hypothesis.hypothesis_id}>
            <div className="research-hypothesis-head"><div><h3>候选 {index + 1}</h3><p>{hypothesis.statement}</p></div>
              <span className="research-review-state">解释关系未验证</span></div>
            <p className="research-hypothesis-boundary">原文能直接核对其字面内容；它是否支持这个解释，仍需结合问题和其他材料判断。</p>
            <div className="research-evidence-groups"><section aria-label={`候选 ${index + 1} 模型选作支持`}>
              <h4>模型选作支持</h4>{evidenceCards(hypothesis.support_claim_ids, "support")}</section>
              <section aria-label={`候选 ${index + 1} 模型选作反对`}>
                <h4>模型选作反对</h4>{evidenceCards(hypothesis.counter_claim_ids, "counter")}</section></div>
            <div className="research-evidence-questions"><div><h4>仍需核对</h4>
              {hypothesis.missing_evidence.map(text => <p key={text}>{text}</p>)}
              {run.debate!.findings.filter(finding => finding.hypothesis_id === hypothesis.hypothesis_id)
                .map((finding, findingIndex) => <p key={`${finding.kind}-${findingIndex}`}>{finding.detail}</p>)}</div>
              <div><h4>其他可能解释</h4>{hypothesis.alternative_explanations.length
                ? hypothesis.alternative_explanations.map(text => <p key={text}>{text}</p>)
                : <p>尚未列出；不能据此排除其他解释。</p>}</div></div>
          </article>)}</section>
        <section className="research-section"><h2>反方检索记录 · 尚未独立验证</h2>
          {run.debate.search_checks.map((check, index) => <details key={index}><summary>反证检索：{check.query}（{check.hits.length} 条）</summary>
            {check.hits.map(hit => <blockquote key={hit.chunk_id}>{hit.excerpt}<cite>{hit.title} / {hit.published_at}</cite></blockquote>)}</details>)}
          {run.debate.quality_issues.length > 0 && <details><summary>程序质量门记录（{run.debate.quality_issues.length}）</summary>
            {run.debate.quality_issues.map((issue, index) => <p key={index}>{issue}</p>)}</details>}</section>
        <section className="research-report"><h2>程序生成的保守报告</h2><p>{run.debate.conclusion.text}</p>
          <ul>{run.debate.conclusion.limitations.map(text => <li key={text}>{text}</li>)}</ul>
          <button className="text-button" type="button" onClick={() => {
            const blob = new Blob([JSON.stringify(run, null, 2)], { type: "application/json" });
            const url = URL.createObjectURL(blob); const link = document.createElement("a");
            link.href = url; link.download = `${run.run_id}.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
          }}>下载本次研究记录</button>
        </section>
      </>}
    </div>}
    {claim && citation && source && <aside className="timeline-drawer" aria-label="研究证据详情">
      <button type="button" className="text-button" onClick={() => setSelected(null)}>关闭证据详情</button>
      <h2>原文说了什么</h2><blockquote>{citation.quote}</blockquote><p>{source.title} / {source.publisher} / {source.published_at}</p>
      <a href={safeUrl(source.source_url)} target="_blank" rel="noopener noreferrer">查看来源</a>
      <h3>模型提取</h3><p>{claim.attribute}：{claim.value_text ?? "见原文"}</p>
      <p>发生日：{event?.occurred_at ?? "未确认"}；报道日：{event?.reported_at}。</p>
      <h3>程序检查</h3><p>{claim.numeric_status === "format_checked" ? "原文定位及数字格式检查通过，未证明语义正确。" : claim.numeric_status === "calculation_mismatch" ? "算式重算存在差异，待核对。" : "无数字格式检查项。"}</p>
      <p>{claim.calculation_note}</p><p>{claim.unit_context}</p>
    </aside>}
  </main>;
}
