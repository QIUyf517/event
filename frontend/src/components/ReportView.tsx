import { AlertTriangle, CheckCircle2, Download, ExternalLink } from "lucide-react";
import { api } from "../api";
import { formatIndicator, indicatorLabels, signalLabels, statusLabels } from "../lib";
import type { DataMode, ResearchReport } from "../types";

export function ReportView({ taskId, report, mode }: { taskId: string; report: ResearchReport; mode: DataMode }) {
  const indicators = report.calculated_results.indicators ?? {};
  const trace = report.execution_trace ?? [];
  const sources = report.source_excerpts ?? [];
  return (
    <div className="report-view">
      <section className="answer-block">
        <div className="eyebrow">针对问题的回答</div>
        <div className="answer-heading">
          <div>
            <span className={`status-dot status-${report.answer_status}`} />
            {statusLabels[report.answer_status] ?? report.answer_status}
          </div>
          <a className="secondary-button" href={api.pdfUrl(taskId, mode)} target="_blank" rel="noreferrer">
            <Download size={16} /> 下载PDF
          </a>
        </div>
        <p className="direct-answer">{report.direct_answer}</p>
      </section>

      <section className="signal-section">
        <div className="signal-name">{signalLabels[report.calculated_results.signal] ?? report.calculated_results.signal}</div>
        <p>该标签描述五项指标当前的同向程度，不是涨跌预测或买卖建议。</p>
        <div className="source-date-line">
          行情截止 {report.calculated_results.source_dates?.market ?? "暂无"} · 份额截止 {report.calculated_results.source_dates?.shares ?? "暂无"}
        </div>
      </section>

      <section>
        <div className="section-title"><span>五项观察</span><small>数值由程序计算</small></div>
        <div className="indicator-grid">
          {Object.entries(indicatorLabels).map(([key, label]) => (
            <article className="indicator-item" key={key}>
              <span>{label}</span>
              <strong>{formatIndicator(key, indicators[key])}</strong>
            </article>
          ))}
        </div>
      </section>

      {trace.length > 0 && <section>
        <div className="section-title"><span>研究过程</span><small>角色、状态与耗时</small></div>
        <div className="trace-list">
          {trace.map((step, index) => (
            <details key={`${step.agent}-${index}`}>
              <summary>
                {step.status === "success" ? <CheckCircle2 size={17} /> : <AlertTriangle size={17} />}
                <strong>{step.agent}</strong><span>{step.summary}</span><time>{step.duration_ms} ms</time>
              </summary>
              {step.error && <p className="error-copy">{step.error}</p>}
              {step.evidence_refs?.length ? <p>证据关联：{step.evidence_refs.join("、")}</p> : <p>本步骤没有独立资料引用。</p>}
            </details>
          ))}
        </div>
      </section>}

      <section>
        <div className="section-title"><span>公开资料</span><small>引用只证明原文内容</small></div>
        {sources.length ? <div className="source-list">{sources.map((source) => (
          <article key={source.citation}>
            <div className="citation">{source.citation}</div>
            <div><h3>{source.title}</h3><p>{source.content}</p><small>{source.published_at}</small></div>
            {mode === "live" && <a href={source.source_url} target="_blank" rel="noreferrer" aria-label={`打开${source.title}`}><ExternalLink size={18} /></a>}
          </article>
        ))}</div> : <div className="empty-state">本次问题没有使用公开资料片段。</div>}
      </section>

      {report.warnings?.length > 0 && <section className="warnings">
        <div className="section-title"><span>限制与提示</span></div>
        {report.warnings.map((warning, index) => <p key={index}><AlertTriangle size={16} />{warning}</p>)}
      </section>}
    </div>
  );
}
