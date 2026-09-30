// 選択肢のメニュー（ブラウザ標準の select の代わり）と、「…」から開く操作のメニュー。
// どちらも docs/UI.md「メニュー」と同じ見た目（#menu）を使う。
//
//   pickerButton({ value, options: [{ value, label, sub }], placeholder, attrs })  … 行の右側に置くボタン
//   openPicker(button, options, value, (v) => { ... })                              … 押されたら開く
//   openMenu(button, [{ label, icon, red, disabled, run }, "-", ...])               … 操作のメニュー
//
// キーボード：↑↓で移動、Enter・Space で決定、Esc・Tab で閉じる。
import { esc } from "./cell.js";
import { ic } from "./icons.js";

let current = null; // { anchor, onIndex }

export function pickerButton({ id = "", value = "", options = [], placeholder = "選んでください", attrs = "" }) {
  const opt = options.find((o) => o.value === value);
  const label = opt ? opt.label : placeholder;
  return `<button type="button" class="picker${opt ? "" : " empty"}" ${id ? `id="${esc(id)}"` : ""} aria-haspopup="listbox" aria-expanded="false" ${attrs}>
    <span class="pv">${esc(label)}</span>${ic("updown")}</button>`;
}

function menuEl() {
  return document.getElementById("menu");
}

export function closePicker({ focus = false } = {}) {
  const m = menuEl();
  if (!m || m.hidden) return;
  m.hidden = true;
  m.innerHTML = "";
  m.classList.remove("picklist");
  if (current) {
    current.anchor.setAttribute("aria-expanded", "false");
    if (focus && current.anchor.isConnected) current.anchor.focus();
  }
  current = null;
}
export const closeMenu = closePicker;
export const isMenuOpen = () => !!current;

function place(m, anchor) {
  const r = anchor.getBoundingClientRect();
  const vw = document.documentElement.clientWidth;
  const vh = window.innerHeight;
  const gap = 6;
  const pad = 12;
  m.style.maxHeight = "";
  m.style.width = "";
  const w = Math.min(Math.max(m.offsetWidth, 240), vw - pad * 2);
  m.style.width = `${w}px`;
  // 右端をボタンにそろえ、画面からはみ出さないようにする
  let left = Math.min(r.right - w, vw - pad - w);
  left = Math.max(pad, left);
  const below = vh - r.bottom - gap - pad;
  const above = r.top - gap - pad;
  const h = m.scrollHeight;
  if (h <= below || below >= above) {
    m.style.top = `${r.bottom + gap}px`;
    m.style.maxHeight = `${Math.max(below, 160)}px`;
  } else {
    const mh = Math.min(h, above);
    m.style.top = `${r.top - gap - mh}px`;
    m.style.maxHeight = `${mh}px`;
  }
  m.style.left = `${left}px`;
}

function openList(anchor, { html, role, picklist, onIndex }) {
  const m = menuEl();
  if (current && current.anchor === anchor) return closePicker({ focus: true });
  closePicker();
  m.classList.toggle("picklist", picklist);
  m.setAttribute("role", role);
  m.innerHTML = html;
  m.hidden = false;
  current = { anchor, onIndex };
  anchor.setAttribute("aria-expanded", "true");
  place(m, anchor);
  const sel = m.querySelector('[aria-selected="true"]') || m.querySelector("button:not([disabled])");
  if (sel) {
    sel.focus({ preventScroll: true });
    sel.scrollIntoView({ block: "nearest" });
  }
}

export function openPicker(anchor, options, value, onPick) {
  openList(anchor, {
    role: "listbox",
    picklist: true,
    html: options
      .map(
        (o, i) =>
          `<button type="button" role="option" data-i="${i}" aria-selected="${o.value === value}" ${o.disabled ? "disabled" : ""}>
          <span class="ol"><span class="ot">${esc(o.label)}</span>${o.sub ? `<span class="os">${esc(o.sub)}</span>` : ""}</span>
          ${o.value === value ? ic("check") : ""}</button>`
      )
      .join(""),
    onIndex: (i) => onPick(options[i].value),
  });
}

export function openMenu(anchor, items) {
  const list = items.filter(Boolean);
  openList(anchor, {
    role: "menu",
    picklist: false,
    html: list
      .map((x, i) =>
        x === "-"
          ? "<hr>"
          : `<button type="button" role="menuitem" data-i="${i}" class="${x.red ? "red" : ""}" ${x.disabled ? "disabled" : ""}>${esc(x.label)}${x.icon ? ic(x.icon) : ""}</button>`
      )
      .join(""),
    onIndex: (i) => list[i].run(),
  });
}

function pick(btn) {
  if (!current || !btn || btn.disabled) return;
  const { onIndex } = current;
  closePicker({ focus: true });
  onIndex(Number(btn.dataset.i));
}

function move(delta) {
  const items = [...menuEl().querySelectorAll("button:not([disabled])")];
  if (!items.length) return;
  const i = items.indexOf(document.activeElement);
  const next = items[(i + delta + items.length) % items.length];
  next.focus({ preventScroll: true });
  next.scrollIntoView({ block: "nearest" });
}

document.addEventListener("click", (e) => {
  if (!current) return;
  const btn = e.target.closest("#menu button[data-i]");
  if (btn) {
    e.stopImmediatePropagation(); // メニューの項目は画面の data-act と関係なく、ここで処理する
    return pick(btn);
  }
  if (!e.target.closest("#menu") && !current.anchor.contains(e.target)) closePicker();
});
document.addEventListener("keydown", (e) => {
  if (!current) return;
  if (e.key === "Escape") {
    e.preventDefault();
    e.stopImmediatePropagation(); // シートの Esc（閉じる）より先に、メニューだけを閉じる
    closePicker({ focus: true });
  } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    move(e.key === "ArrowDown" ? 1 : -1);
  } else if (e.key === "Tab") {
    closePicker();
  } else if ((e.key === "Enter" || e.key === " ") && e.target.closest("#menu")) {
    e.preventDefault();
    pick(e.target.closest("button"));
  }
});
window.addEventListener("resize", () => closePicker());
// ページがスクロールしたら、ボタンに付いて動く（ボタンが画面の外に出たら閉じる）
document.addEventListener(
  "scroll",
  (e) => {
    if (!current || (e.target instanceof Element && e.target.closest("#menu"))) return;
    const r = current.anchor.getBoundingClientRect();
    if (!current.anchor.isConnected || r.bottom < 0 || r.top > window.innerHeight) return closePicker();
    place(menuEl(), current.anchor);
  },
  true,
);
