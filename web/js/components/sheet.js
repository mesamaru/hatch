// 下から出るシート。docs/UI.md 3.3章。
import { esc, group } from "./cell.js";

const closedHandlers = [];
let confirmHandler = null;

export function sheetHead(
  title,
  { left = "閉じる", leftAct = "close", right = "", rightAct = "", rightDisabled = false, rightId = "" } = {}
) {
  return `<div class="sheet-h"><div class="l"><button type="button" data-act="${leftAct}">${left}</button></div><h2 id="sh-title">${title}</h2><div class="r">${
    right
      ? `<button type="button" class="done" data-act="${rightAct}" ${rightDisabled ? "disabled" : ""} ${rightId ? `id="${rightId}"` : ""}>${right}</button>`
      : ""
  }</div></div>`;
}

export function openSheet(html, cls = "") {
  const scrim = document.getElementById("scrim");
  scrim.innerHTML = `<div class="sheet ${cls}" role="dialog" aria-modal="true" aria-labelledby="sh-title"><div class="grab"></div>${html}</div>`;
  scrim.hidden = false;
  // PC では最初の入力欄へ（スマホはキーボードが急に出ないように、フォーカスしない）
  setTimeout(() => {
    if (matchMedia("(pointer:coarse)").matches) return;
    // 既にシートの中を操作していたら動かさない（入力中にフォーカスが飛ぶと、別の欄に文字が入る）
    if (scrim.contains(document.activeElement) && document.activeElement !== scrim) return;
    const f = scrim.querySelector("[autofocus]") || scrim.querySelector(".sheet-b input:not([type=checkbox]), .sheet-b textarea");
    (f || scrim.querySelector(".sheet-h button"))?.focus();
  }, 30);
}

export function closeSheet() {
  const scrim = document.getElementById("scrim");
  const wasOpen = !scrim.hidden;
  scrim.hidden = true;
  scrim.innerHTML = "";
  confirmHandler = null;
  if (wasOpen) closedHandlers.forEach((fn) => fn());
}

export function isSheetOpen() {
  const scrim = document.getElementById("scrim");
  return !!scrim && !scrim.hidden;
}

/** シートが閉じたとき（保留していた描き直しなどに使う）。 */
export function onSheetClosed(fn) {
  closedHandlers.push(fn);
}

/**
 * 確認のシート。元に戻せない操作は requireText（名前など）の入力で確認する（docs/UI.md 3.3）。
 * onOk が例外を投げたら、その message をシートに表示して閉じない。
 */
export function confirmSheet({ title, body, ok, danger = false, requireText = "", onOk }) {
  confirmHandler = onOk;
  openSheet(
    `${sheetHead(esc(title), { left: "キャンセル" })}<div class="sheet-b">
    <div class="sheet-lead" style="color:var(--label)">${body}</div>
    ${
      requireText
        ? `${group([`<div class="field col"><label for="cf-t">確認のため「${esc(requireText)}」と入力</label><input id="cf-t" data-require="${esc(requireText)}" autocomplete="off" autocapitalize="off" spellcheck="false"></div>`])}<div style="height:14px"></div>`
        : ""
    }
    <div id="cf-err" class="err-t" role="alert" style="font-size:14px;margin:0 4px 8px"></div>
    <button type="button" class="btn block ${danger ? "red" : "fill"}" id="cf-ok" data-act="confirm-ok" ${requireText ? "disabled" : ""}>${esc(ok)}</button></div>`,
    "small"
  );
}

document.addEventListener("input", (e) => {
  const t = e.target;
  if (t.id === "cf-t") document.getElementById("cf-ok").disabled = t.value.trim() !== t.dataset.require;
});
document.addEventListener("click", async (e) => {
  const b = e.target.closest("#cf-ok");
  if (!b || b.disabled || !confirmHandler) return;
  b.disabled = true;
  try {
    await confirmHandler();
    closeSheet();
  } catch (err) {
    const box = document.getElementById("cf-err");
    if (box) box.textContent = err.message || String(err);
    b.disabled = false;
  }
});
