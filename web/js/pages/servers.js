// 画面：サーバーの一覧・詳細・ゴミ箱・新しいサーバー（docs/UI.md 3.2・3.3、docs/TASKS.md T16）。
// 見た目の正解は web/demo.html。まだ API の無い項目（バックアップ・共有・プラグインなど）は出さない。
import { api } from "../api.js";
import { banner, cell, esc, group } from "../components/cell.js";
import { ic } from "../components/icons.js";
import { openMenu, openPicker, pickerButton } from "../components/picker.js";
import { closeSheet, confirmSheet, onSheetClosed, openSheet, sheetHead } from "../components/sheet.js";
import { toast } from "../components/toast.js";
import { copyText, ctx, isAdmin, navbar, pageShell, remaining, shortTime } from "../ctx.js";
import { jobBanners, runningStep, trackJob, trackServerJobs } from "./jobs.js";

// 状態 → [表示, 点の色（.dot のクラス）, 札の色（.pill のクラス）]
const STATUS = {
  pending: ["作成中", "installing", "o"],
  provisioning: ["作成中", "installing", "o"],
  running: ["稼働中", "", "g"],
  stopped: ["停止中", "off", ""],
  maintenance: ["メンテナンス中", "maint", "b"],
  suspended: ["利用停止中", "suspended", "r"],
  trashed: ["ゴミ箱", "off", ""],
  purging: ["削除中", "off", ""],
  failed: ["作成に失敗", "down", "r"],
};
const stLabel = (s) => (STATUS[s.status] || [s.status])[0];
const stDot = (s) => `<span class="dot ${(STATUS[s.status] || [])[1] || ""}"></span>`;
const stPill = (s) => `<span class="pill ${(STATUS[s.status] || [])[2] || ""}">${esc(stLabel(s))}</span>`;
const KIND_DESC = { mc: "Minecraft サーバー", mod: "Mod を入れる Minecraft", proxy: "Velocity などのプロキシ", other: "その他のゲーム" };
const NAME_RE = /^[a-z0-9][a-z0-9-]{1,30}[a-z0-9]$/;
const RESERVED = ["www", "edge", "edge-1", "edge-2", "panel", "gp", "api", "status", "mail", "admin", "play", "kuma", "dev", "_minecraft"];

const CACHE = new Map(); // 名前 → Server（… メニューで使う）
let CATALOG = null;

const mine = (s) => s.owner && ctx.me && s.owner.id === ctx.me.user.id;
const canPower = (s) => ["admin", "owner", "console", "files", "full", "support"].includes(s.my_permission);
const canManage = (s) => ["admin", "owner"].includes(s.my_permission);
const busy = (s) => !!s.job || ["pending", "provisioning", "purging"].includes(s.status);

export function nameProblem(name) {
  if (!name) return "名前を入力してください";
  if (name !== name.toLowerCase()) return "英小文字で入力してください";
  if (!NAME_RE.test(name)) return "英小文字・数字・ハイフンで3〜32文字にしてください（先頭と末尾は英数字）";
  if (name.includes("--")) return "ハイフンを2つ続けることはできません";
  if (RESERVED.includes(name)) return "システムで使う名前のため選べません";
  return "";
}

function remember(items) {
  for (const s of items) CACHE.set(s.name, s);
}

/* ---------------- 一覧 ---------------- */
export async function pServers() {
  const [list, trash] = await Promise.all([api.get("/servers"), api.get("/servers?status=trashed,purging")]);
  const items = list.items;
  remember(items);
  remember(trash.items);
  trackServerJobs(items);
  const lim = ctx.me.limits.max_servers;
  const owned = [...items, ...trash.items].filter((s) => mine(s) && !s.is_dev_copy).length;
  const left = lim === null || lim === undefined ? null : Math.max(0, lim - owned);
  const up = items.filter((s) => s.status === "running").length;
  const need = items.filter(
    (s) => s.status === "suspended" || s.status === "failed" || (s.expires_at && remaining(s.expires_at).days <= 3)
  );
  const needWhy = (s) =>
    [
      s.status === "suspended" ? "管理者により利用停止中" : "",
      s.status === "failed" ? "作成に失敗しました" : "",
      s.expires_at && remaining(s.expires_at).days <= 3 ? `期限まであと${remaining(s.expires_at).days}日` : "",
    ]
      .filter(Boolean)
      .join("・");
  const row = (s) =>
    cell({
      title: `${stDot(s)}<b style="font-weight:600">${esc(s.name)}</b>${!mine(s) ? `<span class="pill">${esc(s.owner.username)}</span>` : ""}${s.is_dev_copy ? '<span class="pill">開発用</span>' : ""}`,
      sub: busy(s)
        ? `<span data-jstep-row="${esc(s.id)}">${esc(runningStep(s.id) || (s.job && s.job.current_step) || "処理中…")}</span>`
        : `<span class="mono">${esc(s.address || "—")}</span>`,
      val: esc(stLabel(s)),
      act: "open-server",
      arg: s.name,
      more: { act: "menu-server", arg: s.name, label: `${s.name} の操作` },
    });
  return `${navbar("サーバー", { right: `<button type="button" class="circle tint" data-act="wizard" aria-label="新しいサーバー">${ic("plus")}</button>` })}
    <div class="page"><h1 class="large">サーバー</h1>
    ${ctx.me.user.status === "deleting" ? banner("w", "退会の手続き中です", "新しいサーバーは作成できません。") : ""}
    ${jobBanners("servers")}
    <div class="tiles four">
      <button type="button" class="tile" data-act="tab" data-arg="monitor"><div class="top"><span class="ico" style="background:var(--green)">${ic("pulse")}</span><span class="n">${up}</span></div><span class="l">稼働中 / ${items.length}台</span></button>
      <button type="button" class="tile" data-act="scroll-need"><div class="top"><span class="ico" style="background:${need.length ? "var(--red)" : "var(--gray)"}">${ic("warn")}</span><span class="n">${need.length}</span></div><span class="l">要対応</span></button>
      <button type="button" class="tile" data-act="wizard" ${left === 0 ? "disabled" : ""}><div class="top"><span class="ico" style="background:var(--blue)">${ic("plus")}</span><span class="n">${left ?? "∞"}</span></div><span class="l">作成できる残り</span></button>
      <button type="button" class="tile" data-act="go" data-arg="trash"><div class="top"><span class="ico" style="background:var(--gray)">${ic("trash")}</span><span class="n">${trash.items.length}</span></div><span class="l">ゴミ箱</span></button>
    </div>
    ${need.length ? `<div id="need">${group(need.map((s) => cell({ icon: "warn", color: "var(--red)", title: esc(s.name), sub: esc(needWhy(s)), act: "open-server", arg: s.name })), "要対応")}</div>` : ""}
    ${group(
      items.length ? items.map(row) : [cell({ title: "サーバーはまだありません", sub: "右上の＋から作成できます" })],
      isAdmin() ? "すべてのサーバー" : "あなたのサーバー"
    )}
    </div>`;
}

/* ---------------- 詳細 ---------------- */
const gb = (bytes) => (bytes / 1024 ** 3).toFixed(bytes >= 10 * 1024 ** 3 ? 0 : 1);
function meter(label, pct, cap) {
  const p = Math.max(0, Math.min(100, Math.round(pct)));
  const c = p >= 90 ? "hot" : p >= 75 ? "mid" : "";
  return `<div class="cell" style="display:block"><div style="display:flex;justify-content:space-between"><span>${label}</span><span class="val">${p}%<span style="font-size:13px"> / ${cap}</span></span></div><div class="meter"><i class="${c}" style="width:${p}%"></i></div></div>`;
}

export async function pServer(name) {
  const s = await api.get(`/servers/${encodeURIComponent(name)}`);
  remember([s]);
  trackServerJobs([s]);
  const running = s.status === "running";
  const locked = s.status === "suspended" && !isAdmin();
  const isBusy = busy(s);
  const power = canPower(s) && !locked && !isBusy && ["running", "stopped"].includes(s.status);
  const panel = ctx.me.panel_url;
  const plan = (CATALOG && CATALOG.plans.find((p) => p.id === s.plan)) || null;
  const exp = s.expires_at ? remaining(s.expires_at) : null;
  const html = `${navbar(s.name, { back: ctx.backLabel(), right: `<button type="button" class="circle" data-act="menu-server" data-arg="${esc(s.name)}" aria-label="その他の操作">${ic("more")}</button>` })}
  <div class="page">
    <div class="hero">
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap"><h1 style="font-size:28px;margin:0;word-break:break-all">${esc(s.name)}</h1>${stPill(s)}</div>
      <div style="color:var(--label2);font-size:15px;margin-top:4px">${esc(s.game)}・${esc(plan ? plan.name : s.plan)}${s.node ? `・${esc(s.node)}` : ""}${!mine(s) ? `・所有者 ${esc(s.owner.username)}` : ""}</div>
      ${s.address ? `<button type="button" class="addr" data-act="copy" data-arg="${esc(s.address)}" aria-label="アドレスをコピー"><span>${esc(s.address)}</span>${ic("copy")}</button>` : ""}
      <div class="acts">
        <button type="button" class="act ${running ? "red" : ""}" data-act="power" data-arg="${running ? "stop" : "start"}|${esc(s.name)}" ${power ? "" : "disabled"}>${running ? `${ic("stop")}停止` : `${ic("play")}起動`}</button>
        <button type="button" class="act" data-act="power" data-arg="restart|${esc(s.name)}" ${power && running ? "" : "disabled"}>${ic("restart")}再起動</button>
        <button type="button" class="act" data-act="maint" data-arg="${esc(s.name)}" ${canManage(s) && !locked && !isBusy && ["running", "stopped", "maintenance"].includes(s.status) ? "" : "disabled"}>${ic("wrench")}${s.status === "maintenance" ? "メンテ終了" : "メンテ"}</button>
        ${panel ? `<a class="act" href="${esc(panel)}" target="_blank" rel="noopener" style="text-decoration:none">${ic("open")}パネル</a>` : `<button type="button" class="act" disabled>${ic("open")}パネル</button>`}
      </div>
    </div>
    ${s.status === "suspended" ? banner("e", "管理者により利用停止中", `理由：${esc(s.suspend_reason || "—")}`) : ""}
    ${s.status === "maintenance" ? banner("info", "メンテナンス中", `監視と自動再起動を止めています。${s.maintenance_until ? `${esc(shortTime(s.maintenance_until))} に自動で終わります。` : ""}`) : ""}
    ${s.status === "failed" ? banner("e", "作成に失敗しました", "作成したものは取り消しました。削除して、もう一度作成してください。") : ""}
    ${isBusy ? `<div data-job="${s.job ? s.job.id : ""}">${banner("info", "処理しています", `<span data-jstep-row="${esc(s.id)}">${esc(runningStep(s.id) || (s.job && s.job.current_step) || "完了するまで一部の操作はできません。")}</span>`)}</div>` : ""}
    <div id="res">${group([cell({ title: "使用量", sub: running ? "読み込み中…" : "停止中は表示しません" })], "いまの状態")}</div>
    ${exp ? group([cell({ title: `あと ${exp.days} 日`, sub: `${esc(shortTime(s.expires_at))} に停止し、その14日後にゴミ箱へ移動します`, subWrap: true })], "利用期限") : ""}
    ${
      s.slot
        ? group(
            [
              cell({ icon: "pin", color: "var(--teal)", title: `<span class="mono">${esc(s.address || "—")}</span>`, sub: `ポート ${s.slot.port}`, val: s.address ? `<button type="button" class="btn sm" data-act="copy" data-arg="${esc(s.address)}" aria-label="アドレスをコピー">${ic("copy")}</button>` : "" }),
              s.direct ? cell({ icon: "net", color: "var(--gray)", title: `<span class="mono">${esc(s.direct)}</span>`, sub: "ポートを指定して直接つなぐ場合" }) : "",
            ].filter(Boolean),
            "アドレス",
            s.proxy ? `${esc(s.proxy.name)} の配下のため、直接は接続できません。` : ""
          )
        : ""
    }
    ${
      canManage(s)
        ? group(
            [
              cell({ title: "ダウン時に自動で再起動", sub: "1時間に3回まで。超えたら止めて通知します", subWrap: true, val: `<input type="checkbox" class="switch" data-srv-set="auto_restart" data-arg="${esc(s.name)}" ${s.auto_restart ? "checked" : ""} ${locked ? "disabled" : ""} aria-label="自動再起動">` }),
              cell({ title: "状態ページに公開", sub: "稼働状況と人数だけを公開します", subWrap: true, val: `<input type="checkbox" class="switch" data-srv-set="public_status" data-arg="${esc(s.name)}" ${s.public_status ? "checked" : ""} ${locked ? "disabled" : ""} aria-label="状態ページに公開">` }),
            ],
            "動作"
          )
        : ""
    }
    ${canManage(s) ? `<div style="height:22px"></div>${group([`<button type="button" class="cell danger" data-act="trash" data-arg="${esc(s.name)}" ${isBusy || s.status === "purging" ? "disabled" : ""}>サーバーを削除</button>`], "", "ゴミ箱へ移動します。72時間以内なら元に戻せます。")}` : ""}
  </div>`;
  const after = async () => {
    if (!running) return;
    try {
      const r = await api.get(`/servers/${encodeURIComponent(s.name)}/resources`);
      const box = document.getElementById("res");
      if (!box) return;
      box.innerHTML = group(
        [
          meter("CPU", r.cpu_percent, "使用率"),
          meter("メモリ", (r.memory_bytes / r.memory_limit) * 100, `${gb(r.memory_limit)}GB`),
          meter("ディスク", (r.disk_bytes / r.disk_limit) * 100, `${gb(r.disk_limit)}GB`),
        ],
        "いまの状態",
        r.disk_bytes / r.disk_limit >= 0.9 ? '<span class="err-t">ディスクが90%を超えています。不要なワールドやログを整理してください。</span>' : ""
      );
    } catch (e) {
      const box = document.getElementById("res");
      if (box) box.innerHTML = group([cell({ icon: "warn", color: "var(--orange)", title: "使用量を取得できませんでした", sub: esc(e.message), subWrap: true })], "いまの状態");
    }
  };
  return { html, after };
}

/* ---------------- ゴミ箱 ---------------- */
export async function pTrash() {
  const r = await api.get("/servers?status=trashed,purging");
  remember(r.items);
  trackServerJobs(r.items);
  const rows = r.items.map((s) => {
    const left = s.purge_after ? remaining(s.purge_after) : null;
    return cell({
      icon: "trash",
      color: "var(--gray)",
      title: `${esc(s.name)}${!mine(s) ? ` <span class="pill">${esc(s.owner.username)}</span>` : ""}`,
      sub: s.status === "purging" ? "完全に削除しています…" : left ? `あと${left.hours}時間で完全に削除・${esc(shortTime(s.trashed_at))} に移動` : "",
      subWrap: true,
      more: s.status === "trashed" && canManage(s) ? { act: "menu-trash", arg: s.name, label: `${s.name} の操作` } : undefined,
    });
  });
  return pageShell(
    "ゴミ箱",
    `${jobBanners("servers")}${group(rows.length ? rows : [cell({ title: "ゴミ箱は空です" })], "", "72時間後に完全に削除されます。それまでは元に戻せます。名前とアドレスは、完全に削除されるまで予約されたままです。")}`
  );
}

/* ---------------- 新しいサーバー（シート） ---------------- */
let W = null;

async function openWizard() {
  try {
    [CATALOG, ctx.me] = await Promise.all([CATALOG ? Promise.resolve(CATALOG) : api.get("/catalog"), api.get("/me")]);
  } catch (e) {
    return toast(e.message, "warn");
  }
  W = {
    step: 0,
    owner: ctx.me.user.id,
    ownerName: ctx.me.user.username,
    users: null,
    game: CATALOG.games[0] ? CATALOG.games[0].id : "",
    plan: CATALOG.plans[0] ? CATALOG.plans[0].id : "",
    name: "",
    slots: [],
    port: null,
    idem: crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random(),
  };
  await loadSlots();
  renderWizard();
}

async function loadSlots() {
  const q = W.owner !== ctx.me.user.id ? `?owner_id=${encodeURIComponent(W.owner)}` : "";
  W.slots = (await api.get(`/slots/available${q}`)).items;
  if (!W.slots.some((x) => x.port === W.port)) W.port = W.slots[0] ? W.slots[0].port : null;
}

function slotLabel(x) {
  return x.fqdn || `${W.name || "（サーバー名）"}.${x.rule.domain}`;
}
function wizAddress() {
  const x = W.slots.find((y) => y.port === W.port);
  return x ? slotLabel(x) : "—";
}
function slotOptions() {
  // サーバー名がホスト名になる枠は、どのポートでも同じアドレスになるので、枠ごとに1つだけ出す
  const seen = new Set();
  return W.slots
    .filter((x) => {
      if (x.fqdn) return true;
      if (seen.has(x.rule.id) && x.port !== W.port) return false;
      seen.add(x.rule.id);
      return true;
    })
    .map((x) => ({
      value: String(x.port),
      label: slotLabel(x),
      sub: x.fqdn
        ? `${x.rule.name}・ポート ${x.port}${x.dns_ready ? "・DNS 作成済み" : ""}`
        : `${x.rule.name}・ポート ${x.port}（空きから自動）`,
    }));
}

function renderWizard() {
  if (!W) return;
  const game = CATALOG.games.find((g) => g.id === W.game);
  const plan = CATALOG.plans.find((p) => p.id === W.plan);
  const lim = ctx.me.limits;
  const selfOwner = W.owner === ctx.me.user.id;
  const atLimit = selfOwner && !isAdmin() && lim.max_servers !== null && lim.used >= lim.max_servers;
  const deleting = ctx.me.user.status === "deleting";
  const noSlot = !W.slots.length;
  let head = "";
  let body = "";
  if (W.step === 0) {
    const blocked = atLimit || deleting || noSlot || !CATALOG.games.length;
    head = sheetHead("新しいサーバー", { left: "キャンセル", right: "次へ", rightAct: "wz-next", rightDisabled: blocked });
    body = `<div class="pick" role="group" aria-label="ゲーム">${CATALOG.games
      .map((g) => `<button type="button" class="pk" data-act="wz-game" data-arg="${esc(g.id)}" aria-pressed="${W.game === g.id}"><b>${esc(g.label)}</b><small>${esc(KIND_DESC[g.kind] || "")}</small></button>`)
      .join("")}</div>
      ${!CATALOG.games.length ? `<div style="height:12px"></div>${banner("w", "選べるゲームがありません", "管理者が /etc/hatch/games.yml にゲームを登録すると選べるようになります。")}` : ""}
      ${deleting ? `<div style="height:12px"></div>${banner("w", "退会の手続き中は作成できません", "設定から退会を取り消すと作成できます。")}` : ""}
      ${atLimit ? `<div style="height:12px"></div>${banner("w", "作成できる台数の上限です", `${lim.used} / ${lim.max_servers} 台を使っています。ゴミ箱のサーバーも台数に含まれます。`)}` : ""}
      ${noSlot && !atLimit ? `<div style="height:12px"></div>${banner("w", "空いているアドレスがありません", isAdmin() ? "管理 → アドレス枠 で枠を追加してください。" : "ゴミ箱のサーバーを完全に削除するか、管理者に枠の追加を依頼してください。")}` : ""}
      <div style="height:16px"></div><button type="button" class="btn fill block" data-act="wz-next" ${blocked ? "disabled" : ""}>次へ</button>`;
  }
  if (W.step === 1) {
    const prob = W.name ? nameProblem(W.name) : "";
    head = sheetHead(`${esc(game.label)} の設定`, { left: "戻る", leftAct: "wz-back", right: "次へ", rightAct: "wz-next", rightDisabled: !W.name || !!prob || !W.port, rightId: "wz-next" });
    const ownerRow = isAdmin()
      ? `${group([`<div class="field"><label>所有者</label>${pickerButton({ options: (W.users || []).map((u) => ({ value: u.id, label: u.username })), value: W.owner, placeholder: W.ownerName, attrs: 'data-act="wz-owner" aria-label="所有者"' })}</div>`])}<div style="height:14px"></div>`
      : "";
    body = `${ownerRow}
      ${group([`<div class="field"><label for="wz-name">名前</label><input id="wz-name" value="${esc(W.name)}" placeholder="例：storia" autocomplete="off" autocapitalize="off" spellcheck="false" enterkeyhint="next" aria-describedby="wz-h"></div>`], "", `<span id="wz-h" class="${prob ? "err-t" : ""}">${prob ? esc(prob) : "英小文字・数字・ハイフンで3〜32文字。ゲームパネルでの表示名にもなります。"}</span>`)}
      <div class="gh">プラン</div><div class="pick">${CATALOG.plans
        .map((p) => `<button type="button" class="pk" data-act="wz-plan" data-arg="${esc(p.id)}" aria-pressed="${W.plan === p.id}"><b>${esc(p.name)}</b><small>メモリ ${p.memory_mb / 1024}GB・${p.cpu_percent / 100}コア・${p.disk_mb / 1024}GB<br>バックアップ ${p.backup_limit}個・${p.period_days}日</small></button>`)
        .join("")}</div>
      ${group([`<div class="field"><label>アドレス</label>${pickerButton({ options: slotOptions(), value: String(W.port), attrs: 'data-act="wz-port" aria-label="アドレス"' })}</div>`], "接続アドレス", `<span id="wz-addr" class="mono">${esc(wizAddress())}</span>`)}`;
  }
  if (W.step === 2) {
    const x = W.slots.find((y) => y.port === W.port);
    const minecraft = ["mc", "mod", "proxy"].includes(game.kind);
    head = sheetHead("確認", { left: "戻る", leftAct: "wz-back", right: "作成", rightAct: "wz-run", rightId: "wz-run-top" });
    body = `<div class="hero" style="text-align:center;margin-bottom:6px"><div style="font-size:13px;color:var(--label2)">接続アドレス</div><div class="mono" style="font-size:20px;font-weight:600;margin-top:4px;word-break:break-all">${esc(wizAddress())}</div><div style="font-size:14px;color:var(--label2);margin-top:4px">${esc(game.label)}・${esc(plan.name)}・ポート ${W.port}${W.owner !== ctx.me.user.id ? `・所有者 ${esc(W.ownerName)}` : ""}</div></div>
      ${group(
        [
          cell({ icon: "server", color: "var(--blue)", title: "ゲームパネルにサーバーを作成", sub: "空きメモリが最も多いノードに作ります", subWrap: true }),
          cell({ icon: "net", color: "var(--indigo)", title: `edge にポート ${W.port} を公開`, sub: "接続数の制限付き" }),
          cell({ icon: "globe", color: "var(--teal)", title: x && x.dns_ready ? "DNS は作成済み（確認のみ）" : "DNS を作成", sub: `CNAME${minecraft ? "・SRV" : ""}（プロキシなし）` }),
          cell({ icon: "clock", color: "var(--orange)", title: "利用期限", sub: `${plan.period_days}日後。期限の前に通知します` }),
        ],
        "実行する内容",
        "途中で失敗したら、作成したものはすべて自動で取り消します。"
      )}
      <div id="wz-err" class="err-t" role="alert" style="font-size:14px;margin:8px 4px 0"></div>
      <div style="height:12px"></div><button type="button" class="btn fill block" data-act="wz-run" id="wz-run">作成</button>`;
  }
  openSheet(head + `<div class="sheet-b">${body}</div>`);
  if (W.step === 1) {
    const inp = document.getElementById("wz-name");
    if (inp && !matchMedia("(pointer:coarse)").matches) {
      inp.focus();
      inp.setSelectionRange(inp.value.length, inp.value.length);
    }
  }
}

function onWizardName(value) {
  W.name = value.trim();
  const prob = W.name ? nameProblem(W.name) : "";
  const h = document.getElementById("wz-h");
  if (h) {
    h.textContent = prob || "英小文字・数字・ハイフンで3〜32文字。ゲームパネルでの表示名にもなります。";
    h.className = prob ? "err-t" : "";
  }
  const next = document.getElementById("wz-next");
  if (next) next.disabled = !W.name || !!prob || !W.port;
  const a = document.getElementById("wz-addr");
  if (a) a.textContent = wizAddress();
  const pv = document.querySelector('[data-act="wz-port"] .pv');
  if (pv) pv.textContent = wizAddress();
}

async function runWizard() {
  const btns = ["wz-run", "wz-run-top"].map((id) => document.getElementById(id)).filter(Boolean);
  btns.forEach((b) => (b.disabled = true));
  const body = { name: W.name, game: W.game, plan: W.plan, slot_port: W.port };
  if (W.owner !== ctx.me.user.id) body.owner_id = W.owner;
  try {
    const r = await api.post("/servers", body, { "Idempotency-Key": W.idem });
    trackJob({ id: r.job.id, kind: "deploy", name: r.server.name, serverId: r.server.id });
    W = null;
    closeSheet();
    toast("作成を始めました");
    if (ctx.route.tab !== "servers" || ctx.route.stack.length) ctx.goTab("servers", []);
    else ctx.refresh();
  } catch (e) {
    const err = document.getElementById("wz-err");
    if (err) err.textContent = e.message;
    btns.forEach((b) => (b.disabled = false));
  }
}

/* ---------------- 操作 ---------------- */
async function power(signal, name) {
  const label = { start: "起動", stop: "停止", restart: "再起動" }[signal];
  try {
    await api.post(`/servers/${encodeURIComponent(name)}/power`, { signal });
    toast(`${name} を${label}しました`);
  } catch (e) {
    toast(e.message, "warn");
  }
  ctx.refresh();
}

function trashServer(name) {
  confirmSheet({
    title: "サーバーを削除",
    body: `<b>${esc(name)}</b> をゴミ箱へ移動します。サーバーは停止し、アドレスからつながらなくなります。72時間以内なら元に戻せます。`,
    ok: "ゴミ箱へ移動",
    danger: true,
    onOk: async () => {
      const r = await api.del(`/servers/${encodeURIComponent(name)}`);
      trackJob({ id: r.job.id, kind: "trash", name });
      const top = ctx.route.stack[ctx.route.stack.length - 1];
      if (top && top.page === "server") ctx.goTab("servers", []);
      else ctx.refresh();
    },
  });
}

async function restoreServer(name) {
  try {
    const r = await api.post(`/servers/${encodeURIComponent(name)}/restore`);
    trackJob({ id: r.job.id, kind: "restore", name });
    toast(`${name} を元に戻しています`);
  } catch (e) {
    toast(e.message, "warn");
  }
  ctx.refresh();
}

function purgeServer(name) {
  confirmSheet({
    title: "完全に削除",
    body: `<b>${esc(name)}</b> を完全に削除します。<b>元に戻せません。</b>ワールドなどのデータもすべて消えます。`,
    ok: "完全に削除",
    danger: true,
    requireText: name,
    onOk: async () => {
      const r = await api.post(`/servers/${encodeURIComponent(name)}/purge`, { confirm_name: name });
      trackJob({ id: r.job.id, kind: "purge", name });
      ctx.refresh();
    },
  });
}

export const SERVER_ACTIONS = {
  wizard() {
    openWizard();
  },
  "open-server"(name) {
    const top = ctx.route.stack[ctx.route.stack.length - 1];
    if (ctx.route.tab === "servers" && !top) ctx.go("server", name);
    else ctx.goTab("servers", [{ page: "server", arg: name }]);
  },
  "scroll-need"() {
    document.getElementById("need")?.scrollIntoView({ behavior: "smooth", block: "start" });
  },
  copy(text) {
    copyText(text);
  },
  power(arg) {
    const [signal, ...rest] = arg.split("|");
    power(signal, rest.join("|"));
  },
  async maint(name) {
    const s = CACHE.get(name);
    const on = !(s && s.status === "maintenance");
    try {
      await api.post(`/servers/${encodeURIComponent(name)}/maintenance`, { enabled: on, hours: 2 });
      toast(on ? "メンテナンスを始めました（2時間後に自動で終わります）" : "メンテナンスを終えました");
    } catch (e) {
      toast(e.message, "warn");
    }
    ctx.refresh();
  },
  trash(name) {
    trashServer(name);
  },
  "menu-server"(name, el) {
    const s = CACHE.get(name);
    if (!s) return;
    const ok = canPower(s) && !busy(s) && ["running", "stopped"].includes(s.status) && !(s.status === "suspended" && !isAdmin());
    openMenu(el, [
      { label: "開く", icon: "chev", run: () => SERVER_ACTIONS["open-server"](name) },
      s.status === "running" && ok ? { label: "停止", icon: "stop", run: () => power("stop", name) } : null,
      s.status === "running" && ok ? { label: "再起動", icon: "restart", run: () => power("restart", name) } : null,
      s.status === "stopped" && ok ? { label: "起動", icon: "play", run: () => power("start", name) } : null,
      s.address ? { label: "アドレスをコピー", icon: "copy", run: () => copyText(s.address) } : null,
      canManage(s) ? "-" : null,
      canManage(s) ? { label: "削除", icon: "trash", red: true, disabled: busy(s), run: () => trashServer(name) } : null,
    ]);
  },
  "menu-trash"(name, el) {
    openMenu(el, [
      { label: "元に戻す", icon: "restore", run: () => restoreServer(name) },
      { label: "完全に削除", icon: "trash", red: true, run: () => purgeServer(name) },
    ]);
  },
  "wz-game"(id) {
    W.game = id;
    renderWizard();
  },
  "wz-plan"(id) {
    W.plan = id;
    document.querySelectorAll('[data-act="wz-plan"]').forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.arg === id)));
  },
  async "wz-owner"(_a, el) {
    if (!W.users) {
      try {
        W.users = (await api.get("/admin/users")).items.filter((u) => u.status === "active");
      } catch (e) {
        return toast(e.message, "warn");
      }
    }
    openPicker(el, W.users.map((u) => ({ value: u.id, label: u.username, sub: { admin: "管理者", supporter: "サポーター", user: "利用者" }[u.role] })), W.owner, async (v) => {
      W.owner = v;
      W.ownerName = (W.users.find((u) => u.id === v) || {}).username || "";
      await loadSlots();
      renderWizard();
    });
  },
  "wz-port"(_a, el) {
    openPicker(el, slotOptions(), String(W.port), (v) => {
      W.port = Number(v);
      onWizardName(W.name);
      document.querySelector('[data-act="wz-port"]')?.focus();
    });
  },
  "wz-next"() {
    W.step += 1;
    renderWizard();
  },
  "wz-back"() {
    W.step -= 1;
    renderWizard();
  },
  "wz-run"() {
    runWizard();
  },
};

document.addEventListener("input", (e) => {
  if (e.target.id === "wz-name" && W) onWizardName(e.target.value);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && e.target.id === "wz-name" && W) {
    const next = document.getElementById("wz-next");
    if (next && !next.disabled) {
      e.preventDefault();
      next.click();
    }
  }
});
document.addEventListener("change", async (e) => {
  const t = e.target;
  if (!t.dataset.srvSet) return;
  try {
    await api.patch(`/servers/${encodeURIComponent(t.dataset.arg)}`, { [t.dataset.srvSet]: t.checked });
    toast("変更しました");
  } catch (err) {
    t.checked = !t.checked;
    toast(err.message, "warn");
  }
});

/** 画面の見出し（詳細のスタックの表示名）。 */
export function serverTitle(entry) {
  if (entry.page === "server") return entry.arg;
  if (entry.page === "trash") return "ゴミ箱";
  return null;
}

export const SERVER_PAGES = {
  root: () => pServers(),
  server: (arg) => pServer(arg),
  trash: () => pTrash(),
};

onSheetClosed(() => {
  W = null;
});
