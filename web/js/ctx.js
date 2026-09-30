// 各画面（pages/*.js）から使う、アプリ全体の状態と操作。中身は app.js が起動時に入れる。
// 画面のモジュールが app.js を import すると循環するため、ここを仲介にする。
import { esc } from "./components/cell.js";
import { ic } from "./components/icons.js";
import { toast } from "./components/toast.js";

export const ctx = {
  me: null, // GET /api/me の応答
  route: { tab: "servers", stack: [] },
  /** 画面を積む（詳細へ進む）。 */
  go(_page, _arg) {},
  /** タブを切り替えて、必要なら画面を積む。 */
  goTab(_tab, _stack) {},
  /** 今の画面を描き直す（データを取り直す）。シートや入力中なら閉じた後に行う。 */
  refresh() {},
  backLabel() {
    return "";
  },
};

export const isAdmin = () => !!ctx.me && ctx.me.user.role === "admin";

/** 見出しのバー。back を渡すと「＜ 戻る先」を出す。 */
export function navbar(title, { back, right = "" } = {}) {
  return `<div class="navbar" id="navbar"><div class="l">${
    back ? `<button type="button" class="back" data-act="back">${ic("back")}<span>${esc(back)}</span></button>` : ""
  }</div><div class="ttl">${esc(title)}</div><div class="r">${right}</div></div>`;
}

/** 詳細画面の枠（見出しのバー＋大きな見出し＋本文）。 */
export function pageShell(title, body, { right = "", wide = false, back = true, sub = "" } = {}) {
  return `${navbar(title, { back: back ? ctx.backLabel() : "", right })}<div class="page ${wide ? "wide" : ""}"><h1 class="large">${esc(title)}</h1>${sub}${body}</div>`;
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("コピーしました");
  } catch {
    toast("コピーできませんでした。長押しで選択してください。", "warn");
  }
}

/** 日時を「9/27 04:00」の形にする（端末の時刻で表示）。 */
export function shortTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getMonth() + 1}/${d.getDate()} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** 今から iso までの残り（日・時間）。過ぎていれば 0。 */
export function remaining(iso) {
  const ms = Math.max(0, new Date(iso).getTime() - Date.now());
  return { days: Math.floor(ms / 86400000), hours: Math.ceil(ms / 3600000) };
}

/** 入力の途中で画面を描き直さないための判定（docs/UI.md 4「ジョブの進捗」）。 */
export function isBusyUi() {
  const a = document.activeElement;
  const scrim = document.getElementById("scrim");
  const menu = document.getElementById("menu");
  return (scrim && !scrim.hidden) || (menu && !menu.hidden) || (a && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName));
}
