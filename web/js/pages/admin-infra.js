// 画面：管理 → ノードと edge（Linode のアカウント・ファイアウォール・edge）。docs/SPEC.md 6B、docs/TASKS.md T43。
import { api } from "../api.js";
import { banner, cell, esc, group } from "../components/cell.js";
import { openMenu, openPicker, pickerButton } from "../components/picker.js";
import { closeSheet, confirmSheet, onSheetClosed, openSheet, sheetHead } from "../components/sheet.js";
import { toast } from "../components/toast.js";
import { ctx, pageShell, shortTime } from "../ctx.js";
import { jobBanners, trackJob } from "./jobs.js";

let DATA = null; // 最後に読んだ一覧（シートで使う）
let FORM = null; // 入力中のシート { kind, values, options }

const fieldErr = (e) => [e.message, ...Object.values((e.detail && e.detail.fields) || {})].join("\n");

/* ---------------- 画面 ---------------- */
/** 一覧を読み直す（はじめの設定など、他の画面からシートやメニューを開くときにも使う）。 */
export async function loadData() {
  const [edges, fws, accounts] = await Promise.all([api.get("/admin/edges"), api.get("/admin/firewalls"), api.get("/admin/linode-accounts")]);
  DATA = { edges: edges.items, fws: fws.items, accounts: accounts.items };
  return DATA;
}

async function withData(fn) {
  try {
    await loadData();
  } catch (e) {
    return toast(e.message, "warn");
  }
  fn();
}

async function pInfra() {
  await loadData();
  const shared = (fw) => (fw.edges.length > 1 ? `共有：${fw.edges.join("・")}` : fw.edges.length ? `${fw.edges[0]} 専用` : "どの edge も使っていません");
  const edgeRows = DATA.edges.map((e) =>
    cell({
      icon: "net",
      color: e.is_active ? "var(--green)" : "var(--gray)",
      title: `${esc(e.id)}${e.is_active ? '<span class="pill g">使用中</span>' : ""}${e.last_error ? '<span class="pill r">エラー</span>' : ""}`,
      sub: `<span class="mono">${esc(e.public_ip)}</span>${e.tailscale_ip ? "" : "・Tailscale の IP は接続後に入ります"}・${e.account ? `${esc(e.account)}・ファイアウォール ${e.firewall ? esc(e.firewall) : "なし"}` : "Linode 以外"}${e.last_seen_at ? `・最終応答 ${esc(shortTime(e.last_seen_at))}` : "・まだ応答がありません"}`,
      subWrap: true,
      more: { act: "menu-edge", arg: e.id, label: `${e.id} の操作` },
    })
  );
  const fwRows = DATA.fws.map((f) =>
    cell({
      icon: "shield",
      color: f.last_error ? "var(--red)" : "var(--indigo)",
      title: `${esc(f.label)}${f.last_error ? '<span class="pill r">反映できません</span>' : ""}`,
      sub: `${esc(f.account)}・${esc(shared(f))}${f.last_error ? `<br><span class="err-t">${esc(f.last_error)}</span>` : f.synced_at ? `・${esc(shortTime(f.synced_at))} に反映` : ""}`,
      subWrap: true,
      more: { act: "menu-firewall", arg: String(f.id), label: `${f.label} の操作` },
    })
  );
  const accRows = DATA.accounts.map((a) =>
    cell({
      icon: "key",
      color: "var(--gray)",
      title: esc(a.label),
      sub: `edge ${a.edges}台・ファイアウォール ${a.firewalls}個`,
      more: { act: "menu-linode", arg: String(a.id), label: `${a.label} の操作` },
    })
  );
  return pageShell(
    "ノードと edge",
    `${jobBanners("admin")}
    ${DATA.edges.length ? "" : banner("info", "edge がまだ登録されていません", "Linode などで edge/install-edge.sh を実行した後、ここで登録してください。")}
    ${group(edgeRows.concat([`<button type="button" class="cell action" data-act="edge-add">edge を登録</button>`]), "edge（プレイヤーの入口）", "複数の edge で1つのファイアウォールを共有することも、edge ごとに別のファイアウォールを使うこともできます。")}
    ${group(
      fwRows.concat([`<button type="button" class="cell action" data-act="firewall-add" ${DATA.accounts.length ? "" : "disabled"}>ファイアウォールを登録</button>`]),
      "Linode Cloud Firewall",
      "ゲームのポートは、サーバーを作ると開き、完全に削除すると締まります（ゴミ箱の間は開いたまま）。Hatch が作るルールの名前は hatch- で始まり、手で作ったルール（SSH など）には触れません。"
    )}
    ${group(accRows.concat([`<button type="button" class="cell action" data-act="linode-add">Linode のアカウントを追加</button>`]), "Linode のアカウント", "契約ごとに API トークンを登録します。トークンは暗号化して保存し、画面には表示しません。")}
    ${group([cell({ icon: "server", color: "var(--gray)", title: "Wings のノード", val: '<span class="pill">準備中</span>', sub: "複数のゲームパネルとノードの登録は、今後この画面でできるようになります。", subWrap: true })], "ゲームサーバーのノード")}`
  );
}

/* ---------------- シート（入力欄と選択肢の組み合わせ） ---------------- */
function renderForm() {
  const f = FORM;
  const rows = f.fields.map((x) => {
    if (x.type === "pick") {
      const opts = x.options();
      return `<div class="field"><label>${esc(x.label)}</label>${pickerButton({ options: opts, value: f.values[x.key] ?? "", placeholder: x.placeholder || "選んでください", attrs: `data-act="form-pick" data-arg="${x.key}" aria-label="${esc(x.label)}"` })}</div>`;
    }
    return `<div class="field"><label for="ff-${x.key}">${esc(x.label)}</label><input id="ff-${x.key}" data-form="${x.key}" type="${x.secret ? "password" : "text"}" value="${esc(f.values[x.key] ?? "")}" placeholder="${esc(x.placeholder || "")}" autocomplete="off" autocapitalize="off" spellcheck="false" ${x.mono ? 'class="mono"' : ""}></div>`;
  });
  openSheet(
    `${sheetHead(esc(f.title), { left: "キャンセル" })}<div class="sheet-b">
      ${f.lead ? `<div class="sheet-lead">${f.lead}</div>` : ""}
      ${group(rows, "", f.foot || "")}
      <div id="form-err" class="err-t" role="alert" style="font-size:14px;margin:8px 4px;white-space:pre-line"></div>
      <button type="button" class="btn fill block" data-act="form-ok" id="form-ok">${esc(f.ok)}</button></div>`,
    "small"
  );
}

async function submitForm() {
  if (!FORM) return;
  const btns = ["form-ok"].map((i) => document.getElementById(i)).filter(Boolean);
  btns.forEach((b) => (b.disabled = true));
  try {
    await FORM.submit(FORM.values);
    FORM = null;
    closeSheet();
    ctx.refresh();
  } catch (e) {
    const box = document.getElementById("form-err");
    if (box) box.textContent = fieldErr(e);
    btns.forEach((b) => (b.disabled = false));
  }
}

export const TOKEN_GUIDE = `API トークンの作り方
  <ul class="howto">
    <li>Linode の Cloud Manager で、右上のアカウント → <b>API Tokens</b> を開く</li>
    <li><b>Create a Personal Access Token</b> を押す</li>
    <li><b>Linodes</b> と <b>Firewalls</b> を <b>Read/Write</b> にし、他はすべて <b>No Access</b> にする（Linode をファイアウォールに付けるため、Linodes も Read/Write が必要です）</li>
    <li>有効期限（Expiry）は <b>Never</b> にして作成し、表示されたトークンを下に貼り付ける</li>
  </ul>`;

function addAccount() {
  FORM = {
    title: "Linode を追加",
    ok: "追加",
    lead: TOKEN_GUIDE,
    values: {},
    fields: [
      { key: "label", label: "名前", placeholder: "例：Linode" },
      { key: "token", label: "API トークン", secret: true },
    ],
    submit: async (v) => {
      await api.post("/admin/linode-accounts", { label: (v.label || "").trim(), token: (v.token || "").trim() });
      toast("Linode のアカウントを追加しました");
    },
  };
  renderForm();
}

function replaceToken(a) {
  FORM = {
    title: "トークンを入れ替える",
    ok: "入れ替え",
    lead: `「${esc(a.label)}」の API トークンを、新しく作ったものに入れ替えます。古いトークンは Cloud Manager で削除して構いません。<br><br>${TOKEN_GUIDE}`,
    values: {},
    fields: [{ key: "token", label: "新しい API トークン", secret: true }],
    submit: async (v) => {
      await api.patch(`/admin/linode-accounts/${a.id}`, { token: (v.token || "").trim() });
      toast("トークンを入れ替えました");
    },
  };
  renderForm();
}

async function addFirewall() {
  FORM = {
    title: "ファイアウォールを登録",
    ok: "登録",
    lead: "Linode で作ったファイアウォールを選びます。まだ無ければ、Cloud Manager の <b>Firewalls → Create Firewall</b> で作り、SSH など必要なルールだけを入れてください。",
    values: { account: DATA.accounts[0] ? String(DATA.accounts[0].id) : "" },
    cache: {},
    fields: [
      { key: "account", label: "アカウント", type: "pick", options: () => DATA.accounts.map((a) => ({ value: String(a.id), label: a.label })) },
      {
        key: "firewall",
        label: "ファイアウォール",
        type: "pick",
        options: () => (FORM.cache[FORM.values.account] || []).filter((x) => !x.managed_id).map((x) => ({ value: String(x.id), label: x.label, sub: x.status })),
      },
    ],
    submit: async (v) => {
      if (!v.firewall) throw new Error("ファイアウォールを選んでください。");
      await api.post("/admin/firewalls", { linode_account_id: Number(v.account), linode_firewall_id: Number(v.firewall) });
      toast("ファイアウォールを登録しました");
    },
  };
  await loadFirewallChoices();
  renderForm();
}

async function loadFirewallChoices() {
  const acc = FORM.values.account;
  if (!acc || FORM.cache[acc]) return;
  try {
    FORM.cache[acc] = (await api.get(`/admin/linode-accounts/${acc}/firewalls`)).items;
  } catch (e) {
    FORM.cache[acc] = [];
    toast(e.message, "warn");
  }
}

async function loadLinodes() {
  const acc = FORM.values.account;
  if (!acc || FORM.linodes[acc]) return;
  try {
    FORM.linodes[acc] = (await api.get(`/admin/linode-accounts/${acc}/linodes`)).items;
  } catch (e) {
    FORM.linodes[acc] = [];
    toast(e.message, "warn");
  }
}

export const EDGE_GUIDE = `edge は、プレイヤーが接続する入口のサーバーです（edge/install-edge.sh を実行したサーバー）。
  <ul class="howto">
    <li><b>名前</b>：install-edge.sh を実行したときに「edge の名前」に入力したもの（例 edge-1）。edge はこの名前で Hatch から設定を受け取るので、同じにします。忘れたときは、edge のサーバーで <code>grep EDGE_ID /etc/hatch-edge/agent.env</code></li>
    <li><b>Linode・ファイアウォール</b>：Linode で動かしている場合に選びます。公開 IP は自動で入ります</li>
    <li><b>Tailscale の IP</b>：空欄で構いません。edge が Hatch に接続すると自動で入ります</li>
  </ul>`;

function renameAccount(a) {
  FORM = {
    title: "名前を変更",
    ok: "保存",
    values: { label: a.label },
    fields: [{ key: "label", label: "名前", placeholder: "例：Linode" }],
    submit: async (v) => {
      await api.patch(`/admin/linode-accounts/${a.id}`, { label: (v.label || "").trim() });
      toast("名前を変更しました");
    },
  };
  renderForm();
}

async function edgeForm(edge) {
  const isNew = !edge;
  FORM = {
    title: isNew ? "edge を登録" : `${edge.id} を変更`,
    ok: isNew ? "登録" : "保存",
    lead: isNew ? EDGE_GUIDE : "",
    values: isNew
      ? { account: "" }
      : {
          id: edge.id,
          public_ip: edge.public_ip,
          tailscale_ip: edge.tailscale_ip,
          account: edge.linode_account_id ? String(edge.linode_account_id) : "",
          linode: edge.linode_id ? String(edge.linode_id) : "",
          firewall: edge.firewall_id ? String(edge.firewall_id) : "",
        },
    linodes: {},
    fields: [
      ...(isNew ? [{ key: "id", label: "名前", placeholder: "例：edge-1" }] : []),
      { key: "account", label: "Linode のアカウント", type: "pick", placeholder: "Linode 以外", options: () => [{ value: "", label: "Linode 以外", sub: "ファイアウォールは管理しません" }, ...DATA.accounts.map((a) => ({ value: String(a.id), label: a.label }))] },
      {
        key: "linode",
        label: "Linode",
        type: "pick",
        options: () => (FORM.linodes[FORM.values.account] || []).map((x) => ({ value: String(x.id), label: x.label, sub: `${x.region}・${x.ipv4.join(", ")}` })),
      },
      {
        key: "firewall",
        label: "ファイアウォール",
        type: "pick",
        placeholder: "使わない",
        options: () => [
          { value: "", label: "使わない" },
          ...DATA.fws.filter((f) => String(f.linode_account_id) === FORM.values.account).map((f) => ({ value: String(f.id), label: f.label, sub: f.edges.length ? `共有：${f.edges.join("・")}` : "まだどの edge も使っていません" })),
        ],
      },
      { key: "public_ip", label: "公開 IP", placeholder: "Linode を選ぶと自動で入ります", mono: true },
      { key: "tailscale_ip", label: "Tailscale の IP", placeholder: "空欄なら自動", mono: true },
    ],
    submit: async (v) => {
      const body = { public_ip: (v.public_ip || "").trim() };
      if ((v.tailscale_ip || "").trim()) body.tailscale_ip = v.tailscale_ip.trim();
      if (v.account) {
        body.linode_account_id = Number(v.account);
        body.linode_id = v.linode ? Number(v.linode) : null;
        if (v.firewall) body.firewall_id = Number(v.firewall);
      }
      let r;
      if (isNew) {
        r = await api.post("/admin/edges", { id: (v.id || "").trim(), ...body });
        toast("edge を登録しました");
      } else {
        if (!v.account) body.clear_linode = true;
        else if (!v.firewall) body.clear_firewall = true;
        r = await api.patch(`/admin/edges/${encodeURIComponent(edge.id)}`, body);
        toast("edge を変更しました");
      }
      if (r.job && r.job.id) trackJob({ id: r.job.id, kind: "sync_firewall", name: "ファイアウォール" });
      for (const id of r.dns_jobs || []) trackJob({ id, kind: "sync_binding", name: "edge の A レコード" });
    },
  };
  await loadLinodes();
  renderForm();
}

/* ---------------- 操作 ---------------- */
export const INFRA_ACTIONS = {
  "linode-add"() {
    addAccount();
  },
  "firewall-add"() {
    withData(addFirewall);
  },
  "edge-add"() {
    withData(() => edgeForm(null));
  },
  "form-ok"() {
    submitForm();
  },
  "form-pick"(key, el) {
    const field = FORM.fields.find((x) => x.key === key);
    openPicker(el, field.options(), FORM.values[key] ?? "", async (v) => {
      FORM.values[key] = v;
      if (key === "account") {
        FORM.values.linode = "";
        FORM.values.firewall = "";
        if (FORM.linodes) await loadLinodes();
        if (FORM.cache) await loadFirewallChoices();
      }
      if (key === "linode" && FORM.linodes) {
        const li = (FORM.linodes[FORM.values.account] || []).find((x) => String(x.id) === v);
        if (li && li.ipv4.length) FORM.values.public_ip = li.ipv4[0];
      }
      renderForm();
      document.querySelector(`[data-act="form-pick"][data-arg="${key}"]`)?.focus();
    });
  },
  "menu-edge"(id, el) {
    const e = DATA.edges.find((x) => x.id === id);
    if (!e) return;
    openMenu(el, [
      { label: "変更（Linode・ファイアウォール）", icon: "tune", run: () => edgeForm(e) },
      "-",
      {
        label: "削除",
        icon: "trash",
        red: true,
        disabled: e.is_active,
        run: () =>
          confirmSheet({
            title: "edge を削除",
            body: `<b>${esc(id)}</b> の登録を削除します。edge のサーバー自体は消えません。`,
            ok: "削除",
            danger: true,
            onOk: async () => {
              await api.del(`/admin/edges/${encodeURIComponent(id)}`);
              toast("edge を削除しました");
              ctx.refresh();
            },
          }),
      },
    ]);
  },
  "menu-firewall"(id, el) {
    const f = DATA.fws.find((x) => String(x.id) === id);
    if (!f) return;
    openMenu(el, [
      {
        label: "今すぐ反映",
        icon: "restart",
        run: async () => {
          try {
            const r = await api.post(`/admin/firewalls/${id}/sync`);
            trackJob({ id: r.job.id, kind: "sync_firewall", name: f.label });
          } catch (e) {
            toast(e.message, "warn");
          }
        },
      },
      "-",
      {
        label: "管理をやめる",
        icon: "trash",
        red: true,
        disabled: f.edges.length > 0,
        run: () =>
          confirmSheet({
            title: "ファイアウォールの管理をやめる",
            body: `「${esc(f.label)}」から Hatch のルールを削除し、管理をやめます。手で作ったルールとファイアウォール自体は残ります。`,
            ok: "管理をやめる",
            danger: true,
            onOk: async () => {
              // Linode のルールを消せなかったら、もう一度押すと Hatch の登録だけ外す
              const keep = document.getElementById("cf-ok")?.dataset.keep === "1";
              try {
                await api.del(`/admin/firewalls/${id}${keep ? "?keep_rules=true" : ""}`);
              } catch (e) {
                const b = document.getElementById("cf-ok");
                if (e.code === "linode_cleanup_failed" && b) {
                  b.dataset.keep = "1";
                  b.textContent = "登録だけ外す";
                }
                throw e;
              }
              toast(keep ? "Hatch の登録を外しました" : "管理をやめました");
              ctx.refresh();
            },
          }),
      },
    ]);
  },
  "menu-linode"(id, el) {
    const a = DATA.accounts.find((x) => String(x.id) === id);
    if (!a) return;
    openMenu(el, [
      { label: "名前を変更", icon: "tune", run: () => renameAccount(a) },
      { label: "トークンを入れ替える", icon: "key", run: () => replaceToken(a) },
      "-",
      {
        label: "削除",
        icon: "trash",
        red: true,
        disabled: a.edges > 0 || a.firewalls > 0,
        run: () =>
          confirmSheet({
            title: "Linode のアカウントを削除",
            body: `「${esc(a.label)}」の API トークンを削除します。Linode の契約やサーバーには影響しません。`,
            ok: "削除",
            danger: true,
            onOk: async () => {
              await api.del(`/admin/linode-accounts/${id}`);
              toast("アカウントを削除しました");
              ctx.refresh();
            },
          }),
      },
    ]);
  },
};

document.addEventListener("input", (e) => {
  const t = e.target;
  if (FORM && t.dataset.form) FORM.values[t.dataset.form] = t.value;
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && FORM && e.target.dataset && e.target.dataset.form) {
    e.preventDefault();
    submitForm();
  }
});
onSheetClosed(() => {
  FORM = null;
});

export const INFRA_PAGES = {
  nodes: () => pInfra(),
};
