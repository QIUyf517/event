import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import App from "./App";
import { readableAlert } from "./lib";

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const payload = url.includes("/etfs") ? []
      : url.includes("/settings") ? { default_etf: "510300", default_mode: "demo", default_question: "查看当前资金信号" }
      : { items: [], rows: [], warnings: [] };
    return { ok: true, json: async () => payload } as Response;
  }));
});

afterEach(cleanup);

test("research and operations are separate routes", async () => {
  const { unmount } = render(<MemoryRouter initialEntries={["/research"]}><App /></MemoryRouter>);
  expect(screen.getByRole("heading", { name: "让每个结论都有证据。" })).toBeInTheDocument();
  expect(screen.queryByText("三只基金横向观察")).not.toBeInTheDocument();
  unmount();

  render(<MemoryRouter initialEntries={["/operations"]}><App /></MemoryRouter>);
  expect(screen.getByRole("heading", { name: "数据日期与运行记录。" })).toBeInTheDocument();
  expect(screen.queryByText("生成研究简报")).not.toBeInTheDocument();
});

test("alert signal codes are fully translated", () => {
  expect(readableAlert("信号由 cautious 变为 moderately_positive"))
    .toBe("信号由 同向走弱 变为 温和走强");
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
