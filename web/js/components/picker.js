// 選択肢のメニュー（ブラウザ標準の select の代わり）。docs/UI.md「メニュー」と同じ見た目（#menu）を使う。
//
//   pickerButton({ id: "f-guild", value, options: [{ value, label, sub }], placeholder })  … 行の右側に置くボタン
//   openPicker(button, options, value, (v) => { ... })                                    … 押されたら開く
//
// キーボード：↑↓で移動、Enter・Space で決定、Esc・Tab で閉じる。
import { esc } from "./cell.js";
import { ic } from "./icons.js";

let current = null; // { anchor, onPick }

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
    if (focus) current.anchor.focus();
  }
  current = null;
}

function place(m, anchor) {
  const r = anchor.getBoundingClientRect();
  const vw = document.documentElement.clientWidth;
  const vh = window.innerHeight;
  const gap = 6;
  const pad = 12;
  m.style.maxHeight = "";
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

export function openPicker(anchor, options, value, onPick) {
  const m = menuEl();
  if (current && current.anchor === anchor) return closePicker({ focus: true });
  closePicker();
  m.classList.add("picklist");
  m.setAttribute("role", "listbox");
  m.innerHTML = options
    .map(
      (o, i) =>
        `<button type="button" role="option" data-i="${i}" aria-selected="${o.value === value}" ${o.disabled ? "disabled" : ""}>
          <span class="ol"><span class="ot">${esc(o.label)}</span>${o.sub ? `<span class="os">${esc(o.sub)}</span>` : ""}</span>
          ${o.value === value ? ic("check") : ""}</button>`,
    )
    .join("");
  m.hidden = false;
  current = { anchor, onPick, options };
  anchor.setAttribute("aria-expanded", "true");
  place(m, anchor);
  const sel = m.querySelector('[aria-selected="true"]') || m.querySelector("button:not([disabled])");
  if (sel) {
    sel.focus({ preventScroll: true });
    sel.scrollIntoView({ block: "nearest" });
  }
}

function pick(btn) {
  if (!current || !btn || btn.disabled) return;
  const { onPick, options } = current;
  const opt = options[Number(btn.dataset.i)];
  closePicker({ focus: true });
  onPick(opt.value);
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
  const btn = e.target.closest("#menu.picklist button");
  if (btn) return pick(btn);
  if (!e.target.closest("#menu") && !current.anchor.contains(e.target)) closePicker();
});
document.addEventListener("keydown", (e) => {
  if (!current) return;
  if (e.key === "Escape") {
    e.preventDefault();
    e.stopPropagation();
    closePicker({ focus: true });
  } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    move(e.key === "ArrowDown" ? 1 : -1);
  } else if (e.key === "Tab") {
    closePicker();
  } else if ((e.key === "Enter" || e.key === " ") && e.target.closest("#menu.picklist")) {
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
