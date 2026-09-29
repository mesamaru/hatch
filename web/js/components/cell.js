// 一覧の1行（cell）とグループ（group）。docs/UI.md「インセットのグループリスト」。
import { ic } from "./icons.js";

export const esc = (v) =>
  String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

export function cell({ icon, color, title, sub, val, act, arg, chev, cls = "", subWrap } = {}) {
  const tag = act ? "button" : "div";
  const attrs = act ? ` type="button" data-act="${act}" data-arg="${esc(arg ?? "")}"` : "";
  const inner =
    `${icon ? `<span class="ico" style="background:${color || "var(--gray)"}">${ic(icon)}</span>` : ""}` +
    `<div class="tx"><div class="t">${title}</div>${sub ? `<div class="s ${subWrap ? "wrap" : ""}">${sub}</div>` : ""}</div>` +
    `${val !== undefined && val !== "" ? `<span class="val">${val}</span>` : ""}` +
    `${chev ?? !!act ? ic("chev", "chev") : ""}`;
  return `<${tag} class="cell ${cls} ${icon ? "has-ico" : ""}"${attrs}>${inner}</${tag}>`;
}

export function group(items, head = "", foot = "") {
  return `${head ? `<div class="gh">${esc(head)}</div>` : ""}<div class="group">${items.join("")}</div>${foot ? `<div class="gf">${foot}</div>` : ""}`;
}

export function banner(kind, title, text, btns = "") {
  const icn = { info: "info", w: "warn", e: "xc", ok: "ok" }[kind];
  return `<div class="banner ${kind}" role="${kind === "e" ? "alert" : "status"}">${ic(icn)}<div class="tx">${title ? `<b>${esc(title)}</b>` : ""}${text}${btns ? `<div class="bb">${btns}</div>` : ""}</div></div>`;
}
