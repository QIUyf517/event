import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import App from "./App";

afterEach(cleanup);

test("research main entry restores a saved run without a model call", async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const payload = url.endsWith("/subjects") ? {
      subjects: [{ subject_id: "600519", code: "600519", name: "贵州茅台" }],
      available_start: "2026-08-14", available_end: "2026-09-13", source_kind: "configured"
    } : url.endsWith("/demo") ? { enabled: false, runs: [] } : { run: {
      run_id: "saved-1", question: "已保存的问题", subject_code: "600519", window_start: "2026-08-14", as_of_date: "2026-09-13",
      status: "partial", total_tokens: 25, estimated_cost_usd: .001, elapsed_seconds: 3,
      stop_reason: "已完成保守报告", planning: null, graph: null,
      stages: [{ key: "report", label: "程序报告", status: "completed", elapsed_seconds: 1 }],
      debate: { hypotheses: [], findings: [], search_checks: [], quality_issues: [], publication_mode: "evidence_bound_pending_review",
        conclusion: { text: "无法确认因果，解释关系未验证。", answer_status: "partial", limitations: ["原文仍需核对"] } }
    } };
    return { ok: true, json: async () => payload } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<MemoryRouter initialEntries={["/research?run=saved-1"]}><App /></MemoryRouter>);
  expect(await screen.findByText("无法确认因果，解释关系未验证。")).toBeInTheDocument();
  expect(screen.getByText("25 Token")).toBeInTheDocument();
  expect(screen.getByText("原文仍需核对")).toBeInTheDocument();
  expect(fetchMock.mock.calls.every(([url]) => !String(url).endsWith("/runs"))).toBe(true);
});

test.each([
  ["accepted_sample", "阶段二验收样本"],
  ["accepted_phase3_sample", "阶段三验收样本"]
])("event source page searches chosen filters using %s", async (sourceKind, sourceLabel) => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const payload = url.includes("/event-research/subjects") ? {
      subjects: [
        { subject_id: "600519", code: "600519", name: "贵州茅台" },
        { subject_id: "601318", code: "601318", name: "中国平安" }
      ],
      available_start: "2026-08-14", available_end: "2026-09-13",
      source_kind: sourceKind
    } : {
      count: 1, items: [{ chunk_id: "chunk-1", document_id: "doc-1",
        title: "中国平安半年度报告", publisher: "中国平安", source_url: "https://example.com/report",
        published_at: "2026-08-21", document_type: "financial", excerpt: "营业收入资料原文片段" }]
    };
    return { ok: true, json: async () => payload } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<MemoryRouter initialEntries={["/event-sources"]}><App /></MemoryRouter>);

  expect(await screen.findByText(sourceLabel)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("研究对象"), { target: { value: "601318" } });
  fireEvent.change(screen.getByLabelText("开始日期"), { target: { value: "2026-08-20" } });
  fireEvent.click(screen.getByLabelText("公告"));
  fireEvent.click(screen.getByLabelText("新闻"));
  fireEvent.change(screen.getByLabelText("检索关键词"), { target: { value: "营业收入" } });
  fireEvent.click(screen.getByRole("button", { name: "检索已保存资料" }));

  expect(await screen.findByText("中国平安半年度报告")).toBeInTheDocument();
  expect(screen.getByText("营业收入资料原文片段")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "查看原文" })).toHaveAttribute("href", "https://example.com/report");
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  const searchUrl = new URL(String(fetchMock.mock.calls[1][0]), "http://localhost");
  expect(searchUrl.searchParams.get("subject_id")).toBe("601318");
  expect(searchUrl.searchParams.get("start_date")).toBe("2026-08-20");
  expect(searchUrl.searchParams.get("end_date")).toBe("2026-09-13");
  expect(searchUrl.searchParams.get("query")).toBe("营业收入");
  expect(searchUrl.searchParams.getAll("source_types")).toEqual(["financial"]);
});

test("event timeline shows conflicting dates and original quotes after an authenticated build", async () => {
  sessionStorage.setItem("event-api-key", "test-key");
  const graph = {
    run_id: "graph-1", question: "核对半年报", subject_code: "600519", subject_name: "贵州茅台",
    window_start: "2026-08-14", as_of_date: "2026-09-13", status: "extracted",
    model: "fake-model", planning_tokens: 10, extraction_tokens: 20, estimated_cost_usd: 0.001,
    warnings: [],
    sources: [
      { document_id: "doc-1", source_id: "official", source_type: "financial", title: "半年度报告",
        publisher: "公司", source_url: "https://example.com/report", published_at: "2026-08-20", reprint_of: null },
      { document_id: "doc-2", source_id: "news", source_type: "news", title: "新闻报道",
        publisher: "媒体", source_url: "https://example.com/news", published_at: "2026-08-21", reprint_of: null }
    ],
    citations: [
      { citation_id: "cite-1", document_id: "doc-1", chunk_id: "chunk-1", quote: "原文记载营业收入100亿元。", page: 4, location: null },
      { citation_id: "cite-2", document_id: "doc-2", chunk_id: "chunk-2", quote: "报道记载营业收入105亿元。", page: null, location: null }
    ],
    claims: [
      { claim_id: "claim-1", event_id: "event-1", citation_id: "cite-1", attribute: "营业收入", value_text: "100亿元", numeric_status: "format_checked", text: "原文记载营业收入100亿元。" },
      { claim_id: "claim-2", event_id: "event-1", citation_id: "cite-2", attribute: "营业收入", value_text: "105亿元", numeric_status: "format_checked", text: "报道记载营业收入105亿元。" }
    ],
    conflicts: [{ conflict_id: "conflict-1", event_id: "event-1", kind: "number", attribute: "营业收入",
      variants: ["100亿元", "105亿元"], claim_ids: ["claim-1", "claim-2"] }],
    relations: [{ relation_id: "relation-1", event_id: "event-1", left_claim_id: "claim-1",
      right_claim_id: "claim-2", kind: "conflict" }],
    events: [{ event_id: "event-1", title: "半年报披露", occurred_at: null,
      date_status: "conflict", reported_at: "2026-08-20", importance: "high",
      claim_ids: ["claim-1", "claim-2"], document_ids: ["doc-1", "doc-2"], conflict_ids: ["conflict-1"] }]
  };
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/event-research/subjects")) return { ok: true, json: async () => ({
      subjects: [{ subject_id: "600519", code: "600519", name: "贵州茅台" }],
      available_start: "2026-08-14", available_end: "2026-09-13", source_kind: "accepted_phase3_sample"
    }) } as Response;
    if (url.includes("/timeline/build")) return { ok: true, json: async () => ({ graph }) } as Response;
    return { ok: false, status: 404, json: async () => ({ detail: "尚无已保存的事件时间线" }) } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<MemoryRouter initialEntries={["/event-timeline"]}><App /></MemoryRouter>);
  expect(await screen.findByRole("heading", { name: "按发生时间梳理，逐条回到原文。" })).toBeInTheDocument();
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(screen.getByRole("button", { name: "生成事件时间线（调用模型）" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "生成事件时间线（调用模型）" }));
  expect(await screen.findByText("半年报披露")).toBeInTheDocument();
  const buildCall = fetchMock.mock.calls.find(([url]) => String(url).includes("/timeline/build"));
  expect(buildCall?.[1]?.method).toBe("POST");
  expect(new Headers(buildCall?.[1]?.headers).get("X-API-Key")).toBe("test-key");
  fireEvent.click(screen.getByRole("button", { name: /发生日期有冲突.*半年报披露/ }));
  expect(screen.getByText("100亿元；105亿元")).toBeInTheDocument();
  expect(screen.getByText(/半年度报告 与 新闻报道：数值冲突/)).toBeInTheDocument();
  expect(screen.getByText("原文记载营业收入100亿元。")).toBeInTheDocument();
  expect(screen.getByText("报道记载营业收入105亿元。")).toBeInTheDocument();
  sessionStorage.removeItem("event-api-key");
});

test("research evidence cards separate original quotes from unreviewed support", async () => {
  const graph = {
    events: [{ event_id: "event-1", title: "会议召开", occurred_at: null, date_status: "unknown",
      reported_at: "2026-08-15", claim_ids: ["claim-1"], document_ids: ["doc-1"], conflict_ids: [] }],
    sources: [{ document_id: "doc-1", source_type: "announcement", title: "董事会公告", publisher: "公司",
      published_at: "2026-08-15", source_url: "https://example.org/notice", reprint_of: null }],
    citations: [{ citation_id: "cite-1", document_id: "doc-1", chunk_id: "chunk-1",
      quote: "会议计划于8月13日召开。", page: 1, location: null }],
    claims: [{ claim_id: "claim-1", event_id: "event-1", citation_id: "cite-1", attribute: "会议日期",
      value_text: "8月13日", numeric_status: "not_applicable", text: "会议计划于8月13日召开。" }],
    conflicts: [], relations: [], warnings: [],
  };
  const run = {
    run_id: "saved-evidence", question: "会议与股价变化有关吗？", subject_code: "600519",
    window_start: "2026-08-13", as_of_date: "2026-09-12", status: "partial",
    elapsed_seconds: 1, total_tokens: 50, estimated_cost_usd: .001, stop_reason: "待审核",
    planning: null, graph, stages: [{ key: "report", label: "程序报告", status: "completed", elapsed_seconds: 1 }],
    debate: { publication_mode: "typed_evidence_pending_review", search_checks: [], findings: [], quality_issues: [],
      hypotheses: [{ hypothesis_id: "h1", interpretation_kind: "event_candidate",
        statement: "候选事件与问题的关系尚未验证。", support_claim_ids: ["claim-1"], counter_claim_ids: [],
        missing_evidence: ["会议是否实际召开待核对。"], alternative_explanations: [] }],
      conclusion: { text: "无法确认因果。", answer_status: "partial", limitations: [] } }
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => ({ ok: true, json: async () =>
    String(input).endsWith("/subjects") ? { subjects: [], available_start: null, available_end: null } :
      String(input).endsWith("/demo") ? { enabled: false, runs: [] } : { run } })));
  render(<MemoryRouter initialEntries={["/research?run=saved-evidence"]}><App /></MemoryRouter>);
  expect(await screen.findByRole("heading", { name: "候选解释与证据卡" })).toBeInTheDocument();
  const card = screen.getByRole("region", { name: "候选 1 模型选作支持" });
  expect(card).toHaveTextContent("会议计划于8月13日召开。");
  expect(card).toHaveTextContent("资料发布日期：2026-08-15；图谱发生日：未确认");
  expect(card).toHaveTextContent("原文可定位，不等于它已证明这个解释。");
  expect(card).not.toHaveTextContent("发生日：2026-08-15");
  fireEvent.click(screen.getByRole("button", { name: "查看定位与模型提取" }));
  expect(screen.getByRole("complementary", { name: "研究证据详情" })).toHaveTextContent("模型提取");
});
