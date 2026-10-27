// 使用真实的无头 Chrome 检查已保存的时间线运行结果，不调用模型。
import { spawn } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { setTimeout as pause } from "node:timers/promises";

const chromePath = process.env.EVENT_BROWSER_PATH ||
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const output = path.resolve(process.argv[2] || "output/event-timeline-browser-check");
const profile = path.join(output, "chrome-profile");
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
  await call("Page.navigate", { url: "http://127.0.0.1:5173/event-timeline" });
  await until(() => evaluate("Boolean(document.querySelector('select')?.options.length)"));
  const cases = [];
  for (const [code, name] of [["600519", "贵州茅台"], ["601318", "中国平安"], ["688981", "中芯国际"]]) {
    await evaluate(`(() => {
      const node = document.querySelector('select');
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(node, '${code}');
      node.dispatchEvent(new Event('change', { bubbles: true }));
    })()`);
    try {
      await until(() => evaluate(`(() => {
        const summary = document.querySelector('.timeline-summary');
        return summary?.innerText.includes('${name}') &&
          document.querySelectorAll('.timeline-entry button').length > 0;
      })()`));
    } catch (error) {
      process.stderr.write(JSON.stringify(await evaluate(`(() => ({
        selected: document.querySelector('select')?.value,
        text: document.body.innerText.slice(0, 2000)
      }))()`), null, 2) + "\n");
      throw error;
    }
    const listing = await evaluate(`(() => ({
      summary: document.querySelector('.timeline-summary')?.innerText,
      eventCount: document.querySelectorAll('.timeline-entry button').length,
      knownCount: [...document.querySelectorAll('.timeline-group')]
        .find((section) => section.querySelector('h3')?.innerText === '已确认发生日期')
        ?.querySelectorAll('.timeline-entry button').length || 0,
      uncertainCount: [...document.querySelectorAll('.timeline-group')]
        .find((section) => section.querySelector('h3')?.innerText === '日期待确认或存在冲突')
        ?.querySelectorAll('.timeline-entry button').length || 0,
      warningCount: document.querySelectorAll('.timeline-warnings li').length,
      internalLabels: /run_id|claim_id|chunk_id|\\bJSON\\b/.test(document.body.innerText)
    }))()`);
    await evaluate("document.querySelector('.timeline-entry button').click()");
    await until(() => evaluate("Boolean(document.querySelector('aside.timeline-drawer blockquote'))"));
    const drawer = await evaluate(`(() => ({
      title: document.querySelector('aside.timeline-drawer h2')?.innerText,
      hasClaim: document.querySelector('aside.timeline-drawer')?.innerText.includes('系统提取：'),
      hasOriginal: document.querySelectorAll('aside.timeline-drawer blockquote').length > 0,
      hasSource: document.querySelectorAll('aside.timeline-drawer .timeline-source').length > 0,
      hasLink: document.querySelectorAll('aside.timeline-drawer a[href^="http"]').length > 0
    }))()`);
    if (!drawer.hasClaim || !drawer.hasOriginal || !drawer.hasSource || !drawer.hasLink || listing.internalLabels) {
      throw new Error(`${name} 的证据抽屉或可见文案未通过检查`);
    }
    const screenshot = await call("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
    await writeFile(path.join(output, `timeline_${code}.png`), Buffer.from(screenshot.data, "base64"));
    await evaluate("document.querySelector('button[aria-label=\"关闭证据详情\"]').click()");
    if (await evaluate("Boolean(document.querySelector('aside.timeline-drawer'))")) {
      throw new Error(`${name} 的证据抽屉未能关闭`);
    }
    if (listing.uncertainCount) {
      await evaluate(`(() => {
        [...document.querySelectorAll('.timeline-group')]
          .find((section) => section.querySelector('h3')?.innerText === '日期待确认或存在冲突')
          .querySelector('.timeline-entry button').click();
      })()`);
      await until(() => evaluate("Boolean(document.querySelector('aside.timeline-drawer blockquote'))"));
      const uncertainText = await evaluate("document.querySelector('aside.timeline-drawer').innerText");
      if (!uncertainText.includes("发生日期未确认") || !uncertainText.includes("系统提取：")) {
        throw new Error(`${name} 的未知日期证据展开状态错误`);
      }
      await evaluate("document.querySelector('button[aria-label=\"关闭证据详情\"]').click()");
    }
    cases.push({ code, name, ...listing, drawer });
  }
  await writeFile(path.join(output, "browser_audit.json"),
    JSON.stringify({ checked_at: new Date().toISOString(), cases }, null, 2), "utf8");
  process.stdout.write(JSON.stringify({ cases }, null, 2) + "\n");
} finally {
  socket?.close();
  browser.kill();
}
