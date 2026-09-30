// ジョブの進捗（作成・削除・DNS の作成など）。docs/UI.md 4「ジョブの進捗」。
// 受け付けたジョブを覚えておき、終わるまで GET /api/jobs/{id} で確かめる。
// 進捗はバナーと行の中だけを書き換え、終わったら画面を描き直す（入力中・シート表示中は閉じた後）。
import { api } from "../api.js";
import { banner, esc } from "../components/cell.js";
import { ic } from "../components/icons.js";
import { openSheet, sheetHead } from "../components/sheet.js";
import { toast } from "../components/toast.js";
import { ctx } from "../ctx.js";

const POLL_MS = 1500;
const FINISHED = ["succeeded", "failed", "rolled_back", "cancelled"];
const KIND = {
  deploy: { run: "を作成中", ok: "を公開しました", ng: "の作成に失敗しました", scope: "servers" },
  trash: { run: "をゴミ箱へ移動中", ok: "をゴミ箱へ移動しました", ng: "をゴミ箱へ移動できませんでした", scope: "servers" },
  restore: { run: "を元に戻しています", ok: "を元に戻しました", ng: "を元に戻せませんでした", scope: "servers" },
  purge: { run: "を完全に削除中", ok: "を完全に削除しました", ng: "を削除できませんでした", scope: "servers" },
  publish_slots: { run: "の DNS を作成中", ok: "の DNS を作成しました", ng: "の DNS を作成できませんでした", scope: "admin" },
  unpublish_slots: { run: "の DNS を削除中", ok: "の DNS を削除しました", ng: "の DNS を削除できませんでした", scope: "admin" },
  sync_binding: { run: "の DNS を反映中", ok: "の DNS を反映しました", ng: "の DNS を反映できませんでした", scope: "admin" },
  sync_firewall: { run: "に反映中", ok: "に反映しました", ng: "に反映できませんでした", scope: "admin" },
  delete_binding: { run: "の紐付けを削除中", ok: "の紐付けを削除しました", ng: "の紐付けを削除できませんでした", scope: "admin" },
};

const JOBS = new Map(); // id → { id, kind, name, serverId, status, step, steps, error, dismissed }
let timer = null;

/** 受け付けたジョブを追いかける。name はバナーに出す名前（サーバー名・枠の名前など）。 */
export function trackJob({ id, kind, name, serverId = null }) {
  if (!id || JOBS.has(id)) return;
  JOBS.set(id, { id, kind, name, serverId, status: "queued", step: "順番を待っています", steps: [], error: null });
  schedule();
}

/** 一覧を読み込んだときに、実行中のジョブがあるサーバーも追いかける（再読み込みしても進捗が出るように）。 */
export function trackServerJobs(servers) {
  for (const s of servers) if (s.job) trackJob({ id: s.job.id, kind: s.job.kind, name: s.name, serverId: s.id });
}

export function runningStep(serverId) {
  for (const j of JOBS.values()) if (j.serverId === serverId && !FINISHED.includes(j.status)) return j.step;
  return "";
}

function schedule() {
  if (!timer) timer = setTimeout(poll, POLL_MS);
}

async function poll() {
  timer = null;
  const active = [...JOBS.values()].filter((j) => !FINISHED.includes(j.status));
  let finished = false;
  for (const j of active) {
    let r;
    try {
      r = await api.get(`/jobs/${j.id}`);
    } catch (e) {
      if (e.status === 404) JOBS.delete(j.id);
      continue;
    }
    j.status = r.status;
    j.steps = r.steps || [];
    j.error = r.error;
    j.step = r.current_step || (r.status === "queued" ? "順番を待っています" : "");
    if (r.server_id && !j.serverId) j.serverId = r.server_id;
    if (FINISHED.includes(r.status)) {
      finished = true;
      const k = KIND[j.kind] || { ok: "が完了しました", ng: "に失敗しました" };
      if (r.status === "succeeded") {
        toast(`${j.name} ${k.ok}`);
        if (j.kind !== "deploy") j.dismissed = true; // 作成以外は、成功したらバナーを残さない
      } else {
        toast(`${j.name} ${k.ng}`, "warn");
      }
    }
    updateDom(j);
  }
  if (finished) ctx.refresh();
  if ([...JOBS.values()].some((j) => !FINISHED.includes(j.status))) schedule();
}

function progress(j) {
  const total = j.steps.length || 1;
  const done = j.steps.filter((s) => ["done", "skipped"].includes(s.status)).length;
  return Math.round((done / total) * 100);
}

function updateDom(j) {
  document.querySelectorAll(`[data-job="${j.id}"]`).forEach((el) => {
    if (FINISHED.includes(j.status)) return; // 終わったら描き直しで置き換える
    const bar = el.querySelector("[data-jbar]");
    if (bar) bar.style.width = `${progress(j)}%`;
    const st = el.querySelector("[data-jstep]");
    if (st) st.textContent = j.step;
  });
  document.querySelectorAll(`[data-jstep-row="${j.serverId}"]`).forEach((el) => {
    el.textContent = j.step || "処理中…";
  });
  const list = document.querySelector(`[data-jsteps="${j.id}"]`);
  if (list) list.innerHTML = stepsHtml(j);
}

function stepsHtml(j) {
  if (!j.steps.length) return `<li>${ic("ring")}<span>順番を待っています</span></li>`;
  const icon = { done: "ok", running: "spin", failed: "xc", undone: "restore", skipped: "ring" };
  const note = { done: "完了", running: "実行中", failed: "失敗", undone: "取り消し", skipped: "対象外" };
  return j.steps
    .map(
      (s) =>
        `<li class="${s.status === "done" ? "done" : s.status === "running" ? "run" : s.status === "failed" ? "fail" : s.status === "undone" ? "undone" : ""}">${ic(icon[s.status] || "ring")}<span>${esc(s.name)}</span><small>${esc(s.error || s.note || note[s.status] || "")}</small></li>`
    )
    .join("");
}

/** バナー（scope：servers / admin）。 */
export function jobBanners(scope) {
  return [...JOBS.values()]
    .filter((j) => !j.dismissed && (KIND[j.kind]?.scope || "servers") === scope)
    .map((j) => {
      const k = KIND[j.kind] || { run: "を処理中", ok: "が完了しました", ng: "に失敗しました" };
      const name = esc(j.name);
      const steps = `<button type="button" class="btn sm" data-act="job-steps" data-arg="${j.id}">手順</button>`;
      const close = `<button type="button" class="btn sm" data-act="job-dismiss" data-arg="${j.id}">閉じる</button>`;
      if (!FINISHED.includes(j.status)) {
        return `<div data-job="${j.id}">${banner(
          "info",
          `${name} ${k.run}`,
          `<div class="prog" style="margin:6px 0"><i data-jbar style="width:${progress(j)}%"></i></div><div style="font-size:14px;color:var(--label2)" data-jstep>${esc(j.step)}</div>`,
          steps
        )}</div>`;
      }
      if (j.status === "succeeded") {
        const open =
          j.kind === "deploy"
            ? `<button type="button" class="btn sm fill" data-act="open-server" data-arg="${esc(j.name)}">開く</button>`
            : "";
        return banner("ok", `${name} ${k.ok}`, "", open + steps + close);
      }
      const why =
        j.status === "rolled_back"
          ? "途中で失敗したため、作成したものはすべて取り消しました。"
          : j.status === "cancelled"
            ? "取り消されました。"
            : "取り消しにも失敗しました。管理者が確認します。";
      return banner("e", `${name} ${k.ng}`, `${j.error ? `${esc(j.error)}<br>` : ""}${why}`, steps + close);
    })
    .join("");
}

export const JOB_ACTIONS = {
  "job-dismiss"(id) {
    const j = JOBS.get(Number(id));
    if (j) j.dismissed = true;
    document.querySelectorAll(`[data-job="${id}"]`).forEach((el) => el.remove());
    ctx.refresh();
  },
  "job-steps"(id) {
    const j = JOBS.get(Number(id));
    if (!j) return;
    openSheet(
      `${sheetHead("手順", { left: "閉じる" })}<div class="sheet-b"><p class="sheet-lead">${esc(j.name)} ${esc((KIND[j.kind] || {}).run || "")}</p><ul class="steps" data-jsteps="${j.id}">${stepsHtml(j)}</ul></div>`,
      "small"
    );
  },
};
