// Selected pure display helpers only; no log-reading, deletion or clipboard actions.
export function formatTime(ts) {
  const d = new Date(ts * 1000);
  const pad = (x) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export function formatTokens(n) {
  if (!n) return "0";
  if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
  return String(n);
}

export function formatMaybeJson(text) {
  if (typeof text !== "string") return JSON.stringify(text ?? null, null, 2);
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

export default function UsageLogs() { return <p>归档未包含运行日志查询和管理后端。</p>; }
