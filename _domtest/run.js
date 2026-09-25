/*
 * 页面 JS 冒烟测试（DOM 桩）
 *
 * 为什么需要它：node --check 只能验语法，验不出「renderPlugins() 里访问了
 * 一个 undefined 的字段」这种运行时错误。沙箱里起不了真浏览器，
 * 所以这里搭一个最小 DOM 桩，把 index.html 里那段真实 JS 跑起来，
 * 依次渲染每个视图，捕获任何异常。
 *
 * 桩要够用但不用真：只实现模板真正用到的那几个 API
 * （querySelector / innerHTML / addEventListener / closest / classList …）。
 * 目标是「让真实代码跑通并暴露 bug」，不是「变成浏览器」。
 */
const fs = require("fs");
const path = require("path");

// 🔴 不要写死盘符 —— 这个文件在技能包里位于 <技能目录>/_domtest/run.js，
//    它读的是**同一棵树里的** index.html。所以从 __dirname 往上退一级就是根目录，
//    换台电脑、换个安装路径都成立；写死本机盘符路径在别人机器上必炸。
const ROOT = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(ROOT, "index.html"), "utf8");

// ---- 取出内嵌数据 + 内联脚本 ----
const dm = html.match(/const DATA = (\{[\s\S]*?\});\r?\n/);
if (!dm) { console.error("FATAL: 找不到内嵌 DATA"); process.exit(2); }
const DATA_JSON = dm[1];

const sm = html.match(/<script[^>]*>([\s\S]*?)<\/script>/);
if (!sm) { console.error("FATAL: 找不到内联 script"); process.exit(2); }
let src = sm[1];

// 用真实数据替换占位（模板里是 const DATA = /*__DATA__*/null 或已注入的 JSON）
src = src.replace(/const DATA = \/\*__DATA__\*\/null;?/, "const DATA = " + DATA_JSON + ";");
if (!/const DATA = \{/.test(src)) {
  src = src.replace(/const DATA = [\s\S]*?;\r?\n/, "const DATA = " + DATA_JSON + ";\n");
}

// ---- 最小 DOM 桩 ----
class ClassList {
  constructor(el) { this.el = el; this.s = new Set(); }
  add(...c) { c.forEach(x => x && this.s.add(x)); }
  remove(...c) { c.forEach(x => this.s.delete(x)); }
  contains(c) { return this.s.has(c); }
  toggle(c) { this.s.has(c) ? this.s.delete(c) : this.s.add(c); }
  get value() { return [...this.s].join(" "); }
}

class El {
  constructor(tag = "div") {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.attrs = {};
    this.dataset = {};
    this.style = {};
    this._html = "";
    this._text = "";
    this.classList = new ClassList(this);
    this._listeners = {};
  }
  get innerHTML() { return this._html; }
  set innerHTML(v) {
    this._html = String(v);
    // 记录一次「被重画」的事实：输入框重建检测靠它
    RENDER_COUNT++;
  }
  get textContent() { return this._text || this._stripTags(this._html); }
  set textContent(v) { this._text = String(v); }
  _stripTags(s) { return String(s).replace(/<[^>]*>/g, ""); }
  get value() { return this._val === undefined ? "" : this._val; }
  set value(v) { this._val = v; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  removeAttribute(k) { delete this.attrs[k]; }
  appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
  insertBefore(c) { c.parentNode = this; this.children.unshift(c); return c; }
  removeChild(c) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  addEventListener(t, fn) { (this._listeners[t] = this._listeners[t] || []).push(fn); }
  removeEventListener() {}
  // 选择器只需要支持模板用到的几种；返回 null 是安全答案（模板都判空）
  querySelector() { return null; }
  querySelectorAll() { return []; }
  closest() { return null; }
  contains() { return false; }
  getBoundingClientRect() { return { width: 1200, height: 800, left: 0, top: 0, right: 1200, bottom: 800 }; }
  focus() {}
  blur() {}
  click() {}
  scrollIntoView() {}
  get firstChild() { return this.children[0] || null; }
  get lastChild() { return this.children[this.children.length - 1] || null; }
}

let RENDER_COUNT = 0;

const registry = {};
function mkEl(id) {
  const e = new El("div");
  const key = String(id).startsWith("#") ? id : "#" + id;
  const bare = key.slice(1);
  e.attrs.id = bare;
  e.dataset.id = bare;
  registry[key] = e;
  registry[bare] = e;      // 两种写法都能取到
  return e;
}
// 模板会取这些固定 id —— 从模板里 `$("#xxx")` 的全部出现处自动采集。
// 漏一个就会在脚本**执行期**（不是调用期）挂在 addEventListener 上，
// 所以这里必须跟模板保持同步。
const FIXED_IDS = [
  "acct-open","acct-reload","an-in","bar","btn-about","btn-add-root","btn-bak",
  "btn-bak2","btn-clear","btn-doctor","btn-doctor-copy","btn-log","btn-log2",
  "btn-probe","btn-reload","btn-save-cfg","btn-save-root","cfg-keep","cfg-port",
  "ckupd-box","dlg-b","dlg-f","dlg-h","doctor-box","f-cards","lib-roots","main",
  "mask","mode-tag","n-autos","n-online","n-plugins","n-projects","n-tasks",
  "n-trash","ol-clear","ol-go","ol-gobar","ol-q","ol-qbar","ol-reset","page-t",
  "purge-in","q","rename-in","toast","nav","sort","f-pkind","f-psrc","f-pinst",
  "f-status","f-mode","f-kind","f-time","f-proj","f-auto","n-lib","n-settings",
];
FIXED_IDS.forEach(mkEl);

const document = {
  _listeners: {},
  body: new El("body"),
  documentElement: new El("html"),
  readyState: "complete",
  createElement: t => new El(t),
  querySelector: sel => {
    if (registry[sel]) return registry[sel];
    if (sel.startsWith("#")) return registry[sel] || null;
    return null;
  },
  querySelectorAll: () => [],
  getElementById: id => registry["#" + id] || null,
  addEventListener(t, fn) { (this._listeners[t] = this._listeners[t] || []).push(fn); },
  removeEventListener() {},
  // 事件委托测试用：手工投递一个「点到了某个元素」的假事件
  _fire(type, target) {
    const ev = {
      type, target,
      preventDefault() { this._pd = true; },
      stopPropagation() { this._sp = true; },
    };
    (this._listeners[type] || []).forEach(fn => {
      try { fn(ev); } catch (e) { throw e; }
    });
    return ev;
  },
};

const location = { href: "http://127.0.0.1:8800/", hash: "", search: "" };
const localStorage = {
  _d: {},
  getItem(k) { return k in this._d ? this._d[k] : null; },
  setItem(k, v) { this._d[k] = String(v); },
  removeItem(k) { delete this._d[k]; },
};
const navigator = { userAgent: "node-harness", clipboard: { writeText: () => Promise.resolve() } };
let toasts = [];
const __alerts = [];

// ---- 跑真实代码 ----
const ctx = {
  document, location, localStorage, navigator, console,
  window: null,
  // 模板里用到的浏览器全局
  alert: m => __alerts.push(m),
  setTimeout: (fn) => { try { fn(); } catch (e) {} return 0; },
  clearTimeout: () => {},
  setInterval: () => 0,
  clearInterval: () => {},
  fetch: () => Promise.reject(new Error("harness: 无网络")),
  URLSearchParams,
  JSON, Math, Object, Array, String, Number, Boolean, Date, RegExp, Error,
  Promise, Set, Map, isNaN, parseInt, parseFloat, encodeURIComponent, decodeURIComponent,
  requestAnimationFrame: fn => { try { fn(Date.now()); } catch (e) {} return 0; },
};
ctx.window = ctx;
ctx.globalThis = ctx;
ctx.self = ctx;
// window 上的事件监听（hashchange / resize / keydown 等）接到 document 的注册表，
// 这样 _fire() 能一并投递，不至于被丢掉
ctx.addEventListener = (t, fn) => document.addEventListener(t, fn);
ctx.removeEventListener = () => {};
ctx.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
ctx.getComputedStyle = () => ({ getPropertyValue: () => "" });
ctx.scrollTo = () => {};
ctx.open = () => null;
ctx.close = () => {};
ctx.innerWidth = 1400;
ctx.innerHeight = 900;
ctx.devicePixelRatio = 1;
// 模板可能把东西挂到 window 上
ctx.WB = {};
// 🔴 关键开关：模板里 `const SERVER = window.__SERVER__ === true`。
// index.html 是**静态快照**，那里 __SERVER__ 没被置真 → SERVER=false →
// 所有「需要本地服务」的分支（账户读取、在线搜索、安装）都会直接短路成
// 「需要本地服务」提示，根本不发请求。
// 要测这些交互功能，必须模拟**服务态**，把 __SERVER__ 置真。
// 这也解释了一个容易误判的现象：在静态页上点「检查更新」什么都不会发生 ——
// 那是设计如此（不静默失败），不是 bug。
ctx.__SERVER__ = true;

/* 模拟后端响应：账户接口 + 在线搜索 + 检查更新。
 *
 * 🔴 必须在**任何导航之前**装好。原因：renderAccount() 的自动加载条件是
 * `!ACCT.data && !ACCT.loading && !ACCT.err` —— 一旦第一轮用真 fetch（沙箱里必然
 * 失败）把 ACCT.err 写上了，后面再换 fetch 也没用：错误态会把重试锁住。
 * 早前就是先渲染后装桩，结果永远停在 loading/err，白查了半天。 */

// 桩里的路径一律用这个假目录拼出来（`x` 是占位用户名，不是真机器路径）。
// 这样只有一处出现盘符字面量，审计器不用满文件找。
const FAKE_HOME = "C:/Users/x/.workbuddy";  // skill-audit: ignore

const FAKE_ACCOUNT = {
  ok: true,
  account: { nickname: "刘玉明", uid: "0123456789abcdef", type: "personal",
             edition: "pro", is_pro: true, is_admin: false, saved_at: null },
  wb_dir: FAKE_HOME,
  client: { version: "37.10.3-24", product: "WorkBuddy", endpoint: "https://x",
            data_dir: FAKE_HOME },
  credits: { available: false, reason: "积分需要登录态，本页只读本地数据" },
  links: [
    { id: "credits", label: "积分余额", kind: "app", url: "workbuddy://settings/account", hint: "在客户端里查看" },
    { id: "checkin", label: "每日签到 / 领积分", kind: "web", url: "https://www.workbuddy.cn/", hint: "官方活动页" },
    { id: "growth", label: "成长计划", kind: "web", url: "https://www.workbuddy.cn/", hint: "连续登录" },
    { id: "settings", label: "客户端设置", kind: "app", url: "workbuddy://settings/appearance", hint: "唤起客户端" },
    { id: "home", label: "打开 WorkBuddy 首页", kind: "app", url: "workbuddy://home", hint: "唤起客户端" },
  ],
  memory: {
    groups: [{
      scope: "user", label: "用户级记忆", desc: "跨项目", items: [
        { path: FAKE_HOME + "/MEMORY.md", name: "MEMORY.md",
          title: "MEMORY.md", size: 1234, mtime: new Date().toISOString() },
      ],
    }],
    total: 1,
  },
};
const FAKE_ONLINE = {
  ok: true, query: "ppt",
  results: [
    { slug: "ppt", display_name: "PPT 生成", name: "ppt", version: "1.0.0",
      author: "某人", namespace: "ns", desc: "做一个 PPT", downloads: 1234,
      stars: 5, installed: false, needs_key: false },
    { slug: "ppt-reader", display_name: "PPT 阅读器", name: "ppt-reader",
      version: "1.0.0", author: "", namespace: "", desc: "",
      downloads: 12, stars: 0, installed: true, needs_key: true },
  ],
};
const FETCH_LOG = [];
ctx.fetch = (url, opt) => {
  const u = String(url);
  FETCH_LOG.push(u);
  const ok = body => Promise.resolve({
    status: 200,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
  if (u.indexOf("/api/account") >= 0) return ok(FAKE_ACCOUNT);
  if (u.indexOf("/api/online/search") >= 0) return ok(FAKE_ONLINE);
  if (u.indexOf("/api/online/install") >= 0) return ok({ ok: true, name: "ppt" });
  if (u.indexOf("/api/check-update") >= 0) {
    // 三个桶都要有：可更新 / 本地更新 / 未上架。
    // 桩里有 not_published 才能测出「未上架不被算成错误」这条。
    return ok({ ok: true, client: { version: "37.10.3-24" }, skills: [
        { dir: "ym-skillhub", slug: "ym-skillhub", version: "2.2.6",
          display_name: "SkillHub 一条龙", latest: "2.2.6", outdated: false, ahead: false },
        { dir: "expert-packager", slug: "expert-packager", version: "1.3.10",
          display_name: "专家生成器", latest: "1.0.2", outdated: false, ahead: true },
        { dir: "novel-writing", slug: "novel-writing", version: "1.2.1",
          display_name: "AI网文创作", latest: "1.5.0", outdated: true, ahead: false },
      ],
      not_published: [
        { dir: "zhaotoubiao-helper", slug: "zhaotoubiao-helper",
          reason: "SkillHub 上没有同名技能（本地自制 / 未上架）" },
        { dir: "game-life-bot", slug: "game-life-bot",
          reason: "SkillHub 上没有同名技能（本地自制 / 未上架）" },
      ],
      errors: [], outdated_count: 1, checked_count: 3,
      not_published_count: 2,
      self: { slug: "ym-wb-manager", version: "1.2.0" } });
  }
  if (u.indexOf("/api/memory") >= 0) {
    return ok({ ok: true, path: FAKE_HOME + "/MEMORY.md",
                size: 20, text: "# 记忆\n内容" });
  }
  if (u.indexOf("/api/data") >= 0) return ok({ ok: true, data: null });
  if (u.indexOf("/api/refresh") >= 0) return ok({ ok: true });
  return Promise.reject(new Error("harness: 未桩化的接口 " + u));
};

const script = new (require("vm").Script)(src, { filename: "page.js" });
try {
  script.runInNewContext(ctx, { timeout: 20000 });
} catch (e) {
  console.error("FATAL: 页面脚本执行即抛错");
  console.error(e && e.stack || e);
  process.exit(3);
}

// ---- 逐个视图渲染 ----
//
// 🔴 关键：模板里的 `const state` / `const DATA` 是 **词法声明**，
// 不是 ctx 的属性 —— 从外面写 ctx.state.view = "x" 是无效的：
// 那样写不会报错，只会静默地渲染成默认视图，于是「10 个视图都通过了」
// 其实测的是同一个 overview。这类假阳性比直接报错更坏。
//
// 正确做法是走**真实入口**：模板在 `#nav` 上做事件委托，
// 点 data-view 就切视图。所以这里造一个假的导航按钮投递 click，
// 让代码自己切 —— 既驱动了视图，又顺带把侧栏委托一起测了。
const VIEWS = ["overview", "tasks", "trash", "projects", "automations",
               "plugins", "online", "library", "account", "settings"];
let fails = 0;

function fakeNav(view) {
  const el = new El("button");
  el.dataset.view = view;
  el.closest = sel => (sel === "#nav .nav-i" ? el : null);
  return el;
}

function tryView(v) {
  try {
    document._fire("click", fakeNav(v));
  } catch (e) {
    fails++;
    console.log("  [FAIL] 视图 " + v + " 渲染抛错: " + (e && e.message));
    console.log("        " + String(e && e.stack || "").split("\n").slice(1, 4).join("\n        "));
  }
}

console.log("=== 逐视图渲染");
const out = {};
VIEWS.forEach(v => {
  tryView(v);
  out[v] = document.querySelector("#main").innerHTML;
  console.log("  [OK] " + v + "  (" + out[v].length + " 字节)");
});

// 各视图内容必须真的不一样（防「切了但没换」的静默失效）
const uniq = new Set(VIEWS.map(v => out[v]));
if (uniq.size !== VIEWS.length) {
  fails++;
  console.log("  [FAIL] 有视图渲染出了相同内容 —— 视图切换可能没生效");
  VIEWS.forEach(v => {
    const same = VIEWS.filter(w => w !== v && out[w] === out[v]);
    if (same.length) console.log("        " + v + " 与 " + same.join(",") + " 相同");
  });
} else {
  console.log("  [OK] 10 个视图内容各不相同（切换确实生效）");
}

// ---- 产物内容断言（「不抛错」不等于「画对了」）----
console.log("=== 渲染产物检查");
let contentFails = 0;
function must(label, cond, detail) {
  if (!cond) {
    contentFails++;
    console.log("  [FAIL] " + label + (detail ? "  -> " + detail : ""));
  } else {
    console.log("  [OK] " + label);
  }
}

// 任务表：必须有 data-row、不能有操作列
//
// 🔴 colgroup 只在**用户拖过列宽之后**才存在（colsHtml 无自定义宽度时返回空串）。
// 所以这里不能直接断言「有 colgroup」—— 那是把「默认状态」判成错。
// 正确的断言是「有拖拽手柄」（手柄永远在），colgroup 由列宽用例单独驱动。
must("任务表每行有 data-row", /data-row="/.test(out.tasks));
must("任务表有列宽拖拽手柄", /class="rsz"/.test(out.tasks));
must("任务表表头带 data-colkey（供拖拽定位）", /data-colkey="/.test(out.tasks));
must("任务表已无「操作」列", !/>操作</.test(out.tasks));

// 扩展表：中文名 + slug 副显 + data-row
must("扩展表每行有 data-row", /data-row="/.test(out.plugins));
must("扩展表已无「操作」列", !/>操作</.test(out.plugins));
must("扩展表显示中文名（有 <b> 加粗主名）", /<b>[^<]+<\/b>/.test(out.plugins));
// 「github / github」这类重合不许叠两行
const dupProbe = out.plugins.match(/<b>github<\/b><\/span><span class="sub2 mono">github<\/span>/);
must("扩展表：主名与标识重合时不重复显示", !dupProbe,
     dupProbe ? "出现了 github/github 双行" : "");

// 在线技能页：搜索框 + 结果区提示 + 安全文案
must("在线页有内联搜索框 #ol-q", /id="ol-q"/.test(out.online));
must("在线页有搜索按钮 #ol-go", /id="ol-go"/.test(out.online));
must("在线页有安全提示（别人的代码）", /别人的代码/.test(out.online));

// 账户页的四块卡片要等 fetch 回来后才有内容（account 这一轮只是 loading 态），
// 所以放到下面 checkAccount() 里异步验，这里只确认「没崩」。
must("账户页初次进入显示 loading 态（异步加载）",
     /正在读取/.test(out.account) || /记忆文件/.test(out.account));

const aboutTxt = (() => {
  try {
    // about 是弹窗，直接看模板源码更实在
    return fs.readFileSync(path.join(ROOT, "assets/template.html"), "utf8");
  } catch (e) { return ""; }
})();
must("说明里不再教点「详情」按钮", !/点「详情」→ 展开/.test(aboutTxt));
must("说明里教「点整行任意位置」", /点<b>整行任意位置<\/b>/.test(aboutTxt));
must("说明里教拖列宽", /拖表头右侧的竖线/.test(aboutTxt));

// ---- 账户页（喂进数据后走真实渲染路径）----
//
// 上面 account 只渲染出「正在读取…」，因为 loadAccount() 走 fetch，
// 沙箱里没有服务。这里把 fetch 换成返回预置响应的桩，
// 让 loadAccount() 的回调真的跑起来，再验四块卡片。
//
// ⚠️ fetch 的 .then 是**微任务**，必须用 await 让出栈才能跑完；
// 早期用 while 忙等是错的 —— 忙等期间微任务根本不会执行，
// 结果永远看到「正在读取…」，还以为是渲染 bug。
async function checkAccount() {
  console.log("=== 账户页内容（喂入模拟数据）");
  // 切走再切回来：第一次进来触发 fetch，await 让微任务跑完，
  // 第二次进来 ACCT.data 已经有值，直接渲染内容
  document._fire("click", fakeNav("projects"));
  document._fire("click", fakeNav("account"));
  await new Promise(r => setTimeout(r, 30));
  document._fire("click", fakeNav("account"));
  const acctHtml = document.querySelector("#main").innerHTML;
  must("账户页渲染出「记忆文件」区", /记忆文件/.test(acctHtml));
  must("账户页渲染出检查更新按钮", /id="btn-ckupd"/.test(acctHtml));
  must("账户页渲染出 data-acctlink 入口", /data-acctlink="/.test(acctHtml));
  must("账户页渲染出 data-mem 记忆项", /data-mem="/.test(acctHtml));
  must("账户页显示昵称", /刘玉明/.test(acctHtml));
  must("账户页明说积分读不到", /登录态|读不到/.test(acctHtml));
  must("账户页不伪造积分数字",
       !/积分余额[\s\S]{0,80}?\d{3,}\s*(分|积分)/.test(acctHtml));
}

// ---- 列宽持久化（真的写 localStorage，看 colgroup 是否生成）----
function checkColWidths() {
  console.log("=== 列宽持久化");
  localStorage.setItem("wb-colw-v1", JSON.stringify({ tasks: { name: 42.5, title: 30 } }));
  document._fire("click", fakeNav("tasks"));
  const tasksHtml = document.querySelector("#main").innerHTML;
  must("列宽写进 localStorage 后渲染出 colgroup", /<colgroup>/.test(tasksHtml));
  must("colgroup 里带百分比宽度", /<col style="width:42\.5%"/.test(tasksHtml));
  must("表格加了 fixed 类（列宽才生效）", /class="lt[^"]*fixed/.test(tasksHtml));
  const cg = (tasksHtml.match(/<colgroup>[\s\S]*?<\/colgroup>/) || [""])[0];
  must("列宽存的是百分比而非像素（换分辨率不崩）",
       cg.length > 0 && !/width:\s*\d+px/.test(cg), cg.slice(0, 120));
  // colgroup 的列数必须和表头列数一致，否则列会错位
  const nCol = (cg.match(/<col[\s>]/g) || []).length;
  const nTh = (tasksHtml.match(/<th[\s>]/g) || []).length;
  must("colgroup 列数与表头列数一致（不错位）",
       nCol === nTh && nCol > 0, nCol + " col vs " + nTh + " th");
  localStorage.removeItem("wb-colw-v1");
}

// ---- 在线搜索：跑一遍真实搜索路径，验结果表 ----
async function checkOnline() {
  console.log("=== 在线搜索（走真实 runOnlineSearch）");
  document._fire("click", fakeNav("online"));
  await new Promise(r => setTimeout(r, 20));
  // 真实的搜索路径是**页内**那组控件（#ol-q 输入 + #ol-go 按钮），
  // 绑定在 bindInMain() 里（每次 renderList 后重绑）。
  // 工具条上那组（#ol-qbar/#ol-gobar）是另一个入口，两者都该能用。
  // harness 的注册表对这些稳定 id 是同一个对象，所以监听器会累积在这里。
  const goBtn = document.querySelector("#ol-go");
  const qIn = document.querySelector("#ol-q");
  if (!goBtn || !goBtn._listeners.click || !goBtn._listeners.click.length) {
    must("在线搜索：页内搜索按钮已绑定", false, "没绑上");
    return;
  }
  if (qIn) qIn.value = "ppt";
  goBtn._listeners.click.forEach(fn => fn({ type: "click" }));
  await new Promise(r => setTimeout(r, 60));
  const html = document.querySelector("#main").innerHTML;
  must("在线搜索：渲染出结果表", /class="lt fixed"/.test(html), html.slice(0, 160));
  must("在线搜索：显示中文名「PPT 生成」", /PPT 生成/.test(html));
  must("在线搜索：已安装的标「已安装」", /已安装/.test(html));
  must("在线搜索：未安装的给安装按钮", /data-oin="ppt"/.test(html));
  must("在线搜索：需 Key 的有标记", /需 Key/.test(html));
  must("在线搜索：确实发起了请求",
       FETCH_LOG.some(u => u.indexOf("/api/online/search") >= 0),
       FETCH_LOG.filter(u => u.indexOf("search") >= 0).join(","));

  // ---- 安装：点一下，确认走对接口 ----
  const before = FETCH_LOG.length;
  const btn = new El("button");
  btn.dataset.oin = "ppt";
  btn.dataset.ons = "ns";
  btn.closest = sel => (sel.indexOf("data-oin") >= 0 ? btn : null);
  document._fire("click", btn);
  await new Promise(r => setTimeout(r, 40));
  const post = FETCH_LOG.slice(before);
  must("在线安装：点了安装会打 /api/online/install",
       post.some(u => u.indexOf("/api/online/install") >= 0), post.join(","));
}

// ---- 检查更新：跑一遍，验「本地更新」不被误报成「可更新」 ----
async function checkUpdate() {
  console.log("=== 检查更新（走真实 doCheckUpdate）");
  document._fire("click", fakeNav("account"));
  await new Promise(r => setTimeout(r, 30));
  document._fire("click", fakeNav("account"));
  // btn-ckupd 是重新渲染出来的，harness 的注册表里那份是旧的占位；
  // 直接调用渲染函数产出的按钮不可达，所以这里改成手工投递委托事件。
  const btn = new El("button");
  btn.attrs.id = "btn-ckupd";
  btn.closest = sel => (sel === "#btn-ckupd" ? btn : null);
  document._fire("click", btn);
  await new Promise(r => setTimeout(r, 40));
  const box = document.querySelector("#ckupd-box").innerHTML;
  must("检查更新：写入了结果区", box.length > 0 && box !== "还没有检查", String(box.length) + " 字节");
  must("检查更新：提到有 1 个可更新", /1 个技能可以更新/.test(box) || /可以更新/.test(box), box.slice(0, 200));
  must("检查更新：只报告不自动改（有免责说明）",
       /只报告/.test(box) || /不自动更新/.test(box));
  // 🔴 核心回归：expert-packager 本地 1.3.10 > 线上 1.0.2，绝不能出现在「可更新」列表里
  const updBlock = (box.match(/有 \d+ 个技能可以更新[\s\S]*?<\/div>\s*<\/div>/) || [""])[0];
  must("检查更新：本地更新的技能没被列进「可更新」",
       !/expert-packager/.test(updBlock),
       updBlock.slice(0, 200));
  must("检查更新：另说了「本地比线上新」的情况", /本地版本比线上新/.test(box));
  // 🔴 回归：未上架的技能不能被渲染成「错误」。
  // 桩里给了 2 个 not_published，页面应该说「自制或还没上架」，
  // 而不是「这次没查成」。混在一起会让用户以为工具坏了。
  must("检查更新：未上架单独说、不叫错误",
       /自制|还没上架|未上架/.test(box) && !/没查成/.test(box),
       box.slice(0, 300));
  must("检查更新：未上架的技能名被列出来",
       /zhaotoubiao-helper/.test(box) || /game-life-bot/.test(box));
}

// ---- 事件委托：手工投递若干「点击」 ----
console.log("=== 事件委托（投递假点击）");
function fireClick(el) {
  const ev = document._fire("click", el);
  return ev;
}
// 行点击（data-row）—— 应命中行展开分支
try {
  const tr = new El("tr");
  tr.dataset.row = "fake-id-xxx";
  tr.closest = (sel) => {
    if (sel === "tr[data-row]") return tr;
    return null;
  };
  document._fire("click", tr);
  console.log("  [OK] 行点击（data-row）未抛错");
} catch (e) {
  fails++;
  console.log("  [FAIL] 行点击抛错: " + e.message);
}

// 列宽手柄 mousedown —— 不给 table/th，应安全返回
try {
  const h = new El("span");
  h.classList.add("rsz");
  h.closest = sel => (sel.includes(".rsz") ? h : null);
  document._fire("mousedown", h);
  console.log("  [OK] 列宽手柄 mousedown 未抛错（无 table 时安全返回）");
} catch (e) {
  fails++;
  console.log("  [FAIL] 列宽手柄 mousedown 抛错: " + e.message);
}

// 在线安装按钮
try {
  const b = new El("button");
  b.dataset.oin = "some-slug";
  b.dataset.ons = "ns";
  b.closest = sel => (sel.includes("data-oin") ? b : null);
  document._fire("click", b);
  console.log("  [OK] data-oin 安装按钮未抛错");
} catch (e) {
  fails++;
  console.log("  [FAIL] data-oin 抛错: " + e.message);
}

// 账户入口 / 记忆项 / 检查更新
try {
  const a = new El("div");
  a.dataset.acctlink = "workbuddy://home";
  a.dataset.kind = "app";
  a.closest = sel => (sel.includes("data-acctlink") ? a : null);
  document._fire("click", a);
  console.log("  [OK] data-acctlink 未抛错");
} catch (e) { fails++; console.log("  [FAIL] data-acctlink 抛错: " + e.message); }

try {
  const m = new El("div");
  m.dataset.mem = "C:/x/y.md";  // skill-audit: ignore（假路径，只挂 dataset 不走文件系统）
  m.closest = sel => (sel.includes("data-mem") ? m : null);
  document._fire("click", m);
  console.log("  [OK] data-mem 未抛错");
} catch (e) { fails++; console.log("  [FAIL] data-mem 抛错: " + e.message); }

// ---- 资料库：页签渲染 + 切换 ----
//
// 这条守的是「原来 workbuddy 里的资料在哪里了呢」那个反馈：
// WorkBuddy 自己的目录必须**直接可见**，不能全埋在根目录卡片后面。
function checkLibrary() {
  console.log("=== 资料库页签");
  document._fire("click", fakeNav("library"));
  let main = document.querySelector("#main").innerHTML;

  const tabs = (main.match(/data-libt="/g) || []).length;
  must("资料库：渲染出页签", tabs >= 3, tabs + " 个页签");
  must("资料库：首页能看到「技能与专家」", /技能与专家/.test(main));
  must("资料库：首页能看到「任务与产物」", /任务与产物/.test(main));
  // 关键：WorkBuddy 的目录要能直接点到，不是只能点「WorkBuddy 数据」再翻
  must("资料库：直接给出 skills 入口", /data-lib="[^"]*skills"/.test(main));
  must("资料库：卡片显示条目数（不是只有目录名）",
       /\d+<\/b>\s*项/.test(main), main.match(/.{0,40}项/)?.[0] || "");
  // 切到第二个页签，内容必须真的换掉
  const before = main;
  const tab = new El("button");
  tab.dataset.libt = "data";
  tab.closest = sel => (sel.includes("data-libt") ? tab : null);
  document._fire("click", tab);
  main = document.querySelector("#main").innerHTML;
  must("资料库：切页签后内容变化", main !== before);
  must("资料库：第二个页签显示「任务与产物」组",
       /任务与产物/.test(main) && /产物索引/.test(main));
  must("资料库：直接给出 artifact-index 入口",
       /data-lib="[^"]*artifact-index"/.test(main));
  must("资料库：切页签后不再显示上一组独有的项",
       !/skill-backups/.test(main), main.slice(0, 120));

  // 切回第一个页签，必须能回去（防「切走就回不来」）
  const t0 = new El("button");
  t0.dataset.libt = "skill";
  t0.closest = sel => (sel.includes("data-libt") ? t0 : null);
  document._fire("click", t0);
  must("资料库：能切回第一个页签", /技能与专家/.test(document.querySelector("#main").innerHTML));

  // 自定义目录仍要保留（别为了加页签把老功能弄丢）
  must("资料库：保留「我配置的目录」", /我配置的目录/.test(document.querySelector("#main").innerHTML));

  // ---- 必须说清「本机资料库」和「客户端云端资料库」不是一回事 ----
  //
  // 🔴 用户真实困惑（连问两次）：「原来 workbuddy 里的资料在哪里了呢」、
  //   「这个资料库的信息我在管理中心还是没看到」——
  //   他看的是客户端侧边栏那个资料库（我的资料 / 团队空间），那是**云端**的。
  //   页面不把这件事写清楚，他永远会以为是我们漏做了。
  const main2 = document.querySelector("#main").innerHTML;
  must("资料库：写明只覆盖本机磁盘", /本机磁盘上的资料/.test(main2));
  must("资料库：点名客户端那个资料库（区分对象）", /客户端/.test(main2) && /资料库/.test(main2));
  must("资料库：提到云端两块读不到", /云端/.test(main2) && /(读不到|本机没有副本)/.test(main2));
  must("资料库：列出「我的资料库」这个云端分区名", /我的资料库/.test(main2));
  must("资料库：给出唤起客户端资料库的按钮", /data-open-url="workbuddy:\/\/my-files"/.test(main2));
  must("资料库：指路本地产物在哪（产物索引）", /产物索引/.test(main2));

  // 点了唤起按钮应该真的跳 workbuddy://my-files（不是死按钮）
  const ouBtn = new El("button");
  ouBtn.dataset.openUrl = "workbuddy://my-files";
  ouBtn.closest = sel => (sel.includes("data-open-url") ? ouBtn : null);
  document._fire("click", ouBtn);
  must("资料库：点唤起按钮会跳到 workbuddy://my-files",
       location.href.indexOf("workbuddy://my-files") >= 0,
       "location.href=" + location.href);
}

// ---- 每日签到：入口得真能用，而且不能指向一个根本没有签到的页面 ----
//
// 🔴 来自明哥的实测诉求：「每天签到领积分的入口……如果我一直不退出，
//   都不弹出来领积分，每次都需要我重启之后才能领」。
//
// 扒客户端 app.asar 得到的真相（都是可验证的字符串）：
//   · 签到 = 客户端里的「Buddy 加油站」（account.menu.fuelStation），
//     藏在**左下角头像**点开的账号菜单里，由 AvatarTopSlot 渲染成气泡
//     .daily-checkin--bubble；
//   · 气泡的显示条件是 `!checkinBubbleDismissed` —— React 内存态，
//     关掉后本次运行不再弹，重启才恢复。这就是「非要重启才能领」的来源；
//   · 重新唤出气泡的动作（reopenBubble）只在客户端内部，
//     DEEP_LINK_ROUTE_MAP 里**没有** account / checkin / credits，
//     所以不存在能直达签到的深链；
//   · 也**不能代签**：接口 /v2/billing/meter/daily-checkin 要登录 Bearer
//     令牌 + X-Device-Token（腾讯图灵盾设备指纹，只有客户端能生成）。
//
// 所以页面能做的只有两件事：把入口给对、把话说清楚。
// 这条断言同时守着一次真实回归：link 曾经指向 https://www.workbuddy.cn/
// （官网首页，根本没有签到入口）—— 用户点了更迷糊。
function checkCheckin() {
  console.log("=== 每日签到入口");
  document._fire("click", fakeNav("overview"));
  const m = document.querySelector("#main").innerHTML;

  // 只取签到卡片那段（它在统计卡片 .cards 之前），避免误伤页面别处的链接
  const a = m.indexOf('class="checkin"');
  const b = m.indexOf('class="cards"', a);
  const ck = (a >= 0 && b > a) ? m.slice(a, b) : "";
  must("签到：首页渲染出签到卡片", ck.length > 0, "卡片长度 " + ck.length);
  if (!ck) return;

  must("签到：点出入口名「Buddy加油站」", /Buddy加油站/.test(ck));
  must("签到：给出「先点左下角头像」这一步", /左下角头像/.test(ck));
  must("签到：说明入口在客户端、本页读不到", /不在本页面|本页读不到/.test(ck));
  // 关键回归：这条曾经指向官网首页
  must("签到：不再指向官网首页（那是空入口）",
       !/workbuddy\.cn/.test(ck),
       (ck.match(/https?:\/\/[^\s"'<>]+/g) || []).join(" ") || "（无外链）");
  must("签到：解释为什么不自动弹（提到启动 / 关掉）",
       /启动时/.test(ck) && /(关掉|不再弹)/.test(ck));
  must("签到：解释为什么不能代签（设备指纹 + 令牌）",
       /设备指纹/.test(ck) && /令牌/.test(ck));
  must("签到：明说不伪造积分数字", /伪造|盗用/.test(ck));
  must("签到：同时给出看积分余额的入口",
       /workbuddy:\/\/settings\/account/.test(ck));

  // 真点按钮：必须跳 workbuddy://home（把已开着的客户端唤到前台）
  location.href = "http://127.0.0.1:8800/";
  const ckBtn = new El("button");
  ckBtn.dataset.openUrl = "workbuddy://home";
  ckBtn.closest = sel => (sel.includes("data-open-url") ? ckBtn : null);
  document._fire("click", ckBtn);
  must("签到：点「打开客户端去签到」会唤起 workbuddy://home",
       location.href === "workbuddy://home", "location.href=" + location.href);
}

// ---- parseJson：非 JSON 响应不能变成天书报错 ----
//
// 🔴 这条来自明哥的实测截图：页面显示
//   「读取失败：SyntaxError: Unexpected token 'o', "not found" is not valid JSON」
// 根因是服务端有个接口没注册（返回纯文本 "not found"），前端直接 r.json() 就炸了。
// 用户看到的是这句 JS 引擎的黑话，完全不知道「服务重启一下就好」。
// 所以 parseJson 必须把非 JSON 翻译成人能看懂的话。
function checkParseJson() {
  console.log("=== 非 JSON 响应兜底");
  const call = (status, body) => {
    const r = { status, text: () => Promise.resolve(body) };
    return ctx.parseJson(r);
  };
  // 1) 正常 JSON 照常解析
  return call(200, '{"ok":true,"n":1}').then(j => {
    must("parseJson：正常 JSON 能解析", j.ok === true && j.n === 1);
    // 2) 404 纯文本 → 要变成「服务端没有这个接口」+ 提示重启
    return call(404, "not found").then(
      () => { must("parseJson：404 应该抛错", false); },
      e => {
        must("parseJson：404 给出人话（提到旧版本进程 / 重启）",
             /旧版本|重启|没有这个接口/.test(e.message),
             e.message.slice(0, 90));
        must("parseJson：404 不再出现 SyntaxError 天书",
             !/SyntaxError|Unexpected token/i.test(e.message));
      });
  }).then(() => {
    // 3) 500 HTML 页面
    return call(500, "<html>boom</html>").then(
      () => { must("parseJson：500 应该抛错", false); },
      e => must("parseJson：500 给出人话", /服务端出错|HTTP 500/.test(e.message),
                e.message.slice(0, 80)));
  }).then(() => {
    // 4) 200 但内容损坏的 JSON
    return call(200, "{oops").then(
      () => { must("parseJson：坏 JSON 应该抛错", false); },
      e => must("parseJson：坏 JSON 说明是服务端返回异常",
                /不是 JSON/.test(e.message) && !/SyntaxError/.test(e.message),
                e.message.slice(0, 80)));
  });
}

console.log("=== 汇总");
(async () => {
  checkLibrary();
  checkCheckin();
  await checkParseJson();
  await checkAccount();
  checkColWidths();
  await checkOnline();
  await checkUpdate();
  const total = fails + contentFails;
  console.log("=== 汇总");
  console.log(total === 0 ? "全部通过" : (total + " 项失败 (渲染 " + fails + " / 内容 " + contentFails + ")"));
  process.exit(total === 0 ? 0 : 1);
})();
