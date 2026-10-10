export const signalLabels: Record<string, string> = {
  positive: "同向走强",
  moderately_positive: "温和走强",
  neutral: "方向不明确",
  cautious: "同向走弱",
  insufficient_data: "数据不足"
};

export const statusLabels: Record<string, string> = {
  answered: "已回答",
  partial: "部分回答",
  unsupported: "超出范围",
  success: "已完成",
  partial_success: "部分完成",
  failed: "失败",
  skipped: "已跳过"
};

export const syncStatusLabels: Record<string, string> = {
  success: "已更新",
  partial_success: "部分更新",
  failed: "更新失败",
  skipped: "未执行",
  no_data: "没有新数据",
  not_requested: "未请求"
};

export const indicatorLabels: Record<string, string> = {
  price_position_60d: "近60日价格位置",
  share_change_5d: "近5次披露份额变化",
  turnover_percentile: "成交额历史分位",
  price_return_5d: "近5日价格变化",
  volume_ratio_5d: "近5日成交量比"
};

export function formatIndicator(key: string, value: number | null | undefined): string {
  if (value === null || value === undefined) return "暂无";
  if (key === "volume_ratio_5d") return `${value.toFixed(2)}倍`;
  const signed = key === "share_change_5d" || key === "price_return_5d";
  return `${signed && value > 0 ? "+" : ""}${(value * 100).toFixed(1)}%`;
}

export function today(): string {
  return new Date().toISOString().slice(0, 10);
}

export function yearStart(): string {
  return `${new Date().getFullYear()}-01-01`;
}

export function readableAlert(message: string): string {
  const labelsByLongestKey = Object.entries(signalLabels).sort(([left], [right]) => right.length - left.length);
  return labelsByLongestKey.reduce(
    (text, [key, label]) => text.replaceAll(key, label),
    message
  );
}
