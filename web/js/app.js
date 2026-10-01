// 画面の骨組み。docs/TASKS.md T15。
// ナビゲーション（タブ・サイドバー・ナビゲーションバー）、ログイン・二段階認証・利用規約の同意、
// 表示設定（テーマ・背景）を組み立てる。各画面の中身は pages/*.js（T16）。
import { api, setCsrfToken } from "./api.js";
import { navigate, onLocationChange, parseLocation } from "./router.js";
import { ic } from "./components/icons.js";
import { esc, cell, group, banner } from "./components/cell.js";
import { toast } from "./components/toast.js";
import { sheetHead, openSheet, closeSheet, isSheetOpen, onSheetClosed } from "./components/sheet.js";
import { openPicker, pickerButton } from "./components/picker.js";
import { renderSetup, setupStatus } from "./setup.js";
import { ctx, isBusyUi, navbar, pageShell } from "./ctx.js";
import { SERVER_ACTIONS, SERVER_PAGES, serverTitle } from "./pages/servers.js";
import { ADMIN_ACTIONS, ADMIN_PAGES, adminTitle } from "./pages/admin-address.js";
import { JOB_ACTIONS } from "./pages/jobs.js";
import { INFRA_ACTIONS, INFRA_PAGES } from "./pages/admin-infra.js";
import { START_ACTIONS, START_PAGES } from "./pages/getting-started.js";

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
  start: "はじめの設定",
  audit: "操作ログ",
  keys: "API キー",
  expiring: "期限が近い",
  system: "システムの状態",
  history: "操作履歴",
};
const LOGIN_ERRORS = {
  state: "確認用の情報が一致しませんでした。もう一度ログインしてください。",
  not_member: "対象の Discord サーバーに参加していません。",
  no_role:
    "Hatch を使えるロールが、あなたの Discord アカウントに付いていません。管理者に、管理者・サポーター・利用者のいずれかのロールを付けてもらってから、もう一度ログインしてください。",
  not_allowed: "このアカウントは利用できません。心当たりがない場合は管理者に問い合わせてください。",
  email: "Discord のメールアドレスが確認されていません。",
  email_taken: "同じメールアドレスの別アカウントが既にあります。",
  suspended: "アカウントが利用停止中です。",
  discord: "Discord との通信に失敗しました。時間をおいて試してください。",
};
const ROLE_LABELS = { admin: "管理者", supporter: "サポーター", user: "利用者" };
const THEMES = [
  { value: "", label: "自動", sub: "端末の設定に合わせます" },
  { value: "light", label: "ライト" },
  { value: "dark", label: "ダーク" },
];
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
function pageTitle(entry) {
  return serverTitle(entry) || adminTitle(entry) || TITLES[entry.page] || "";
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
    <div class="foot"><span class="ico" style="background:var(--gray);border-radius:50%;font-weight:700;font-size:14px">${esc((u.username || "?")[0].toUpperCase())}</span><div><div>${esc(u.username)}</div><div style="font-size:13px;color:var(--label2)">${ROLE_LABELS[u.role] || "利用者"}</div></div></div>`;
}
function onScroll() {
  const n = document.getElementById("navbar");
  if (n) n.classList.toggle("scrolled", window.scrollY > 30);
}
addEventListener("scroll", onScroll, { passive: true });

// 画面の中身。文字列か { html, after }（描いた後に呼ぶ関数）を返す。データの取得を待つので async
function laterPage(title, text) {
  return pageShell(title, group([cell({ icon: "clock", color: "var(--gray)", title: "この画面は準備中です", sub: text, subWrap: true })]), {
    back: ROUTE.stack.length > 0,
  });
}
async function pageFor(top) {
  if (ROUTE.tab === "servers") {
    if (!top) return SERVER_PAGES.root();
    if (SERVER_PAGES[top.page]) return SERVER_PAGES[top.page](top.arg);
    return laterPage(pageTitle(top), "今後のバージョンで使えるようになります。");
  }
  if (ROUTE.tab === "admin") {
    if (!top) return ADMIN_PAGES.root();
    if (START_PAGES[top.page]) return START_PAGES[top.page](top.arg);
    if (INFRA_PAGES[top.page]) return INFRA_PAGES[top.page](top.arg);
    if (ADMIN_PAGES[top.page]) return ADMIN_PAGES[top.page](top.arg);
    return ADMIN_PAGES.later(top.page);
  }
  if (ROUTE.tab === "monitor") return laterPage("監視", "サーバーごとの稼働状況は、今後この画面にまとめて表示します。今はサーバーの画面で状態を確認できます。");
  if (ROUTE.tab === "settings" && !top) return pSettings();
  return laterPage(pageTitle(top) || "設定", "今後のバージョンで使えるようになります。");
}
function errorPage(e) {
  const top = ROUTE.stack[ROUTE.stack.length - 1];
  if (e.status === 404) {
    return pageShell("見つかりません", `<p>削除されたか、表示する権限がありません。</p>`, { back: !!top });
  }
  const title = top ? pageTitle(top) : TAB_TITLES[ROUTE.tab];
  return `${navbar(title, { back: top ? backLabel() : "" })}<div class="page"><h1 class="large">${esc(title)}</h1>
    ${banner("e", "読み込めませんでした", esc(e.message || String(e)), '<button type="button" class="btn sm fill" data-act="reload">もう一度読み込む</button>')}</div>`;
}

let renderSeq = 0;
let pendingRefresh = false;
async function renderMain({ keepScroll = false } = {}) {
  const seq = ++renderSeq;
  const main = document.getElementById("main");
  const top = ROUTE.stack[ROUTE.stack.length - 1];
  const y = window.scrollY;
  // 読み込みが遅いときだけ「読み込んでいます」を出す（速いときに画面がちらつかないように）
  const slow = setTimeout(() => {
    if (seq !== renderSeq || keepScroll) return;
    const title = top ? pageTitle(top) : TAB_TITLES[ROUTE.tab];
    main.innerHTML = `${navbar(title, { back: top ? backLabel() : "" })}<div class="page"><h1 class="large">${esc(title)}</h1><p class="loading" role="status">読み込んでいます…</p></div>`;
  }, 250);
  let out;
  try {
    out = await pageFor(top);
  } catch (e) {
    if (e.status === 401) return location.reload();
    out = errorPage(e);
  }
  clearTimeout(slow);
  if (seq !== renderSeq) return; // もっと新しい描画が始まっている
  const { html, after } = typeof out === "string" ? { html: out } : out;
  main.innerHTML = html;
  if (keepScroll) window.scrollTo(0, y);
  onScroll();
  if (after) after();
}
/** データを取り直して描き直す。入力中・シートやメニューの表示中は、閉じた後に行う。 */
function refresh() {
  if (!ME) return;
  if (isBusyUi()) {
    pendingRefresh = true;
    return;
  }
  pendingRefresh = false;
  renderMain({ keepScroll: true });
}
onSheetClosed(() => {
  if (pendingRefresh) setTimeout(refresh, 0);
});
document.addEventListener("focusout", () => {
  if (pendingRefresh) setTimeout(() => pendingRefresh && refresh(), 300);
});
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
    ${group([cell({ icon: "server", color: "var(--gray)", title: `<b style="font-weight:600">${esc(u.username)}</b>`, sub: ROLE_LABELS[u.role] || "利用者" })])}
    ${group(
      [
        `<div class="field"><label>テーマ</label>${pickerButton({ options: THEMES, value: DISPLAY.theme, attrs: 'data-act="theme-pick" aria-label="テーマ"' })}</div>`,
        cell({ icon: "sparkle", color: "var(--indigo)", title: "背景", val: esc(bgName(DISPLAY.bg)), act: "bg-open" }),
      ],
      "表示"
    )}
    ${group(
      [cell({ icon: "key", color: "var(--orange)", title: "二段階認証", val: ME.totp_enabled ? "オン" : "オフ", act: "totp-open" })],
      "セキュリティ",
      "オンにすると、ログインのときに認証アプリのコードも入力します。任意です。"
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

/* ---------------- 二段階認証（任意） ---------------- */
// ログインの後、二段階認証を有効にしている人だけにコードを求める
function runTotpFlow() {
  document.getElementById("app").hidden = true;
  document.getElementById("tabbar").hidden = true;
  document.getElementById("tsearch").hidden = true;
  document.getElementById("auth").innerHTML = `<div class="authscreen"><div class="authcard glass">
    <h1>二段階認証</h1>
    <p>認証アプリに表示されている6桁のコードを入力してください。</p>
    <input id="totp-code" inputmode="numeric" autocomplete="one-time-code" placeholder="123456" maxlength="8" aria-label="認証コード">
    <div id="totp-err" class="err-t" style="min-height:20px;font-size:14px"></div>
    <button type="button" class="btn fill block" id="totp-btn">確認</button>
    <p class="totp-help">認証アプリを使えなくなった場合は、管理者に二段階認証の解除を依頼してください。<button type="button" class="linkbtn" id="totp-logout">ログアウト</button></p>
  </div></div>`;
  const input = document.getElementById("totp-code");
  const submit = async () => {
    try {
      await api.post("/auth/totp/verify", { code: input.value.trim() });
      await boot();
    } catch (e) {
      document.getElementById("totp-err").textContent = e.message;
      if (e.status === 401) setTimeout(() => (location.href = "/"), 1500);
    }
  };
  document.getElementById("totp-btn").addEventListener("click", submit);
  input.addEventListener("keydown", (e) => e.key === "Enter" && submit());
  document.getElementById("totp-logout").addEventListener("click", () => ACT.logout());
  input.focus();
}

function totpCodeField() {
  return `<div class="group"><div class="field"><label for="totp-in">コード</label><input id="totp-in" inputmode="numeric" autocomplete="one-time-code" placeholder="123456" maxlength="8"></div></div>
    <div id="totp-sheet-err" class="err-t" role="alert" style="min-height:20px;font-size:14px;margin:6px 4px"></div>`;
}

async function openTotpSheet() {
  if (ME.totp_enabled) {
    openSheet(`${sheetHead("二段階認証", { left: "閉じる" })}<div class="sheet-b">
      <p class="sheet-lead">二段階認証はオンです。オフにするには、認証アプリに表示されている今のコードを入力してください。</p>
      ${totpCodeField()}
      <button type="button" class="btn red block" data-act="totp-disable">二段階認証をオフにする</button>
    </div>`);
  } else {
    let setup;
    try {
      setup = await api.post("/auth/totp/setup");
    } catch (e) {
      return toast(e.message, "warn");
    }
    const key = setup.secret.replace(/(.{4})/g, "$1 ").trim();
    openSheet(`${sheetHead("二段階認証をオンにする", { left: "キャンセル" })}<div class="sheet-b">
      <ol class="totp-steps">
        <li>スマートフォンに認証アプリ（Google Authenticator・Microsoft Authenticator など）を入れます。</li>
        <li>認証アプリで「セットアップキーを入力」を選び、次のキーを登録します。スマートフォンでこの画面を見ている場合は <a href="${esc(setup.otpauth_url)}">認証アプリで開く</a> でも登録できます。
          <div class="totp-key"><code>${esc(key)}</code><button type="button" class="btn sm" data-act="totp-copy" data-arg="${esc(setup.secret)}">コピー</button></div></li>
        <li>認証アプリに表示された6桁のコードを入力します。</li>
      </ol>
      ${totpCodeField()}
      <button type="button" class="btn fill block" data-act="totp-enable">オンにする</button>
    </div>`);
  }
  setTimeout(() => document.getElementById("totp-in")?.focus(), 40);
}

async function submitTotp(path, on) {
  const input = document.getElementById("totp-in");
  const err = document.getElementById("totp-sheet-err");
  try {
    await api.post(path, { code: input.value.trim() });
  } catch (e) {
    err.textContent = e.message;
    if (e.status === 401) setTimeout(() => (location.href = "/"), 1500);
    return;
  }
  ME.totp_enabled = on;
  closeSheet();
  toast(on ? "二段階認証をオンにしました" : "二段階認証をオフにしました");
  renderMain();
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
// 画面のモジュールから使う入口（web/js/ctx.js）
Object.defineProperty(ctx, "me", { get: () => ME, set: (v) => (ME = v) });
Object.defineProperty(ctx, "route", { get: () => ROUTE });
ctx.go = (page, arg) => {
  ROUTE.stack.push(arg === undefined ? { page } : { page, arg: String(arg) });
  navigate(ROUTE.tab, ROUTE.stack);
  window.scrollTo(0, 0);
  renderMain();
};
ctx.goTab = (tab, stack = []) => {
  ROUTE = { tab, stack };
  navigate(tab, stack);
  window.scrollTo(0, 0);
  render();
};
ctx.refresh = refresh;
ctx.backLabel = backLabel;

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
  const setup = await setupStatus();
  if (setup && setup.needed) return renderSetup();
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
    ctx.go(a);
  },
  reload() {
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
  "theme-pick"(_a, el) {
    openPicker(el, THEMES, DISPLAY.theme, (v) => {
      DISPLAY.theme = v;
      saveDisplay();
      applyDisplay();
      renderMain();
      document.querySelector('[data-act="theme-pick"]')?.focus();
    });
  },
  "totp-open"() {
    openTotpSheet();
  },
  "totp-enable"() {
    submitTotp("/auth/totp/verify", true);
  },
  "totp-disable"() {
    submitTotp("/auth/totp/disable", false);
  },
  async "totp-copy"(a, el) {
    try {
      await navigator.clipboard.writeText(a);
      el.textContent = "コピーしました";
    } catch {
      el.textContent = "コピーできません";
    }
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
  ...SERVER_ACTIONS,
  ...ADMIN_ACTIONS,
  ...JOB_ACTIONS,
  ...INFRA_ACTIONS,
  ...START_ACTIONS,
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
document.addEventListener("keydown", (e) => {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "");
  if (e.key === "Escape" && isSheetOpen() && !document.getElementById("tos-btn")) return closeSheet();
  if (e.key === "Enter" && e.target.id === "totp-in") {
    e.preventDefault();
    return document.querySelector('[data-act="totp-enable"],[data-act="totp-disable"]')?.click();
  }
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
