// URL と画面の対応。docs/UI.md 3.2 の表。再読み込みしても同じ画面に戻ること。
// 状態は {tab, stack} で表す。stack は [{page, arg}] の積み重ね（スタック）。

const SERVER_SUBPAGES = ["plugins", "network", "backups", "share", "address", "behavior"];
const ADMIN_SIMPLE = ["start", "health", "nodes", "expiring", "users", "roles", "announce", "violations", "audit", "keys", "system"];

const ROUTES = [
  { re: /^\/servers\/trash\/?$/, to: () => ({ tab: "servers", stack: [{ page: "trash" }] }) },
  {
    re: new RegExp(`^/servers/([^/]+)/(${SERVER_SUBPAGES.join("|")})/?$`),
    to: (m) => ({ tab: "servers", stack: [{ page: "server", arg: decodeURIComponent(m[1]) }, { page: m[2] }] }),
  },
  {
    re: /^\/servers\/([^/]+)\/?$/,
    to: (m) => ({ tab: "servers", stack: [{ page: "server", arg: decodeURIComponent(m[1]) }] }),
  },
  { re: /^\/servers\/?$/, to: () => ({ tab: "servers", stack: [] }) },
  { re: /^\/monitor\/?$/, to: () => ({ tab: "monitor", stack: [] }) },
  {
    re: /^\/admin\/domains\/([^/]+)\/?$/,
    to: (m) => ({ tab: "admin", stack: [{ page: "domains" }, { page: "domain", arg: decodeURIComponent(m[1]) }] }),
  },
  {
    re: /^\/admin\/start\/([a-z]+)\/?$/,
    to: (m) => ({ tab: "admin", stack: [{ page: "start" }, { page: "start-step", arg: m[1] }] }),
  },
  { re: /^\/admin\/domains\/?$/, to: () => ({ tab: "admin", stack: [{ page: "domains" }] }) },
  { re: /^\/admin\/ips\/?$/, to: () => ({ tab: "admin", stack: [{ page: "ips" }] }) },
  {
    re: /^\/admin\/slots\/([^/]+)\/?$/,
    to: (m) => ({ tab: "admin", stack: [{ page: "rules" }, { page: "rule", arg: decodeURIComponent(m[1]) }] }),
  },
  { re: /^\/admin\/slots\/?$/, to: () => ({ tab: "admin", stack: [{ page: "rules" }] }) },
  {
    re: new RegExp(`^/admin/(${ADMIN_SIMPLE.join("|")})/?$`),
    to: (m) => ({ tab: "admin", stack: [{ page: m[1] }] }),
  },
  { re: /^\/admin\/?$/, to: () => ({ tab: "admin", stack: [] }) },
  { re: /^\/settings\/history\/?$/, to: () => ({ tab: "settings", stack: [{ page: "history" }] }) },
  { re: /^\/settings\/?$/, to: () => ({ tab: "settings", stack: [] }) },
];

export function parseLocation() {
  const path = location.pathname;
  for (const r of ROUTES) {
    const m = path.match(r.re);
    if (m) return r.to(m);
  }
  return { tab: "servers", stack: [] };
}

export function pathFor(tab, stack) {
  if (tab === "servers") {
    if (!stack.length) return "/servers";
    if (stack[0].page === "trash") return "/servers/trash";
    if (stack[0].page === "server") {
      const base = `/servers/${encodeURIComponent(stack[0].arg)}`;
      return stack[1] ? `${base}/${stack[1].page}` : base;
    }
  }
  if (tab === "monitor") return "/monitor";
  if (tab === "admin") {
    if (!stack.length) return "/admin";
    if (stack[0].page === "domains") return stack[1] ? `/admin/domains/${encodeURIComponent(stack[1].arg)}` : "/admin/domains";
    if (stack[0].page === "rules") return stack[1] ? `/admin/slots/${encodeURIComponent(stack[1].arg)}` : "/admin/slots";
    if (stack[0].page === "ips") return "/admin/ips";
    if (stack[0].page === "start" && stack[1]) return `/admin/start/${encodeURIComponent(stack[1].arg)}`;
    return `/admin/${stack[0].page}`;
  }
  if (tab === "settings") return stack.length ? `/settings/${stack[0].page}` : "/settings";
  return "/servers";
}

export function navigate(tab, stack, { replace = false } = {}) {
  const url = pathFor(tab, stack);
  if (replace) history.replaceState({ tab, stack }, "", url);
  else history.pushState({ tab, stack }, "", url);
}

export function onLocationChange(handler) {
  addEventListener("popstate", () => handler(parseLocation()));
}
