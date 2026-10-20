import { api } from "../api.js";
import { navbar } from "../app.js";
import { esc, cell, group, banner } from "../components/cell.js";
import { ic } from "../components/icons.js";
import { toast } from "../components/toast.js";

let SERVER_CACHE = {};
let JOB_CACHE = {};

async function getServer(key) {
  if (!SERVER_CACHE[key]) {
    try {
      SERVER_CACHE[key] = await api.get(`/api/servers/${key}`);
    } catch (e) {
      toast(e.message, "err");
      throw e;
    }
  }
  return SERVER_CACHE[key];
}

async function getJob(id) {
  if (!JOB_CACHE[id]) {
    try {
      JOB_CACHE[id] = await api.get(`/api/jobs/${id}`);
    } catch (e) {
      toast(e.message, "err");
      throw e;
    }
  }
  return JOB_CACHE[id];
}

export async function pServers() {
  try {
    const servers = await api.get("/api/servers");

    const items = servers.map(s =>
      cell({
        icon: "server",
        color: s.status === "running" ? "var(--green)" : "var(--gray)",
        title: esc(s.name),
        sub: esc(s.address || s.slot?.host || "アドレス未割当"),
        act: "go",
        arg: `servers/${s.name}`,
      })
    );

    return `${navbar("サーバー", {right: `<button class="btn icon" data-act="add-server">${ic("plus")}</button>`})}
      <div class="page">
        <h1 class="large">サーバー</h1>
        ${servers.length > 0 ? group(items) : '<div style="padding:16px;color:var(--label2)">サーバーがまだありません。右上の＋から作成できます。</div>'}
      </div>`;
  } catch (e) {
    return `${navbar("サーバー")}
      <div class="page">
        <h1 class="large">サーバー</h1>
        <div style="padding:16px;color:var(--red)">${esc(e.message)}</div>
      </div>`;
  }
}

export async function pServer(entry) {
  try {
    const server = await getServer(entry.arg);
    const status = server.status === "running" ? "稼働中" : server.status === "stopped" ? "停止中" : "その他";

    return `${navbar(esc(server.name), {back: "サーバー"})}
      <div class="page">
        <h1 class="large">${esc(server.name)}</h1>
        ${group([
          cell({title: "状態", val: status}),
          cell({title: "アドレス", val: esc(server.address || server.slot?.host || "-"), chev: true, act: "go", arg: `servers/${server.name}/address`}),
        ])}
        <div style="height:16px"></div>
        ${group([
          cell({title: "削除", act: "delete-server", arg: server.name, cls: "action center"}),
        ])}
      </div>`;
  } catch (e) {
    return `${navbar("詳細", {back: "サーバー"})}
      <div class="page">
        <h1 class="large">エラー</h1>
        <div style="padding:16px;color:var(--red)">${esc(e.message)}</div>
      </div>`;
  }
}

export async function pServerSub(entry) {
  try {
    const server = await getServer(entry.arg);
    const subPage = entry.page;

    if (subPage === "address") {
      return `${navbar("アドレスとドメイン", {back: esc(server.name)})}
        <div class="page">
          <h1 class="large">アドレスとドメイン</h1>
          ${group([
            cell({title: "標準のアドレス", val: esc(server.slot?.host || "-")}),
            cell({title: "ポート", val: String(server.slot?.port || "-")}),
          ])}
        </div>`;
    }

    if (subPage === "behavior") {
      return `${navbar("動作", {back: esc(server.name)})}
        <div class="page">
          <h1 class="large">動作</h1>
          ${group([
            cell({title: "自動再起動", val: server.auto_restart ? "有効" : "無効"}),
            cell({title: "メンテナンス", val: server.maintenance_until ? "中" : "解除"}),
          ])}
        </div>`;
    }

    return `${navbar("サーバー", {back: esc(server.name)})}
      <div class="page"><h1 class="large">${esc(subPage)}</h1></div>`;
  } catch (e) {
    return `${navbar("詳細", {back: "戻る"})}
      <div class="page"><h1 class="large">エラー</h1><div style="padding:16px;color:var(--red)">${esc(e.message)}</div></div>`;
  }
}

export async function pTrash() {
  try {
    const servers = await api.get("/api/servers?status=trash");

    const items = servers.map(s =>
      cell({
        icon: "trash",
        color: "var(--red)",
        title: esc(s.name),
        sub: `削除予定: ${s.expire_at ? new Date(s.expire_at).toLocaleDateString("ja-JP") : "不明"}`,
      })
    );

    return `${navbar("ゴミ箱", {back: "サーバー"})}
      <div class="page">
        <h1 class="large">ゴミ箱</h1>
        ${servers.length > 0 ? group(items) : '<div style="padding:16px;color:var(--label2)">ゴミ箱は空です。</div>'}
      </div>`;
  } catch (e) {
    return `${navbar("ゴミ箱", {back: "サーバー"})}
      <div class="page">
        <h1 class="large">ゴミ箱</h1>
        <div style="padding:16px;color:var(--red)">${esc(e.message)}</div>
      </div>`;
  }
}
