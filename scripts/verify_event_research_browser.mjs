// 使用真实的无头 Chrome 检查已保存的时间线运行结果，不调用模型。
import { spawn } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { setTimeout as pause } from "node:timers/promises";

const chromePath = process.env.EVENT_BROWSER_PATH ||
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const output = path.resolve(process.argv[2] || "output/event-timeline-browser-check");
const profile = path.join(output, `chrome-profile-${Date.now()}`);
await mkdir(profile, { recursive: true });

const browser = spawn(chromePath, [
  "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
  "--remote-allow-origins=*", "--remote-debugging-port=0",
  `--user-data-dir=${profile}`, "about:blank"
], { stdio: "ignore", windowsHide: true });

async function until(fn, timeout = 30000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    try {
      const value = await fn();
      if (value) return value;
    } catch { /* 浏览器或页面可能仍在启动。 */ }
    await pause(250);
  }
  throw new Error("浏览器检查等待超时");
}

let socket;
try {
  const port = await until(async () => {
    const value = await readFile(path.join(profile, "DevToolsActivePort"), "utf8");
    return Number(value.split("\n")[0]) || null;
  });
  const pages = await until(async () => {
    const response = await fetch(`http://127.0.0.1:${port}/json/list`);
    const result = await response.json();
    return result.find((item) => item.type === "page" && item.webSocketDebuggerUrl);
  });
  socket = new WebSocket(pages.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  let nextId = 1;
  const waiting = new Map();
  socket.addEventListener("message", ({ data }) => {
    const response = JSON.parse(data);
    if (!response.id || !waiting.has(response.id)) return;
    const { resolve, reject } = waiting.get(response.id);
    waiting.delete(response.id);
    response.error ? reject(new Error(response.error.message)) : resolve(response.result);
  });
  function call(method, params = {}) {
    const id = nextId++;
    return new Promise((resolve, reject) => {
      waiting.set(id, { resolve, reject });
      socket.send(JSON.stringify({ id, method, params }));
    });
  }
  async function evaluate(expression) {
    const result = await call("Runtime.evaluate", {
      expression, returnByValue: true, awaitPromise: true
    });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
    return result.result?.value;
  }
  await call("Page.enable");
  await call("Runtime.enable");
  await call("Emulation.setDeviceMetricsOverride", {
    width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false
  });
  const base = process.env.EVENT_DEMO_URL || "http://127.0.0.1:8006";
  const health = await (await fetch(`${base}/health`)).json();
  if (health.status !== "ok") throw new Error("API health failed");
  const audits = [];
  for (const code of ["600519", "601318", "688981"]) {
    await call("Page.navigate", {url: `${base}/research?run=demo-${code}`});
    await until(() => evaluate("Boolean(document.querySelector('.research-report button')) && document.querySelectorAll('.research-event').length > 0"));
    const listing = await evaluate(`(() => ({
      events: document.querySelectorAll('.research-event').length,
      hypotheses: document.querySelectorAll('.research-hypothesis').length,
      report: document.body.innerText.includes('程序生成的保守报告'),
      unverified: document.body.innerText.includes('未验证')
    }))()`);
    await evaluate("document.querySelector('.research-event').open=true; document.querySelector('.evidence-chip').click()");
    await until(() => evaluate("Boolean(document.querySelector('.timeline-drawer blockquote'))"));
    const drawer = await evaluate(`(() => ({
      quote: document.querySelector('.timeline-drawer blockquote').innerText,
      source: document.querySelector('.timeline-drawer a').href,
      distinctions: ['原文说了什么','模型提取','程序检查'].every(s => document.querySelector('.timeline-drawer').innerText.includes(s))
    }))()`);
    if (!listing.report || !listing.unverified || !drawer.distinctions || !drawer.quote) throw new Error("Evidence or publication boundary missing");
    await evaluate("document.querySelector('.timeline-drawer button').click()");
    if (await evaluate("Boolean(document.querySelector('.timeline-drawer'))")) throw new Error("Drawer did not close");
    const cards = await evaluate(`(() => ({
      count: document.querySelectorAll('.research-evidence-row').length,
      hasQuote: Boolean(document.querySelector('.research-evidence-row blockquote')?.innerText),
      boundary: document.body.innerText.includes('原文可定位，不等于它已证明这个解释。'),
      sourceDates: document.body.innerText.includes('资料发布日期：')
    }))()`);
    if (!cards.count || !cards.hasQuote || !cards.boundary || !cards.sourceDates) throw new Error("Evidence card boundary missing");
    await evaluate("document.querySelector('.research-evidence-row').scrollIntoView({block:'center'})");
    const evidenceScreenshot = await call("Page.captureScreenshot", {format:"png",captureBeyondViewport:false});
    await writeFile(path.join(output, `evidence_${code}.png`), Buffer.from(evidenceScreenshot.data,"base64"));
    await evaluate("scrollTo(0,0)");
    const screenshot = await call("Page.captureScreenshot", {format:"png",captureBeyondViewport:false});
    await writeFile(path.join(output, `research_${code}.png`), Buffer.from(screenshot.data,"base64"));
    audits.push({code,...listing,drawer,cards});
  }
  await call("Emulation.setDeviceMetricsOverride", {width:390,height:844,deviceScaleFactor:1,mobile:true});
  await evaluate("scrollTo(0,0)");
  await pause(300);
  const overflow = await evaluate("document.documentElement.scrollWidth > innerWidth");
  if (overflow) throw new Error("Mobile horizontal overflow");
  const mobile = await call("Page.captureScreenshot", {format:"png",captureBeyondViewport:false});
  await writeFile(path.join(output,"research_mobile.png"),Buffer.from(mobile.data,"base64"));
  await evaluate("document.querySelector('.research-evidence-row').scrollIntoView({block:'start'})");
  const evidenceMobile = await call("Page.captureScreenshot", {format:"png",captureBeyondViewport:false});
  await writeFile(path.join(output,"evidence_mobile.png"),Buffer.from(evidenceMobile.data,"base64"));
  await writeFile(path.join(output,"browser_audit.json"),JSON.stringify({health,audits,mobile_horizontal_overflow:overflow},null,2));
  process.stdout.write(JSON.stringify({health,cases:audits.length,mobile_horizontal_overflow:overflow})+"\n");
} finally {
  socket?.close(); browser.kill();
}
