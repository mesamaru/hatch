// 初期設定画面（ウィザード）。docs/UI.md「初期設定」、docs/API.md「初期設定」。
// 初期設定が終わるまでは、パネルを開くとこの画面になる。操作は初期設定コードで許可される。
import { esc } from "./components/cell.js";
import { ic } from "./components/icons.js";
import { closePicker, openPicker, pickerButton } from "./components/picker.js";

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
  roleMap: {}, // Discord のロール ID → { grants: "admin" | "supporter" | "user", max: 台数 }
  busy: false,
  error: "",
};

// 権限の3段階（docs/SPEC.md「権限」）
const TIERS = [
  { value: "admin", label: "管理者", sub: "すべての操作と設定ができます" },
  { value: "supporter", label: "サポーター", sub: "管理者が割り当てたサーバーを閲覧・操作できます" },
  { value: "user", label: "利用者", sub: "自分のサーバーを作って使えます" },
];
const TIER_OPTIONS = [{ value: "", label: "使わない", sub: "このロールでは Hatch を使えません" }, ...TIERS];
const TIER_LABEL = Object.fromEntries(TIERS.map((t) => [t.value, t.label]));
const DEFAULT_MAX = { admin: 10, supporter: 3, user: 1 };

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
        ? `<div class="group"><div class="field"><label>Discord サーバー</label>${pickerButton({
            options: guildOptions(),
            value: val("DISCORD_GUILD_ID"),
            attrs: 'data-pick="guild" aria-label="Discord サーバー"',
          })}</div></div>`
        : ""
    }`;
}

function guildOptions() {
  return ((S.discord && S.discord.guilds) || []).map((g) => ({ value: g.id, label: g.name }));
}
function channelOptions() {
  const chans = (S.discord && S.discord.channels) || [];
  return [{ value: "", label: "使わない" }, ...chans.map((c) => ({ value: c.id, label: `# ${c.name}` }))];
}
function channelPicker(key, label) {
  return `<div class="field"><label>${esc(label)}</label>${pickerButton({
    options: channelOptions(),
    value: val(key),
    attrs: `data-pick="chan:${key}" aria-label="${esc(label)}のチャンネル"`,
  })}</div>`;
}

/** 画面に出ている Discord のロールのうち、Hatch で使うもの。 */
function assignedRoles() {
  const roles = (S.discord && S.discord.roles) || [];
  return roles.filter((r) => S.roleMap[r.id]).map((r) => ({ ...r, ...S.roleMap[r.id] }));
}
function membersText(r) {
  if (r.members === null || r.members === undefined) return "";
  return r.members ? `${r.members}人が持っています` : "まだ誰も持っていません";
}

function pRoles() {
  const d = S.discord || {};
  if (!d.roles) {
    return `<div class="banner w">${ic("warn")}<div class="tx">ロールの一覧を読み込めていません。<div class="bb"><button type="button" class="btn sm" data-s="reload-roles">一覧を読み込む</button></div></div></div>`;
  }
  const rows = d.roles.map((r) => {
    const m = S.roleMap[r.id];
    const warn = m && m.grants === "admin" && r.members === 0;
    const sub = membersText(r);
    return `<div class="field rolerow"><div class="rn"><b>${esc(r.name)}</b>${sub ? `<small class="${warn ? "warn" : ""}">${esc(sub)}</small>` : ""}</div>
      ${pickerButton({ options: TIER_OPTIONS, value: m ? m.grants : "", placeholder: "使わない", attrs: `data-pick="role:${esc(r.id)}" aria-label="${esc(r.name)} の権限"` })}</div>`;
  });
  const used = assignedRoles();
  const maxRows = used.map(
    (r) => `<div class="field maxrow"><label for="f-max-${esc(r.id)}">${esc(r.name)}${r.name === TIER_LABEL[r.grants] ? "" : `<small class="unitlabel">（${esc(TIER_LABEL[r.grants])}）</small>`}</label>
      <input id="f-max-${esc(r.id)}" type="number" min="0" max="100" data-role-max="${esc(r.id)}" value="${esc(r.max)}"><span class="unit">台</span></div>`,
  );
  const admins = used.filter((r) => r.grants === "admin");
  const nobodyAdmin = admins.length && admins.every((r) => r.members === 0);
  const owner = d.owner;
  const ownerIsAdmin = owner && admins.some((r) => owner.role_ids.includes(r.id));
  let notice = "";
  if (nobodyAdmin) {
    notice = `<div class="banner w">${ic("warn")}<div class="tx">管理者にしたロールを、まだ誰も持っていません。Discord で自分にロールを付けてから「一覧を更新」を押してください。${owner ? `（Discord サーバーのオーナーの ${esc(owner.name)} さんは、ロールが無くても管理者としてログインできます）` : ""}</div></div>`;
  } else if (owner && admins.length && !ownerIsAdmin) {
    notice = `<div class="banner">${ic("info")}<div class="tx">Discord サーバーのオーナー（${esc(owner.name)} さん）は、ロールが無くても管理者としてログインできます。</div></div>`;
  }
  const intent = d.members_intent === false
    ? `<div class="note">ロールごとの人数を表示するには、Developer Portal の <b>Bot → Server Members Intent</b> をオンにしてから <b>一覧を更新</b> を押してください。</div>`
    : "";
  return `<p class="lead">Discord のロールごとに、Hatch での権限を選びます。どのロールも持っていない人はログインできません（Discord サーバーのオーナーは例外で、常に管理者になります）。</p>
    <div class="tiers">${TIERS.map((t) => `<div class="tier"><span class="tier-n ${t.value}">${t.label}</span><span class="tier-d">${esc(t.sub)}${t.value === "admin" ? "。最初のログインで二段階認証を登録します" : t.value === "supporter" ? "（起動・停止・再起動）。自分のサーバーも作れます" : ""}</span></div>`).join("")}</div>
    ${guide("ロールがまだ無い場合", [
      "Discord のサーバー名 → <b>サーバー設定 → ロール → ロールを作成</b> で、管理者用（例 <code>運営</code>）・サポーター用（例 <code>サポーター</code>）・利用者用（例 <code>メンバー</code>）のロールを作ります。",
      "<b>自分に管理者用のロールを付けます</b>（メンバー一覧で自分を右クリック → ロール）。",
      "作ったら下の <b>一覧を更新</b> を押します。",
    ])}
    <div class="row"><button type="button" class="btn sm" data-s="reload-roles">一覧を更新</button></div>
    ${notice}
    <div class="gh">Discord のロールと権限</div>
    <div class="group">${rows.join("") || `<div class="field">選べるロールがありません</div>`}</div>
    <div class="gf">複数のロールを持つ人は、いちばん強い権限になります。後から「管理 → ロール連携」で変えられます。</div>
    ${intent}
    ${
      maxRows.length
        ? `<div class="gh">作成できるサーバーの台数（1人あたり）</div><div class="group">${maxRows.join("")}</div>`
        : ""
    }
    <div class="gh">通知するチャンネル（任意）</div>
    <div class="group">${channelPicker("DISCORD_CHANNEL_ANNOUNCE", "お知らせ")}${channelPicker("DISCORD_CHANNEL_OPS", "管理者向け")}</div>
    <div class="gf">お知らせは利用者全員に、管理者向けはエラーなどの通知に使います。Bot がそのチャンネルでメッセージを送れるようにしてください。</div>`;
}

function pSave() {
  const used = assignedRoles();
  const names = (g) => used.filter((r) => r.grants === g).map((r) => r.name).join("、");
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
      ${line("管理者", !!names("admin"), names("admin") || "未選択")}
      ${line("サポーター", true, names("supporter") || "なし")}
      ${line("利用者", true, names("user") || "なし（管理者とサポーターだけが使えます）")}
    </div>
    <div class="note">最初のログインのあと、管理者には二段階認証の登録を求められます。Google Authenticator などの認証アプリを用意してください。</div>`;
}

function pDone(message) {
  const url = publicUrl();
  const same = url === location.origin;
  return `<div class="authcard glass setup"><h1>初期設定が完了しました</h1>
    ${message ? `<div class="banner w">${ic("warn")}<div class="tx">${esc(message)}</div></div>` : ""}
    <p class="lead">Discord でログインしてください。管理者にしたロールを持っている人と、Discord サーバーのオーナーは、管理者としてログインできます。</p>
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
  closePicker();
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
    // やり直しのときは、今のロールの割り当てを初期値にする
    S.roleMap = Object.fromEntries((st.role_rules || []).map((r) => [r.id, { grants: r.grants, max: r.max_servers }]));
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
  if (id === "roles" && !assignedRoles().some((r) => r.grants === "admin")) {
    return "管理者にするロールを1つ以上選んでください。";
  }
  return "";
}

async function complete() {
  S.busy = true;
  S.error = "";
  render();
  try {
    await call("POST", "/complete", {
      values: stepValues(),
      roles: assignedRoles().map((r) => ({
        id: r.id,
        name: r.name,
        grants: r.grants,
        max_servers: Math.min(100, Math.max(0, Number(r.max) || 0)),
      })),
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

function refocus(pick) {
  const b = root().querySelector(`[data-pick="${CSS.escape(pick)}"]`);
  if (b) b.focus();
}

function onPick(el) {
  const kind = el.dataset.pick;
  if (kind === "guild") {
    return openPicker(el, guildOptions(), val("DISCORD_GUILD_ID"), (v) => {
      if (v === val("DISCORD_GUILD_ID")) return;
      S.values.DISCORD_GUILD_ID = v;
      runCheck("discord");
    });
  }
  if (kind.startsWith("chan:")) {
    const key = kind.slice(5);
    return openPicker(el, channelOptions(), val(key), (v) => {
      S.values[key] = v;
      render();
      refocus(kind);
    });
  }
  if (kind.startsWith("role:")) {
    const id = kind.slice(5);
    const now = S.roleMap[id];
    return openPicker(el, TIER_OPTIONS, now ? now.grants : "", (v) => {
      if (!v) delete S.roleMap[id];
      else S.roleMap[id] = { grants: v, max: now && now.grants === v ? now.max : DEFAULT_MAX[v] };
      S.error = "";
      render();
      refocus(kind);
    });
  }
}

async function onClick(e) {
  const picker = e.target.closest("[data-pick]");
  if (picker && !picker.disabled) return onPick(picker);
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
  if (t.dataset.k) {
    S.values[t.dataset.k] = t.value.trim();
    const step = STEPS[S.step];
    if (step.check && S.checks[step.check] && e.type === "change") {
      delete S.checks[step.check];
      render();
    }
    return;
  }
  if (t.dataset.roleMax && S.roleMap[t.dataset.roleMax]) S.roleMap[t.dataset.roleMax].max = t.value;
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
