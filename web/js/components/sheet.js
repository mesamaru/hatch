// 下から出るシート。docs/UI.md 3.3章。
export function sheetHead(title, { left = "閉じる", leftAct = "close", right = "", rightAct = "", rightDisabled = false } = {}) {
  return `<div class="sheet-h"><div class="l"><button type="button" data-act="${leftAct}">${left}</button></div><h2 id="sh-title">${title}</h2><div class="r">${
    right ? `<button type="button" class="done" data-act="${rightAct}" ${rightDisabled ? "disabled" : ""}>${right}</button>` : ""
  }</div></div>`;
}

export function openSheet(html, cls = "") {
  const scrim = document.getElementById("scrim");
  scrim.innerHTML = `<div class="sheet ${cls}" role="dialog" aria-modal="true" aria-labelledby="sh-title"><div class="grab"></div>${html}</div>`;
  scrim.hidden = false;
}

export function closeSheet() {
  const scrim = document.getElementById("scrim");
  scrim.hidden = true;
  scrim.innerHTML = "";
}

export function isSheetOpen() {
  const scrim = document.getElementById("scrim");
  return !!scrim && !scrim.hidden;
}
