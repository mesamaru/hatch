// 画面：管理 → はじめの設定。docs/SPEC.md 8「はじめの設定」、docs/UI.md 3.2、docs/TASKS.md T48。
// 一覧（/admin/start）と、手順ごとの専用画面（/admin/start/<手順>）の2段。
// 専用画面には、説明・やり方・登録した内容（押すと変更・削除のメニュー）・追加・次の手順へ、を並べる。
// 登録や変更は各画面と同じシートを使い、終わるとこの画面のまま描き直す（別の画面へは移らない）。
import { api } from "../api.js";
import { banner, cell, esc, group } from "../components/cell.js";
import { openMenu } from "../components/picker.js";
import { confirmSheet } from "../components/sheet.js";
import { toast } from "../components/toast.js";
import { ctx, pageShell } from "../ctx.js";
import { editRule } from "./admin-address.js";
import { EDGE_GUIDE, TOKEN_GUIDE, loadData } from "./admin-infra.js";
import { jobBanners, trackJob } from "./jobs.js";

let LAST = null; // 最後に読んだ進み具合（やり直しで使う）
let REG = null; // 登録済みの内容（メニューで使う）

const ORDER = ["linode", "firewall", "edge", "domain", "rule", "dns"];

const STEPS = {
  linode: {
    title: "Linode のアカウント",
    text: "edge を Linode で動かす場合に、契約ごとの API トークンを登録します。登録すると、ゲームのポートを Hatch が自動で開け閉めできるようになります。Linode を使わないなら飛ばして構いません。",
    howto: TOKEN_GUIDE,
    act: "linode-add",
    btn: "Linode のアカウントを追加",
    more: "別の契約を追加",
    empty: "まだ登録していません。",
  },
  firewall: {
    title: "ファイアウォール",
    text: "Linode で作ったファイアウォールを登録します。サーバーを作るとゲームのポートを開け、完全に削除すると締めます（ゴミ箱の間は開いたまま）。",
    howto: `ファイアウォールの用意
      <ul class="howto">
        <li>Cloud Manager の <b>Firewalls</b> → <b>Create Firewall</b> で作る</li>
        <li>SSH など、必要なルールだけを入れておく（Inbound の既定は Drop）</li>
        <li>Hatch が作るルールの名前は <b>hatch-</b> で始まります。手で作ったルールには触れません</li>
        <li>複数の edge で1つを共有しても、edge ごとに別にしても構いません</li>
      </ul>`,
    act: "firewall-add",
    btn: "ファイアウォールを登録",
    more: "別のファイアウォールを登録",
    locked: "先に「Linode のアカウント」を登録してください。",
    empty: "まだ登録していません。",
  },
  edge: {
    title: "edge を登録",
    text: "プレイヤーが接続する入口のサーバーを登録します。edge.<ドメイン> はこの edge の公開 IP を向きます。",
    howto: EDGE_GUIDE,
    act: "edge-add",
    btn: "edge を登録",
    more: "別の edge を登録",
    empty: "まだ登録していません。",
  },
  domain: {
    title: "ドメインを登録",
    text: "ゲームサーバーのアドレスに使うドメインを登録します。登録すると edge.<ドメイン> の A レコードを、使用中の edge の IP で自動で作ります。",
    howto: `入力するもの
      <ul class="howto">
        <li><b>ドメイン</b>：Cloudflare で管理しているドメイン（例 nuids.jp）</li>
        <li><b>ゾーン ID</b>：Cloudflare でドメインを開いた「概要」ページの右下にある、32桁の英数字</li>
        <li>Cloudflare の API トークンに、そのドメインの DNS を編集する権限が必要です</li>
      </ul>`,
    act: "domain-add",
    btn: "ドメインを追加",
    more: "別のドメインを追加",
    locked: "先に「edge を登録」を済ませてください（edge.<ドメイン> の向き先が必要です）。",
    empty: "まだ登録していません。",
  },
  rule: {
    title: "アドレス枠を作る",
    text: "ポートとホスト名の対応（例 25561 ↔ mc01trt.<ドメイン>）を作ります。利用者はサーバーを作るとき、空きの中から選ぶだけです。",
    howto: `決めるもの
      <ul class="howto">
        <li><b>ポートの範囲</b>：例 25560〜25569。対象外にしたいポートも指定できます</li>
        <li><b>ホスト名の形</b>：例 mc{nn}trt（{nn} はポートの下2桁）。サーバー名を使うなら {server}</li>
        <li><b>使える人</b>：全員で共有するか、特定のユーザー専用か</li>
      </ul>`,
    act: "rule-new",
    btn: "アドレス枠を作る",
    more: "別のアドレス枠を作る",
    locked: "先に「ドメインを登録」を済ませてください。",
    empty: "まだ作っていません。",
  },
  dns: {
    title: "アドレス枠の DNS を作る",
    text: "固定のホスト名のアドレス枠は、割り当てる前に DNS を作っておくと、サーバーの作成直後から接続できます。サーバー名を使う枠（{server}）は、作成時に作るので不要です。",
    act: "gs-retry",
    btn: "DNS を作成",
    locked: "先に「アドレス枠を作る」を済ませてください。",
    empty: "DNS を先に作る枠はありません（サーバーを作るときに作ります）。",
  },
};

const LOOK = {
  done: { icon: "check", color: "var(--green)", label: "済み", pill: "g" },
  todo: { icon: "plus", color: "var(--blue)", label: "未登録", pill: "" },
  locked: { icon: "clock", color: "var(--gray)", label: "まだできません", pill: "" },
  working: { icon: "spin", color: "var(--orange)", label: "反映中", pill: "o" },
  error: { icon: "xc", color: "var(--red)", label: "失敗", pill: "r" },
  skipped: { icon: "ok", color: "var(--gray)", label: "不要", pill: "" },
};

/** 進み具合を読む。読めないときは null（案内を出さないだけにする）。 */
export async function loadStart() {
  try {
    return await api.get("/admin/getting-started");
  } catch {
    return null;
  }
}

/** 管理トップ・サーバー一覧に出す案内（残っているときだけ）。 */
export function startBanner(gs) {
  if (!gs || gs.complete) return "";
  return banner(
    "info",
    `はじめの設定が残っています（${gs.done} / ${gs.total}）`,
    "サーバーを作れるようになるまでの手順を、順番どおりに並べています。",
    '<button type="button" class="btn sm fill" data-act="gs-open">はじめの設定を開く</button>'
  );
}

/** 見出し（ナビゲーションバー・戻るボタン）に出す名前。 */
export function startTitle(entry) {
  if (entry.page !== "start-step") return null;
  const i = ORDER.indexOf(entry.arg);
  return i < 0 ? "はじめの設定" : `${i + 1}. ${STEPS[entry.arg].title}`;
}

const pill = (s) => {
  const look = LOOK[s.state];
  return `${s.required ? "" : '<span class="pill">任意</span>'}<span class="pill ${look.pill}">${look.label}</span>`;
};
const nextOf = (gs) => gs.steps.find((s) => s.state === "todo" || s.state === "error");

/* ---------------- 一覧 ---------------- */
async function pStart() {
  const gs = await api.get("/admin/getting-started");
  LAST = gs;
  const next = nextOf(gs);
  const rows = gs.steps.map((s, i) => {
    const d = STEPS[s.key];
    let sub = s.detail ? esc(s.detail) : s.state === "locked" ? esc(d.locked) : "";
    if (s.state === "error") sub = '<span class="err-t">うまくいっていません。開いて確認してください</span>';
    if (s.state === "skipped") sub = "Linode を使わない edge なので不要です";
    if (s.state === "todo" && !sub) sub = s === next ? "次にやる手順です" : "まだ登録していません";
    if (s.state === "working") sub = "反映しています…";
    return cell({ icon: LOOK[s.state].icon, color: LOOK[s.state].color, title: `${i + 1}. ${esc(d.title)}${s === next ? '<span class="pill b">次にやる</span>' : pill(s)}`, sub, subWrap: true, act: "gs-step", arg: s.key });
  });
  const head = gs.complete
    ? banner("ok", "はじめの設定はすべて済みました", "サーバーの画面の＋から、サーバーを作成できます。手順を押すと、登録した内容の確認・変更・削除ができます。", '<button type="button" class="btn sm fill" data-act="tab" data-arg="servers">サーバーの画面へ</button>')
    : `<p class="sheet-lead">上から順に進めてください。進み具合：<b>${gs.done} / ${gs.total}</b>（任意の手順は数えません）。手順を押すと、その手順の画面が開きます。</p>`;
  const main = next ? `<button type="button" class="btn fill block" data-act="gs-step" data-arg="${next.key}" style="margin:12px 0 4px">次へ：${esc(STEPS[next.key].title)}</button>` : "";
  return pageShell("はじめの設定", `${jobBanners("admin")}${head}${group(rows, "手順")}${main}`);
}

/* ---------------- 手順ごとの画面 ---------------- */
const fqdn = (b) => (b.host === "@" ? b.domain : `${b.host}.${b.domain}`);
const row = (o) => cell({ color: "var(--gray)", subWrap: true, ...o });

/** 登録した内容。行を押すと、変更・削除などのメニューが開く。 */
function itemsFor(key) {
  const R = REG;
  if (key === "linode")
    return R.infra.accounts.map((a) => row({ icon: "key", title: esc(a.label), sub: `edge ${a.edges}台・ファイアウォール ${a.firewalls}個`, act: "menu-linode", arg: String(a.id) }));
  if (key === "firewall")
    return R.infra.fws.map((f) =>
      row({
        icon: "shield",
        title: `${esc(f.label)}${f.last_error ? '<span class="pill r">反映できません</span>' : ""}`,
        sub: `アカウント ${esc(f.account)}・${f.edges.length ? `${esc(f.edges.join("・"))} が使用` : "どの edge も使っていません"}${f.last_error ? `<br><span class="err-t">${esc(f.last_error)}</span>` : ""}`,
        act: "menu-firewall",
        arg: String(f.id),
      })
    );
  if (key === "edge")
    return R.infra.edges.map((e) =>
      row({
        icon: "net",
        title: `${esc(e.id)}${e.is_active ? '<span class="pill g">使用中</span>' : ""}`,
        sub: `公開 IP <span class="mono">${esc(e.public_ip)}</span><br>${e.account ? `Linode ${esc(e.account)}・ファイアウォール ${e.firewall ? esc(e.firewall) : "なし"}` : "Linode 以外"}<br>Tailscale ${e.tailscale_ip ? `<span class="mono">${esc(e.tailscale_ip)}</span>` : "（接続後に入ります）"}・${e.last_seen_at ? "応答あり" : "まだ応答がありません"}`,
        act: "menu-edge",
        arg: e.id,
      })
    );
  if (key === "domain")
    return R.domains.map((d) => {
      const bs = R.bindings.filter((b) => b.domain_id === d.id && b.follow_active_edge);
      const dns = bs.map((b) => `<span class="mono">${esc(fqdn(b))}</span> ${b.synced_at ? "反映済み" : '<span class="err-t">未反映</span>'}`).join("<br>");
      return row({ icon: "globe", title: esc(d.name), sub: `ゾーン ID <span class="mono">${esc(d.cf_zone_id)}</span>${dns ? `<br>${dns}` : ""}`, act: "gs-menu-domain", arg: String(d.id) });
    });
  if (key === "rule")
    return R.rules.map((r) =>
      row({
        icon: "grid",
        title: esc(r.name),
        sub: `ポート ${r.port_start}〜${r.port_end}<br><span class="mono">${esc(r.host_template)}.${esc(r.domain)}</span>${r.stats ? `・空き ${r.stats.free} / ${r.stats.total}` : ""}`,
        act: "gs-menu-rule",
        arg: String(r.id),
      })
    );
  if (key === "dns")
    return R.rules
      .filter((r) => r.prepublish && !r.host_template.includes("{server}"))
      .map((r) =>
        row({
          icon: "cloud",
          title: `${esc(r.name)}${r.dns_published ? '<span class="pill g">作成済み</span>' : '<span class="pill">未作成</span>'}`,
          sub: `<span class="mono">${esc(r.host_template)}.${esc(r.domain)}</span>`,
          act: "gs-menu-dns",
          arg: String(r.id),
        })
      );
  return [];
}

function stateBanner(s, d) {
  if (s.state === "done") return banner("ok", "この手順は済んでいます", "登録した内容を押すと、変更・削除できます。");
  if (s.state === "skipped") return banner("info", "この手順は不要です", "Linode を使わない edge を登録しています。Linode を使う場合は、ここで追加できます。");
  if (s.state === "locked") return banner("info", "まだこの手順はできません", esc(d.locked));
  if (s.state === "working") return banner("info", "反映しています", "終わると自動で表示が変わります。");
  if (s.state === "error") return banner("e", "うまくいっていません", esc(s.problem || ""), `<button type="button" class="btn sm fill" data-act="gs-retry" data-arg="${s.key}">やり直す</button>`);
  return "";
}

async function pStep(key) {
  const [gs, infra, domains, bindings, rules] = await Promise.all([
    api.get("/admin/getting-started"),
    loadData(),
    api.get("/admin/domains"),
    api.get("/admin/bindings"),
    api.get("/admin/slot-rules"),
  ]);
  LAST = gs;
  REG = { infra, domains: domains.items, bindings: bindings.items, rules: rules.items };
  const i = ORDER.indexOf(key);
  const s = gs.steps.find((x) => x.key === key);
  if (!s) return pageShell("はじめの設定", banner("e", "見つかりません", "この手順はありません。"));
  const d = STEPS[key];
  const items = itemsFor(key);
  const canAdd = s.state !== "locked" && d.act !== "gs-retry" && (key !== "firewall" || infra.accounts.length);
  // 登録した内容 ＋ 追加のボタン
  const list = [...(items.length ? items : [cell({ title: `<span style="color:var(--label2)">${esc(d.empty)}</span>` })])];
  if (canAdd) list.push(`<button type="button" class="cell action" data-act="${d.act}" data-arg="">${esc(items.length && d.more ? d.more : d.btn)}</button>`);
  // いちばん下のボタン：やること → 登録、DNS は作成、済んだら次の手順へ
  // Linode を使わないなら、ファイアウォールも飛ばして edge へ
  const nextKey = key === "linode" && s.state === "todo" ? "edge" : ORDER[i + 1];
  let main = "";
  if (s.state === "todo" && key === "dns") main = `<button type="button" class="btn fill block" data-act="gs-retry" data-arg="dns">DNS を作成</button>`;
  else if ((s.state === "done" || s.state === "skipped" || (!s.required && s.state === "todo")) && nextKey)
    main = `<button type="button" class="btn ${s.state === "todo" ? "" : "fill"} block" data-act="gs-step" data-arg="${nextKey}">${s.state === "todo" ? "使わずに次へ" : "次へ"}：${esc(STEPS[nextKey].title)}</button>`;
  else if (s.state === "done" && !nextKey) main = `<button type="button" class="btn fill block" data-act="gs-open">はじめの設定の一覧へ</button>`;
  const title = `${i + 1}. ${d.title}`;
  return pageShell(
    title,
    `${jobBanners("admin")}
    <p class="sheet-lead">${pill(s)}<br>${esc(d.text)}</p>
    ${stateBanner(s, d)}
    ${group(list, "登録した内容")}
    ${d.howto ? `<div class="sheet-lead" style="margin-top:18px">${d.howto}</div>` : ""}
    ${main ? `<div style="margin:14px 0 4px">${main}</div>` : ""}`
  );
}

/* ---------------- 操作 ---------------- */
async function syncBinding(id) {
  const r = await api.post(`/admin/bindings/${id}/sync`);
  trackJob({ id: r.job.id, kind: "sync_binding", name: "edge の A レコード" });
}

async function publish(id) {
  const r = await api.post(`/admin/slot-rules/${id}/publish`);
  const rule = REG && REG.rules.find((x) => x.id === Number(id));
  trackJob({ id: r.job.id, kind: "publish_slots", name: rule ? rule.name : "アドレス枠" });
}

/** 失敗したらトーストに出し、どちらにしても描き直す。 */
async function safely(fn) {
  try {
    await fn();
  } catch (e) {
    toast(e.message, "warn");
  }
  ctx.refresh();
}

function retry(key) {
  const s = LAST && LAST.steps.find((x) => x.key === key);
  if (!s || !s.retry) return;
  safely(async () => {
    for (const id of s.retry.bindings || []) await syncBinding(id);
    for (const id of s.retry.rules || []) await publish(id);
  });
}

export const START_ACTIONS = {
  "gs-open"() {
    ctx.goTab("admin", [{ page: "start" }]);
  },
  "gs-step"(key) {
    // 手順の画面から次の手順へ進むときは、積み重ねずに置き換える（戻ると一覧）
    ctx.goTab("admin", [{ page: "start" }, { page: "start-step", arg: key }]);
  },
  "gs-retry"(key) {
    retry(key);
  },
  "gs-menu-domain"(id, el) {
    const d = REG && REG.domains.find((x) => String(x.id) === id);
    if (!d) return;
    const bs = REG.bindings.filter((b) => b.domain_id === d.id && b.follow_active_edge);
    openMenu(el, [
      {
        label: "edge の DNS を反映し直す",
        icon: "restart",
        disabled: !bs.length,
        run: () =>
          safely(async () => {
            for (const b of bs) await syncBinding(b.id);
          }),
      },
      "-",
      {
        label: d.rules > 0 ? "削除（アドレス枠を先に削除）" : "削除",
        icon: "trash",
        red: true,
        disabled: d.rules > 0,
        run: () =>
          confirmSheet({
            title: "ドメインを削除",
            body: `<b>${esc(d.name)}</b> を Hatch から外します。edge.${esc(d.name)} などの A レコードも削除します。Cloudflare のゾーン自体は消えません。`,
            ok: "削除",
            danger: true,
            onOk: async () => {
              const r = await api.del(`/admin/domains/${d.id}`);
              if (r && r.job) trackJob({ id: r.job.id, kind: "delete_domain", name: d.name });
              else toast("ドメインを削除しました");
              ctx.refresh();
            },
          }),
      },
    ]);
  },
  "gs-menu-rule"(id, el) {
    const r = REG && REG.rules.find((x) => String(x.id) === id);
    if (!r) return;
    const used = !!r.stats && r.stats.assigned + r.stats.held > 0;
    openMenu(el, [
      { label: "変更", icon: "tune", run: () => editRule(r.id) },
      "-",
      {
        label: used ? "削除（使用中のサーバーがあります）" : "削除",
        icon: "trash",
        red: true,
        disabled: used,
        run: () =>
          confirmSheet({
            title: "アドレス枠を削除",
            body: `「${esc(r.name)}」を削除します。${r.dns_published ? "先に作成した DNS があるときは、「アドレス枠の DNS を作る」の画面で先に削除してください。" : ""}`,
            ok: "削除",
            danger: true,
            onOk: async () => {
              await api.del(`/admin/slot-rules/${r.id}`);
              toast("アドレス枠を削除しました");
              ctx.refresh();
            },
          }),
      },
    ]);
  },
  "gs-menu-dns"(id, el) {
    const r = REG && REG.rules.find((x) => String(x.id) === id);
    if (!r) return;
    openMenu(el, [
      { label: r.dns_published ? "DNS を作り直す" : "DNS を作成", icon: "restart", run: () => safely(() => publish(r.id)) },
      "-",
      {
        label: "DNS を削除",
        icon: "trash",
        red: true,
        run: () =>
          confirmSheet({
            title: "DNS を削除",
            body: `「${esc(r.name)}」で先に作成した DNS を削除します。使用中のアドレスの DNS は残ります。`,
            ok: "削除",
            danger: true,
            onOk: async () => {
              const res = await api.post(`/admin/slot-rules/${r.id}/unpublish`);
              trackJob({ id: res.job.id, kind: "unpublish_slots", name: r.name });
              ctx.refresh();
            },
          }),
      },
    ]);
  },
};

export const START_PAGES = {
  start: () => pStart(),
  "start-step": (key) => pStep(key),
};
