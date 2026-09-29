// 画面の骨組み。docs/TASKS.md T15。
// ナビゲーション（タブ・サイドバー・ナビゲーションバー）、ログイン・二段階認証・利用規約の同意、
// 表示設定（テーマ・背景）を組み立てる。各画面の中身は T16 で実装する。
import { api, setCsrfToken } from "./api.js";
import { navigate, onLocationChange, parseLocation } from "./router.js";
import { ic } from "./components/icons.js";
import { esc, cell, group } from "./components/cell.js";
import { toast } from "./components/toast.js";
import { sheetHead, openSheet, closeSheet, isSheetOpen } from "./components/sheet.js";

const TABS = [
  { id: "servers", label: "サーバー", icon: "server" },
  { id: "monitor", label: "監視", icon: "pulse" },
  { id: "admin", label: "管理", icon: "shield", admin: true },
  { id: "settings", label: "設定", icon: "gear" },
];
const TAB_TITLES = { servers: "サーバー", monitor: "監視", admin: "管理", settings: "設定" };
const TITLES = {
  trash: "ゴミ箱",
  plugins: "プラグイン",
  network: "ネットワーク",
  backups: "バックアップ",
  share: "共有",
  address: "アドレスとドメイン",
  behavior: "動作",
  domains: "ドメイン",
  domain: "ドメイン",
  ips: "IP と紐付け",
  rules: "アドレス枠",
  rule: "アドレス枠",
  users: "ユーザー",
  roles: "ロール連携",
  announce: "お知らせ",
  violations: "違反対応",
  health: "整合性チェック",
  nodes: "ノードと edge",
  audit: "操作ログ",
  keys: "API キー",
  expiring: "期限が近い",
  system: "システムの状態",
  history: "操作履歴",
};
const LOGIN_ERRORS = {
  state: "確認用の情報が一致しませんでした。もう一度ログインしてください。",
  not_member: "対象の Discord サーバーに参加していません。",
  not_allowed: "利用できるロールがなく、招待もされていません。",
  email: "Discord のメールアドレスが確認されていません。",
  email_taken: "同じメールアドレスの別アカウントが既にあります。",
  suspended: "アカウントが利用停止中です。",
  discord: "Discord との通信に失敗しました。時間をおいて試してください。",
};
const DISPLAY_KEY = "hatch-display";
const BG_PRESETS = [
  { id: "none", name: "なし", light: "none", dark: "none" },
  {
    id: "aurora",
    name: "オーロラ",
    light: "radial-gradient(at 15% 10%,#c9b9ff 0,transparent 50%),radial-gradient(at 85% 15%,#a3f0d2 0,transparent 45%),radial-gradient(at 50% 95%,#a6ccff 0,transparent 55%),linear-gradient(#eef1f8,#eef1f8)",
    dark: "radial-gradient(at 15% 10%,#5b3fc4 0,transparent 50%),radial-gradient(at 85% 15%,#16825b 0,transparent 45%),radial-gradient(at 50% 95%,#0a4fb8 0,transparent 55%),linear-gradient(#0b0d14,#0b0d14)",
  },
  { id: "sunset", name: "夕焼け", light: "linear-gradient(160deg,#ffd6c7 0%,#ffbccd 45%,#cfbcff 100%)", dark: "linear-gradient(160deg,#6e2a35 0%,#521d49 45%,#1b1330 100%)" },
  { id: "ocean", name: "海", light: "linear-gradient(180deg,#e3f1ff 0%,#b1d5ff 60%,#8ab7f2 100%)", dark: "linear-gradient(180deg,#0d2742 0%,#0b3a68 60%,#06162a 100%)" },
  { id: "forest", name: "森", light: "linear-gradient(160deg,#ebf8da 0%,#c6e7a9 50%,#98ccb0 100%)", dark: "linear-gradient(160deg,#1b3718 0%,#153c2b 50%,#0a1f25 100%)" },
  { id: "blocks", name: "ブロック", light: "repeating-conic-gradient(#d3e8b6 0 25%,#c2de9e 0 50%) 0 0/56px 56px", dark: "repeating-conic-gradient(#2a3d21 0 25%,#324729 0 50%) 0 0/56px 56px" },
];

let ME = null;
let ROUTE = { tab: "servers", stack: [] };
let DISPLAY = loadDisplay();

function loadDisplay() {
  try {
    const v = JSON.parse(localStorage.getItem(DISPLAY_KEY) || "null");
    if (v && typeof v === "object") return { theme: v.theme || "", bg: v.bg || { kind: "none" } };
  } catch {
    /* 読み込めなければ既定値にする */
  }
  return { theme: "", bg: { kind: "none" } };
}
function saveDisplay() {
  try {
    localStorage.setItem(DISPLAY_KEY, JSON.stringify(DISPLAY));
  } catch {
    /* 保存できなくても動作は続ける（次回また既定値になるだけ） */
  }
}
const isDark = () => DISPLAY.theme === "dark" || (DISPLAY.theme === "" && matchMedia("(prefers-color-scheme: dark)").matches);
function applyDisplay() {
  const root = document.documentElement;
  root.dataset.theme = DISPLAY.theme;
  const preset = BG_PRESETS.find((p) => p.id === DISPLAY.bg.id) || BG_PRESETS[0];
  const css = isDark() ? preset.dark : preset.light;
  const on = css !== "none";
  root.classList.toggle("has-bg", on);
  root.style.setProperty("--bgimg", on ? css : "none");
  root.style.setProperty("--dim", on ? 0.25 : 0);
  const img = document.querySelector("#bg .img");
  if (img) img.style.background = on ? `${css} center/cover no-repeat` : "none";
}
matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", applyDisplay);

function isAdmin() {
  return !!ME && ME.user.role === "admin";
}
function navbar(title, { back, right = "" } = {}) {
  return `<div class="navbar" id="navbar"><div class="l">${
    back ? `<button type="button" class="back" data-act="back">${ic("back")}<span>${esc(back)}</span></button>` : ""
  }</div><div class="ttl">${esc(title)}</div><div class="r">${right}</div></div>`;
}
function pageTitle(entry) {
  if (entry.page === "server") return entry.arg;
  return TITLES[entry.page] || "";
}
function backLabel() {
  if (ROUTE.stack.length > 1) return pageTitle(ROUTE.stack[ROUTE.stack.length - 2]);
  return TAB_TITLES[ROUTE.tab];
}

/* ---------------- 描画 ---------------- */
function renderChrome() {
  const tabs = TABS.filter((t) => !t.admin || isAdmin());
  document.getElementById("tabbar").innerHTML = tabs
    .map(
      (t) =>
        `<button type="button" class="tb" data-act="tab" data-arg="${t.id}" ${ROUTE.tab === t.id ? 'aria-current="page"' : ""}>${ic(t.icon)}<span>${t.label}</span></button>`
    )
    .join("");
  const u = ME.user;
  document.getElementById("side").innerHTML = `<h1>Hatch</h1>
    <button type="button" class="searchbox search" data-act="search">${ic("search")}<span>検索</span><span class="kbd">/</span></button>
    ${tabs
      .map(
        (t) =>
          `<button type="button" class="sbtn" data-act="tab" data-arg="${t.id}" ${ROUTE.tab === t.id ? 'aria-current="page"' : ""}>${ic(t.icon)}<span>${t.label}</span></button>`
      )
      .join("")}
    <div class="foot"><span class="ico" style="background:var(--gray);border-radius:50%;font-weight:700;font-size:14px">${esc((u.username || "?")[0].toUpperCase())}</span><div><div>${esc(u.username)}</div><div style="font-size:13px;color:var(--label2)">${u.role === "admin" ? "管理者" : "利用者"}</div></div></div>`;
}
function onScroll() {
  const n = document.getElementById("navbar");
  if (n) n.classList.toggle("scrolled", window.scrollY > 30);
}
addEventListener("scroll", onScroll, { passive: true });

function renderMain() {
  const top = ROUTE.stack[ROUTE.stack.length - 1];
  let html;
  if (!top) {
    html = ROUTE.tab === "settings" ? pSettings() : `${navbar(TAB_TITLES[ROUTE.tab])}<div class="page"><h1 class="large">${esc(TAB_TITLES[ROUTE.tab])}</h1></div>`;
  } else {
    html = `${navbar(pageTitle(top), { back: backLabel() })}<div class="page"><h1 class="large">${esc(pageTitle(top))}</h1></div>`;
  }
  document.getElementById("main").innerHTML = html;
  onScroll();
}
function render() {
  if (ROUTE.tab === "admin" && !isAdmin()) {
    ROUTE = { tab: "servers", stack: [] };
    navigate(ROUTE.tab, ROUTE.stack, { replace: true });
  }
  renderChrome();
  renderMain();
}

/* ---------------- 設定（テーマ・背景） ---------------- */
function bgName(bg) {
  return (BG_PRESETS.find((p) => p.id === bg.id) || BG_PRESETS[0]).name;
}
function pSettings() {
  const u = ME.user;
  return `${navbar("設定")}<div class="page"><h1 class="large">設定</h1>
    ${group([cell({ icon: "server", color: "var(--gray)", title: `<b style="font-weight:600">${esc(u.username)}</b>`, sub: u.role === "admin" ? "管理者" : "利用者" })])}
    ${group(
      [
        `<div class="field"><label for="th">テーマ</label><select id="th" data-set="theme"><option value="" ${DISPLAY.theme === "" ? "selected" : ""}>自動</option><option value="light" ${DISPLAY.theme === "light" ? "selected" : ""}>ライト</option><option value="dark" ${DISPLAY.theme === "dark" ? "selected" : ""}>ダーク</option></select></div>`,
        cell({ icon: "sparkle", color: "var(--indigo)", title: "背景", val: esc(bgName(DISPLAY.bg)), act: "bg-open" }),
      ],
      "表示"
    )}
    <div style="height:22px"></div>
    ${group([cell({ title: "ログアウト", act: "logout", cls: "action center" })])}
  </div>`;
}
function openBgSheet() {
  openSheet(
    `${sheetHead("背景", { left: "閉じる", right: "完了", rightAct: "close" })}<div class="sheet-b">
    <div class="gh">背景を選ぶ</div>
    <div class="pick bgpick" role="group" aria-label="背景">
      ${BG_PRESETS.map((p) => {
        const css = isDark() ? p.dark : p.light;
        const swatch = css === "none" ? "var(--bg)" : css;
        return `<button type="button" class="pk" data-act="bg-pick" data-arg="${p.id}" aria-pressed="${DISPLAY.bg.id === p.id}"><span class="sw" style="background:${swatch};background-size:cover"></span><b>${p.name}</b></button>`;
      }).join("")}
    </div>
  </div>`,
    ""
  );
}

/* ---------------- ログイン ---------------- */
function renderAuthScreen(errorCode) {
  document.getElementById("app").hidden = true;
  document.getElementById("tabbar").hidden = true;
  document.getElementById("tsearch").hidden = true;
  const message = errorCode ? LOGIN_ERRORS[errorCode] || "ログインできませんでした。時間をおいて試してください。" : "";
  document.getElementById("auth").innerHTML = `<div class="authscreen"><div class="authcard glass">
    <h1>Hatch</h1>
    <p>Discord アカウントでログインしてください。</p>
    ${message ? `<p class="err-t">${esc(message)}</p>` : ""}
    <button type="button" class="btn fill block" id="login-btn">Discord でログイン</button>
  </div></div>`;
  document.getElementById("login-btn").addEventListener("click", () => {
    location.href = "/api/auth/login";
  });
}

/* ---------------- 二段階認証（管理者） ---------------- */
async function runTotpFlow() {
  document.getElementById("app").hidden = true;
  document.getElementById("tabbar").hidden = true;
  document.getElementById("tsearch").hidden = true;
  let setup = null;
  if (!ME.totp_enabled) {
    try {
      setup = await api.post("/auth/totp/setup");
    } catch (e) {
      toast(e.message, "warn");
    }
  }
  document.getElementById("auth").innerHTML = `<div class="authscreen"><div class="authcard glass">
    <h1>二段階認証</h1>
    <p>${setup ? "認証アプリに次のキーを登録してください。" : "認証アプリに表示されているコードを入力してください。"}</p>
    ${setup ? `<p class="mono" style="word-break:break-all">${esc(setup.secret)}</p>` : ""}
    <input id="totp-code" inputmode="numeric" autocomplete="one-time-code" placeholder="123456" maxlength="8">
    <div id="totp-err" class="err-t" style="min-height:20px;font-size:14px"></div>
    <button type="button" class="btn fill block" id="totp-btn">確認</button>
  </div></div>`;
  document.getElementById("totp-btn").addEventListener("click", async () => {
    const code = document.getElementById("totp-code").value.trim();
    try {
      await api.post("/auth/totp/verify", { code });
      await boot();
    } catch (e) {
      document.getElementById("totp-err").textContent = e.message;
    }
  });
}

/* ---------------- 利用規約 ---------------- */
function showTosSheet() {
  openSheet(
    `<div class="sheet-h"><div class="l"></div><h2 id="sh-title">利用規約</h2><div class="r"></div></div><div class="sheet-b">
    <p style="margin-top:4px">使い始める前に、利用規約（${esc(ME.tos_version || "")} 版）に同意してください。</p>
    <div style="height:14px"></div>${group([cell({ title: "利用規約に同意します", val: '<input type="checkbox" class="switch" id="tos-ok" aria-label="同意する">' })])}
    <div style="height:16px"></div><button type="button" class="btn fill block" id="tos-btn" disabled>同意して始める</button>
  </div>`,
    "small"
  );
  document.getElementById("tos-ok").addEventListener("change", (e) => {
    document.getElementById("tos-btn").disabled = !e.target.checked;
  });
  document.getElementById("tos-btn").addEventListener("click", async () => {
    try {
      await api.post("/me/tos", { version: ME.tos_version });
      ME.needs_tos = false;
      closeSheet();
      startApp();
    } catch (e) {
      toast(e.message, "warn");
    }
  });
}

/* ---------------- 起動 ---------------- */
function startApp() {
  document.getElementById("app").hidden = false;
  applyDisplay();
  ROUTE = parseLocation();
  if (ROUTE.tab === "admin" && !isAdmin()) ROUTE = { tab: "servers", stack: [] };
  navigate(ROUTE.tab, ROUTE.stack, { replace: true });
  render();
}

async function boot() {
  const url = new URL(location.href);
  const loginError = url.searchParams.get("login_error");
  if (loginError) {
    url.searchParams.delete("login_error");
    history.replaceState(null, "", url.pathname + url.search);
  }
  try {
    ME = await api.get("/me");
  } catch (e) {
    if (e.status === 401) return renderAuthScreen(loginError);
    toast(e.message, "warn");
    return renderAuthScreen(loginError);
  }
  setCsrfToken(ME.csrf_token);
  if (ME.needs_totp) return runTotpFlow();
  startApp();
  if (ME.needs_tos) showTosSheet();
}

/* ---------------- 操作 ---------------- */
const ACT = {
  tab(a) {
    ROUTE = { tab: a, stack: [] };
    navigate(ROUTE.tab, ROUTE.stack);
    window.scrollTo(0, 0);
    render();
  },
  go(a) {
    ROUTE.stack.push({ page: a });
    navigate(ROUTE.tab, ROUTE.stack);
    window.scrollTo(0, 0);
    renderMain();
  },
  back() {
    ROUTE.stack.pop();
    navigate(ROUTE.tab, ROUTE.stack);
    renderMain();
  },
  close() {
    closeSheet();
  },
  search() {
    openSheet(`${sheetHead("検索", { left: "閉じる" })}<div class="sheet-b"><div class="searchbox" style="margin-bottom:10px">${ic("search")}<input id="q" placeholder="名前・アドレス・ポート・画面" autocomplete="off" autocapitalize="off" aria-label="検索"></div><p style="text-align:center;color:var(--label2);margin-top:30px">検索できる項目は今後追加されます。</p></div>`);
    setTimeout(() => document.getElementById("q")?.focus(), 40);
  },
  "bg-open"() {
    openBgSheet();
  },
  "bg-pick"(a) {
    DISPLAY.bg = { kind: a === "none" ? "none" : "preset", id: a };
    saveDisplay();
    applyDisplay();
    openBgSheet();
    renderMain();
  },
  async logout() {
    try {
      await api.post("/auth/logout");
    } catch {
      /* 失敗してもログイン画面に戻す */
    }
    location.href = "/";
  },
};

document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-act]");
  if (!el || el.disabled) return;
  const fn = ACT[el.dataset.act];
  if (fn) {
    e.preventDefault();
    fn(el.dataset.arg ?? "", el);
  }
});
document.addEventListener("change", (e) => {
  const t = e.target;
  if (t.dataset.set === "theme") {
    DISPLAY.theme = t.value;
    saveDisplay();
    applyDisplay();
    renderMain();
  }
});
document.addEventListener("keydown", (e) => {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "");
  if (e.key === "Escape" && isSheetOpen() && !document.getElementById("tos-btn")) return closeSheet();
  if (!typing && !isSheetOpen() && (e.key === "/" || (e.key.toLowerCase() === "k" && (e.metaKey || e.ctrlKey)))) {
    e.preventDefault();
    ACT.search();
  }
});
document.getElementById("scrim").addEventListener("click", (e) => {
  if (e.target.id === "scrim" && !document.getElementById("tos-btn")) closeSheet();
});
onLocationChange((route) => {
  ROUTE = route;
  render();
});

boot();
