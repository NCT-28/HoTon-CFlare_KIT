"use strict";

const state = { csrf: null, tunnels: [], env: null, poll: null };
const app = document.getElementById("app");
const layer = document.getElementById("layer");
const $ = (s) => document.querySelector(s);

// ---------- helpers ----------
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf || "" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 401 && path !== "/api/login" && path !== "/api/session") { showLogin(); throw new Error("unauthorized"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    throw new Error(typeof d === "string" ? d : JSON.stringify(d) || res.statusText);
  }
  return data;
}

function statusBadge(s) {
  const cls = s === "active" ? "b-ok" : s === "failed" ? "b-bad" : "b-idle";
  return h("span", { class: `badge ${cls}` }, s);
}

function stepsList(steps) {
  return h("ul", { class: "steps" }, steps.map((s) =>
    h("li", { class: s.ok ? "ok" : "fail" }, s.name, s.detail ? ` - ${s.detail}` : "")));
}

function flash(res) {
  const old = document.getElementById("flash");
  if (old) old.remove();
  const box = h("div", { id: "flash", class: `flash ${res.ok ? "ok" : "bad"}` },
    res.ok ? "OK" : "Thất bại", stepsList(res.steps));
  const main = app.querySelector("main");
  if (main) main.prepend(box);
}

function closeLayer() { layer.replaceChildren(); }
function openModal(title, bodyNodes, buttons) {
  closeLayer();
  const overlay = h("div", { class: "overlay on", onclick: closeLayer });
  const modal = h("div", { class: "modal on" }, h("h3", {}, title), bodyNodes,
    h("div", { class: "row right mt" }, buttons));
  layer.append(overlay, modal);
  return modal;
}
const btn = (label, onclick, cls = "") => h("button", { class: cls, onclick }, label);

// ---------- login ----------
function showLogin() {
  clearInterval(state.poll);
  state.csrf = null;
  closeLayer();
  const input = h("input", { type: "password", placeholder: "Mật khẩu admin", autofocus: true });
  const err = h("div", { class: "errors" });
  const submit = async (e) => {
    e.preventDefault();
    try {
      const r = await api("POST", "/api/login", { password: input.value });
      state.csrf = r.csrf;
      boot();
    } catch (ex) { err.textContent = ex.message; }
  };
  app.replaceChildren(h("form", { class: "card login", onsubmit: submit },
    h("h1", {}, "CFKIT"), input, err, h("div", { class: "mt" }, h("button", { class: "pri", type: "submit" }, "Đăng nhập"))));
}

// ---------- main view ----------
async function boot() {
  app.replaceChildren(
    h("header", {}, h("h1", {}, "CFKIT · Tunnel Manager"), btn("Đăng xuất", logout)),
    h("main", {}, h("div", { id: "env" }), h("div", { class: "tools", id: "tools" }), h("div", { id: "list" })));
  renderTools();
  await Promise.all([loadEnv(), loadTunnels()]);
  clearInterval(state.poll);
  state.poll = setInterval(() => { if (!document.hidden && !layer.firstChild) loadTunnels(); }, 5000);
}

async function logout() {
  try { await api("POST", "/api/logout"); } catch (_) { /* already logged out */ }
  showLogin();
}

function renderTools() {
  $("#tools").replaceChildren(
    btn("+ New tunnel", () => openEditor(null), "pri"),
    btn("Import existing", openImport),
    btn("Refresh", () => { loadEnv(true); loadTunnels(); }));
}

async function loadEnv(force = false) {
  try {
    state.env = force ? await api("POST", "/api/env/refresh") : await api("GET", "/api/env");
  } catch (ex) { if (ex.message !== "unauthorized") state.env = null; }
  renderEnv();
}

function renderEnv() {
  const e = state.env;
  const box = $("#env");
  if (!box) return;
  if (!e) { box.replaceChildren(); return; }
  const certs = Object.entries(e.certs);
  box.replaceChildren(h("div", { class: "card env" },
    h("div", {}, h("label", {}, "cloudflared"),
      h("b", {}, e.installed ? "✓ đã cài" : "✗ chưa cài"), h("div", { class: "mono" }, e.path || "")),
    h("div", {}, h("label", {}, "Version"), h("b", {}, e.version || "?"),
      e.update_available ? h("div", {}, h("span", { class: "badge b-warn" }, `có bản mới ${e.latest}`)) : null,
      e.latest_error ? h("div", { class: "env-note" }, `không kiểm tra được bản mới: ${e.latest_error}`) : null),
    h("div", {}, h("label", {}, "cert.pem"),
      certs.length ? certs.map(([u, ok]) => h("div", {}, h("span", { class: `badge ${ok ? "b-ok" : "b-bad"}` }, ok ? u : `${u} · thiếu`)))
        : h("div", { class: "env-note" }, "(chưa có tunnel)")),
    h("div", { class: "envact" },
      btn("Update…", openUpdate), btn("Fix cert.pem…", openCert), btn("Check lại", () => loadEnv(true)))));
}

async function loadTunnels() {
  try { state.tunnels = await api("GET", "/api/tunnels"); } catch (_) { return; }
  const list = $("#list");
  if (!list) return;
  const groups = new Map();
  for (const t of state.tunnels) {
    const key = t.project || "(không có project)";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(t);
  }
  if (!state.tunnels.length) {
    list.replaceChildren(h("div", { class: "card login" }, "Chưa có tunnel. Dùng “Import existing” hoặc “+ New tunnel”."));
    return;
  }
  list.replaceChildren(...[...groups].map(([project, items]) =>
    h("div", { class: "group" },
      h("h2", {}, `${project} · ${items.length} tunnel`),
      h("div", { class: "card" }, h("table", {},
        h("tr", {}, ["Tunnel", "Hostnames", "Status", "UUID", "User", "Actions"].map((c) => h("th", {}, c))),
        items.map(row))))));
}

function row(t) {
  const act = (label, a, disabled) => h("button", { class: "sm", disabled, onclick: () => control(t.name, a) }, label);
  const on = t.status === "active";
  return h("tr", {},
    h("td", {}, h("b", {}, t.name), t.note ? h("div", { class: "note" }, t.note) : null),
    h("td", {}, t.hostnames.map((x) => h("span", { class: "chip" }, x))),
    h("td", {}, statusBadge(t.status)),
    h("td", { class: "mono" }, t.uuid ? `${t.uuid.slice(0, 8)}…` : "-"),
    h("td", {}, t.user_name),
    h("td", {}, h("div", { class: "acts" },
      act("Run", "start", on), act("Stop", "stop", !on), act("Restart", "restart", !on),
      h("button", { class: "sm", onclick: () => openEditor(t.name) }, "Edit"),
      h("button", { class: "sm", onclick: () => openLogs(t.name) }, "Logs"),
      h("button", { class: "sm danger", onclick: () => openDelete(t) }, "Delete"))));
}

async function control(name, action) {
  try { flash(await api("POST", `/api/tunnels/${encodeURIComponent(name)}/${action}`)); }
  catch (ex) { flash({ ok: false, steps: [{ name: action, ok: false, detail: ex.message }] }); }
  loadTunnels();
}

// ---------- editor (create / edit) ----------
const emptyRule = () => ({ hostname: "", path: "", service: "http://127.0.0.1:", http_host_header: "", no_tls_verify: false });

async function openEditor(name) {
  let draft;
  if (name) {
    const t = await api("GET", `/api/tunnels/${encodeURIComponent(name)}`);
    draft = {
      name: t.name, user_name: t.user_name, project: t.project, note: t.note,
      rules: t.rules.map((r) => ({ ...r, path: r.path || "", http_host_header: r.http_host_header || "" })),
    };
  } else {
    const users = state.env ? Object.keys(state.env.certs) : [];
    draft = { name: "", user_name: users[0] || "root", project: "", note: "", rules: [emptyRule()] };
  }
  const payload = () => ({
    name: draft.name, user_name: draft.user_name, project: draft.project, note: draft.note,
    rules: draft.rules.map((r) => ({
      hostname: r.hostname, service: r.service, path: r.path || null,
      http_host_header: r.http_host_header || null, no_tls_verify: r.no_tls_verify,
    })),
  });

  const field = (label, key, locked) => h("div", {}, h("label", {}, label),
    h("input", { value: draft[key], disabled: locked, oninput: (e) => { draft[key] = e.target.value; } }));

  const rulesBox = h("div", { class: "tabc rules card" });
  const yamlPre = h("pre", {});
  const diffPre = h("pre", {});
  const yamlTab = h("div", { class: "tabc hidden" }, yamlPre);
  const diffTab = h("div", { class: "tabc hidden" }, diffPre);
  const errBox = h("ul", { class: "errors" });
  const stepsBox = h("div", {});
  const applyBtn = h("button", { class: "pri", onclick: apply }, "Apply");
  const msg = h("span", { class: "msg" });

  function renderRules() {
    const input = (r, key, ph) => h("td", {}, h("input", { value: r[key], placeholder: ph || "", oninput: (e) => { r[key] = e.target.value; } }));
    const move = (i, d) => { const j = i + d; if (j < 0 || j >= draft.rules.length) return; [draft.rules[i], draft.rules[j]] = [draft.rules[j], draft.rules[i]]; renderRules(); };
    rulesBox.replaceChildren(
      h("table", {},
        h("tr", {}, ["", "Hostname", "Path", "Service", "Host header", "noTLS", ""].map((c) => h("th", {}, c))),
        draft.rules.map((r, i) => h("tr", {},
          h("td", {}, h("button", { class: "sm", onclick: () => move(i, -1) }, "↑"), h("button", { class: "sm", onclick: () => move(i, 1) }, "↓")),
          input(r, "hostname"), input(r, "path", "(mọi path)"), input(r, "service"), input(r, "http_host_header"),
          h("td", {}, h("input", { type: "checkbox", checked: r.no_tls_verify, onchange: (e) => { r.no_tls_verify = e.target.checked; } })),
          h("td", {}, h("button", { class: "sm danger", onclick: () => { draft.rules.splice(i, 1); renderRules(); } }, "✕")))),
        h("tr", { class: "fixed" }, h("td", {}, "🔒"), h("td", { colspan: 2 }, "(catch-all, tự động)"), h("td", { colspan: 4 }, "http_status:404"))),
      h("div", { class: "row mt" }, btn("+ Add rule", () => { draft.rules.push(emptyRule()); renderRules(); }, "sm"),
        h("span", { class: "note" }, "thứ tự quan trọng: cloudflared khớp từ trên xuống")));
  }

  async function preview() {
    const p = await api("POST", "/api/tunnels/preview", { name: name || null, tunnel: payload() });
    yamlPre.textContent = p.yaml;
    diffPre.replaceChildren(...(p.diff ? p.diff.split("\n").map((l) =>
      h("span", { class: l.startsWith("+") && !l.startsWith("+++") ? "add" : l.startsWith("-") && !l.startsWith("---") ? "del" : "" }, l + "\n"))
      : [document.createTextNode(name ? "(không có thay đổi)" : "(tunnel mới)")]));
    errBox.replaceChildren(...p.errors.map((e) => h("li", {}, e)));
    applyBtn.disabled = p.errors.length > 0;
    return p;
  }

  async function apply() {
    msg.textContent = "";
    stepsBox.replaceChildren();
    const p = await preview();
    if (p.errors.length) return;
    msg.textContent = "Đang apply…";
    applyBtn.disabled = true;
    try {
      const res = name ? await api("PUT", `/api/tunnels/${encodeURIComponent(name)}`, payload())
                       : await api("POST", "/api/tunnels", payload());
      stepsBox.replaceChildren(stepsList(res.steps));
      if (res.data && res.data.removed_hostnames && res.data.removed_hostnames.length) {
        stepsBox.append(h("div", { class: "warn-line" },
          `DNS còn lại trong Cloudflare (xóa tay nếu không dùng): ${res.data.removed_hostnames.join(", ")}`));
      }
      msg.textContent = res.ok ? "✓ Applied" : "✗ Thất bại - xem các bước";
      if (res.ok && !name) { name = draft.name; }
    } catch (ex) { msg.textContent = ex.message; }
    applyBtn.disabled = false;
    loadTunnels();
  }

  const tabs = [["Rules", rulesBox], ["Preview YAML", yamlTab], ["Diff vs hiện tại", diffTab]];
  const tabBar = h("div", { class: "tabs" }, tabs.map(([label, pane], i) =>
    h("div", { class: `tab${i === 0 ? " on" : ""}`, onclick: async (e) => {
      for (const t of tabBar.children) t.classList.remove("on");
      e.currentTarget.classList.add("on");
      for (const [, p] of tabs) p.classList.add("hidden");
      pane.classList.remove("hidden");
      if (pane !== rulesBox) await preview();
    } }, label)));

  renderRules();
  closeLayer();
  layer.append(
    h("div", { class: "overlay on", onclick: closeLayer }),
    h("aside", { class: "panel on" },
      h("header", {}, h("h1", {}, name ? `Edit tunnel · ${name}` : "New tunnel"), btn("✕", closeLayer)),
      h("div", { class: "pbody" },
        h("div", { class: "grid" }, field("Name" + (name ? " 🔒" : ""), "name", !!name), field("Service user" + (name ? " 🔒" : ""), "user_name", !!name),
          field("Project (tag)", "project"), field("Note", "note")),
        tabBar, rulesBox, yamlTab, diffTab, errBox, stepsBox),
      h("div", { class: "pfoot" }, msg, btn("Close", closeLayer), applyBtn)));
}

// ---------- modals ----------
async function openLogs(name) {
  const pre = h("pre", { class: "logbox" }, "…");
  let timer = null;
  const follow = h("input", { type: "checkbox", onchange: (e) => {
    clearInterval(timer);
    if (e.target.checked) timer = setInterval(load, 3000);
  } });
  async function load() {
    try { pre.textContent = (await api("GET", `/api/tunnels/${encodeURIComponent(name)}/logs?lines=200`)).logs || "(trống)"; }
    catch (ex) { pre.textContent = ex.message; }
  }
  const modal = openModal(`Logs · cloudflared-${name}`, pre,
    [h("label", { class: "row" }, follow, "Follow"), btn("Refresh", load), btn("Đóng", () => { clearInterval(timer); closeLayer(); })]);
  load();
  return modal;
}

function openDelete(t) {
  const input = h("input", { placeholder: t.name });
  const out = h("div", {});
  const del = h("button", { class: "danger", disabled: true, onclick: async () => {
    const res = await api("POST", `/api/tunnels/${encodeURIComponent(t.name)}/delete`, { confirm: input.value });
    out.replaceChildren(stepsList(res.steps));
    if (res.ok) {
      out.append(h("div", { class: "warn-line" }, "Xóa tay các DNS record trong Cloudflare dashboard:"),
        h("div", {}, res.data.leftover_dns.map((x) => h("span", { class: "chip" }, x))));
      del.disabled = true;
    }
    loadTunnels();
  } }, "Delete");
  input.addEventListener("input", () => { del.disabled = input.value !== t.name; });
  openModal(`Xóa tunnel «${t.name}»?`, [
    h("p", { class: "note" }, "Sẽ stop + disable service, xóa unit/YAML/credentials và chạy tunnel delete. DNS KHÔNG tự xóa:"),
    h("div", {}, t.hostnames.map((x) => h("span", { class: "chip" }, x))),
    h("label", { class: "mt" }, `Gõ ${t.name} để xác nhận`), input, out,
  ], [btn("Đóng", closeLayer), del]);
}

async function openImport() {
  const cands = await api("GET", "/api/import/scan");
  const picks = new Map();
  const rows = cands.map((c) => {
    const can = c.importable && !c.known;
    const cb = h("input", { type: "checkbox", disabled: !can, checked: can, onchange: (e) => picks.set(c.name, e.target.checked) });
    picks.set(c.name, can);
    return h("tr", {}, h("td", {}, cb), h("td", {}, h("b", {}, c.name)),
      h("td", { class: "note" }, `${c.rule_count} rules · user ${c.user_name}`,
        c.error ? h("div", { class: "errors" }, c.error) : null,
        c.warnings.map((w) => h("div", { class: "warn-line" }, w))),
      h("td", {}, h("span", { class: `badge ${c.known ? "b-idle" : can ? "b-ok" : "b-bad"}` }, c.known ? "đã có" : can ? "mới" : "lỗi")));
  });
  openModal("Import tunnel có sẵn", [
    h("p", { class: "note" }, "Quét ~/.cloudflared/config-*.yaml. Chỉ ghi vào DB của CFKit, không đụng file/service."),
    h("div", { class: "card" }, h("table", {}, rows.length ? rows : h("tr", {}, h("td", {}, "Không tìm thấy config nào")))),
  ], [btn("Cancel", closeLayer), btn("Import", async () => {
    const names = [...picks].filter(([, v]) => v).map(([k]) => k);
    await api("POST", "/api/import", { names });
    closeLayer();
    loadTunnels(); loadEnv();
  }, "pri")]);
}

function openUpdate() {
  const e = state.env || {};
  const out = h("div", {});
  const run = async (restart) => {
    out.replaceChildren("Đang chạy…");
    try { out.replaceChildren(stepsList((await api("POST", "/api/env/update", { restart })).steps)); }
    catch (ex) { out.textContent = ex.message; }
    loadEnv(true);
  };
  openModal("Update cloudflared", [
    h("p", { class: "note" }, `Hiện tại ${e.version || "?"} → mới nhất ${e.latest || "?"}. Sẽ chạy: curl tải .deb từ GitHub releases, rồi dpkg -i.`),
    h("p", { class: "note" }, "Restart làm mỗi tunnel mất kết nối vài giây (restart lần lượt từng cái)."), out,
  ], [btn("Đóng", closeLayer), btn("Chỉ cài", () => run(false)), btn("Cài + restart tất cả", () => run(true), "pri")]);
}

function openCert() {
  openModal("Thiếu cert.pem", [
    h("p", { class: "note" }, "Server không có browser. Chọn 1 cách:"),
    h("p", {}, "1) Trên máy có browser: cloudflared login, rồi scp ~/.cloudflared/cert.pem root@IP:<home của user>/.cloudflared/cert.pem"),
    h("p", {}, "2) Chạy cloudflared login trong terminal SSH của server và mở URL hiện ra trên browser."),
    h("p", { class: "note" }, "CFKit không tự chạy login (cần tương tác). Bấm “Check lại” sau khi xong."),
  ], [btn("Đóng", closeLayer), btn("Check lại", () => { closeLayer(); loadEnv(true); }, "pri")]);
}

// ---------- start ----------
(async function init() {
  try {
    const s = await api("GET", "/api/session");
    if (s.authenticated) { state.csrf = s.csrf; boot(); } else showLogin();
  } catch (_) { showLogin(); }
})();
