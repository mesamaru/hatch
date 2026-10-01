// 画面：管理 → はじめの設定。docs/SPEC.md 8「はじめの設定」、docs/UI.md 3.2、docs/TASKS.md T48。
// 各手順のボタンは、各画面と同じ登録シート（data-act）を開く。登録が終わると描き直されて次の手順に進む。
// 済んだ手順は、その下に登録した内容を並べ、「…」から変更・削除・やり直しができる（別の画面へは移らない）。
import { api } from "../api.js";
import { banner, cell, esc, group } from "../components/cell.js";
import { openMenu } from "../components/picker.js";
import { confirmSheet } from "../components/sheet.js";
import { toast } from "../components/toast.js";
import { ctx, pageShell } from "../ctx.js";
import { editRule } from "./admin-address.js";
import { loadData } from "./admin-infra.js";
import { jobBanners, trackJob } from "./jobs.js";

let LAST = null; // 最後に読んだ進み具合（やり直しで使う）
let REG = null; // 登録済みの内容（メニューで使う）

const STEPS = {
  linode: {
    title: "Linode のアカウント",
    text: "edge に Linode を使う場合に登録します。ゲームのポートを Hatch が自動で開け閉めできるようになります。",
    act: "linode-add",
    btn: "Linode のアカウントを追加",
    more: "別の契約を追加",
  },
  firewall: {
    title: "ファイアウォール",
    text: "Linode で作ったファイアウォールを登録します。SSH など必要なルールだけを入れておいてください。",
    act: "firewall-add",
    btn: "ファイアウォールを登録",
    more: "別のファイアウォールを登録",
    locked: "Linode のアカウントを登録すると選べます。",
  },
  edge: {
    title: "edge を登録",
    text: "プレイヤーの入口です。edge/install-edge.sh を実行したサーバーを登録します。edge.<ドメイン> はこの IP を向きます。",
    act: "edge-add",
    btn: "edge を登録",
    more: "別の edge を登録",
  },
  domain: {
    title: "ドメインを登録",
    text: "ゲームサーバーのアドレスに使うドメインと、Cloudflare のゾーン ID を登録します。edge.<ドメイン> の A レコードを自動で作ります。",
    act: "domain-add",
    btn: "ドメインを追加",
    more: "別のドメインを追加",
    locked: "edge を登録すると押せます。",
  },
  rule: {
    title: "アドレス枠を作る",
    text: "ポートとホスト名の対応（例 25561 ↔ mc01trt.<ドメイン>）を作ります。利用者は空きから選ぶだけでサーバーを作れます。",
    act: "rule-new",
    btn: "アドレス枠を作る",
    more: "別のアドレス枠を作る",
    locked: "ドメインを登録すると押せます。",
  },
  dns: {
    title: "アドレス枠の DNS を作る",
    text: "割り当てる前にアドレスを使えるよう、アドレス枠の DNS を先に作ります。",
    act: "gs-retry",
    btn: "DNS を作成",
    locked: "アドレス枠を作ると押せます。",
  },
};

const LOOK = {
  done: { icon: "check", color: "var(--green)", label: "済み" },
  todo: { icon: "plus", color: "var(--blue)", label: "" },
  locked: { icon: "clock", color: "var(--gray)", label: "まだ押せません" },
  working: { icon: "spin", color: "var(--orange)", label: "反映中" },
  error: { icon: "xc", color: "var(--red)", label: "失敗" },
  skipped: { icon: "ok", color: "var(--gray)", label: "不要" },
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

function stepCell(s, n, isNext) {
  const d = STEPS[s.key];
  const look = LOOK[s.state];
  const pills = `${s.required ? "" : '<span class="pill">任意</span>'}${isNext ? '<span class="pill b">次にやる</span>' : look.label ? `<span class="pill ${s.state === "error" ? "r" : s.state === "done" ? "g" : ""}">${look.label}</span>` : ""}`;
  let sub = esc(d.text);
  if (s.state === "done") sub = s.key === "dns" && s.detail ? esc(s.detail) : "";
  if (s.state === "skipped") sub = "Linode を使わない edge を登録したので、不要です。";
  if (s.state === "locked") sub = `${esc(d.text)}<br>${esc(d.locked || "")}`;
  if (s.state === "working") sub = "反映しています。終わると自動で表示が変わります。";
  if (s.state === "todo" && s.key === "dns" && s.detail) sub = `${esc(d.text)}<br>対象：${esc(s.detail)}`;
  if (s.state === "todo" && s.key === "edge" && LAST.steps[0].state === "todo") sub += "<br>Linode を使わない場合は、ここから始めます。";
  if (s.state === "error") sub = `<span class="err-t">${esc(s.problem || "")}</span>`;
  const title = `${n}. ${esc(d.title)}${pills}`;
  // 押せるのは、やること（登録シート）と失敗（やり直す）だけ。済んだ手順は下に内容を並べる
  let act = null;
  let arg = "";
  if (s.state === "todo") [act, arg] = [d.act, s.key === "rule" ? "" : s.key];
  else if (s.state === "error") [act, arg] = ["gs-retry", s.key];
  return cell({ icon: look.icon, color: look.color, title, sub, subWrap: true, act, arg });
}

const item = (o) => cell({ color: "var(--gray)", subWrap: true, ...o });
const fqdn = (b) => (b.host === "@" ? b.domain : `${b.host}.${b.domain}`);

/** 手順ごとの登録済みの内容（「…」で変更・削除・やり直し）。 */
function itemsFor(key) {
  const R = REG;
  if (key === "linode")
    return R.infra.accounts.map((a) =>
      item({ icon: "key", title: esc(a.label), sub: `edge ${a.edges}台・ファイアウォール ${a.firewalls}個`, more: { act: "menu-linode", arg: String(a.id), label: `${a.label} の操作` } })
    );
  if (key === "firewall")
    return R.infra.fws.map((f) =>
      item({
        icon: "shield",
        title: `${esc(f.label)}${f.last_error ? '<span class="pill r">反映できません</span>' : ""}`,
        sub: `${esc(f.account)}・${f.edges.length ? `${esc(f.edges.join("・"))} が使用` : "どの edge も使っていません"}${f.last_error ? `<br><span class="err-t">${esc(f.last_error)}</span>` : ""}`,
        more: { act: "menu-firewall", arg: String(f.id), label: `${f.label} の操作` },
      })
    );
  if (key === "edge")
    return R.infra.edges.map((e) =>
      item({
        icon: "net",
        title: `${esc(e.id)}${e.is_active ? '<span class="pill g">使用中</span>' : ""}`,
        sub: `公開 IP <span class="mono">${esc(e.public_ip)}</span>・${e.account ? `${esc(e.account)}・ファイアウォール ${e.firewall ? esc(e.firewall) : "なし"}` : "Linode 以外"}<br>Tailscale ${e.tailscale_ip ? `<span class="mono">${esc(e.tailscale_ip)}</span>` : "（接続後に入ります）"}・${e.last_seen_at ? "応答あり" : "まだ応答がありません"}`,
        more: { act: "menu-edge", arg: e.id, label: `${e.id} の操作` },
      })
    );
  if (key === "domain")
    return R.domains.map((d) => {
      const bs = R.bindings.filter((b) => b.domain_id === d.id && b.follow_active_edge);
      const dns = bs.map((b) => `<span class="mono">${esc(fqdn(b))}</span> ${b.synced_at ? "反映済み" : '<span class="err-t">未反映</span>'}`).join("<br>");
      return item({
        icon: "globe",
        title: esc(d.name),
        sub: `ゾーン ID <span class="mono">${esc(d.cf_zone_id.slice(0, 8))}…</span>${dns ? `<br>${dns}` : ""}`,
        more: { act: "gs-menu-domain", arg: String(d.id), label: `${d.name} の操作` },
      });
    });
  if (key === "rule")
    return R.rules.map((r) =>
      item({
        icon: "grid",
        title: esc(r.name),
        sub: `ポート ${r.port_start}〜${r.port_end}・<span class="mono">${esc(r.host_template)}.${esc(r.domain)}</span>${r.stats ? `・空き ${r.stats.free}` : ""}`,
        more: { act: "gs-menu-rule", arg: String(r.id), label: `${r.name} の操作` },
      })
    );
  if (key === "dns")
    return R.rules
      .filter((r) => r.prepublish && !r.host_template.includes("{server}"))
      .map((r) =>
        item({
          icon: "cloud",
          title: `${esc(r.name)}${r.dns_published ? '<span class="pill g">作成済み</span>' : '<span class="pill">未作成</span>'}`,
          sub: `<span class="mono">${esc(r.host_template)}.${esc(r.domain)}</span>`,
          more: { act: "gs-menu-dns", arg: String(r.id), label: `${r.name} の DNS の操作` },
        })
      );
  return [];
}

async function pStart() {
  const [gs, infra, domains, bindings, rules] = await Promise.all([
    api.get("/admin/getting-started"),
    loadData(),
    api.get("/admin/domains"),
    api.get("/admin/bindings"),
    api.get("/admin/slot-rules"),
  ]);
  LAST = gs;
  REG = { infra, domains: domains.items, bindings: bindings.items, rules: rules.items };
  const next = gs.steps.find((s) => s.state === "todo" || s.state === "error");
  const blocks = gs.steps.map((s, i) => {
    const d = STEPS[s.key];
    const rows = [stepCell(s, i + 1, s === next), ...itemsFor(s.key)];
    if (s.state === "done" && d.more) rows.push(`<button type="button" class="cell action" data-act="${d.act}" data-arg="">${esc(d.more)}</button>`);
    // 飛ばした任意の手順も、あとから登録できる（ファイアウォールは Linode のアカウントがあるときだけ）
    if (s.state === "skipped" && (s.key === "linode" || REG.infra.accounts.length)) rows.push(`<button type="button" class="cell action" data-act="${d.act}" data-arg="">${esc(d.btn)}</button>`);
    return group(rows);
  });
  const d = next && STEPS[next.key];
  const main = next
    ? `<button type="button" class="btn fill block" data-act="${next.state === "error" ? "gs-retry" : d.act}" data-arg="${next.state === "error" || d.act === "gs-retry" ? next.key : ""}" style="margin:12px 0 4px">${esc(next.state === "error" ? "やり直す" : d.btn)}</button>`
    : "";
  const howto = "登録した内容は、各手順の「…」から変更・削除できます。";
  const head = gs.complete
    ? banner("ok", "はじめの設定はすべて済みました", `サーバーの画面の＋から、サーバーを作成できます。${howto}`, '<button type="button" class="btn sm fill" data-act="tab" data-arg="servers">サーバーの画面へ</button>')
    : `<p class="sheet-lead">上から順に進めてください。進み具合：<b>${gs.done} / ${gs.total}</b>（任意の手順は数えません）。${howto}</p>`;
  return pageShell("はじめの設定", `${jobBanners("admin")}${head}${blocks.join("")}${main}`);
}

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
      { label: "編集", icon: "tune", run: () => editRule(r.id) },
      "-",
      {
        label: used ? "削除（使用中のサーバーがあります）" : "削除",
        icon: "trash",
        red: true,
        disabled: used,
        run: () =>
          confirmSheet({
            title: "アドレス枠を削除",
            body: `「${esc(r.name)}」を削除します。${r.dns_published ? "先に作成した DNS があるときは、「アドレス枠の DNS」の「…」から先に削除してください。" : ""}`,
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
};
