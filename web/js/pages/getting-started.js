import { api } from "../api.js";
import { navbar } from "../app.js";
import { esc, cell, group } from "../components/cell.js";
import { ic } from "../components/icons.js";
import { toast } from "../components/toast.js";
import { sheetHead, openSheet, closeSheet } from "../components/sheet.js";

const PAGES = {
  edges: "edge を登録",
  firewalls: "ファイアウォールを登録",
  linode: "Linode アカウントを登録",
};

export async function pGettingStarted(entry) {
  const step = entry.arg || "edges";
  const title = PAGES[step] || "セットアップ";

  if (step === "edges") return pEdgeForm();
  if (step === "firewalls") return pFirewallForm();
  if (step === "linode") return pLinodeForm();

  return `${navbar(title, { back: "管理" })}
    <div class="page"><h1 class="large">セットアップ</h1></div>`;
}

async function pEdgeForm() {
  try {
    const [edges, firewalls, linodeAccounts] = await Promise.all([
      api.get("/api/admin/edges"),
      api.get("/api/admin/firewalls"),
      api.get("/api/admin/linode-accounts"),
    ]);

    const firewallOptions = firewalls.map((f) => `<option>${esc(f.label)}</option>`).join("");
    const linodeOptions = linodeAccounts.map((l) => `<option>${esc(l.label)}</option>`).join("");

    return `${navbar("3. edge を登録", { back: "はじめの設定" })}
      <div class="page">
        <h1 class="large">edge を登録</h1>
        <div style="padding:16px">
          <div style="background:var(--bg2);padding:12px;border-radius:12px;margin-bottom:16px">
            <p style="margin:0;font-size:14px;color:var(--label2)">edge は、プレイヤーが接続する入り口のサーバーです</p>
          </div>
          <form id="edge-form" style="display:flex;flex-direction:column;gap:12px">
            <div>
              <label style="font-size:14px;color:var(--label2)">名前</label>
              <input name="name" type="text" value="edge-1" style="width:100%;padding:8px;background:var(--bg2);border:1px solid var(--sep);border-radius:8px;color:var(--label)">
            </div>
            <div>
              <label style="font-size:14px;color:var(--label2)">Linode のアカウント</label>
              <select name="linode_account" style="width:100%;padding:8px;background:var(--bg2);border:1px solid var(--sep);border-radius:8px;color:var(--label)">
                ${linodeOptions}
              </select>
            </div>
            <div>
              <label style="font-size:14px;color:var(--label2)">Linode インスタンス</label>
              <select name="linode_instance" style="width:100%;padding:8px;background:var(--bg2);border:1px solid var(--sep);border-radius:8px;color:var(--label)">
                <option>linode02</option>
              </select>
            </div>
            <div>
              <label style="font-size:14px;color:var(--label2)">ファイアウォール</label>
              <select name="firewall" style="width:100%;padding:8px;background:var(--bg2);border:1px solid var(--sep);border-radius:8px;color:var(--label)">
                ${firewallOptions}
              </select>
            </div>
            <div>
              <label style="font-size:14px;color:var(--label2)">公開 IP</label>
              <input name="public_ip" type="text" value="172.233.84.237" readonly style="width:100%;padding:8px;background:var(--bg2);border:1px solid var(--sep);border-radius:8px;color:var(--label)">
            </div>
            <div>
              <label style="font-size:14px;color:var(--label2)">Tailscale の IP</label>
              <input name="tailscale_ip" type="text" value="100.111.116.40" readonly style="width:100%;padding:8px;background:var(--bg2);border:1px solid var(--sep);border-radius:8px;color:var(--label)">
            </div>
            <div id="edge-error" style="color:var(--red);font-size:14px;min-height:20px"></div>
            <button type="submit" style="padding:12px;background:var(--blue);color:white;border:none;border-radius:24px;font-weight:600;cursor:pointer">登録</button>
          </form>
        </div>
      </div>`;
  } catch (e) {
    return `${navbar("edge を登録", { back: "はじめの設定" })}
      <div class="page" style="padding:16px"><div style="color:var(--red)">${esc(e.message)}</div></div>`;
  }
}

function pFirewallForm() {
  return `${navbar("2. ファイアウォールを登録", { back: "はじめの設定" })}
    <div class="page"><h1 class="large">ファイアウォール</h1><div style="padding:16px">未実装</div></div>`;
}

function pLinodeForm() {
  return `${navbar("1. Linode アカウントを登録", { back: "はじめの設定" })}
    <div class="page"><h1 class="large">Linode アカウント</h1><div style="padding:16px">未実装</div></div>`;
}

// フォーム送信ハンドラー（app.js で呼び出される）
export function setupGettingStartedHandlers() {
  const form = document.getElementById("edge-form");
  if (!form) return;

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = new FormData(form);
    const payload = {
      name: data.get("name"),
      linode_account_id: parseInt(data.get("linode_account"), 10) || 1,
      linode_instance_id: parseInt(data.get("linode_instance"), 10) || 1,
      firewall_id: parseInt(data.get("firewall"), 10) || 1,
      public_ip: data.get("public_ip"),
      tailscale_ip: data.get("tailscale_ip"),
    };

    try {
      await api.post("/api/admin/edges", payload);
      toast("edge を登録しました", "ok");
      setTimeout(() => window.location.href = "/admin/start/firewalls", 1000);
    } catch (e) {
      document.getElementById("edge-error").textContent = e.message || "通信エラーが発生しました。";
      toast(e.message || "登録に失敗しました", "err");
    }
  });
}
