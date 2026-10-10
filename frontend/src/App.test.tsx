import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, test, vi } from "vitest";
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
