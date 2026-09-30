// 初期設定画面（ウィザード）。docs/UI.md「初期設定」、docs/API.md「初期設定」。
// 初期設定が終わるまでは、パネルを開くとこの画面になる。操作は初期設定コードで許可される。
import { esc } from "./components/cell.js";
import { ic } from "./components/icons.js";

const STEPS = [
  { id: "code", title: "初期設定コード" },
  { id: "url", title: "このパネルの URL" },
  { id: "panel", title: "ゲームパネル", check: "panel" },
  { id: "cloudflare", title: "Cloudflare", check: "cloudflare" },
  { id: "kuma", title: "Uptime Kuma", check: "kuma" },
  { id: "discord", title: "Discord アプリと Bot", check: "discord" },
  { id: "roles", title: "Discord のロールとチャンネル" },
  { id: "save", title: "確認して保存" },
];

const S = {
  step: 0,
  code: "",
  values: {},
  secretsSet: [],
  checks: {},
  discord: null,
  adminRole: "",
  adminMax: 10,
  userRoles: {},
  busy: false,
  error: "",
};

const root = () => document.getElementById("auth");

async function call(method, path, body) {
  const res = await fetch(`/api/setup${path}`, {
    method,
    headers: { "Content-Type": "application/json", "X-Setup-Code": S.code },
    credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (!res.ok) {
    const e = (data && data.error) || {};
    const fields = e.detail && e.detail.fields ? Object.values(e.detail.fields) : [];
    throw new Error([e.message || "通信エラーが発生しました。", ...fields].join("\n"));
  }
  return data;
}

export async function setupStatus() {
  try {
    const res = await fetch("/api/setup/status", { credentials: "same-origin" });
    return res.ok ? await res.json() : null;
  } catch {
    return null;
  }
}

/* ---------------- 部品 ---------------- */
const val = (k) => S.values[k] ?? "";
const publicUrl = () => (val("PD_PUBLIC_URL") || location.origin).replace(/\/+$/, "");
const redirectUri = () => `${publicUrl()}/api/auth/callback`;

function input(key, label, { secret = false, placeholder = "", hint = "", type = "text" } = {}) {
  const isSet = secret && S.secretsSet.includes(key);
  const ph = isSet ? "設定済み（変える場合だけ入力）" : placeholder;
  return `<div class="field col"><label for="f-${key}">${esc(label)}</label>
    <input id="f-${key}" data-k="${key}" type="${secret ? "password" : type}" value="${esc(secret ? "" : val(key))}"
      placeholder="${esc(ph)}" autocomplete="off" autocapitalize="off" spellcheck="false">
    ${hint ? `<div class="hint">${hint}</div>` : ""}</div>`;
}
function fields(items) {
  return `<div class="group">${items.join("")}</div>`;
}
function copyBox(text) {
  return `<div class="copybox"><code>${esc(text)}</code><button type="button" class="btn sm" data-s="copy" data-v="${esc(text)}">コピー</button></div>`;
}
function guide(title, items) {
  return `<details class="guide" open><summary>${esc(title)}</summary><ol>${items.map((i) => `<li>${i}</li>`).join("")}</ol></details>`;
}
function ext(url, label) {
  return `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(label)}</a>`;
}
function result(r) {
  if (!r) return "";
  const kind = r.ok ? "ok" : "e";
  const icon = r.ok ? "ok" : "xc";
  return `<div class="banner ${kind}" role="status">${ic(icon)}<div class="tx">${esc(r.message)}</div></div>`;
}

/* ---------------- 各ステップ ---------------- */
function pCode() {
  return `<p class="lead">パネルを最初に開いた人が設定できないように、初期設定コードで確認します。</p>
    ${guide("コードの見つけ方", [
      "Proxmox でインストールしたときの最後の表示に「初期設定コード」が出ています。",
      "見当たらない場合は、Proxmox ホストで <code>pct enter &lt;コンテナID&gt;</code> を実行し、コンテナ内で <code>hatch-setup</code> を実行すると表示されます。",
    ])}
    <div class="group"><div class="field col"><label for="f-code">初期設定コード</label>
      <input id="f-code" class="code-in" value="${esc(S.code)}" placeholder="XXXX-XXXX-XXXX" autocomplete="off" autocapitalize="characters" spellcheck="false"></div></div>`;
}

function pUrl() {
  if (!S.values.PD_PUBLIC_URL) S.values.PD_PUBLIC_URL = location.origin;
  return `<p class="lead">利用者がこのパネルを開くときの URL と、Tailscale の中から見た URL です。</p>
    ${fields([
      input("PD_PUBLIC_URL", "公開 URL", {
        placeholder: "https://hatch.example.com",
        hint: "Cloudflare Tunnel などで公開した https の URL。Discord ログインの戻り先になります。まだ公開していなければ今の URL のままで進め、公開後に <code>hatch-setup --reset</code> で変更できます。",
      }),
      input("PD_INTERNAL_URL", "Tailscale 内の URL", {
        placeholder: "http://hatch:8080",
        hint: "Uptime Kuma からこのコンテナへ通知を送る先です。<code>http://&lt;Tailscale のマシン名&gt;:8080</code> の形で、マシン名は Tailscale 管理画面の Machines で確認できます（既定は <code>hatch</code>）。",
      }),
    ])}`;
}

function pPanel() {
  return `<p class="lead">サーバーの作成・起動などに使う Pterodactyl の URL と API キーです。キーは作成直後に一度しか表示されないので、すぐに貼り付けてください。</p>
    ${guide("Application API キー（ptla_…）の作り方", [
      "Pterodactyl に<b>管理者</b>でログインし、右上の歯車（管理画面）を開きます。",
      "左のメニューの <b>Application API</b> → <b>Create New</b>。",
      "Description に <code>hatch</code> と入れ、すべての項目を <b>Read &amp; Write</b> にします。",
      "<b>Create Credentials</b> を押し、表示された <code>ptla_</code> で始まるキーをコピーします。",
    ])}
    ${guide("Client API キー（ptlc_…）の作り方", [
      "同じ管理者アカウントのまま、右上のアカウントのアイコン → <b>API Credentials</b>。",
      "Description に <code>hatch</code> と入れ、Allowed IPs は空欄のまま <b>Create</b>。",
      "表示された <code>ptlc_</code> で始まるキーをコピーします。",
    ])}
    ${fields([
      input("PANEL_URL", "ゲームパネルの URL", { placeholder: "https://panel.example.com", hint: "ブラウザで Pterodactyl を開くときのアドレス。Tailscale 経由の URL でも構いません。" }),
      input("PANEL_APP_KEY", "Application API キー", { secret: true, placeholder: "ptla_…" }),
      input("PANEL_CLIENT_KEY", "Client API キー", { secret: true, placeholder: "ptlc_…" }),
      input("PANEL_PUBLIC_URL", "利用者に案内する URL（任意）", { placeholder: "空欄なら上と同じ", hint: "上の URL が内部用のときだけ入力します。" }),
    ])}`;
}

function pCloudflare() {
  return `<p class="lead">ゲームサーバーのアドレス（DNS レコード）を自動で作るための API トークンです。</p>
    ${guide("API トークンの作り方", [
      `${ext("https://dash.cloudflare.com/profile/api-tokens", "Cloudflare の API トークンの画面")}を開き、<b>トークンを作成する</b>。`,
      "テンプレートの <b>ゾーン DNS を編集する</b>（Edit zone DNS）の <b>テンプレートを使用</b>。",
      "権限に <b>ゾーン → ゾーン → 読み取り</b> を追加します（DNS → 編集 は最初から入っています）。",
      "ゾーンリソースで、ゲームサーバーに使うドメイン（例 <code>example.com</code>）を選びます。",
      "<b>概要に進む</b> → <b>トークンを作成</b> で表示された値をコピーします。",
    ])}
    <div class="note">ゾーン ID はここでは不要です。ドメインは初期設定の後に「管理 → ドメイン」で登録します。</div>
    ${fields([input("CF_API_TOKEN", "API トークン", { secret: true })])}`;
}

function pKuma() {
  return `<p class="lead">ゲームサーバーの監視を自動で登録するために使います。</p>
    ${guide("値の調べ方", [
      "URL：Uptime Kuma をブラウザで開くときのアドレス（ポート番号も含む）。Tailscale 経由なら <code>http://kuma:3001</code> のような形です。",
      "ユーザー名・パスワード：Uptime Kuma にログインするときのもの。",
      "API キー：Uptime Kuma の <b>設定 → API キー → API キーを追加</b>。名前は <code>hatch</code>、有効期限は「無期限」にして、表示されたキー（<code>uk</code> で始まる）をコピーします。",
    ])}
    ${fields([
      input("KUMA_URL", "Uptime Kuma の URL", { placeholder: "http://kuma:3001" }),
      input("KUMA_USERNAME", "ユーザー名"),
      input("KUMA_PASSWORD", "パスワード", { secret: true }),
      input("KUMA_METRICS_KEY", "API キー", { secret: true, placeholder: "uk1_…" }),
    ])}
    <div class="note">接続確認では URL と API キーを確かめます。パスワードは監視を登録するときに使われます。</div>`;
}

function pDiscord() {
  const d = S.discord;
  const guilds = d && d.guilds ? d.guilds : [];
  const invite = d && d.invite_url;
  return `<p class="lead">Discord でのログインと通知に使うアプリを作ります。順番どおりに進めれば、ID を手でコピーする必要はありません。</p>
    ${guide("1. アプリを作る", [
      `${ext("https://discord.com/developers/applications", "Discord Developer Portal")}を開き、右上の <b>New Application</b>。`,
      "名前（例 <code>Hatch</code>）を入れ、規約に同意して <b>Create</b>。",
    ])}
    ${guide("2. クライアントID とシークレット", [
      "左のメニューの <b>OAuth2</b> を開きます。",
      "<b>Client ID</b> の <b>Copy</b> を押して、下の「クライアントID」に貼り付けます。",
      "<b>Client Secret</b> の <b>Reset Secret</b> → 表示された値を「クライアントシークレット」に貼り付けます。",
      `同じ画面の <b>Redirects</b> → <b>Add Redirect</b> に次の URL を入れ、<b>Save Changes</b>。${copyBox(redirectUri())}`,
    ])}
    ${guide("3. Bot のトークン", [
      "左のメニューの <b>Bot</b> → <b>Reset Token</b> → 表示された値を「Bot のトークン」に貼り付けます。",
      "同じ画面の <b>Privileged Gateway Intents</b> で <b>Server Members Intent</b> をオンにし、<b>Save Changes</b>。",
    ])}
    ${fields([
      input("DISCORD_CLIENT_ID", "クライアントID", { placeholder: "123456789012345678", type: "text" }),
      input("DISCORD_CLIENT_SECRET", "クライアントシークレット", { secret: true }),
      input("DISCORD_BOT_TOKEN", "Bot のトークン", { secret: true }),
    ])}
    ${guide("4. Bot を Discord サーバーに招待", [
      "下の <b>接続確認</b> を押します。",
      "「Bot をサーバーに招待」ボタンが出たら押して、Hatch で使う Discord サーバーを選んで <b>認証</b>（サーバーの管理権限が必要です）。",
      "招待したら、もう一度 <b>接続確認</b> を押します。",
    ])}
    ${invite ? `<div class="row"><a class="btn" href="${esc(invite)}" target="_blank" rel="noopener">Bot をサーバーに招待</a></div>` : ""}
    ${
      guilds.length
        ? `<div class="group"><div class="field"><label for="f-guild">Discord サーバー</label><select id="f-guild" data-k="DISCORD_GUILD_ID">
        <option value="">選んでください</option>
        ${guilds.map((g) => `<option value="${esc(g.id)}" ${val("DISCORD_GUILD_ID") === g.id ? "selected" : ""}>${esc(g.name)}</option>`).join("")}
        </select></div></div>`
        : ""
    }`;
}

function roleOptions(selected, excludeId = "") {
  const roles = (S.discord && S.discord.roles) || [];
  return roles
    .filter((r) => r.id !== excludeId)
    .map((r) => `<option value="${esc(r.id)}" ${selected === r.id ? "selected" : ""}>${esc(r.name)}</option>`)
    .join("");
}
function channelSelect(key, label) {
  const chans = (S.discord && S.discord.channels) || [];
  return `<div class="field"><label for="f-${key}">${esc(label)}</label><select id="f-${key}" data-k="${key}">
    <option value="">使わない</option>
    ${chans.map((c) => `<option value="${esc(c.id)}" ${val(key) === c.id ? "selected" : ""}># ${esc(c.name)}</option>`).join("")}
    </select></div>`;
}

function pRoles() {
  const d = S.discord || {};
  if (!d.roles) {
    return `<div class="banner w">${ic("warn")}<div class="tx">ロールの一覧を読み込めていません。<div class="bb"><button type="button" class="btn sm" data-s="reload-roles">一覧を読み込む</button></div></div></div>`;
  }
  const userRows = d.roles
    .filter((r) => r.id !== S.adminRole)
    .map((r) => {
      const on = r.id in S.userRoles;
      return `<div class="field rolerow"><label><input type="checkbox" data-role="${esc(r.id)}" ${on ? "checked" : ""}> ${esc(r.name)}</label>
        <input type="number" min="0" max="100" data-role-max="${esc(r.id)}" value="${on ? S.userRoles[r.id] : 1}" ${on ? "" : "disabled"} aria-label="${esc(r.name)} の作成できる台数"><span class="unit">台</span></div>`;
    });
  return `<p class="lead">Discord のロールで、誰が Hatch を使えるかを決めます。ロールを持っていない人はログインできません。</p>
    ${guide("ロールがまだ無い場合", [
      "Discord のサーバー名 → <b>サーバー設定 → ロール → ロールを作成</b> で、管理者用（例 <code>運営</code>）と利用者用（例 <code>メンバー</code>）のロールを作ります。",
      "<b>自分に管理者用のロールを付けます</b>（メンバー一覧で自分を右クリック → ロール）。",
      "作ったら下の <b>一覧を更新</b> を押します。",
    ])}
    <div class="row"><button type="button" class="btn sm" data-s="reload-roles">一覧を更新</button></div>
    <div class="gh">管理者</div>
    <div class="group">
      <div class="field"><label for="f-admin">管理者のロール</label><select id="f-admin" data-s-admin>
        <option value="">選んでください</option>${roleOptions(S.adminRole)}</select></div>
      <div class="field"><label for="f-admin-max">作成できる台数</label><input id="f-admin-max" type="number" min="0" max="100" value="${S.adminMax}"><span class="unit">台</span></div>
    </div>
    <div class="gf">このロールを持つ人が管理者になります。<b>自分が持っているロール</b>を選んでください。</div>
    <div class="gh">利用者（任意・複数可）</div>
    <div class="group">${userRows.join("") || `<div class="field">選べるロールがありません</div>`}</div>
    <div class="gf">チェックしたロールを持つ人は、サーバーを作れる利用者になります。台数は1人あたりの上限です。後から「管理 → ロール連携」で変えられます。</div>
    <div class="gh">通知するチャンネル（任意）</div>
    <div class="group">${channelSelect("DISCORD_CHANNEL_ANNOUNCE", "お知らせ")}${channelSelect("DISCORD_CHANNEL_OPS", "管理者向け")}</div>
    <div class="gf">お知らせは利用者全員に、管理者向けはエラーなどの通知に使います。Bot がそのチャンネルでメッセージを送れるようにしてください。</div>`;
}

function pSave() {
  const roles = (S.discord && S.discord.roles) || [];
  const roleName = (id) => (roles.find((r) => r.id === id) || {}).name || id;
  const line = (label, ok, text) =>
    `<div class="cell"><div class="tx"><div class="t">${esc(label)}</div><div class="s wrap">${esc(text)}</div></div><span class="val ${ok ? "good" : "bad"}">${ok ? ic("ok") : ic("warn")}</span></div>`;
  const chk = (k) => !!(S.checks[k] && S.checks[k].ok);
  return `<p class="lead">内容を確認して保存してください。保存すると Hatch が自動で再起動します（10秒ほどかかります）。</p>
    <div class="group">
      ${line("公開 URL", true, publicUrl())}
      ${line("ゲームパネル", chk("panel"), val("PANEL_URL") || "設定済み")}
      ${line("Cloudflare", chk("cloudflare"), chk("cloudflare") ? S.checks.cloudflare.message : "接続確認をしていません")}
      ${line("Uptime Kuma", chk("kuma"), val("KUMA_URL") || "設定済み")}
      ${line("Discord", chk("discord"), (S.discord && (S.discord.guilds || []).find((g) => g.id === val("DISCORD_GUILD_ID")) || {}).name || "")}
      ${line("管理者のロール", !!S.adminRole, S.adminRole ? roleName(S.adminRole) : "未選択")}
      ${line("利用者のロール", true, Object.keys(S.userRoles).map(roleName).join("、") || "なし（管理者だけが使えます）")}
    </div>
    <div class="note">最初のログインのあと、管理者には二段階認証の登録を求められます。Google Authenticator などの認証アプリを用意してください。</div>`;
}

function pDone(message) {
  const url = publicUrl();
  const same = url === location.origin;
  return `<div class="authcard glass setup"><h1>初期設定が完了しました</h1>
    ${message ? `<div class="banner w">${ic("warn")}<div class="tx">${esc(message)}</div></div>` : ""}
    <p class="lead">Discord でログインしてください。管理者のロールを持っていれば、管理者としてログインできます。</p>
    ${same ? `<button type="button" class="btn fill block" data-s="login">Discord でログイン</button>` : `<a class="btn fill block" href="${esc(url)}/">公開 URL（${esc(url)}）を開いてログイン</a>`}
    ${guide("ログインした後にやること", [
      "<b>管理 → ドメイン</b>：ゲームサーバーに使うドメインを登録します（Cloudflare のゾーン ID はドメインの概要ページの右下にあります）。",
      "<b>管理 → IP と紐付け</b>：edge サーバーの IP を確認します。",
      "<b>管理 → アドレス枠</b>：ポートの範囲とホスト名の形を作り、「DNS を作成」を押します。",
    ])}</div>`;
}

const PAGES = { code: pCode, url: pUrl, panel: pPanel, cloudflare: pCloudflare, kuma: pKuma, discord: pDiscord, roles: pRoles, save: pSave };

/* ---------------- 描画 ---------------- */
function render() {
  const st = STEPS[S.step];
  const last = S.step === STEPS.length - 1;
  const checkBtn = st.check
    ? `<button type="button" class="btn" data-s="check" ${S.busy ? "disabled" : ""}>${S.busy ? "確認しています…" : "接続確認"}</button>`
    : "";
  root().innerHTML = `<div class="authscreen"><div class="authcard glass setup">
    <div class="stepno">初期設定　${S.step + 1} / ${STEPS.length}</div>
    <h1>${esc(st.title)}</h1>
    <div class="bar"><span style="width:${((S.step + 1) / STEPS.length) * 100}%"></span></div>
    ${PAGES[st.id]()}
    ${st.check ? result(S.checks[st.check]) : ""}
    ${S.error ? `<div class="banner e" role="alert">${ic("xc")}<div class="tx" style="white-space:pre-line">${esc(S.error)}</div></div>` : ""}
    <div class="actions">
      ${S.step > 0 ? `<button type="button" class="btn" data-s="back">戻る</button>` : "<span></span>"}
      <div class="r">${checkBtn}<button type="button" class="btn fill" data-s="next" ${S.busy ? "disabled" : ""}>${last ? "保存して再起動" : "次へ"}</button></div>
    </div>
  </div></div>`;
}

/* ---------------- 操作 ---------------- */
function stepValues() {
  const out = {};
  for (const [k, v] of Object.entries(S.values)) if (v !== "" && v !== undefined) out[k] = v;
  return out;
}

async function runCheck(service) {
  S.busy = true;
  S.error = "";
  render();
  try {
    const r = await call("POST", `/check/${service}`, { values: stepValues() });
    S.checks[service] = r;
    if (service === "discord") {
      S.discord = r;
      if (r.guilds && r.guilds.length === 1 && !val("DISCORD_GUILD_ID")) {
        S.values.DISCORD_GUILD_ID = r.guilds[0].id;
        S.busy = false;
        return runCheck("discord");
      }
    }
  } catch (e) {
    S.error = e.message;
  }
  S.busy = false;
  render();
}

async function unlock() {
  S.code = (document.getElementById("f-code").value || "").trim();
  S.busy = true;
  S.error = "";
  render();
  try {
    const st = await call("POST", "/unlock", { code: S.code });
    S.values = { ...st.values };
    S.secretsSet = st.secrets_set;
    S.step = 1;
  } catch (e) {
    S.error = e.message;
  }
  S.busy = false;
  render();
}

function validateStep(id) {
  if (id === "url") {
    for (const k of ["PD_PUBLIC_URL", "PD_INTERNAL_URL"]) {
      if (!/^https?:\/\/[^\s/]+/.test(val(k))) return "URL は http:// か https:// で始めてください。";
    }
  }
  if (id === "discord") {
    if (!S.discord || !S.discord.roles) return "接続確認をして、Discord サーバーを選んでください。";
  }
  if (id === "roles" && !S.adminRole) return "管理者のロールを選んでください。";
  return "";
}

async function complete() {
  S.busy = true;
  S.error = "";
  render();
  const roles = (S.discord && S.discord.roles) || [];
  const name = (id) => (roles.find((r) => r.id === id) || {}).name || id;
  try {
    await call("POST", "/complete", {
      values: stepValues(),
      admin_role: { id: S.adminRole, name: name(S.adminRole), max_servers: Number(S.adminMax) || 0 },
      user_roles: Object.entries(S.userRoles).map(([id, max]) => ({ id, name: name(id), max_servers: Number(max) || 0 })),
    });
  } catch (e) {
    S.error = e.message;
    S.busy = false;
    return render();
  }
  root().innerHTML = `<div class="authscreen"><div class="authcard glass setup"><h1>再起動しています</h1><p class="lead">設定を読み込み直しています。このままお待ちください。</p></div></div>`;
  waitForRestart();
}

async function waitForRestart() {
  const started = Date.now();
  await new Promise((r) => setTimeout(r, 3000));
  while (Date.now() - started < 90000) {
    const st = await setupStatus();
    if (st && !st.needed && !st.restarting) {
      root().innerHTML = `<div class="authscreen">${pDone("")}</div>`;
      return;
    }
    await new Promise((r) => setTimeout(r, 2000));
  }
  root().innerHTML = `<div class="authscreen">${pDone("自動で再起動しなかったようです。コンテナで systemctl restart hatch.target を実行してから、ログインしてください。")}</div>`;
}

async function onClick(e) {
  const el = e.target.closest("[data-s]");
  if (!el || el.disabled) return;
  const act = el.dataset.s;
  const st = STEPS[S.step];
  if (act === "copy") {
    try {
      await navigator.clipboard.writeText(el.dataset.v);
      el.textContent = "コピーしました";
    } catch {
      el.textContent = "コピーできません";
    }
    return;
  }
  if (act === "login") {
    location.href = "/api/auth/login";
    return;
  }
  if (act === "back") {
    S.step -= 1;
    S.error = "";
    return render();
  }
  if (act === "check") return runCheck(st.check);
  if (act === "reload-roles") return runCheck("discord");
  if (act === "next") {
    if (st.id === "code") return unlock();
    if (st.id === "save") return complete();
    const problem = validateStep(st.id);
    if (problem) {
      S.error = problem;
      return render();
    }
    if (st.check && !(S.checks[st.check] && S.checks[st.check].ok) && st.id !== "discord") {
      if (!confirm("接続確認がまだ成功していません。このまま次へ進みますか？（後で確認に失敗した項目は動きません）")) return;
    }
    S.step += 1;
    S.error = "";
    render();
    window.scrollTo(0, 0);
  }
}

function onInput(e) {
  const t = e.target;
  // select とチェックボックスは input と change の両方が来るので、change だけを扱う
  if ((t.tagName === "SELECT" || t.type === "checkbox") && e.type !== "change") return;
  if (t.dataset.k) {
    S.values[t.dataset.k] = t.value.trim();
    if (t.dataset.k === "DISCORD_GUILD_ID" && t.value) return runCheck("discord");
    const step = STEPS[S.step];
    if (step.check && S.checks[step.check] && e.type === "change" && t.tagName !== "SELECT") {
      delete S.checks[step.check];
      render();
    }
    return;
  }
  if (t.id === "f-admin") {
    S.adminRole = t.value;
    delete S.userRoles[t.value];
    return render();
  }
  if (t.id === "f-admin-max") {
    S.adminMax = t.value;
    return;
  }
  if (t.dataset.role) {
    if (t.checked) S.userRoles[t.dataset.role] = 1;
    else delete S.userRoles[t.dataset.role];
    return render();
  }
  if (t.dataset.roleMax) S.userRoles[t.dataset.roleMax] = t.value;
}

export function renderSetup() {
  document.getElementById("app").hidden = true;
  document.getElementById("tabbar").hidden = true;
  document.getElementById("tsearch").hidden = true;
  const el = root();
  el.addEventListener("click", onClick);
  el.addEventListener("input", onInput);
  el.addEventListener("change", onInput);
  el.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.id === "f-code") unlock();
  });
  render();
}
