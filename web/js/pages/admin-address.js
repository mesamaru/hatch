// 画面：管理（トップ）と、アドレス（ドメイン・IP と紐付け・アドレス枠）。docs/UI.md 3.2、docs/TASKS.md T16。
// 見た目の正解は web/demo.html。まだ API の無い管理画面は「準備中」を出す。
import { api } from "../api.js";
import { banner, cell, esc, group } from "../components/cell.js";
import { ic } from "../components/icons.js";
import { openMenu, openPicker, pickerButton } from "../components/picker.js";
import { closeSheet, confirmSheet, onSheetClosed, openSheet, sheetHead } from "../components/sheet.js";
import { toast } from "../components/toast.js";
import { copyText, ctx, navbar, pageShell } from "../ctx.js";
import { loadStart, startBanner } from "./getting-started.js";
import { jobBanners, trackJob } from "./jobs.js";

const NAMES = { domain: new Map(), rule: new Map() }; // 見出しに出す名前（ID → 名前）
const ROLE_LABELS = { admin: "管理者", supporter: "サポーター", user: "利用者" };
const isFixed = (r) => !r.host_template.includes("{server}");
const fieldErr = (e) => [e.message, ...Object.values((e.detail && e.detail.fields) || {}), ...((e.detail && e.detail.problems) || [])].join("\n");

/* ---------------- 管理（トップ） ---------------- */
async function pAdmin() {
  const [domains, rules, ips, bindings, gs] = await Promise.all([
    api.get("/admin/domains"),
    api.get("/admin/slot-rules"),
    api.get("/admin/ips"),
    api.get("/admin/bindings"),
    loadStart(),
  ]);
  const free = rules.items.reduce((a, r) => a + (r.stats ? r.stats.free : 0), 0);
  const later = (icon, color, title, page) => cell({ icon, color, title, val: '<span class="pill">準備中</span>', act: "go", arg: page });
  return `${navbar("管理")}<div class="page"><h1 class="large">管理</h1>
    ${jobBanners("admin")}
    ${startBanner(gs)}
    ${group(
      [
        cell({ icon: "globe", color: "var(--teal)", title: "ドメイン", val: String(domains.items.length), act: "go", arg: "domains" }),
        cell({ icon: "pin", color: "var(--indigo)", title: "IP と紐付け", val: `${ips.items.length} / ${bindings.items.length}件`, act: "go", arg: "ips" }),
        cell({ icon: "grid", color: "var(--blue)", title: "アドレス枠", val: `空き ${free}`, act: "go", arg: "rules" }),
      ],
      "アドレス",
      domains.items.length
        ? "ポートとホスト名の対応（例 25561 ↔ mc01trt.nuids.jp）を先に作っておくと、利用者は空きから選ぶだけで作成できます。"
        : "<b>上の「はじめの設定」から、順番どおりに進めてください。</b>edge を登録してからドメインを登録します。"
    )}
    ${group([cell({ icon: "people", color: "var(--green)", title: "ユーザー", act: "go", arg: "users" }), later("user", "var(--indigo)", "Discord ロール連携", "roles"), later("bell", "var(--red)", "お知らせ", "announce"), later("gavel", "var(--orange)", "違反対応", "violations")], "利用者")}
    ${group([later("pulse", "var(--green)", "システムの状態", "system"), later("sparkle", "var(--green)", "整合性チェック", "health"), cell({ icon: "server", color: "var(--blue)", title: "ノードと edge", sub: "Linode のファイアウォール", act: "go", arg: "nodes" }), later("clock", "var(--orange)", "期限が近いサーバー", "expiring")], "運用")}
    ${group([cell({ icon: "list", color: "var(--blue)", title: "はじめの設定", val: gs ? `${gs.done} / ${gs.total}` : "", act: "go", arg: "start" }), later("list", "var(--gray)", "操作ログ", "audit"), later("key", "var(--gray)", "API キー", "keys")], "記録と設定")}
  </div>`;
}

function pLater(page) {
  const titles = { roles: "ロール連携", announce: "お知らせ", violations: "違反対応", system: "システムの状態", health: "整合性チェック", nodes: "ノードと edge", expiring: "期限が近い", audit: "操作ログ", keys: "API キー" };
  return pageShell(titles[page] || "準備中", group([cell({ icon: "clock", color: "var(--gray)", title: "この画面は準備中です", sub: "今後のバージョンで使えるようになります。", subWrap: true })]));
}

async function pUsers() {
  const r = await api.get("/admin/users");
  const state = { invited: "招待中", suspended: "利用停止中", deleting: "退会予定" };
  return pageShell(
    "ユーザー",
    group(
      r.items.map((u) =>
        cell({
          icon: "user",
          color: "var(--gray)",
          title: `${esc(u.username)}${u.role !== "user" ? `<span class="pill b">${ROLE_LABELS[u.role]}</span>` : ""}${state[u.status] ? `<span class="pill o">${state[u.status]}</span>` : ""}`,
          sub: `${ROLE_LABELS[u.role] || u.role}・作成できる台数 ${u.max_servers}`,
        })
      ),
      "",
      "権限と台数は Discord のロールで決まり、ログインのたびに更新されます。"
    )
  );
}

/* ---------------- ドメイン ---------------- */
async function pDomains() {
  const r = await api.get("/admin/domains");
  r.items.forEach((d) => NAMES.domain.set(String(d.id), d.name));
  return pageShell(
    "ドメイン",
    `${jobBanners("admin")}${group(
      r.items.length
        ? r.items.map((d) =>
            cell({
              icon: "globe",
              color: "var(--teal)",
              title: `<span class="mono">${esc(d.name)}</span>${d.is_default ? '<span class="pill b">既定</span>' : ""}`,
              sub: `ゾーン ${esc(d.cf_zone_id.slice(0, 8))}…・${d.verified_at ? "接続確認済み" : "未確認"}`,
              val: `${d.rules}枠`,
              act: "go-domain",
              arg: String(d.id),
            })
          )
        : [cell({ title: "ドメインはまだありません", sub: "右上の＋から追加できます" })],
      "",
      "Cloudflare の API トークンには、登録するすべてのゾーンの DNS 編集権限が必要です。"
    )}`,
    { right: `<button type="button" class="circle tint" data-act="domain-add" aria-label="ドメインを追加">${ic("plus")}</button>` }
  );
}

async function pDomain(id) {
  const [domains, bindings, rules] = await Promise.all([api.get("/admin/domains"), api.get("/admin/bindings"), api.get("/admin/slot-rules")]);
  const d = domains.items.find((x) => String(x.id) === String(id));
  if (!d) return pageShell("ドメイン", "<p>見つかりません。削除された可能性があります。</p>");
  NAMES.domain.set(String(d.id), d.name);
  const bs = bindings.items.filter((b) => b.domain_id === d.id);
  const rs = rules.items.filter((r) => r.domain_id === d.id);
  rs.forEach((r) => NAMES.rule.set(String(r.id), r.name));
  return pageShell(
    d.name,
    `${jobBanners("admin")}
    ${group([
      cell({ title: "Cloudflare ゾーンID", sub: `<span class="mono">${esc(d.cf_zone_id)}</span>`, subWrap: true, val: `<button type="button" class="btn sm" data-act="copy" data-arg="${esc(d.cf_zone_id)}" aria-label="ゾーンIDをコピー">${ic("copy")}</button>` }),
      cell({ title: "接続確認", val: d.verified_at ? '<span class="pill g">成功</span>' : '<span class="pill o">未確認</span>' }),
      cell({ title: "既定のドメイン", sub: "新しい枠を作るときの初期値", val: `<input type="checkbox" class="switch" data-domain-def="${d.id}" ${d.is_default ? "checked disabled" : ""} aria-label="既定のドメインにする">` }),
    ])}
    ${group(
      bs.map((b) => bindingCell(b)).concat([`<button type="button" class="cell action" data-act="bind-add" data-arg="${d.id}">紐付けを追加</button>`]),
      "IP の紐付け（A レコード）"
    )}
    ${group(
      rs.map((r) => ruleCell(r)).concat([`<button type="button" class="cell action" data-act="rule-new" data-arg="${d.id}">このドメインで枠を追加</button>`]),
      "アドレス枠"
    )}
    <div style="height:22px"></div>${group([`<button type="button" class="cell danger" data-act="domain-del" data-arg="${d.id}" ${bs.length || rs.length ? "disabled" : ""}>このドメインを削除</button>`], "", bs.length || rs.length ? "紐付けとアドレス枠を先に削除すると、ドメインを削除できます。" : "")}`,
    { sub: "" }
  );
}

function bindingCell(b) {
  const fq = b.host === "@" ? b.domain : `${b.host}.${b.domain}`;
  return cell({
    icon: "pin",
    color: "var(--indigo)",
    title: `<span class="mono">${esc(fq)}</span>`,
    sub: b.follow_active_edge ? "使用中の edge に追従" : `→ ${esc(b.ip || "—")}`,
    more: { act: "menu-binding", arg: `${b.id}|${fq}`, label: `${fq} の操作` },
  });
}
function ruleCell(r) {
  const s = r.stats || { total: 0, free: 0 };
  return cell({
    icon: "grid",
    color: isFixed(r) ? "var(--blue)" : "var(--gray)",
    title: esc(r.name),
    sub: `<span class="mono">${esc(r.host_template)}</span>・${r.port_start}–${r.port_end}・${r.assign_to === "shared" ? "全員" : "専用"}`,
    val: `空き ${s.free}/${s.total}`,
    act: "go-rule",
    arg: String(r.id),
  });
}

/* ---------------- IP と紐付け ---------------- */
async function pIps() {
  const [ips, bindings] = await Promise.all([api.get("/admin/ips"), api.get("/admin/bindings")]);
  return pageShell(
    "IP と紐付け",
    `${jobBanners("admin")}
    ${group(
      ips.items.length
        ? ips.items.map((i) => cell({ icon: "pin", color: "var(--indigo)", title: esc(i.label), sub: `<span class="mono">${esc(i.address)}</span>`, more: { act: "menu-ip", arg: `${i.id}|${i.address}`, label: `${i.address} の操作` } }))
        : [cell({ title: "IP はまだありません", sub: "右上の＋から、edge などの公開 IP を登録できます", subWrap: true })],
      "IP アドレス"
    )}
    ${group(bindings.items.map(bindingCell).concat([`<button type="button" class="cell action" data-act="bind-add" data-arg="">紐付けを追加</button>`]), "ホスト名 → IP", "アドレス枠は既定で edge.&lt;ドメイン&gt; への CNAME を作ります。「使用中の edge に追従」の紐付けは、edge を切り替えるとまとめて書き換わります。")}`,
    { right: `<button type="button" class="circle tint" data-act="ip-add" aria-label="IP を追加">${ic("plus")}</button>` }
  );
}

/* ---------------- アドレス枠 ---------------- */
async function pRules() {
  const [domains, rules] = await Promise.all([api.get("/admin/domains"), api.get("/admin/slot-rules")]);
  rules.items.forEach((r) => NAMES.rule.set(String(r.id), r.name));
  const body = domains.items
    .map((d) => {
      const rs = rules.items.filter((r) => r.domain_id === d.id);
      return rs.length ? group(rs.map(ruleCell), esc(d.name)) : "";
    })
    .join("");
  return pageShell(
    "アドレス枠",
    `${jobBanners("admin")}${
      body ||
      group([cell({ title: "アドレス枠はまだありません", sub: domains.items.length ? "右上の＋から追加できます" : "先に「ドメイン」を登録してください", subWrap: true })])
    }<div class="gf" style="margin-top:12px">固定のホスト名（例 mc{nn}trt）は DNS を先に作っておけます。{server} はサーバー名がホスト名になります。</div>`,
    { right: domains.items.length ? `<button type="button" class="circle tint" data-act="rule-new" data-arg="" aria-label="枠を追加">${ic("plus")}</button>` : "" }
  );
}

async function pRule(id) {
  let r;
  try {
    r = await api.get(`/admin/slot-rules/${encodeURIComponent(id)}`);
  } catch (e) {
    if (e.status === 404) return pageShell("アドレス枠", "<p>見つかりません。削除された可能性があります。</p>");
    throw e;
  }
  NAMES.rule.set(String(r.id), r.name);
  const fixed = isFixed(r);
  const live = r.slots.filter((s) => s.status !== "disabled");
  const published = live.length > 0 && live.every((s) => s.dns_state === "published");
  const someDns = r.slots.some((s) => s.dns_state !== "none");
  const excluded = new Set(r.excluded_ports);
  const byPort = new Map(r.slots.map((s) => [s.port, s]));
  const cells = [];
  for (let port = r.port_start; port <= r.port_end; port++) {
    const s = byPort.get(port);
    if (!s || excluded.has(port)) {
      cells.push(`<button type="button" class="slot off" disabled><span class="p">${port}</span><span class="h">—</span><span style="font-size:12px;color:var(--label2)">対象外</span></button>`);
      continue;
    }
    const cls = { assigned: "used", held: "held", disabled: "off" }[s.status] || "";
    const what = { free: "空き", assigned: `使用中：${esc(s.server_name || "")}`, held: `ゴミ箱：${esc(s.server_name || "")}`, disabled: "停止中" }[s.status];
    cells.push(
      `<button type="button" class="slot ${cls}" data-act="menu-slot" data-arg="${port}|${r.id}"><span class="p">${port}</span><span class="h">${esc(s.host || s.server_name || "（名前で決定）")}</span><span style="font-size:12px;color:var(--label2)">${what}</span></button>`
    );
  }
  SLOT_CACHE = { rule: r, byPort };
  const dnsBanner =
    fixed && r.prepublish
      ? banner(
          published ? "ok" : "w",
          published ? "DNS は作成済み" : "DNS はまだ作成していません",
          published ? `割り当てた瞬間から接続できます（CNAME${r.create_srv ? "・SRV" : ""} ${live.length}件）。` : "作成しておくと、割り当てた瞬間から接続できます。",
          `${published ? "" : `<button type="button" class="btn sm fill" data-act="rule-publish" data-arg="${r.id}">DNS を作成</button>`}${someDns ? `<button type="button" class="btn sm" data-act="rule-unpublish" data-arg="${r.id}">DNS を削除</button>` : ""}`
        )
      : "";
  return pageShell(
    r.name,
    `${jobBanners("admin")}${dnsBanner}<div class="slotgrid">${cells.join("")}</div>
      <div class="gf" style="margin-top:12px">スロットを押すと、アドレスのコピー・停止・割り当て先のサーバーへの移動ができます。</div>`,
    {
      wide: true,
      right: `<button type="button" class="back" data-act="rule-edit" data-arg="${r.id}" style="margin:0">編集</button>`,
      sub: `<p class="subtitle"><span class="mono">${esc(r.host_template)}.${esc(r.domain)}</span>・${r.port_start}–${r.port_end}・${r.assign_to === "shared" ? "全員で共有" : "専用"}</p>`,
    }
  );
}
let SLOT_CACHE = null;

/* ---------------- シート：ドメイン・IP・紐付けの追加 ---------------- */
function simpleSheet(title, fields, okLabel, onOk, foot = "") {
  openSheet(
    `${sheetHead(esc(title), { left: "キャンセル", right: esc(okLabel), rightAct: "simple-ok", rightId: "simple-ok-top" })}<div class="sheet-b">
    ${group(fields, "", foot)}
    <div id="simple-err" class="err-t" role="alert" style="font-size:14px;margin:8px 4px;white-space:pre-line"></div>
    <button type="button" class="btn fill block" data-act="simple-ok" id="simple-ok">${esc(okLabel)}</button></div>`,
    "small"
  );
  SIMPLE = onOk;
}
let SIMPLE = null;

async function runSimple() {
  if (!SIMPLE) return;
  const btns = ["simple-ok", "simple-ok-top"].map((i) => document.getElementById(i)).filter(Boolean);
  btns.forEach((b) => (b.disabled = true));
  try {
    await SIMPLE();
    SIMPLE = null;
    closeSheet();
    ctx.refresh();
  } catch (e) {
    const box = document.getElementById("simple-err");
    if (box) box.textContent = fieldErr(e);
    btns.forEach((b) => (b.disabled = false));
  }
}
const val = (id) => (document.getElementById(id)?.value || "").trim();

function addDomain() {
  simpleSheet(
    "ドメインを追加",
    [
      `<div class="field"><label for="d-name">ドメイン</label><input id="d-name" placeholder="例：nuids.jp" autocomplete="off" autocapitalize="off" spellcheck="false"></div>`,
      `<div class="field"><label for="d-zone">ゾーンID</label><input id="d-zone" class="mono" placeholder="32桁の英数字" autocomplete="off" autocapitalize="off" spellcheck="false"></div>`,
    ],
    "追加",
    async () => {
      const r = await api.post("/admin/domains", { name: val("d-name").toLowerCase(), cf_zone_id: val("d-zone").toLowerCase() });
      trackJob({ id: r.job.id, kind: "sync_binding", name: `edge.${r.name}` });
      toast(`${r.name} を追加しました`);
    },
    "ゾーンID は Cloudflare でドメインを開いた「概要」ページの右下にあります。追加すると、edge.&lt;ドメイン&gt; の A レコードを自動で作ります。"
  );
}

function addIp() {
  simpleSheet(
    "IP を追加",
    [
      `<div class="field"><label for="i-label">名前</label><input id="i-label" placeholder="例：edge-1（東京）" autocomplete="off"></div>`,
      `<div class="field"><label for="i-addr">IP アドレス</label><input id="i-addr" class="mono" placeholder="例：203.0.113.10" inputmode="decimal" autocomplete="off"></div>`,
    ],
    "追加",
    async () => {
      await api.post("/admin/ips", { label: val("i-label"), address: val("i-addr") });
      toast("IP を追加しました");
    },
    "公開されている IPv4 アドレスを登録します。"
  );
}

let BIND = null;
async function addBinding(domainId) {
  try {
    const [domains, ips] = await Promise.all([api.get("/admin/domains"), api.get("/admin/ips")]);
    BIND = { domains: domains.items, ips: ips.items, domain: domainId ? Number(domainId) : (domains.items.find((d) => d.is_default) || domains.items[0] || {}).id, target: "follow" };
  } catch (e) {
    return toast(e.message, "warn");
  }
  if (!BIND.domains.length) return toast("先にドメインを登録してください", "warn");
  renderBinding();
}
function renderBinding() {
  const host = val("b-host");
  const targets = [{ value: "follow", label: "使用中の edge に追従", sub: "edge を切り替えると自動で書き換わります" }, ...BIND.ips.map((i) => ({ value: String(i.id), label: `${i.label}（${i.address}）` }))];
  simpleSheet(
    "紐付けを追加",
    [
      `<div class="field"><label>ドメイン</label>${pickerButton({ options: BIND.domains.map((d) => ({ value: String(d.id), label: d.name })), value: String(BIND.domain), attrs: 'data-act="bind-domain" aria-label="ドメイン"' })}</div>`,
      `<div class="field"><label for="b-host">ホスト名</label><input id="b-host" value="${esc(host)}" placeholder="例：edge、@ はドメインそのもの" autocomplete="off" autocapitalize="off" spellcheck="false"></div>`,
      `<div class="field"><label>向き先</label>${pickerButton({ options: targets, value: BIND.target, attrs: 'data-act="bind-target" aria-label="向き先"' })}</div>`,
    ],
    "追加",
    async () => {
      const follow = BIND.target === "follow";
      const r = await api.post("/admin/bindings", { domain_id: BIND.domain, host: val("b-host").toLowerCase(), follow_active_edge: follow, ip_id: follow ? null : Number(BIND.target) });
      const d = BIND.domains.find((x) => x.id === BIND.domain);
      trackJob({ id: r.job.id, kind: "sync_binding", name: `${val("b-host")}.${d ? d.name : ""}` });
      toast("紐付けを追加しました");
    },
    "A レコードを作ります（プロキシなし）。アドレス枠のホスト名と同じ名前は使えません。"
  );
}

/* ---------------- シート：アドレス枠の追加・編集 ---------------- */
let R = null; // 編集中の枠
let previewTimer = null;

async function openRule(id, domainId) {
  try {
    const [domains, ips, rules] = await Promise.all([api.get("/admin/domains"), api.get("/admin/ips"), api.get("/admin/slot-rules")]);
    const base = id ? await api.get(`/admin/slot-rules/${id}`) : null;
    R = { domains: domains.items, ips: ips.items, users: null, problems: [], slots: [], saving: false };
    if (base) {
      R.v = { ...base, excluded_text: base.excluded_ports.join(", ") };
    } else {
      const d = domains.items.find((x) => String(x.id) === String(domainId)) || domains.items.find((x) => x.is_default) || domains.items[0];
      if (!d) return toast("先にドメインを登録してください", "warn");
      let ps = 25560;
      const overlaps = (a, b) => rules.items.some((x) => !(b < x.port_start || a > x.port_end));
      while (overlaps(ps, ps + 9) && ps < 65000) ps += 10;
      R.v = { id: null, name: "", domain_id: d.id, port_start: ps, port_end: ps + 9, host_template: "mc{nn}trt", number_start: ps % 100, excluded_ports: [], excluded_text: "", record_mode: "cname_edge", ip_id: null, create_srv: true, prepublish: true, assign_to: "shared", user_id: null };
    }
    if (R.v.assign_to === "user") R.users = (await api.get("/admin/users")).items;
  } catch (e) {
    return toast(e.message, "warn");
  }
  renderRule();
  preview();
}

function ruleBody() {
  const v = R.v;
  const out = { ...v };
  delete out.excluded_text;
  delete out.id;
  delete out.domain;
  delete out.stats;
  delete out.dns_published;
  delete out.slots;
  out.excluded_ports = (v.excluded_text || "")
    .split(/[\s,、]+/)
    .filter(Boolean)
    .map(Number)
    .filter((n) => Number.isInteger(n));
  for (const k of ["port_start", "port_end", "number_start"]) out[k] = Number(v[k]);
  return out;
}

function renderRule(focusId) {
  const v = R.v;
  const d = R.domains.find((x) => x.id === v.domain_id) || R.domains[0];
  const fixed = !String(v.host_template).includes("{server}");
  const scroll = document.querySelector("#scrim .sheet-b")?.scrollTop || 0;
  openSheet(
    `${sheetHead(v.id ? "アドレス枠を編集" : "アドレス枠を追加", { left: "キャンセル", right: "保存", rightAct: "rule-save", rightDisabled: true, rightId: "rule-save" })}<div class="sheet-b">
    ${group([
      `<div class="field"><label for="r-name">名前</label><input id="r-name" data-rule="name" value="${esc(v.name)}" placeholder="例：Minecraft 共有枠" autocomplete="off"></div>`,
      `<div class="field"><label>ドメイン</label>${pickerButton({ options: R.domains.map((x) => ({ value: String(x.id), label: x.name })), value: String(v.domain_id), attrs: 'data-act="rule-pick" data-arg="domain_id" aria-label="ドメイン"' })}</div>`,
    ])}
    ${group(
      [
        `<div class="field"><label for="r-ps">開始ポート</label><input id="r-ps" data-rule="port_start" type="number" inputmode="numeric" value="${esc(v.port_start)}"></div>`,
        `<div class="field"><label for="r-pe">終了ポート</label><input id="r-pe" data-rule="port_end" type="number" inputmode="numeric" value="${esc(v.port_end)}"></div>`,
        `<div class="field"><label for="r-ex">対象外</label><input id="r-ex" data-rule="excluded_text" value="${esc(v.excluded_text)}" placeholder="例：25560" inputmode="numeric" autocomplete="off"></div>`,
      ],
      "ポート",
      "対象外のポートは使いません。番号はポートに固定なので、25560 を対象外にしても 25561 は mc01 のままです。"
    )}
    ${group(
      [
        `<div class="field"><label for="r-tpl">ホスト名</label><input id="r-tpl" data-rule="host_template" value="${esc(v.host_template)}" class="mono" autocomplete="off" autocapitalize="off" spellcheck="false"><span class="unit">.${esc(d ? d.name : "")}</span></div>`,
        `<div class="field"><label for="r-st">開始番号</label><input id="r-st" data-rule="number_start" type="number" inputmode="numeric" value="${esc(v.number_start)}" ${fixed ? "" : "disabled"}></div>`,
        `<div class="cell" style="flex-wrap:wrap;gap:6px">${["{nn}", "{n}", "{nnn}", "{port}", "{server}"].map((t) => `<button type="button" class="btn sm" data-act="tpl-token" data-arg="${t}">${t}</button>`).join("")}</div>`,
      ],
      "ホスト名の決め方",
      "{nn} は2桁の番号（開始ポートが「開始番号」）、{port} はポート番号、{server} はサーバー名になります。"
    )}
    <div class="gh" id="r-count">プレビュー</div><div class="group"><table class="pv" id="r-preview"></table></div>
    <div class="gf err-t" role="alert" id="r-probs"></div>
    ${group(
      [
        `<div class="field"><label>DNS</label>${pickerButton({ options: [{ value: "cname_edge", label: "edge への CNAME（推奨）" }, { value: "a_ip", label: "IP を直接指定" }], value: v.record_mode, attrs: 'data-act="rule-pick" data-arg="record_mode" aria-label="DNS の形"' })}</div>`,
        v.record_mode === "a_ip"
          ? `<div class="field"><label>IP</label>${pickerButton({ options: R.ips.map((i) => ({ value: String(i.id), label: `${i.label}（${i.address}）` })), value: v.ip_id ? String(v.ip_id) : "", attrs: 'data-act="rule-pick" data-arg="ip_id" aria-label="IP"' })}</div>`
          : "",
        cell({ title: "SRV レコード", sub: "Minecraft でポートを入力せずに接続できます", subWrap: true, val: `<input type="checkbox" class="switch" data-rule-bool="create_srv" ${v.create_srv ? "checked" : ""} aria-label="SRV レコード">` }),
        cell({ title: "DNS を先に作成", sub: fixed ? "割り当てた瞬間から接続できます" : "{server} の枠では使えません", subWrap: true, val: `<input type="checkbox" class="switch" data-rule-bool="prepublish" ${v.prepublish && fixed ? "checked" : ""} ${fixed ? "" : "disabled"} aria-label="DNS を先に作成">` }),
      ].filter(Boolean),
      "DNS",
      v.record_mode === "a_ip" ? "IP 直指定の場合、edge を切り替えるとこの枠のレコードもすべて書き換えます。" : `edge.${esc(d ? d.name : "")} を向くので、edge を切り替えてもこの枠のレコードは変わりません。`
    )}
    ${group(
      [
        `<div class="field"><label>使える人</label>${pickerButton({ options: [{ value: "shared", label: "全員で共有" }, { value: "user", label: "特定のユーザー" }], value: v.assign_to, attrs: 'data-act="rule-pick" data-arg="assign_to" aria-label="使える人"' })}</div>`,
        v.assign_to === "user"
          ? `<div class="field"><label>ユーザー</label>${pickerButton({ options: (R.users || []).map((u) => ({ value: u.id, label: u.username })), value: v.user_id || "", attrs: 'data-act="rule-pick" data-arg="user_id" aria-label="ユーザー"' })}</div>`
          : "",
      ].filter(Boolean),
      "割り当て"
    )}
    <div id="r-err" class="err-t" role="alert" style="font-size:14px;margin:8px 4px;white-space:pre-line"></div>
    ${v.id ? `<div style="height:22px"></div>${group([`<button type="button" class="cell danger" data-act="rule-del" data-arg="${v.id}">この枠を削除</button>`], "", "使用中・ゴミ箱のスロットがある間は削除できません。事前作成した DNS は先に削除してください。")}` : ""}
  </div>`
  );
  const b = document.querySelector("#scrim .sheet-b");
  if (b) b.scrollTop = scroll;
  if (focusId) document.querySelector(`[data-act="rule-pick"][data-arg="${focusId}"]`)?.focus();
  showPreview();
}

let previewSeq = 0;
function preview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(async () => {
    if (!R) return;
    const seq = ++previewSeq;
    const q = R.v.id ? `?rule_id=${R.v.id}` : "";
    let problems;
    let slots;
    try {
      const r = await api.post(`/admin/slot-rules/preview${q}`, ruleBody());
      problems = r.problems;
      slots = r.slots;
    } catch (e) {
      problems = [fieldErr(e)];
      slots = [];
    }
    if (!R || seq !== previewSeq) return; // 後から出した確認の結果を優先する（応答の順番が入れ替わっても）
    R.problems = problems;
    R.slots = slots;
    showPreview();
  }, 300);
}

function showPreview() {
  if (!R) return;
  const table = document.getElementById("r-preview");
  if (!table) return;
  const rows = R.slots.slice(0, 40).map((s) => `<tr class="${s.excluded ? "x" : ""}"><td>${s.port}</td><td class="mono">${esc(s.fqdn)}${s.port === 25565 && !s.excluded ? ' <span class="pill b">既定ポート</span>' : ""}</td></tr>`);
  table.innerHTML = rows.join("") || `<tr><td></td><td>—</td></tr>`;
  const count = R.slots.filter((s) => !s.excluded).length;
  document.getElementById("r-count").textContent = `プレビュー（${count}件${R.slots.length > 40 ? "・先頭40件を表示" : ""}）`;
  const probs = [...R.problems];
  if (!R.v.name.trim()) probs.unshift("名前を入力してください");
  document.getElementById("r-probs").innerHTML = probs.map(esc).join("<br>");
  const save = document.getElementById("rule-save");
  if (save) save.disabled = probs.length > 0 || R.saving;
}

async function saveRule() {
  R.saving = true;
  showPreview();
  try {
    const body = ruleBody();
    const saved = R.v.id ? await api.patch(`/admin/slot-rules/${R.v.id}`, body) : await api.post("/admin/slot-rules", body);
    const id = saved.id;
    const needsPublish = !R.v.id ? saved.prepublish && isFixed(saved) : saved.needs_republish;
    R = null;
    closeSheet();
    toast("アドレス枠を保存しました");
    if (needsPublish) toast("「DNS を作成」を押すと、割り当て前にアドレスを使えるようにできます");
    const top = ctx.route.stack[ctx.route.stack.length - 1];
    if (top && (top.page === "rule" || top.page === "start")) ctx.refresh();
    else ctx.go("rule", String(id));
  } catch (e) {
    R.saving = false;
    const box = document.getElementById("r-err");
    if (box) box.textContent = fieldErr(e);
    showPreview();
  }
}

async function dnsJob(id, kind) {
  const path = kind === "publish_slots" ? "publish" : "unpublish";
  try {
    const r = await api.post(`/admin/slot-rules/${id}/${path}`);
    trackJob({ id: r.job.id, kind, name: NAMES.rule.get(String(id)) || "アドレス枠" });
  } catch (e) {
    toast(e.message, "warn");
  }
  ctx.refresh();
}

/* ---------------- 操作 ---------------- */
export const ADMIN_ACTIONS = {
  "go-domain"(id) {
    ctx.goTab("admin", [{ page: "domains" }, { page: "domain", arg: id }]);
  },
  "go-rule"(id) {
    ctx.goTab("admin", [{ page: "rules" }, { page: "rule", arg: id }]);
  },
  "domain-add"() {
    addDomain();
  },
  "domain-del"(id) {
    confirmSheet({
      title: "ドメインを削除",
      body: `<b>${esc(NAMES.domain.get(String(id)) || "")}</b> を Hatch から外します。Cloudflare のゾーン自体は消えません。`,
      ok: "削除",
      danger: true,
      onOk: async () => {
        await api.del(`/admin/domains/${id}`);
        toast("ドメインを削除しました");
        ctx.goTab("admin", [{ page: "domains" }]);
      },
    });
  },
  "ip-add"() {
    addIp();
  },
  "menu-ip"(arg, el) {
    const [id, addr] = arg.split("|");
    openMenu(el, [
      { label: "アドレスをコピー", icon: "copy", run: () => copyText(addr) },
      "-",
      {
        label: "削除",
        icon: "trash",
        red: true,
        run: () =>
          confirmSheet({
            title: "IP を削除",
            body: `<span class="mono">${esc(addr)}</span> を削除します。`,
            ok: "削除",
            danger: true,
            onOk: async () => {
              await api.del(`/admin/ips/${id}`);
              toast("IP を削除しました");
              ctx.refresh();
            },
          }),
      },
    ]);
  },
  "bind-add"(domainId) {
    addBinding(domainId);
  },
  "bind-domain"(_a, el) {
    openPicker(el, BIND.domains.map((d) => ({ value: String(d.id), label: d.name })), String(BIND.domain), (v) => {
      BIND.domain = Number(v);
      renderBinding();
    });
  },
  "bind-target"(_a, el) {
    const targets = [{ value: "follow", label: "使用中の edge に追従" }, ...BIND.ips.map((i) => ({ value: String(i.id), label: `${i.label}（${i.address}）` }))];
    openPicker(el, targets, BIND.target, (v) => {
      BIND.target = v;
      renderBinding();
    });
  },
  "menu-binding"(arg, el) {
    const [id, ...rest] = arg.split("|");
    const fq = rest.join("|");
    openMenu(el, [
      { label: "ホスト名をコピー", icon: "copy", run: () => copyText(fq) },
      {
        label: "DNS を反映し直す",
        icon: "restart",
        run: async () => {
          try {
            const r = await api.post(`/admin/bindings/${id}/sync`);
            trackJob({ id: r.job.id, kind: "sync_binding", name: fq });
          } catch (e) {
            toast(e.message, "warn");
          }
        },
      },
      "-",
      {
        label: "削除",
        icon: "trash",
        red: true,
        run: () =>
          confirmSheet({
            title: "紐付けを削除",
            body: `<span class="mono">${esc(fq)}</span> の A レコードを削除します。`,
            ok: "削除",
            danger: true,
            onOk: async () => {
              const r = await api.del(`/admin/bindings/${id}`);
              trackJob({ id: r.job.id, kind: "delete_binding", name: fq });
              ctx.refresh();
            },
          }),
      },
    ]);
  },
  "simple-ok"() {
    runSimple();
  },
  "rule-new"(domainId) {
    openRule(null, domainId);
  },
  "rule-edit"(id) {
    openRule(id);
  },
  "rule-save"() {
    saveRule();
  },
  "rule-del"(id) {
    const name = R ? R.v.name : "";
    confirmSheet({
      title: "アドレス枠を削除",
      body: `「${esc(name)}」を削除します。`,
      ok: "削除",
      danger: true,
      onOk: async () => {
        await api.del(`/admin/slot-rules/${id}`);
        R = null;
        toast("アドレス枠を削除しました");
        ctx.goTab("admin", [{ page: "rules" }]);
      },
    });
  },
  "rule-publish"(id) {
    dnsJob(id, "publish_slots");
  },
  "rule-unpublish"(id) {
    confirmSheet({
      title: "DNS を削除",
      body: "この枠で事前に作成した DNS レコードを削除します。使用中のスロットのレコードは残します。",
      ok: "削除",
      danger: true,
      onOk: async () => dnsJob(id, "unpublish_slots"),
    });
  },
  async "rule-pick"(key, el) {
    const v = R.v;
    let options = [];
    if (key === "domain_id") options = R.domains.map((x) => ({ value: String(x.id), label: x.name }));
    if (key === "record_mode") options = [{ value: "cname_edge", label: "edge への CNAME（推奨）", sub: "edge を切り替えてもレコードは変わりません" }, { value: "a_ip", label: "IP を直接指定" }];
    if (key === "ip_id") options = R.ips.map((i) => ({ value: String(i.id), label: `${i.label}（${i.address}）` }));
    if (key === "assign_to") options = [{ value: "shared", label: "全員で共有" }, { value: "user", label: "特定のユーザー" }];
    if (key === "user_id") options = (R.users || []).map((u) => ({ value: u.id, label: u.username }));
    const cur = v[key] === null || v[key] === undefined ? "" : String(v[key]);
    openPicker(el, options, cur, async (x) => {
      v[key] = ["domain_id", "ip_id"].includes(key) ? Number(x) : x;
      if (key === "assign_to" && x === "user" && !R.users) {
        try {
          R.users = (await api.get("/admin/users")).items.filter((u) => u.status === "active");
        } catch (e) {
          toast(e.message, "warn");
        }
      }
      if (key === "assign_to" && x === "shared") v.user_id = null;
      if (key === "record_mode" && x === "cname_edge") v.ip_id = null;
      renderRule(key);
      preview();
    });
  },
  "tpl-token"(token) {
    const inp = document.getElementById("r-tpl");
    if (!inp || !R) return;
    const s = inp.selectionStart ?? inp.value.length;
    const e = inp.selectionEnd ?? inp.value.length;
    inp.value = token === "{server}" ? "{server}" : inp.value.slice(0, s) + token + inp.value.slice(e);
    R.v.host_template = inp.value;
    const st = document.getElementById("r-st");
    if (st) st.disabled = inp.value.includes("{server}");
    inp.focus();
    preview();
  },
  "menu-slot"(arg, el) {
    const [port] = arg.split("|").map(Number);
    const s = SLOT_CACHE && SLOT_CACHE.byPort.get(port);
    if (!s) return;
    const fq = s.host ? `${s.host}.${SLOT_CACHE.rule.domain}` : s.server_name ? `${s.server_name}.${SLOT_CACHE.rule.domain}` : "";
    const setStatus = async (status) => {
      try {
        await api.patch(`/admin/slots/${port}`, { status });
        toast(status === "disabled" ? `${port} を停止しました` : `${port} を再開しました`);
      } catch (e) {
        toast(e.message, "warn");
      }
      ctx.refresh();
    };
    openMenu(el, [
      fq ? { label: "アドレスをコピー", icon: "copy", run: () => copyText(fq) } : null,
      { label: "ポートをコピー", icon: "copy", run: () => copyText(String(port)) },
      s.server_name && s.status === "assigned" ? { label: `${s.server_name} を開く`, icon: "chev", run: () => ctx.goTab("servers", [{ page: "server", arg: s.server_name }]) } : null,
      s.server_name && s.status === "held" ? { label: "ゴミ箱を開く", icon: "trash", run: () => ctx.goTab("servers", [{ page: "trash" }]) } : null,
      s.status === "free" ? "-" : null,
      s.status === "free" ? { label: "このスロットを停止", icon: "stop", red: true, run: () => setStatus("disabled") } : null,
      s.status === "disabled" ? { label: "このスロットを再開", icon: "play", run: () => setStatus("free") } : null,
    ]);
  },
};

document.addEventListener("input", (e) => {
  const t = e.target;
  if (!R || !t.dataset.rule) return;
  R.v[t.dataset.rule] = t.value;
  if (t.dataset.rule === "host_template") {
    const st = document.getElementById("r-st");
    if (st) st.disabled = t.value.includes("{server}");
  }
  preview();
});
document.addEventListener("change", async (e) => {
  const t = e.target;
  if (R && t.dataset.ruleBool) {
    R.v[t.dataset.ruleBool] = t.checked;
    preview();
    return;
  }
  if (t.dataset.domainDef) {
    try {
      await api.patch(`/admin/domains/${t.dataset.domainDef}`, { is_default: true });
      toast("既定のドメインにしました");
    } catch (err) {
      t.checked = false;
      toast(err.message, "warn");
    }
    ctx.refresh();
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && SIMPLE && e.target.closest && e.target.closest("#scrim .sheet-b input")) {
    e.preventDefault();
    runSimple();
  }
});

export function adminTitle(entry) {
  if (entry.page === "domain") return NAMES.domain.get(String(entry.arg)) || "ドメイン";
  if (entry.page === "rule") return NAMES.rule.get(String(entry.arg)) || "アドレス枠";
  return null;
}

export const ADMIN_PAGES = {
  root: () => pAdmin(),
  domains: () => pDomains(),
  domain: (arg) => pDomain(arg),
  ips: () => pIps(),
  rules: () => pRules(),
  rule: (arg) => pRule(arg),
  users: () => pUsers(),
  later: (page) => pLater(page),
};

// シートを閉じたら、入力中の内容を捨てる（Enter で古いシートの処理が走らないように）
onSheetClosed(() => {
  SIMPLE = null;
  R = null;
  BIND = null;
});
