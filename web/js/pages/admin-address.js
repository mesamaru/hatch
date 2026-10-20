import { api } from "../api.js";
import { navbar } from "../app.js";
import { esc, cell, group } from "../components/cell.js";
import { ic } from "../components/icons.js";
import { toast } from "../components/toast.js";

let SLOT_RULES_CACHE = {};

async function getSlotRule(id) {
  if (!SLOT_RULES_CACHE[id]) {
    try {
      SLOT_RULES_CACHE[id] = await api.get(`/api/admin/slot-rules/${id}`);
    } catch (e) {
      toast(e.message, "err");
      throw e;
    }
  }
  return SLOT_RULES_CACHE[id];
}

export async function pAdminSlots() {
  try {
    const rules = await api.get("/api/admin/slot-rules");

    const items = rules.map(r =>
      cell({
        icon: "folder",
        color: "var(--blue)",
        title: esc(r.name),
        sub: `${r.port_start}–${r.port_end} (${r.stats.free}/${r.stats.total})`,
        chev: true,
        act: "go",
        arg: `admin/rules/${r.id}`,
      })
    );

    return `${navbar("アドレス枠", {right: `<button class="btn icon" data-act="add-slot">${ic("plus")}</button>`})}
      <div class="page">
        <h1 class="large">アドレス枠</h1>
        ${rules.length > 0 ? group(items) : '<div style="padding:16px;color:var(--label2)">アドレス枠がまだありません。</div>'}
      </div>`;
  } catch (e) {
    return `${navbar("アドレス枠")}
      <div class="page">
        <h1 class="large">アドレス枠</h1>
        <div style="padding:16px;color:var(--red)">${esc(e.message)}</div>
      </div>`;
  }
}

export async function pAdminSlotDetail(entry) {
  try {
    const rule = await getSlotRule(entry.arg);

    const slotItems = (rule.slots || []).slice(0, 40).map(slot => {
      const statusColor = slot.status === "free" ? "var(--green)" : slot.status === "assigned" ? "var(--blue)" : "var(--gray)";
      const statusLabel = slot.status === "free" ? "空き" : slot.status === "assigned" ? "使用中" : "その他";

      return cell({
        icon: "circle",
        color: statusColor,
        title: esc(slot.fqdn),
        val: statusLabel,
      });
    });

    return `${navbar(esc(rule.name), {back: "アドレス枠", right: `<button class="btn icon" data-act="edit-slot" data-arg="${rule.id}">${ic("pencil")}</button>`})}
      <div class="page">
        <h1 class="large">${esc(rule.name)}</h1>
        <div style="padding:8px 16px;font-size:14px;color:var(--label2)">ポート範囲: ${rule.port_start}–${rule.port_end}</div>
        ${group(slotItems)}
      </div>`;
  } catch (e) {
    return `${navbar("詳細", {back: "アドレス枠"})}
      <div class="page">
        <h1 class="large">エラー</h1>
        <div style="padding:16px;color:var(--red)">${esc(e.message)}</div>
      </div>`;
  }
}

export async function pAdminSlotEdit(entry) {
  // This is called as a sheet, not a full page
  // Entry can have arg with rule ID for edit, or null for create
  return `${navbar("アドレス枠編集")}
    <div class="page">
      <h1 class="large">編集</h1>
      <div style="padding:16px;color:var(--label2)">アドレス枠の編集シートはこちらで実装されます</div>
    </div>`;
}
