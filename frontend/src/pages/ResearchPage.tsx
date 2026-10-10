import { ArrowRight, Bot, FileText, RefreshCw, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api";
import { ApiKeyField } from "../components/ApiKeyField";
import { PriceChart } from "../components/PriceChart";
import { ReportView } from "../components/ReportView";
import { statusLabels, today } from "../lib";
import type { ArchiveItem, DataMode, Etf, MarketBar, ResearchMethod, ResearchResponse } from "../types";

const fallbackEtfs: Etf[] = [
  { etf_code: "510300", name: "沪深300ETF" },
  { etf_code: "510050", name: "华夏上证50ETF" },
  { etf_code: "510500", name: "南方中证500ETF" }
];

export function ResearchPage() {
  const [etfs, setEtfs] = useState<Etf[]>(fallbackEtfs);
  const [etf, setEtf] = useState("510300");
  const [mode, setMode] = useState<DataMode>("demo");
  const [method, setMethod] = useState<ResearchMethod>("rules");
  const [question, setQuestion] = useState("当前资金信号和已入库资料分别能说明什么？");
  const [market, setMarket] = useState<MarketBar[]>([]);
  const [archive, setArchive] = useState<ArchiveItem[]>([]);
  const [result, setResult] = useState<ResearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([api.etfs(), api.settings()]).then(([rows, settings]) => {
      setEtfs(rows);
      setEtf(settings.default_etf);
      setMode(settings.default_mode);
      setQuestion(settings.default_question);
    }).catch(() => undefined);
  }, []);

  useEffect(() => {
    Promise.all([api.market(etf, mode, today()), api.reports(mode)])
      .then(([prices, reports]) => { setMarket(prices.items); setArchive(reports.items); })
      .catch((reason: Error) => setError(reason.message));
  }, [etf, mode, result]);

  async function runResearch() {
    if (!question.trim()) return;
    setLoading(true);
    setError("");
    try {
      const response = await api.research({
        question: question.trim(), etf_code: etf, as_of_date: today(), mode, method
      });
      setResult(response);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "研究请求失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="page-shell">
      <header className="page-header">
        <div><div className="eyebrow">RESEARCH DESK · 可核验研究</div><h1>让每个结论都有证据。</h1></div>
        <p>模型负责规划和检索，关键数字由程序计算。每份结论都保留日期、来源和未知范围。</p>
      </header>

      <section className="research-controls">
        <div className="control-grid">
          <label>数据模式<select value={mode} onChange={(event) => { setMode(event.target.value as DataMode); setResult(null); }}>
            <option value="demo">演示数据</option><option value="live">真实数据</option>
          </select></label>
          <label>研究标的<select value={etf} onChange={(event) => { setEtf(event.target.value); setResult(null); }}>
            {etfs.map((item) => <option key={item.etf_code} value={item.etf_code}>{item.etf_code} · {item.name}</option>)}
          </select></label>
        </div>

        <fieldset className="method-picker">
          <legend>研究方式</legend>
          <button type="button" className={method === "rules" ? "selected" : ""} onClick={() => setMethod("rules")}>
            <ShieldCheck /><span><strong>规则研究</strong><small>快速、确定，不调用模型</small></span>
          </button>
          <button type="button" className={method === "ai" ? "selected" : ""} onClick={() => setMethod("ai")}>
            <Bot /><span><strong>AI协作研究</strong><small>模型规划、取证和复核，会产生费用</small></span>
          </button>
        </fieldset>

        <label className="question-field">研究问题
          <textarea value={question} onChange={(event) => setQuestion(event.target.value)} rows={4} maxLength={500} />
          <small>支持资金信号、价格位置、份额变化、成交热度和已入库资料。</small>
        </label>
        <ApiKeyField />
        {error && <div className="error-banner">{error}</div>}
        <button className="primary-button" type="button" disabled={loading || !question.trim()} onClick={runResearch}>
          {loading ? <><RefreshCw className="spin" /> 正在规划和核验</> : <>生成研究简报 <ArrowRight /></>}
        </button>
      </section>

      <section className="market-strip">
        <div className="section-title"><span>已保存的价格轨迹</span><small>最近90个可用交易日</small></div>
        <PriceChart rows={market} />
      </section>

      {result ? <ReportView taskId={result.task_id} report={result.report} mode={mode} /> : (
        <section className="blank-report">
          <FileText size={30} /><h2>研究结果会出现在这里</h2>
          <p>先确认数据模式、研究标的和问题，再生成一份带日期与来源的简报。</p>
        </section>
      )}

      <section className="archive-section">
        <div className="section-title"><span>最近报告</span><small>当前数据模式 · {archive.length}份</small></div>
        {archive.length ? <div className="archive-list">{archive.map((item) => (
          <article key={item.task_id}>
            <time>{item.created_at}</time>
            <div><strong>{item.etf_code} · {item.question}</strong><small>数据截止 {item.data_cutoff_date ?? "暂无"}</small></div>
            <span>{statusLabels[item.answer_status] ?? item.answer_status}</span>
          </article>
        ))}</div> : <div className="empty-state">当前模式还没有报告存档。</div>}
      </section>
    </main>
  );
}
