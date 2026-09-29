// トースト（画面上部の通知）。docs/UI.md 4章「トースト」。
import { ic } from "./icons.js";

let hideTimer = null;

export function toast(message, kind = "ok") {
  const el = document.getElementById("toast");
  if (!el) return;
  el.innerHTML = `${ic(kind)}<span></span>`;
  el.querySelector("span").textContent = message;
  el.classList.add("on");
  clearTimeout(hideTimer);
  hideTimer = setTimeout(() => el.classList.remove("on"), 2600);
}
