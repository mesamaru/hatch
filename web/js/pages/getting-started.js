// 画面：管理 → はじめの設定。docs/SPEC.md 8「はじめの設定」、docs/UI.md 3.2、docs/TASKS.md T48。
// 各手順のボタンは、各画面と同じ登録シート（data-act）を開く。登録が終わると描き直されて次の手順に進む。
import { api } from "../api.js";
import { banner, cell, esc, group } from "../components/cell.js";
import { toast } from "../components/toast.js";
import { ctx, pageShell } from "../ctx.js";
import { jobBanners, trackJob } from "./jobs.js";

let LAST = null; // 最後に読んだ進み具合（やり直しで使う）

const STEPS = {
  linode: {
    title: "Linode のアカウント",
    text: "edge に Linode を使う場合に登録します。ゲームのポートを Hatch が自動で開け閉めできるようになります。",
    act: "linode-add",
    btn: "Linode のアカウントを追加",
    page: "nodes",
  },
  firewall: {
    title: "ファイアウォール",
    text: "Linode で作ったファイアウォールを登録します。SSH など必要なルールだけを入れておいてください。",
    act: "firewall-add",
    btn: "ファイアウォールを登録",
    page: "nodes",
    locked: "Linode のアカウントを登録すると選べます。",
  },
  edge: {
    title: "edge を登録",
    text: "プレイヤーの入口です。edge/install-edge.sh を実行したサーバーを登録します。edge.<ドメイン> はこの IP を向きます。",
    act: "edge-add",
    btn: "edge を登録",
    page: "nodes",
  },
  domain: {
    title: "ドメインを登録",
    text: "ゲームサーバーのアドレスに使うドメインと、Cloudflare のゾーン ID を登録します。edge.<ドメイン> の A レコードを自動で作ります。",
    act: "domain-add",
    btn: "ドメインを追加",
    page: "domains",
    locked: "edge を登録すると押せます。",
  },
  rule: {
    title: "アドレス枠を作る",
    text: "ポートとホスト名の対応（例 25561 ↔ mc01trt.<ドメイン>）を作ります。利用者は空きから選ぶだけでサーバーを作れます。",
    act: "rule-new",
    btn: "アドレス枠を作る",
    page: "rules",
    locked: "ドメインを登録すると押せます。",
  },
  dns: {
    title: "アドレス枠の DNS を作る",
    text: "割り当てる前にアドレスを使えるよう、アドレス枠の DNS を先に作ります。",
    act: "gs-retry",
    btn: "DNS を作成",
    page: "rules",
    locked: "アドレス枠を作ると押せます。",
  },
};

const LOOK = {
  done: { icon: "ok", color: "var(--green)", label: "済み" },
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
  if (s.state === "done" && s.detail) sub = `登録済み：${esc(s.detail)}`;
  if (s.state === "skipped") sub = "Linode を使わない edge を登録したので、不要です。";
  if (s.state === "locked") sub = `${esc(d.text)}<br>${esc(d.locked || "")}`;
  if (s.state === "working") sub = "反映しています。終わると自動で表示が変わります。";
  if (s.state === "todo" && s.key === "dns" && s.detail) sub = `${esc(d.text)}<br>対象：${esc(s.detail)}`;
  if (s.state === "todo" && s.key === "edge" && LAST.steps[0].state === "todo") sub += "<br>Linode を使わない場合は、ここから始めます。";
  if (s.state === "error") sub = `<span class="err-t">${esc(s.problem || "")}</span>`;
  const title = `${n}. ${esc(d.title)}${pills}`;
  // 押せるもの：やること → 登録シート、失敗 → やり直す、済み → その画面を開く
  let act = null;
  let arg = "";
  if (s.state === "todo") [act, arg] = [d.act, s.key];
  else if (s.state === "error") [act, arg] = ["gs-retry", s.key];
  else if (s.state === "done") [act, arg] = ["go", d.page];
  return cell({ icon: look.icon, color: look.color, title, sub, subWrap: true, act, arg: act === "rule-new" ? "" : arg });
}

async function pStart() {
  const gs = await api.get("/admin/getting-started");
  LAST = gs;
  const next = gs.steps.find((s) => s.state === "todo" || s.state === "error");
  const rows = gs.steps.map((s, i) => stepCell(s, i + 1, s === next));
  const d = next && STEPS[next.key];
  const main = next
    ? `<button type="button" class="btn fill block" data-act="${next.state === "error" ? "gs-retry" : d.act}" data-arg="${next.state === "error" || d.act === "gs-retry" ? next.key : ""}" style="margin:12px 0 4px">${esc(next.state === "error" ? "やり直す" : d.btn)}</button>`
    : "";
  const head = gs.complete
    ? banner("ok", "はじめの設定はすべて済みました", "サーバーの画面の＋から、サーバーを作成できます。", '<button type="button" class="btn sm fill" data-act="tab" data-arg="servers">サーバーの画面へ</button>')
    : `<p class="sheet-lead">上から順に進めてください。進み具合：<b>${gs.done} / ${gs.total}</b>（任意の手順は数えません）</p>`;
  return pageShell("はじめの設定", `${jobBanners("admin")}${head}${group(rows, "手順", "済んだ手順を押すと、その画面を開きます。")}${main}`);
}

async function retry(key) {
  const s = LAST && LAST.steps.find((x) => x.key === key);
  if (!s || !s.retry) return;
  try {
    for (const id of s.retry.bindings || []) {
      const r = await api.post(`/admin/bindings/${id}/sync`);
      trackJob({ id: r.job.id, kind: "sync_binding", name: "edge の A レコード" });
    }
    for (const id of s.retry.rules || []) {
      const r = await api.post(`/admin/slot-rules/${id}/publish`);
      trackJob({ id: r.job.id, kind: "publish_slots", name: "アドレス枠の DNS" });
    }
  } catch (e) {
    toast(e.message, "warn");
  }
  ctx.refresh();
}

export const START_ACTIONS = {
  "gs-open"() {
    ctx.goTab("admin", [{ page: "start" }]);
  },
  "gs-retry"(key) {
    retry(key);
  },
};

export const START_PAGES = {
  start: () => pStart(),
};
