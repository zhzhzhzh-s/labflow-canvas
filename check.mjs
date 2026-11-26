// 开发检查：连本机调试模式的 Chrome，把画布 demo 真跑一遍。
// 用法：node check.mjs （Chrome 需已开 --remote-debugging-port=9223）
import fsp from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const CDP = process.env.CDP_ENDPOINT || "http://127.0.0.1:9223";
const APP = process.env.APP_BASE || "http://127.0.0.1:43300/";
const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE = path.join(HERE, ".tmp", "第二批数据.csv");

class Client {
  constructor(url) {
    this.ws = new WebSocket(url);
    this.seq = 0;
    this.pending = new Map();
    this.errors = [];
    this.ws.addEventListener("message", (event) => {
      const msg = JSON.parse(event.data);
      if (msg.id) {
        const waiter = this.pending.get(msg.id);
        if (!waiter) return;
        this.pending.delete(msg.id);
        if (msg.error) waiter.reject(new Error(msg.error.message));
        else waiter.resolve(msg.result);
        return;
      }
      if (msg.method === "Runtime.exceptionThrown") {
        this.errors.push(msg.params.exceptionDetails?.exception?.description ?? "exception");
      }
      if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error") {
        this.errors.push(msg.params.args.map((a) => a.value ?? a.description ?? "").join(" "));
      }
    });
  }
  async open() {
    if (this.ws.readyState === 1) return;
    await new Promise((resolve, reject) => {
      this.ws.addEventListener("open", resolve, { once: true });
      this.ws.addEventListener("error", () => reject(new Error("CDP 连接失败")), { once: true });
    });
  }
  send(method, params = {}) {
    const id = (this.seq += 1);
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      setTimeout(() => {
        if (this.pending.delete(id)) reject(new Error("CDP 超时：" + method));
      }, 15000);
    });
  }
  async eval(expression) {
    const result = await this.send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description ?? "页面报错");
    return result.result.value;
  }
  async waitFor(expression, label, timeout = 10000) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      try {
        if (await this.eval(expression)) return;
      } catch {
        /* 页面还没就绪 */
      }
      await sleep(120);
    }
    throw new Error("等待超时：" + label);
  }
  async mouse(type, x, y, extra = {}) {
    await this.send("Input.dispatchMouseEvent", { type, x, y, button: "left", clickCount: 1, ...extra });
  }
  async drag(from, to, steps = 8) {
    await this.mouse("mousePressed", from.x, from.y);
    for (let i = 1; i <= steps; i += 1) {
      await this.mouse("mouseMoved", from.x + ((to.x - from.x) * i) / steps, from.y + ((to.y - from.y) * i) / steps);
      await sleep(25);
    }
    await this.mouse("mouseReleased", to.x, to.y);
    await sleep(400);
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  await fsp.mkdir(path.dirname(FIXTURE), { recursive: true });
  await fsp.writeFile(FIXTURE, "批次,样品,pH,产率%\n第二批,A,7.2,66\n", "utf8");

  const target = await (await fetch(CDP + "/json/new?about:blank", { method: "PUT" })).json();
  const client = new Client(target.webSocketDebuggerUrl);
  await client.open();
  await client.send("Page.enable");
  await client.send("Runtime.enable");
  await client.send("DOM.enable");
  await client.send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 980, deviceScaleFactor: 1, mobile: false });

  let passed = 0;
  const ok = (name) => {
    passed += 1;
    console.log("  ✔ " + name);
  };

  await client.send("Page.navigate", { url: APP });
  await client.waitFor("document.readyState === 'complete'", "页面加载");
  await client.eval("localStorage.removeItem('labflow.canvas.v3')");
  await client.send("Page.reload");
  await client.waitFor("document.readyState === 'complete'", "重新加载");
  await client.waitFor("document.querySelectorAll('.node').length === 4", "示例节点渲染");
  ok("示例 4 块渲染出来");

  const ids = await client.eval("[...document.querySelectorAll('.node')].map((n) => n.dataset.id)");
  const counts = await client.eval(`({
    edges: document.querySelectorAll('#edgePaths path[data-edge]').length,
    stickers: document.querySelectorAll('.sticker').length,
    bands: document.querySelectorAll('#bands .band').length,
  })`);
  if (counts.edges !== 3) throw new Error("连线数不对：" + counts.edges);
  if (counts.stickers !== 2) throw new Error("标记数不对：" + counts.stickers);
  if (counts.bands < 4) throw new Error("列太少：" + counts.bands);
  ok("3 条连线 / 2 个标记 / " + counts.bands + " 列");

  const colors = await client.eval("[...document.querySelectorAll('.node')].map((n) => getComputedStyle(n).getPropertyValue('--owner').trim())");
  if (new Set(colors).size < 3) throw new Error("颜色没有区分开：" + colors.join(","));
  ok("每块颜色分开了（" + new Set(colors).size + " 种）");

  const shot = await client.send("Page.captureScreenshot", { format: "png" });
  await fsp.writeFile(path.join(HERE, "preview.png"), Buffer.from(shot.data, "base64"));
  ok("存了预览图 preview.png");

  // 换颜色
  const colorBefore = colors[0];
  await client.eval("document.querySelector('.node .color-btn').click()");
  await client.waitFor("!document.querySelector('#colorPop').hidden", "颜色面板");
  await client.eval("document.querySelectorAll('#colorSwatches .swatch')[3].click()");
  await sleep(200);
  const colorAfter = await client.eval("getComputedStyle(document.querySelector('.node')).getPropertyValue('--owner').trim()");
  if (colorAfter === colorBefore) throw new Error("换色没生效");
  await client.eval("document.getElementById('colorPop').hidden = true");
  ok("点色块换颜色");

  // 附件（选文件）
  const doc = await client.send("DOM.getDocument", { depth: 0 });
  const inputNode = await client.send("DOM.querySelector", { nodeId: doc.root.nodeId, selector: `[data-id="${ids[0]}"] .file-input` });
  if (!inputNode.nodeId) throw new Error("找不到文件输入框");
  await client.send("DOM.setFileInputFiles", { nodeId: inputNode.nodeId, files: [FIXTURE] });
  await client.waitFor(`document.querySelectorAll('[data-id="${ids[0]}"] .file-chip').length === 2`, "附件出现", 8000);
  ok("选文件能挂到块上");

  // 拖一个标记到画布
  const mark = await client.eval(`(() => {
    const r = document.querySelector('.pmark[data-tone="urgent"]').getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  })()`);
  const stageRect = await client.eval(`(() => { const r = document.querySelector('#stage').getBoundingClientRect(); return { left: r.left, top: r.top, width: r.width, height: r.height }; })()`);
  await client.drag(mark, { x: stageRect.left + stageRect.width * 0.74, y: stageRect.top + stageRect.height * 0.68 });
  await client.waitFor("document.querySelectorAll('.sticker').length === 3", "拖出标记");
  ok("从左边拖出标记");

  // 列设置：日 → 周
  await client.eval("document.querySelector('#btnCols').click()");
  await client.waitFor("!document.querySelector('#colPop').hidden", "列设置面板");
  const weekBands = await client.eval("document.querySelectorAll('#bands .band').length");
  await client.eval("document.querySelector('#colUnit button[data-unit=\"day\"]').click()");
  await sleep(300);
  const dayBands = await client.eval("document.querySelectorAll('#bands .band').length");
  if (!(dayBands > weekBands)) throw new Error("切到「日」列数没变多：" + weekBands + " → " + dayBands);
  const dayLabel = await client.eval("document.querySelector('#bands .band-label').textContent");
  if (!dayLabel.includes("周")) throw new Error("日列标题不对：" + dayLabel);
  await client.eval("document.querySelector('#colUnit button[data-unit=\"week\"]').click()");
  await sleep(300);
  const backBands = await client.eval("document.querySelectorAll('#bands .band').length");
  if (backBands !== weekBands) throw new Error("切回周列数不对：" + backBands);
  await client.eval("document.querySelector('#colClose').click()");
  ok("列设置：每天一列 / 每周一列都生效");

  // 新建一块（点左侧面板的「新块」）
  await client.eval(`(() => {
    const el = document.querySelector('#pNew');
    const r = el.getBoundingClientRect();
    const opts = { bubbles: true, cancelable: true, button: 0, pointerId: 1, clientX: r.x + r.width / 2, clientY: r.y + r.height / 2 };
    el.dispatchEvent(new PointerEvent('pointerdown', opts));
    window.dispatchEvent(new PointerEvent('pointerup', opts));
  })()`);
  await client.waitFor("document.querySelectorAll('.node').length === 5", "新建块");
  ok("新建一块");

  // 拖动第一块
  const before = await client.eval(`(() => { const el = document.querySelector('[data-id="${ids[0]}"]'); return { left: el.style.left, top: el.style.top }; })()`);
  const grip = await client.eval(`(() => { const r = document.querySelector('[data-id="${ids[0]}"] .grip').getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()`);
  await client.drag(grip, { x: grip.x + 80, y: grip.y + 40 });
  const after = await client.eval(`(() => { const el = document.querySelector('[data-id="${ids[0]}"]'); return { left: el.style.left, top: el.style.top }; })()`);
  if (after.left === before.left && after.top === before.top) throw new Error("拖动没有改变位置");
  ok("拖动块改变位置");

  // 拉线
  const port = await client.eval(`(() => { const r = document.querySelector('[data-id="${ids[0]}"] .port').getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()`);
  const targetCenter = await client.eval(`(() => { const r = document.querySelector('[data-id="${ids[1]}"]').getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()`);
  await client.drag(port, targetCenter, 8);
  await client.waitFor("!!document.querySelector('.edge-label-input')", "连线说明输入框");
  await client.send("Input.insertText", { text: "对照" });
  await client.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 });
  await client.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 });
  await sleep(400);
  const edgesAfter = await client.eval("document.querySelectorAll('#edgePaths path[data-edge]').length");
  if (edgesAfter !== 4) throw new Error("新连线没加上：" + edgesAfter);
  ok("从圆点拉线并写说明");

  // 刷新恢复
  await sleep(900);
  await client.send("Page.reload");
  await client.waitFor("document.readyState === 'complete'", "刷新完成");
  await client.waitFor("document.querySelectorAll('.node').length === 5", "刷新后节点恢复");
  const restored = await client.eval(`({
    edges: document.querySelectorAll('#edgePaths path[data-edge]').length,
    stickers: document.querySelectorAll('.sticker').length,
    chips: document.querySelectorAll('[data-id="${ids[0]}"] .file-chip').length,
  })`);
  if (restored.edges !== 4 || restored.stickers !== 3 || restored.chips !== 2) {
    throw new Error("刷新后丢失：" + JSON.stringify(restored));
  }
  ok("刷新后块 / 线 / 标记 / 附件都在");

  // 按时间排列：点一次排、再点一次还原
  if ((await client.eval("document.querySelectorAll('.node').length")) !== 5) throw new Error("整理后节点丢了");
  const positionsBefore = await client.eval(`[...document.querySelectorAll('.node')].map((n) => [n.dataset.id, n.style.left, n.style.top])`);
  await client.eval("document.querySelector('#btnLayout').click()");
  await sleep(700);
  if (!(await client.eval("document.querySelector('#btnLayout').classList.contains('active')"))) throw new Error("排列后按钮没进入开启状态");
  const arranged = await client.eval(`[...document.querySelectorAll('.node')].map((n) => [n.dataset.id, n.style.left, n.style.top])`);
  if (JSON.stringify(arranged) === JSON.stringify(positionsBefore)) throw new Error("点了排列但位置没变");
  await client.eval("document.querySelector('#btnLayout').click()");
  await sleep(700);
  const restoredPositions = await client.eval(`[...document.querySelectorAll('.node')].map((n) => [n.dataset.id, n.style.left, n.style.top])`);
  if (JSON.stringify(restoredPositions) !== JSON.stringify(positionsBefore)) throw new Error("再点一次没有恢复到原位");
  ok("按时间排列：点一下排，再点一下还原");
  await client.eval("document.querySelector('#btnLayout').click()");
  await sleep(700);

  const snapBefore = await client.eval(`(() => { const el = document.querySelector('[data-id="${ids[0]}"]'); return { left: el.style.left, top: el.style.top }; })()`);
  const snapGrip = await client.eval(`(() => { const r = document.querySelector('[data-id="${ids[0]}"] .grip').getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()`);
  await client.mouse("mousePressed", snapGrip.x, snapGrip.y);
  await client.mouse("mouseMoved", snapGrip.x + 8, snapGrip.y + 6);
  await sleep(150);
  const guidesOn = await client.eval("document.querySelector('#guides').classList.contains('on')");
  await client.mouse("mouseReleased", snapGrip.x + 8, snapGrip.y + 6);
  await sleep(350);
  const snapAfter = await client.eval(`(() => { const el = document.querySelector('[data-id="${ids[0]}"]'); return { left: el.style.left, top: el.style.top }; })()`);
  if (snapAfter.left !== snapBefore.left || snapAfter.top !== snapBefore.top) {
    throw new Error("吸附没把块拉回列/行：" + JSON.stringify({ snapBefore, snapAfter }));
  }
  if (!guidesOn) throw new Error("拖动时没有出现对齐参考线");
  ok("拖到列/行附近会吸附并显示参考线");

  await client.eval("document.querySelectorAll('.node .del')[4].click()");
  await client.waitFor("document.querySelectorAll('.node').length === 4", "删除块");
  await client.eval("document.querySelector('.toast button')?.click()");
  await client.waitFor("document.querySelectorAll('.node').length === 5", "撤销删除");
  ok("按时间排列、吸附、删除撤销");

  if (client.errors.length) {
    console.error("控制台错误：", client.errors);
    throw new Error("页面有 " + client.errors.length + " 条报错");
  }
  ok("控制台无报错");

  console.log("\n检查通过：" + passed + " 项");
  client.ws.close();
}

main().catch((err) => {
  console.error("检查失败：", err.message);
  process.exit(1);
});
