/* API Definition Studio — single-page UI (no external libraries). */
"use strict";

const TYPES = ["string", "integer", "number", "boolean", "date", "date-time", "enum"];
const OPS = [["list", "List"], ["get", "Get"], ["create", "Create"], ["update", "Replace (PUT)"], ["patch", "Update (PATCH)"], ["delete", "Delete"]];
const CARDS = [["many-to-one", "many → one"], ["one-to-many", "one → many"], ["one-to-one", "one → one"], ["many-to-many", "many → many"]];
const EXPOSE = [
  ["none", "Not exposed"],
  ["link", "Link — { id, href }"],
  ["embedded", "Embedded — full object nested"],
  ["subresource", "Sub-resource path — GET …/{id}/name"],
];
const SECURITY = [["bearer", "Bearer token (JWT)"], ["apiKey", "API key header"], ["oauth2", "OAuth2 client credentials"], ["mutualTLS", "Mutual TLS"], ["none", "None"]];

const S = {
  model: null, issues: [], route: { view: "entity", id: null },
  diag: { labels: "business", ns: "", src: "", tags: [], linked: false, cols: "all", groups: true, z: 1, px: 0, py: 0, fitted: false },
  navTag: "",
  apiv: { ns: null, fmt: "yaml", view: "" }, mapFilter: "", importPreview: null,
};

/* ---------- helpers ---------- */
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style") el.style.cssText = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "html") el.innerHTML = v;
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = !!v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.appendChild(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
function words(t) { return String(t || "").replace(/([a-z0-9])([A-Z])/g, "$1 $2").split(/[^A-Za-z0-9]+/).filter(Boolean).map((w) => w.toLowerCase()); }
function camel(t) { const w = words(t); if (!w.length) return ""; let s = w[0] + w.slice(1).map((x) => x[0].toUpperCase() + x.slice(1)).join(""); return /^\d/.test(s) ? "n" + s : s; }
function pascal(t) { const c = camel(t); return c ? c[0].toUpperCase() + c.slice(1) : ""; }
function kebab(t) { return words(t).join("-"); }
function upperSnake(t) { return words(t).map((w) => w.toUpperCase()).join("_"); }
// an enum code from a label, in the namespace's enum code style
function enumCode(t, style) {
  if (style === "kebab-case") return words(t).join("-");
  if (style === "camelCase") return camel(t);
  if (style === "lower_snake") return words(t).join("_");
  return upperSnake(t);
}
const ENUM_STYLES = [["UPPER_SNAKE", "UPPER_SNAKE (SERVICE_REQUEST)"], ["kebab-case", "kebab-case (service-request)"],
  ["camelCase", "camelCase (serviceRequest)"], ["lower_snake", "lower_snake (service_request)"]];
function plural(w) {
  if (!w) return w; const l = w.toLowerCase();
  const irr = { person: "people", child: "children", status: "statuses", address: "addresses", analysis: "analyses" };
  if (irr[l]) return irr[l];
  if (/[^aeiou]y$/.test(l)) return w.slice(0, -1) + "ies";
  if (/(s|x|z|ch|sh)$/.test(l)) return w + "es";
  return w + "s";
}

async function api(method, url, body, raw) {
  const opt = { method, headers: {} };
  if (body !== undefined) {
    if (raw) { opt.body = body; opt.headers["Content-Type"] = "application/octet-stream"; }
    else { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
  }
  const r = await fetch(url, opt);
  if (r.status === 204) return null;
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) {
    let msg = "Request failed (" + r.status + ")";
    const d = data && data.detail;
    if (typeof d === "string") msg = d;
    else if (d && d.message) msg = d.message;
    else if (Array.isArray(d) && d[0]) msg = (d[0].loc || []).slice(1).join(".") + ": " + d[0].msg;
    const err = new Error(msg); err.status = r.status; throw err;
  }
  return data;
}

function toast(msg, isErr) {
  const t = h("div", { class: "toast" + (isErr ? " err" : "") }, msg);
  document.getElementById("toasts").appendChild(t);
  setTimeout(() => t.remove(), isErr ? 6000 : 2600);
}
function download(url) { const a = h("a", { href: url, download: "" }); document.body.appendChild(a); a.click(); a.remove(); }
function saveBlob(blob, name) { const u = URL.createObjectURL(blob); const a = h("a", { href: u, download: name }); document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(u), 2000); }

const M = () => S.model;
const nsById = (id) => M().namespaces.find((n) => n.id === id);
const entById = (id) => M().entities.find((e) => e.id === id);
const ownerOfObject = (oid) => {
  for (const e of M().entities) { if (e.id === oid) return e; if (e.attributes.some((a) => a.id === oid)) return e; }
  const r = M().relationships.find((x) => x.id === oid); if (r) return entById(r.from_entity_id);
  return null;
};
const issuesOf = (oid) => S.issues.filter((i) => i.object_id === oid);
function entityIssueCount(e, level) {
  const ids = new Set([e.id, ...e.attributes.map((a) => a.id), ...M().relationships.filter((r) => r.from_entity_id === e.id).map((r) => r.id)]);
  return S.issues.filter((i) => ids.has(i.object_id) && (!level || i.level === level)).length;
}
function keyAttr(e) { return e.attributes.find((a) => a.key); }

/* source systems: each type names the (optional) source fields its own way */
const UNSOURCED = { label: "Not sourced", object: "Source object", container: "Source container", field: "Source field", location: "Source location", code_list: "Code list", link: "Source link", ph: {}, tokens: [] };
const SYS_COLOURS = ["#0e7c86", "#b54708", "#6941c6", "#175cd3", "#067647", "#b42318", "#475467"];
function srcById(id) { return id ? (M().source_systems || []).find((s) => s.id === id) || null : null; }
function srcOf(e) { return e ? srcById(e.source_system_id) : null; }
function kindOf(s) { return s ? (S.kinds[s.kind] || S.kinds.other) : UNSOURCED; }
function labelsOf(e) { return kindOf(srcOf(e)); }
function sysColour(s) { if (!s) return "#98a2b3"; const i = M().source_systems.findIndex((x) => x.id === s.id); return SYS_COLOURS[(i < 0 ? 0 : i) % SYS_COLOURS.length]; }
function sysChip(s) { return h("span", { class: "sys-chip", style: "--c:" + sysColour(s) }, s ? s.name : "Not sourced"); }
// an entity with no operations is either embedded in another, or schema only (e.g. an imported logical entity)
function isEmbedded(e) { return M().relationships.some((r) => (r.to_entity_id === e.id && r.expose === "embedded") || (r.from_entity_id === e.id && r.inverse_expose === "embedded")); }
function noPathLabel(e) { return isEmbedded(e) ? "embedded" : "schema only"; }
function sysOptions() { return [["", "Not sourced (derived or new data)"]].concat(M().source_systems.map((s) => [s.id, s.name + " — " + kindOf(s).label])); }
function resourcePath(e) { const n = nsById(e.namespace_id); return (n ? n.base_path : "") + "/" + e.plural; }

/* ---------- data load & routing ---------- */
async function reload() {
  const d = await api("GET", "api/model");
  if (!d.source_kinds || !Array.isArray(d.model.source_systems)) {
    throw new Error("the studio server is running an older version (" + (d.version || "unknown") + ") than this page. "
      + "Stop it (Ctrl+C in its terminal), start api-definition-studio again, then reload this page.");
  }
  S.model = d.model; S.issues = d.issues; S.kinds = d.source_kinds; S.dicts = d.dictionaries || [];
  document.getElementById("app-version") && (document.getElementById("app-version").textContent = d.version);
}

function parseHash() {
  const p = location.hash.replace(/^#\/?/, "").split("/");
  const view = p[0] || "";
  if (view === "entity") return { view, id: p[1] };
  if (["namespaces", "sources", "headers", "diagram", "api", "mapping", "issues", "settings"].includes(view)) return { view, id: p[1] || null };
  return { view: "entity", id: null };
}
function go(view, id) { location.hash = "#/" + view + (id ? "/" + id : ""); }
window.addEventListener("hashchange", () => { S.route = parseHash(); render(); window.scrollTo(0, 0); });

/* ---------- render shell ---------- */
function render() {
  if (!S.model) return;
  if (S.route.view === "entity" && (!S.route.id || !entById(S.route.id))) {
    if (M().entities.length) S.route.id = M().entities[0].id; else S.route = { view: "namespaces" };
  }
  renderChrome();
  const main = document.getElementById("main");
  const active = document.activeElement;
  const fkey = active && active.dataset ? active.dataset.k : null;
  let sel = null; try { sel = fkey && active.selectionStart != null ? [active.selectionStart, active.selectionEnd] : null; } catch (_) { sel = null; }
  const scrollers = [...main.querySelectorAll(".table-wrap")].map((x) => x.scrollLeft);
  main.style.minHeight = main.offsetHeight + "px";
  main.innerHTML = "";
  const v = S.route.view;
  const page = v === "namespaces" ? viewNamespaces() : v === "sources" ? viewSources() : v === "headers" ? viewHeaders() : v === "diagram" ? viewDiagram() : v === "api" ? viewApi()
    : v === "mapping" ? viewMapping() : v === "issues" ? viewIssues() : v === "settings" ? viewSettings()
    : viewEntity(entById(S.route.id));
  main.appendChild(page);
  main.style.minHeight = "";
  [...main.querySelectorAll(".table-wrap")].forEach((x, i) => { if (scrollers[i]) x.scrollLeft = scrollers[i]; });
  if (fkey) {
    const el = main.querySelector('[data-k="' + CSS.escape(fkey) + '"]');
    if (el) { el.focus(); if (sel && el.setSelectionRange) try { el.setSelectionRange(sel[0], sel[1]); } catch (_) { /* not a text input */ } }
  }
  if (v === "diagram") drawDiagram();
}

function renderChrome() {
  document.getElementById("catalogue-title").textContent = M().settings.title + (M().settings.organisation ? " · " + M().settings.organisation : "");
  const errs = S.issues.filter((i) => i.level === "error").length, warns = S.issues.length - errs;
  const b = document.getElementById("issues-badge");
  b.className = "issues-badge" + (S.issues.length ? "" : " clean");
  const parts = [];
  if (errs) parts.push('<span class="e">' + errs + " error" + (errs === 1 ? "" : "s") + "</span>");
  if (warns) parts.push('<span class="w">' + warns + " warning" + (warns === 1 ? "" : "s") + "</span>");
  b.innerHTML = parts.length ? parts.join(" · ") : "✓ No issues";
  b.onclick = () => go("issues");

  const sb = document.getElementById("sidebar"); sb.innerHTML = "";
  const v = S.route.view;
  const item = (label, view, count, cls) => h("button", { class: "nav-item" + (v === view ? " active" : ""), onclick: () => go(view) },
    h("span", null, label), count !== undefined ? h("span", { class: "count" + (cls ? " " + cls : "") }, count) : null);
  sb.appendChild(item("Namespaces", "namespaces", M().namespaces.length));
  sb.appendChild(item("Source systems", "sources", M().source_systems.length));
  sb.appendChild(item("Headers", "headers", (M().headers || []).length));
  sb.appendChild(h("div", { class: "nav-head" }, h("span", null, "Entities"), h("button", { class: "link-btn", onclick: newEntityModal }, "+ New")));
  const tagsNow = allTags();
  if (S.navTag && !tagsNow.some((t) => t.name.toLowerCase() === S.navTag.toLowerCase())) S.navTag = "";
  if (tagsNow.length) {
    const tsel = selectEl([["", "All tags"]].concat(tagsNow.map((t) => [t.name, t.name + " (" + t.count + ")"])), S.navTag, { class: "nav-tag", "data-k": "nav-tag", title: "Show only entities with this tag" });
    tsel.onchange = () => { S.navTag = tsel.value; render(); };
    sb.appendChild(tsel);
  }
  for (const n of M().namespaces) {
    const list = M().entities.filter((e) => e.namespace_id === n.id && (!S.navTag || hasTag(e, S.navTag)));
    if (S.navTag && !list.length) continue;
    sb.appendChild(h("div", { class: "nav-ns" }, n.key + "  " + n.base_path));
    if (!list.length) sb.appendChild(h("div", { class: "nav-ns", style: "font-style:italic" }, "no entities"));
    for (const e of list) {
      const ec = entityIssueCount(e, "error");
      sb.appendChild(h("button", { class: "nav-ent" + (v === "entity" && S.route.id === e.id ? " active" : ""), onclick: () => go("entity", e.id) },
        h("span", { class: "b" }, h("span", { class: "sys-dot", style: "background:" + sysColour(srcOf(e)), title: srcOf(e) ? srcOf(e).name : "Not sourced" }), e.business_name, ec ? h("span", { class: "count err", style: "margin-left:6px" }, ec) : null),
        h("span", { class: "a" }, h("span", { class: "arrow" }, "→ "), e.api_name, e.operations.length ? "" : "  (" + noPathLabel(e) + ")")));
    }
  }
  sb.appendChild(h("div", { class: "nav-head" }, "Outputs"));
  sb.appendChild(item("Data model diagram", "diagram"));
  sb.appendChild(item("API definition", "api"));
  sb.appendChild(item("Mapping sheet", "mapping"));
  sb.appendChild(item("Issues", "issues", S.issues.length, S.issues.some((i) => i.level === "error") ? "err" : ""));
  sb.appendChild(h("div", { class: "nav-head" }, "Studio"));
  sb.appendChild(item("Settings & model file", "settings"));
}

/* ---------- inline editing ---------- */
// An input that saves on change. save(value) returns a promise; on failure the old value comes back.
function editable(el, current, save) {
  el.dataset.orig = current == null ? "" : String(current);
  el.addEventListener("change", async () => {
    const val = el.type === "checkbox" ? el.checked : el.value;
    try { await save(val); }
    catch (err) {
      toast(err.message, true);
      if (el.type === "checkbox") el.checked = !el.checked; else el.value = el.dataset.orig;
      el.classList.add("invalid"); setTimeout(() => el.classList.remove("invalid"), 1800);
    }
  });
  return el;
}
async function patch(url, body, quiet) {
  await api("PATCH", url, body);
  await reload(); render();
  if (!quiet) toast("Saved");
}
/* ---------- tags ---------- */
const TAG_COLOURS = [["#E3F0FB", "#1D5C99"], ["#E5F4EA", "#1E6B3A"], ["#FCEEE1", "#94460A"], ["#F0E9FB", "#5B3A99"],
  ["#FBE6ED", "#982350"], ["#E2F3F3", "#185F5F"], ["#F5F0D9", "#675608"], ["#EAEEF3", "#36475B"]];
function tagColour(t) { let x = 0; for (const c of t.toLowerCase()) x = (x * 31 + c.charCodeAt(0)) >>> 0; return TAG_COLOURS[x % TAG_COLOURS.length]; }
function hasTag(e, t) { return (e.tags || []).some((x) => x.toLowerCase() === t.toLowerCase()); }
function allTags() {
  const m = {};
  M().entities.forEach((e) => (e.tags || []).forEach((t) => { const k = t.toLowerCase(); (m[k] = m[k] || { name: t, count: 0 }).count++; }));
  return Object.values(m).sort((a, b) => a.name.localeCompare(b.name));
}
function tagChip(t, opts) {
  opts = opts || {};
  const [bg, fg] = tagColour(t);
  return h(opts.onclick ? "button" : "span", { class: "tag" + (opts.off ? " off" : ""), style: "background:" + bg + ";color:" + fg, onclick: opts.onclick || null, title: opts.title || null },
    t, opts.count != null ? h("span", { class: "tag-n" }, opts.count) : null,
    opts.remove ? h("button", { class: "tag-x", title: "Remove " + t, onclick: opts.remove }, "×") : null);
}
function tagEditor(e) {
  const url = "api/entities/" + e.id;
  const input = h("input", { type: "text", placeholder: "Add a tag, e.g. Agreements", list: "tag-list", "data-k": "e-tags", style: "width:240px" });
  const dl = h("datalist", { id: "tag-list" }, allTags().filter((t) => !hasTag(e, t.name)).map((t) => h("option", { value: t.name })));
  const setTags = async (next) => { try { await patch(url, { tags: next }, true); } catch (err) { toast(err.message, true); } };
  const add = () => {
    const v = input.value.split(",").map((x) => x.trim()).filter(Boolean);
    if (!v.length) return;
    input.value = "";
    setTags((e.tags || []).concat(v));
  };
  input.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === ",") { ev.preventDefault(); add(); } });
  input.addEventListener("change", add);
  return h("div", { class: "field wide" }, h("label", null, "Tags"),
    h("div", { class: "tag-row" }, (e.tags || []).map((t) => tagChip(t, { remove: () => setTags(e.tags.filter((x) => x !== t)) })), input, dl),
    h("div", { class: "help" }, "Group entities by subject area, e.g. Agreements, Service type, Case. An entity can have several. Used to filter the diagram and the entity list."));
}
// pick which entities have one tag (new or existing)
function tagMembersModal(tag) {
  const isNew = !tag;
  const chosen = new Set(isNew ? [] : M().entities.filter((e) => hasTag(e, tag)).map((e) => e.id));
  const name = h("input", { type: "text", value: tag || "", placeholder: "e.g. Agreements", "data-k": "tag-name" });
  const search = h("input", { type: "search", placeholder: "Find an entity…" });
  const list = h("div", { class: "tag-pick" });
  const shown = () => { const q = search.value.trim().toLowerCase(); return M().entities.filter((e) => !q || (e.business_name + " " + e.api_name + " " + e.schema_name + " " + (e.tags || []).join(" ")).toLowerCase().includes(q)); };
  const count = h("span", { class: "muted small" });
  const draw = () => {
    list.innerHTML = "";
    for (const n of M().namespaces) {
      const ents = shown().filter((e) => e.namespace_id === n.id);
      if (!ents.length) continue;
      list.appendChild(h("div", { class: "nav-ns", style: "padding:6px 2px 2px" }, n.key + "  " + n.base_path));
      ents.forEach((e) => {
        const cb = h("input", { type: "checkbox", checked: chosen.has(e.id) });
        cb.onchange = () => { if (cb.checked) chosen.add(e.id); else chosen.delete(e.id); count.textContent = chosen.size + " ticked"; };
        list.appendChild(h("label", { class: "tag-pick-row" }, cb, h("span", null, e.business_name), h("span", { class: "mono small muted" }, e.api_name),
          h("span", { class: "tag-row" }, (e.tags || []).filter((t) => !tag || t.toLowerCase() !== tag.toLowerCase()).map((t) => tagChip(t)))));
      });
    }
    if (!list.children.length) list.appendChild(h("div", { class: "empty" }, "No entities match."));
    count.textContent = chosen.size + " ticked";
  };
  search.oninput = draw; draw();
  const onDiagram = diagEntities();
  const filtered = onDiagram.length < M().entities.length;
  const body = h("div", null,
    h("div", { class: "grid" }, h("div", { class: "field" }, h("label", null, isNew ? "Tag" : "Tag (rename here)"), name), h("div", { class: "field" }, h("label", null, "Find"), search)),
    h("div", { class: "row-actions", style: "margin:8px 0" },
      h("button", { class: "btn btn-sm", onclick: () => { shown().forEach((e) => chosen.add(e.id)); draw(); } }, "Tick all shown"),
      h("button", { class: "btn btn-sm", onclick: () => { shown().forEach((e) => chosen.delete(e.id)); draw(); } }, "Untick all shown"),
      filtered ? h("button", { class: "btn btn-sm", onclick: () => { onDiagram.forEach((e) => chosen.add(e.id)); draw(); } }, "Tick the " + onDiagram.length + " on the diagram") : null,
      count),
    list);
  openModal(isNew ? "New tag" : "Tag: " + tag, body, [
    isNew ? null : h("button", { class: "btn btn-danger", style: "margin-right:auto", onclick: async () => {
      if (!confirm("Remove the tag " + tag + " from every entity?")) return;
      try { await api("DELETE", "api/tags/" + encodeURIComponent(tag)); S.diag.tags = S.diag.tags.filter((t) => t.toLowerCase() !== tag.toLowerCase()); await reload(); closeModal(); render(); toast("Tag removed"); }
      catch (err) { toast(err.message, true); }
    } }, "Delete tag"),
    h("button", { class: "btn", onclick: closeModal }, "Cancel"),
    h("button", { class: "btn btn-primary", onclick: async () => {
      const nm = name.value.trim();
      if (!nm) { toast("Give the tag a name", true); return; }
      try {
        if (!isNew && nm !== tag) {
          if (M().entities.some((e) => hasTag(e, tag))) await api("POST", "api/tags/" + encodeURIComponent(tag) + "/rename", { name: nm });
          S.diag.tags = S.diag.tags.map((t) => (t.toLowerCase() === tag.toLowerCase() ? nm : t));
          if (S.navTag && S.navTag.toLowerCase() === tag.toLowerCase()) S.navTag = nm;
        }
        await api("PUT", "api/tags/" + encodeURIComponent(nm), { entity_ids: Array.from(chosen) });
        await reload(); closeModal(); render(); toast("Tag " + nm + " on " + chosen.size + " entit" + (chosen.size === 1 ? "y" : "ies"));
      } catch (err) { toast(err.message, true); }
    } }, "Save")].filter(Boolean));
}
function groupsModal() {
  const gs = allGroups();
  const body = h("div", null,
    h("p", { class: "muted small", style: "margin-top:0" }, "A logical group draws a frame round related entities on the diagram, e.g. Agreements round Service Agreement and Agreement Change. An entity is in one group. Tidy layout keeps each group together; drag a frame to move its entities."),
    gs.length ? h("table", null, h("thead", null, h("tr", null, ["Group", "Entities", ""].map((x) => h("th", null, x)))),
      h("tbody", null, gs.map((g) => { const [bg, fg] = groupColour(g.name); return h("tr", null,
        h("td", null, h("span", { class: "tag", style: "background:" + bg + ";color:" + fg + ";border:1px dashed " + fg }, g.name)), h("td", { class: "small" }, String(g.count)),
        h("td", { style: "text-align:right" }, h("button", { class: "btn btn-sm", onclick: () => groupMembersModal(g.name) }, "Edit"))); })))
      : h("div", { class: "empty" }, "No groups yet."));
  openModal("Logical groups", body, [h("button", { class: "btn", onclick: closeModal }, "Close"),
    h("button", { class: "btn btn-primary", "data-k": "new-group", onclick: () => groupMembersModal(null) }, "+ New group")]);
}
function groupMembersModal(group) {
  const isNew = !group;
  const chosen = new Set(isNew ? [] : M().entities.filter((e) => (e.group || "").toLowerCase() === group.toLowerCase()).map((e) => e.id));
  const name = h("input", { type: "text", value: group || "", placeholder: "e.g. Agreements", "data-k": "group-name" });
  const search = h("input", { type: "search", placeholder: "Find an entity…" });
  const list = h("div", { class: "tag-pick" });
  const count = h("span", { class: "muted small" });
  const shown = () => { const q = search.value.trim().toLowerCase(); return M().entities.filter((e) => !q || (e.business_name + " " + e.api_name + " " + (e.group || "") + " " + (e.tags || []).join(" ")).toLowerCase().includes(q)); };
  const draw = () => {
    list.innerHTML = "";
    shown().forEach((e) => {
      const cb = h("input", { type: "checkbox", checked: chosen.has(e.id), "data-k": "gm-" + e.api_name });
      cb.onchange = () => { if (cb.checked) chosen.add(e.id); else chosen.delete(e.id); count.textContent = chosen.size + " ticked"; };
      const other = e.group && (!group || e.group.toLowerCase() !== group.toLowerCase()) ? e.group : "";
      list.appendChild(h("label", { class: "tag-pick-row" }, cb, h("span", null, e.business_name), h("span", { class: "mono small muted" }, e.api_name),
        other ? h("span", { class: "muted small" }, "now in " + other + " (moves here)") : null,
        h("span", { class: "tag-row" }, (e.tags || []).map((t) => tagChip(t)))));
    });
    if (!list.children.length) list.appendChild(h("div", { class: "empty" }, "No entities match."));
    count.textContent = chosen.size + " ticked";
  };
  search.oninput = draw; draw();
  const tagPick = selectEl([["", "Tick the entities with tag…"]].concat(allTags().map((t) => [t.name, t.name + " (" + t.count + ")"])), "");
  tagPick.onchange = () => { if (tagPick.value) { M().entities.filter((e) => hasTag(e, tagPick.value)).forEach((e) => chosen.add(e.id)); tagPick.value = ""; draw(); } };
  openModal(isNew ? "New group" : "Group: " + group, h("div", null,
    h("div", { class: "grid" }, h("div", { class: "field" }, h("label", null, isNew ? "Group name" : "Group name (rename here)"), name),
      h("div", { class: "field" }, h("label", null, "Find"), search), allTags().length ? h("div", { class: "field" }, h("label", null, "From a tag"), tagPick) : null),
    h("div", { style: "margin:8px 0" }, count), list), [
    isNew ? null : h("button", { class: "btn btn-danger", style: "margin-right:auto", onclick: async () => {
      if (!confirm("Remove the group " + group + "? Its entities stay.")) return;
      try { await api("DELETE", "api/groups/" + encodeURIComponent(group)); await reload(); closeModal(); render(); toast("Group removed"); } catch (err) { toast(err.message, true); }
    } }, "Delete group"),
    h("button", { class: "btn", onclick: closeModal }, "Cancel"),
    h("button", { class: "btn btn-primary", "data-k": "group-save", onclick: async () => {
      const nm = name.value.trim();
      if (!nm) { toast("Give the group a name", true); return; }
      try {
        if (!isNew && nm !== group && M().entities.some((e) => (e.group || "").toLowerCase() === group.toLowerCase()))
          await api("POST", "api/groups/" + encodeURIComponent(group) + "/rename", { name: nm });
        await api("PUT", "api/groups/" + encodeURIComponent(nm), { entity_ids: Array.from(chosen) });
        await reload(); closeModal(); S.diag.fitted = false; render(); toast("Group " + nm + ": " + chosen.size + " entit" + (chosen.size === 1 ? "y" : "ies"));
      } catch (err) { toast(err.message, true); }
    } }, "Save")].filter(Boolean));
}

function tagsModal() {
  const tags = allTags();
  const body = h("div", null,
    h("p", { class: "muted small", style: "margin-top:0" }, "Tags group entities by subject area. Click a tag to choose its entities, rename it or delete it."),
    tags.length ? h("table", null, h("thead", null, h("tr", null, ["Tag", "Entities", ""].map((x) => h("th", null, x)))),
      h("tbody", null, tags.map((t) => h("tr", null, h("td", null, tagChip(t.name)), h("td", { class: "small" }, String(t.count)),
        h("td", { style: "text-align:right" }, h("button", { class: "btn btn-sm", onclick: () => tagMembersModal(t.name) }, "Edit"))))))
      : h("div", { class: "empty" }, "No tags yet."));
  openModal("Tags", body, [h("button", { class: "btn", onclick: closeModal }, "Close"),
    h("button", { class: "btn btn-primary", onclick: () => tagMembersModal(null) }, "+ New tag")]);
}

function textField(label, key, value, save, opts) {
  opts = opts || {};
  const inp = h(opts.area ? "textarea" : "input", { type: "text", class: opts.mono ? "mono" : null, value: value || "", "data-k": key, placeholder: opts.placeholder || null });
  if (opts.area) inp.value = value || "";
  editable(inp, value, save);
  return h("div", { class: "field" + (opts.wide ? " wide" : "") }, h("label", null, label), inp, opts.help ? h("div", { class: "help" }, opts.help) : null);
}
function selectEl(options, value, attrs) {
  const s = h("select", attrs || null, options.map(([v, l]) => h("option", { value: v }, l)));
  s.value = value; return s;
}
function selectField(label, key, options, value, save, help) {
  const s = selectEl(options, value, { "data-k": key });
  editable(s, value, save);
  return h("div", { class: "field" }, h("label", null, label), s, help ? h("div", { class: "help" }, help) : null);
}
function issueCallout(list) {
  if (!list.length) return null;
  const errs = list.filter((i) => i.level === "error");
  return h("div", { class: "callout " + (errs.length ? "err" : "warn") },
    h("ul", { class: "issue-list" }, list.map((i) => h("li", null, (i.level === "error" ? "✕ " : "⚠ ") + i.message))));
}

/* ---------- modal ---------- */
function openModal(title, body, footButtons) {
  const back = document.getElementById("modal-back"), m = document.getElementById("modal");
  m.innerHTML = "";
  m.appendChild(h("div", { class: "modal-head" }, h("h3", null, title), h("button", { class: "icon-btn", onclick: closeModal, "aria-label": "Close" }, "✕")));
  m.appendChild(h("div", { class: "modal-body" }, body));
  m.appendChild(h("div", { class: "modal-foot" }, footButtons));
  back.hidden = false;
  const first = m.querySelector("input,select,textarea"); if (first) first.focus();
}
function closeModal() { document.getElementById("modal-back").hidden = true; }
document.getElementById("modal-back").addEventListener("mousedown", (ev) => { if (ev.target.id === "modal-back") closeModal(); });
document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") closeModal(); });

/* ---------- entity page ---------- */
function viewEntity(e) {
  if (!e) return h("div", { class: "empty" }, "No entities yet. ", h("button", { class: "btn btn-primary", onclick: newEntityModal }, "+ New entity"));
  const n = nsById(e.namespace_id);
  const url = "api/entities/" + e.id;
  const root = h("div");
  const hasPath = e.operations.length > 0;
  root.appendChild(h("div", { class: "page-head" },
    h("div", null,
      h("h1", null, e.business_name),
      h("div", { class: "crumb" },
        h("span", null, "API resource ", h("b", null, e.api_name)),
        h("span", null, "Path ", h("b", null, hasPath ? resourcePath(e) : "none (" + noPathLabel(e) + ")")),
        h("span", null, "Schema ", h("b", null, e.schema_name)),
        h("span", null, "Source ", sysChip(srcOf(e)), e.source_object ? h("b", { style: "margin-left:6px" }, e.source_object) : null),
        (e.tags || []).length ? h("span", { class: "tag-row" }, e.tags.map((t) => tagChip(t, { title: "Show " + t + " on the diagram", onclick: () => { S.diag.tags = [t]; S.diag.fitted = false; go("diagram"); } }))) : null)),
    h("div", { class: "row-actions" },
      h("button", { class: "btn", onclick: () => go("diagram") }, "View on diagram"),
      h("button", { class: "btn btn-danger", onclick: async () => {
        if (!confirm("Delete " + e.business_name + ", its columns and its links?")) return;
        try { await api("DELETE", url); await reload(); S.route = { view: "entity", id: null }; go("entity"); render(); toast("Deleted " + e.business_name); }
        catch (err) { toast(err.message, true); }
      } }, "Delete entity"))));

  root.appendChild(issueCallout(issuesOf(e.id)) || h("span"));

  // entity card
  const save = (k) => (v) => patch(url, { [k]: v });
  const ops = h("div", { class: "checks" }, OPS.map(([op, label]) => {
    const cb = h("input", { type: "checkbox", checked: e.operations.includes(op), "data-k": "op-" + op });
    editable(cb, e.operations.includes(op), (on) => {
      const next = OPS.map((o) => o[0]).filter((o) => (o === op ? on : e.operations.includes(o)));
      return patch(url, { operations: next });
    });
    return h("label", null, cb, label);
  }));
  root.appendChild(h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h2", null, "Entity"), h("span", { class: "hint" }, "Business name is what people call it; API name is what the API calls it.")),
    h("div", { class: "card-body" },
      h("div", { class: "grid" },
        textField("Business name", "e-bn", e.business_name, save("business_name")),
        textField("API name (singular)", "e-api", e.api_name, save("api_name"), { mono: true, help: "e.g. Contact is exposed as customer" }),
        textField("Path segment (plural)", "e-plural", e.plural, save("plural"), { mono: true, help: hasPath ? resourcePath(e) : "Used when operations are enabled" }),
        textField("Schema name", "e-schema", e.schema_name, save("schema_name"), { mono: true }),
        selectField("Namespace", "e-ns", M().namespaces.map((x) => [x.id, x.key + " — " + x.title]), e.namespace_id, save("namespace_id")),
        selectField("Source system", "e-sys", sysOptions(), e.source_system_id || "", save("source_system_id"),
          srcOf(e) ? null : "Pick a source system to map source fields"),
        srcOf(e) ? (() => {
          const tf = textField(labelsOf(e).object, "e-src", e.source_object, save("source_object"), {
            placeholder: (e.source_variants || []).length ? "Varies: see Source variants" : (labelsOf(e).ph.object || null),
            help: (e.source_variants || []).length ? "Records come from a different " + labelsOf(e).object.toLowerCase() + " by " + (e.attributes.find((x) => x.id === e.varies_by) ? colPath(e, e.attributes.find((x) => x.id === e.varies_by)) : "a column") + ": set per value under Source variants."
              : dictOf(e) ? "Columns use it unless they name their own. Type a name or a physical table to search the dictionary." : null });
          if (dictOf(e)) { const inp = tf.querySelector("input"), at = inp.nextSibling; tf.insertBefore(suggest(inp, bcOptions(e.source_system_id)), at); }
          return tf;
        })() : null,
        srcOf(e) ? textField(labelsOf(e).container, "e-io", e.source_io, save("source_io"), { placeholder: labelsOf(e).ph.container || null, help: "Optional" }) : null,
        h("div", { class: "field" }, h("label", null, "Operations"), ops,
          h("div", { class: "help" }, "None ticked: schema only, reached through a link.")),
        e.operations.includes("get") ? selectField("Get one by", "e-getby",
          [["path", "Path: " + resourcePath(e) + "/{" + (keyAttr(e) ? keyAttr(e).api_name : "id") + "}"], ["query", "Query: " + resourcePath(e) + "?" + (keyAttr(e) ? keyAttr(e).api_name : "id") + "="]],
          e.get_by || "path", save("get_by"), e.get_by === "query" && e.operations.includes("list")
            ? "Through the list: returns a page holding that record. Untick List to return the record itself." : null) : null,
        tagEditor(e),
        (() => {
          const tf = textField("Logical group", "e-group", e.group || "", save("group"), { placeholder: "e.g. Agreements",
            help: "Drawn as a frame round its entities on the data model diagram. One group per entity; tags can overlap." });
          const inp = tf.querySelector("input"), dl = h("datalist", { id: "group-list" }, allGroups().map((g) => h("option", { value: g.name })));
          inp.setAttribute("list", "group-list"); tf.appendChild(dl);
          return tf;
        })(),
        textField("Description", "e-desc", e.description, save("description"), { wide: true, area: true })))));

  root.appendChild(columnsCard(e));
  if (srcOf(e)) root.appendChild(variantsCard(e));
  root.appendChild(linksCard(e));
  root.appendChild(previewCard(e, n));
  return root;
}

function columnsCard(e) {
  const L = labelsOf(e), sourced = !!srcOf(e), dic = dictOf(e);
  const tbody = h("tbody");
  e.attributes.forEach((a, i) => {
    const iss = issuesOf(a.id);
    const au = "api/attributes/" + a.id;
    const sv = (k) => (v) => patch(au, { [k]: v }, true);
    const ti = (k, cls, ph) => editable(h("input", { type: "text", class: cls, value: a[k] == null ? "" : a[k], "data-k": a.id + k, placeholder: ph || null }), a[k], sv(k));
    const cb = (k) => editable(h("input", { type: "checkbox", checked: a[k], "data-k": a.id + k, title: k }), a[k], sv(k));
    const keyRadio = editable(h("input", { type: "radio", name: "key-" + e.id, checked: a.key, "data-k": a.id + "key", title: "Key column" }), a.key, (v) => patch(au, { key: v }, true));
    keyRadio.addEventListener("click", async () => { if (a.key) { try { await patch(au, { key: false }, true); } catch (err) { toast(err.message, true); } } });
    const typeSel = selectEl(TYPES.map((t) => [t, t]), a.type, { "data-k": a.id + "type" });
    editable(typeSel, a.type, async (v) => { await patch(au, { type: v }, true); if (v === "enum") columnModal(e.id, a.id); });
    const ml = editable(h("input", { type: "number", min: "1", value: a.max_length || "", "data-k": a.id + "ml", style: "width:64px" }), a.max_length,
      (v) => patch(au, { max_length: v ? parseInt(v, 10) : null }, true));
    const move = async (d) => {
      const ids = e.attributes.map((x) => x.id); const j = i + d; if (j < 0 || j >= ids.length) return;
      [ids[i], ids[j]] = [ids[j], ids[i]];
      await api("POST", "api/entities/" + e.id + "/attributes/order", { ids }); await reload(); render();
    };
    const dv = derivedOf(e, a);
    tbody.appendChild(h("tr", { class: [iss.some((x) => x.level === "error") ? "has-err" : iss.length ? "has-warn" : "", dv.derived ? "derived-row" : ""].join(" ").trim() || null, title: iss.map((x) => x.message).join("\n") || null },
      h("td", { style: "white-space:nowrap" }, h("button", { class: "icon-btn", title: "Move up", onclick: () => move(-1) }, "↑"), h("button", { class: "icon-btn", title: "Move down", onclick: () => move(1) }, "↓")),
      h("td", { class: "c" }, keyRadio),
      h("td", null, (() => {
        const bnIn = ti("business_name", "w-bn");
        return withSimilar(bnIn, () => ({ business: bnIn.value, api: "", dd: a.dd_reference, location: a.source_column, exclude: a.id }),
          async (g) => { try { await patch(au, { business_name: g.business_name, api_name: g.api_name }, true); toast(a.business_name + " → " + g.api_name); } catch (err) { toast(err.message, true); } });
      })(), dv.derived ? h("div", { class: "drv-pill", title: dv.from ? "Comes from " + dv.from + ", not " + (e.source_object || "this entity's business component") : "Derived: calculated or looked up, not held on this entity's record" }, "derived" + (dv.from ? " · " + dv.from : "")) : null),
      h("td", null, (() => {
        const apiIn = ti("api_name", "mono w-api");
        return withSimilar(apiIn, () => ({ business: "", api: apiIn.value, dd: a.dd_reference, location: a.source_column, exclude: a.id }),
          async (g) => { try { await patch(au, { business_name: g.business_name, api_name: g.api_name }, true); toast(a.business_name + " → " + g.api_name); } catch (err) { toast(err.message, true); } });
      })()),
      h("td", null, (() => {
        const groups = e.column_groups || [];
        const sel = selectEl([["", "—"]].concat(groups.map((g) => [g.id, g.api_name + (g.many ? " [ ]" : " { }")]), [["__new", "+ New group…"]]),
          a.group_id || "", { "data-k": a.id + "group", title: "Send this column inside an object or list property, e.g. bankDetails" });
        sel.style.minWidth = "110px";
        sel.addEventListener("change", async () => {
          if (sel.value === "__new") { sel.value = a.group_id || ""; columnGroupModal(e, null, [a.id]); return; }
          try { await patch(au, { group_id: sel.value }, true); } catch (err) { toast(err.message, true); }
        });
        return sel;
      })()),
      h("td", null, typeSel, a.pattern ? h("div", { class: "muted small mono", title: "Pattern: " + a.pattern, style: "white-space:nowrap" }, "regex " + (a.pattern.length > 14 ? a.pattern.slice(0, 13) + "…" : a.pattern)) : null, a.money ? h("div", { class: "muted small", style: "white-space:nowrap" }, ["integer", "number"].includes(a.type) ? "£ { amount, currency }" : "£ needs integer") : null, a.type === "enum" ? h("div", { class: "type-enums" }, a.enum.length ? a.enum.slice(0, 3).map((v) => h("span", { class: "enum-chip" }, v.code)) : h("span", { class: "lvl-error small" }, "no values"), a.enum.length > 3 ? h("span", { class: "muted small" }, "+" + (a.enum.length - 3)) : null) : null),
      h("td", null, ti("format", "w-n")),
      h("td", null, ml),
      h("td", { class: "c" }, cb("required")), h("td", { class: "c" }, cb("read_only")), h("td", { class: "c" }, cb("system")), h("td", null, (() => {
        const v0 = !a.filterable ? "" : a.filter_required ? "required" : "optional";
        const sel = selectEl([["", "—"], ["optional", "Optional"], ["required", "Required"]], v0, { "data-k": a.id + "filter", title: "Query parameter on GET lists" + (a.filterable && a.filter_match !== "equals" ? " (" + a.filter_match + ")" : "") });
        sel.style.minWidth = "96px";
        return editable(sel, v0, (v) => patch(au, { filterable: !!v, filter_required: v === "required" }, true));
      })()), h("td", { class: "c" }, cb("pii")), h("td", { class: "c" }, cb("money")),
      h("td", null, dic ? suggest(ddInput(e, a), ddOptions(e, a)) : ti("dd_reference", "mono w-dd", "DD-…")),
      sourced ? h("td", null, (() => {
        const bcIn = editable(h("input", { type: "text", class: "w-bc" + (a.source_object ? "" : " inherited"), value: a.source_object || "", "data-k": a.id + "bc",
          placeholder: e.source_object || L.object, title: a.source_object ? "This column's own " + L.object.toLowerCase() : "Same as the entity: " + (e.source_object || "none") }),
          a.source_object, (v) => patch(au, { source_object: v.trim() && v.trim().toLowerCase() !== (e.source_object || "").toLowerCase() ? v.trim() : "" }, true));
        return dic ? suggest(bcIn, bcOptions(e.source_system_id)) : bcIn;
      })()) : null,
      sourced ? h("td", null, dic ? suggest(fieldInput(e, a), fieldOptions(e, a)) : ti("source_field", "w-src", L.field)) : null,
      sourced ? h("td", null, (() => {
        const locIn = ti("source_column", "mono w-src", L.location);
        return withSimilar(locIn, () => ({ business: "", api: "", location: locIn.value, exclude: a.id }),
          async (g) => { try { await patch(au, { business_name: g.business_name, api_name: g.api_name }, true); toast(a.business_name + " → " + g.business_name + " (" + g.api_name + ")"); } catch (err) { toast(err.message, true); } });
      })()) : null,
      h("td", null, ti("example", "w-src")),
      h("td", { style: "white-space:nowrap" },
        dic ? h("button", { class: "icon-btn", title: "Look up in the dictionary", "data-k": a.id + "lookup", onclick: () => lookupModal(e, a) }, "🔍") : null,
        h("button", { class: "icon-btn", title: "All details" + (a.type === "enum" ? " and enum values" : ""), onclick: () => columnModal(e.id, a.id) }, "⋯"),
        h("button", { class: "icon-btn danger", title: "Delete column", onclick: async () => {
          if (!confirm("Delete column " + a.business_name + "?")) return;
          await api("DELETE", au); await reload(); render(); toast("Column deleted");
        } }, "🗑"))));
  });

  // add row
  const bn = h("input", { type: "text", placeholder: "Business name, e.g. Tax Reference Number", "data-k": "new-bn", class: "w-bn" });
  const an = h("input", { type: "text", class: "mono w-api", placeholder: "apiName", "data-k": "new-api" });
  const ty = selectEl(TYPES.map((t) => [t, t]), "string", { "data-k": "new-type" });
  const sf = h("input", { type: "text", placeholder: L.field, "data-k": "new-sf", class: "w-src" });
  const sfCell = dic ? suggest(sf, fieldOptions(e, null)) : sf;
  const sc = h("input", { type: "text", placeholder: L.location, class: "mono w-src", "data-k": "new-sc" });
  let apiTouched = false;
  bn.addEventListener("input", () => { if (!apiTouched) an.value = camel(bn.value); });
  const useNew = (g) => { bn.value = g.business_name; an.value = g.api_name; apiTouched = true; bn.focus(); };
  withSimilar(bn, () => ({ business: bn.value, api: "", dd: "", exclude: "" }), useNew);
  withSimilar(an, () => ({ business: "", api: an.value, dd: "", exclude: "" }), useNew);
  withSimilar(sc, () => ({ business: "", api: "", location: sc.value, exclude: "" }), useNew);
  an.addEventListener("input", () => { apiTouched = !!an.value; });
  const add = async () => {
    if (!bn.value.trim()) { bn.focus(); return; }
    try {
      const body = { business_name: bn.value, api_name: an.value || camel(bn.value), type: ty.value, source_field: sf.value, source_column: sc.value };
      const row = dic && sf.value.trim() ? await dictField(e.source_system_id, e.source_object, sf.value.trim()) : null;
      if (row) Object.assign(body, { source_column: sc.value || dictLocation(row), dd_reference: row.dd, data_item: row.item });
      const a = await api("POST", "api/entities/" + e.id + "/attributes", body);
      await reload(); render(); toast("Added " + a.business_name);
      if (a.type === "enum") columnModal(e.id, a.id);
      const f = document.querySelector('[data-k="new-bn"]'); if (f) f.focus();
    } catch (err) { toast(err.message, true); }
  };
  [bn, an, sf, sc].forEach((x) => x.addEventListener("keydown", (ev) => { if (ev.key === "Enter") add(); }));
  tbody.appendChild(h("tr", { class: "add-row" }, h("td", null), h("td", { class: "c muted small" }, "new"), h("td", null, bn), h("td", null, an), h("td", null), h("td", null, ty),
    h("td", { colspan: "9" }), sourced ? h("td", null) : null, sourced ? h("td", null, sfCell) : null, sourced ? h("td", null, sc) : null, h("td", { colspan: "2" }, h("button", { class: "btn btn-primary btn-sm", onclick: add }, "+ Add column"))));

  return h("div", { class: "card" },
    h("div", { class: "card-head" }, h("div", null, h("h2", null, "Columns (" + e.attributes.length + ")"),
      h("div", { class: "hint" }, "Business name ↔ API name ↔ source field. Edits save when you leave a cell; ⋯ for enum values, LOV, transformation and description."
        + (dic ? " " + L.field + " and DD ref suggest from the dictionary; 🔍 searches it." : ""))),
      h("div", { class: "row-actions" },
        (e.column_groups || []).map((g) => h("button", { class: "tag", style: "background:#EEF3F8;color:#1F3A5F;border:1px solid #9DB1C7", title: "Edit group", onclick: () => columnGroupModal(e, g) },
          g.api_name + (g.many ? " [ ]" : " { }"))),
        h("button", { class: "btn btn-sm", "data-k": "new-cgroup", onclick: () => columnGroupModal(e, null) }, "+ Column group")),
      dic ? h("div", { class: "row-actions" },
        h("button", { class: "btn btn-primary btn-sm", "data-k": "from-dict", onclick: () => addFromDictionaryModal(e) }, "+ Columns from dictionary"),
        h("button", { class: "btn btn-sm", title: "Fill blank " + L.location + ", DD ref and data item for columns whose " + L.field + " is in the dictionary", onclick: () => fillFromDictionary(e) }, "Fill from dictionary"))
        : null),
    h("div", { class: "table-wrap" }, h("table", { class: "cols-table" },
      h("thead", null, h("tr", null, [["", ""], ["Key", "Key column"], ["Business name", ""], ["API name", ""], ["Group", "Column group: sent inside an object { } or a list [ ] property, e.g. bankDetails"], ["Type", ""], ["Format", ""], ["Max", "Max length"], ["Req", "Required"], ["RO", "Read only: never accepted in any request"],
        ["Sys", "System column: set by the source system, not accepted on POST or PUT"], ["Filter", "Query parameter on GET lists: optional or required. Match type in ⋯"], ["PII", "Personal data"], ["£", "Money: sent as { amount, currency }; the type is the amount's (integer = pence)"], ["DD ref", "Data dictionary attribute reference"],
        ...(sourced ? [[L.object, "Blank: the entity's (" + (e.source_object || "none") + "). Name another when the column comes from a different " + L.object.toLowerCase()],
          [L.field, "Source field in " + srcOf(e).name], [L.location, "Optional"]] : []), ["Example", ""], ["", ""]].map(([x, t]) => h("th", { title: t || null }, x)))),
      tbody)));
}

/* ---------- column groups: bankDetails { … } or bankDetails [ { … } ] ---------- */
function columnGroupModal(e, g, preselect) {
  const isNew = !g;
  g = Object.assign({ id: "", business_name: "", api_name: "", many: false, required: false, description: "" }, g || {});
  const f = {
    bn: h("input", { type: "text", value: g.business_name, placeholder: "e.g. Bank Details", "data-k": "cg-bn" }),
    api: h("input", { type: "text", class: "mono", value: g.api_name, placeholder: "bankDetails", "data-k": "cg-api" }),
    shape: selectEl([["object", "Object: bankDetails: { bankName, sortCode }"], ["list", "List: bankDetails: [ { bankName, sortCode }, … ]"]], g.many ? "list" : "object", { "data-k": "cg-shape" }),
    req: h("input", { type: "checkbox", checked: g.required }),
    desc: h("textarea", { placeholder: "What the group holds" }),
  };
  f.desc.value = g.description || "";
  let apiTouched = !!g.api_name;
  f.bn.oninput = () => { if (!apiTouched) f.api.value = camel(f.bn.value); preview(); };
  f.api.oninput = () => { apiTouched = !!f.api.value; preview(); };
  const chosen = new Set(isNew ? (preselect || []) : e.attributes.filter((a) => a.group_id === g.id).map((a) => a.id));
  const prev = h("pre", { class: "code", style: "max-height:160px;margin-top:8px" });
  const preview = () => {
    const names = e.attributes.filter((a) => chosen.has(a.id)).map((a) => a.api_name);
    const obj = "{ " + names.map((n) => '"' + n + '": …').join(", ") + " }";
    prev.textContent = '"' + (f.api.value || "group") + '": ' + (f.shape.value === "list" ? "[ " + obj + ", … ]" : obj);
  };
  f.shape.onchange = preview;
  const list = h("div", { class: "tag-pick", style: "max-height:240px" }, e.attributes.map((a) => {
    const other = a.group_id && a.group_id !== g.id ? (e.column_groups || []).find((x) => x.id === a.group_id) : null;
    const cb = h("input", { type: "checkbox", checked: chosen.has(a.id), disabled: a.key, "data-k": "cg-m-" + a.api_name });
    cb.onchange = () => { if (cb.checked) chosen.add(a.id); else chosen.delete(a.id); preview(); };
    return h("label", { class: "tag-pick-row", title: a.key ? "Key columns stay at the top level" : null }, cb, h("span", null, a.business_name), h("span", { class: "mono small muted" }, a.api_name),
      other ? h("span", { class: "muted small" }, "now in " + other.api_name + " (moves here)") : null);
  }));
  preview();
  const fld = (l, el, help) => h("div", { class: "field" }, h("label", null, l), el, help ? h("div", { class: "help" }, help) : null);
  openModal((isNew ? "New column group in " : "Column group in ") + e.business_name, h("div", null,
    h("div", { class: "grid" }, fld("Business name", f.bn), fld("API name", f.api, "The property, camelCase"), fld("Shape", f.shape)),
    h("div", { class: "checks", style: "margin-top:8px" }, h("label", null, f.req, "Required in POST and PUT")),
    h("div", { class: "field", style: "margin-top:8px" }, h("label", null, "Description"), f.desc),
    h("div", { class: "section-label" }, "Columns in the group"), list, prev), [
    isNew ? null : h("button", { class: "btn btn-danger", style: "margin-right:auto", onclick: async () => {
      if (!confirm("Remove the group " + g.api_name + "? Its columns go back to the top level.")) return;
      try { await api("PATCH", "api/entities/" + e.id, { column_groups: (e.column_groups || []).filter((x) => x.id !== g.id) }); await reload(); closeModal(); render(); toast("Group removed"); }
      catch (err) { toast(err.message, true); }
    } }, "Delete group"),
    h("button", { class: "btn", onclick: closeModal }, "Cancel"),
    h("button", { class: "btn btn-primary", "data-k": "cg-save", onclick: async () => {
      const api_name = f.api.value.trim() || camel(f.bn.value);
      if (!api_name) { f.bn.focus(); return; }
      const next = { id: g.id, business_name: f.bn.value.trim(), api_name, many: f.shape.value === "list", required: f.req.checked, description: f.desc.value.trim() };
      try {
        const all = (e.column_groups || []).filter((x) => x.id !== g.id).concat([next]);
        const saved = await api("PATCH", "api/entities/" + e.id, { column_groups: isNew ? all : (e.column_groups || []).map((x) => (x.id === g.id ? next : x)) });
        const gid = isNew ? saved.column_groups.find((x) => x.api_name === api_name).id : g.id;
        for (const a of e.attributes) {
          const want = chosen.has(a.id) ? gid : a.group_id === gid ? "" : null;
          if (want !== null && want !== (a.group_id || "")) await api("PATCH", "api/attributes/" + a.id, { group_id: want });
        }
        await reload(); closeModal(); render(); toast("Group " + api_name + ": " + chosen.size + " column" + (chosen.size === 1 ? "" : "s"));
      } catch (err) { toast(err.message, true); }
    } }, "Save")].filter(Boolean));
}

/* ---------- similar names (consistency) ---------- */
const simPop = { el: null, input: null, timer: null, seq: 0 };
function hideSimilar() { if (simPop.el) simPop.el.hidden = true; simPop.input = null; }
function similarPanel() {
  if (!simPop.el) {
    simPop.el = h("div", { class: "sim-pop", hidden: true, "data-k": "sim-pop" });
    document.body.appendChild(simPop.el);
    document.addEventListener("mousedown", (ev) => { if (simPop.el && !simPop.el.contains(ev.target) && ev.target !== simPop.input) hideSimilar(); });
    window.addEventListener("scroll", hideSimilar, true);
  }
  return simPop.el;
}
function usedIn(g) {
  const names = [...new Set(g.used_in.map((u) => u.schema_name))];
  return "used in " + names.slice(0, 4).join(", ") + (names.length > 4 ? " +" + (names.length - 4) : "");
}
// shows names already used in the model that are like what is being typed; ctx() -> {business, api, dd, exclude}
function withSimilar(input, ctx, onUse) {
  const run = async () => {
    const c = ctx();
    if (![c.business, c.api, c.location].some((x) => (x || "").trim())) { if (simPop.input === input) hideSimilar(); return; }
    const seq = ++simPop.seq;
    let list;
    try { list = await api("GET", "api/names/similar?" + qs({ business: c.business, api: c.api, dd: c.dd, location: c.location, exclude: c.exclude, limit: 8 })); } catch (err) { return; }
    if (seq !== simPop.seq || document.activeElement !== input) return;
    const el = similarPanel();
    if (!list.length) { hideSimilar(); return; }
    el.innerHTML = "";
    simPop.input = input;
    el.appendChild(h("div", { class: "sim-head" }, c.location && !c.business && !c.api ? "Columns already mapped to " + c.location.toUpperCase() : "Similar names already in the catalogue", h("span", { class: "muted" }, " · Esc to close")));
    list.forEach((g) => {
      const pct = Math.round(g.score * 100);
      el.appendChild(h("div", { class: "sim-row" + (pct >= 100 ? " exact" : "") },
        h("div", { class: "sim-score", title: pct + "% similar" }, h("span", { style: "width:" + pct + "%" }), h("b", null, pct + "%")),
        h("div", { class: "sim-main" },
          h("div", null, g.business_name, h("span", { class: "muted" }, " → "), h("code", null, g.api_name), h("span", { class: "muted small" }, "  " + g.type)),
          h("div", { class: "muted small" }, g.reasons.join(" · ") + " · " + usedIn(g))),
        h("button", { class: "btn btn-sm", title: "Use " + g.business_name + " → " + g.api_name,
          onmousedown: (ev) => { ev.preventDefault(); hideSimilar(); onUse(g); } }, "Use")));
    });
    const r = input.getBoundingClientRect();
    el.style.left = Math.max(8, Math.min(r.left, window.innerWidth - 470)) + "px";
    el.style.top = (r.bottom + 4) + "px";
    el.hidden = false;
  };
  input.addEventListener("focus", run);
  input.addEventListener("input", () => { clearTimeout(simPop.timer); simPop.timer = setTimeout(run, 250); });
  input.addEventListener("keydown", (ev) => { if (ev.key === "Escape") hideSimilar(); });
  input.addEventListener("blur", () => setTimeout(() => { if (simPop.input === input && document.activeElement !== input) hideSimilar(); }, 200));
  return input;
}

/* ---------- derived columns ---------- */
// a column is derived when flagged, or when its own business component is not the entity's
function derivedOf(e, a) {
  const own = (a.source_object || "").trim();
  const other = own && own.toLowerCase() !== (e.source_object || "").trim().toLowerCase() ? own : "";
  return { derived: !!a.derived || !!other, from: other };
}
const DRV = "#6B46C1";

/* ---------- money ---------- */
async function moneyCandidatesModal() {
  let list;
  try { list = await api("GET", "api/money-candidates"); } catch (err) { toast(err.message, true); return; }
  const st = M().settings || {};
  const amt = selectEl([["integer", "Amount as integer pence (number columns become integer)"], ["keep", "Keep each column's type (number stays decimal)"]], "integer", { "data-k": "mc-amt" });
  const boxes = list.map((c) => { const cb = h("input", { type: "checkbox", checked: c.type !== "string", "data-k": "mc-" + c.api_name }); cb.dataset.id = c.attr_id; return [c, cb]; });
  const body = h("div", null,
    h("p", { class: "muted small", style: "margin-top:0" }, "Ticked columns are flagged as money and sent as { \"amount\": 2999, \"currency\": \"" + (st.money_currency || "GBP") + "\" }. "
      + "A decimal column made integer gets a transformation note (pounds × 100 = pence) when it has none."),
    h("div", { class: "field", style: "max-width:460px;margin-bottom:10px" }, h("label", null, "Amount"), amt),
    list.length ? h("div", { class: "table-wrap", style: "max-height:380px" }, h("table", null,
      h("thead", null, h("tr", null, ["", "Entity", "Column", "API name", "Type now", "Example"].map((x) => h("th", null, x)))),
      h("tbody", null, boxes.map(([c, cb]) => h("tr", null, h("td", null, cb), h("td", { class: "small" }, c.entity), h("td", null, c.business_name),
        h("td", { class: "mono small" }, c.api_name), h("td", { class: "small muted" }, c.type + (c.format ? " (" + c.format + ")" : "")), h("td", { class: "mono small" }, c.example))))))
      : h("div", { class: "empty" }, "No number columns with amount-like names. Tick £ on a column to make it money."));
  openModal("Amount columns", body, [h("button", { class: "btn", onclick: closeModal }, "Cancel"),
    list.length ? h("button", { class: "btn btn-primary", "data-k": "mc-go", onclick: async () => {
      const ids = boxes.filter(([, cb]) => cb.checked).map(([, cb]) => cb.dataset.id);
      if (!ids.length) { toast("Tick the columns to change", true); return; }
      try { const r = await api("POST", "api/attributes/to-money", { ids, amount_type: amt.value }); await reload(); closeModal(); render(); toast(r.converted.length + " column" + (r.converted.length === 1 ? "" : "s") + " now money"); }
      catch (err) { toast(err.message, true); }
    } }, "Make money") : null].filter(Boolean));
}

/* ---------- source variants: records from different sources by a column's value ---------- */
const VFIELDS = ["source_object", "source_field", "source_column", "dd_reference", "data_item", "transform"];
function colPath(e, a) { const g = (e.column_groups || []).find((x) => x.id === a.group_id); return (g ? g.api_name + "." : "") + a.api_name; }
// a column's mapping in one variant: its override where given, else its own mapping (a location without
// a table takes the variant's table)
function effVariant(e, a, v, ignoreOverride) {
  const vm = ignoreOverride ? {} : ((a.variant_sources || {})[v.id] || {});
  const out = { not_applicable: !!vm.not_applicable, inherited: [] };
  for (const f of VFIELDS) {
    const own = (vm[f] || "").trim();
    if (own) { out[f] = own; continue; }
    out.inherited.push(f);
    if (f === "source_object") out[f] = (a.source_object || "").trim() || (v.source_object || "").trim();
    else if (f === "source_column") {
      const base = (a.source_column || "").trim(), pre = (v.location_prefix || "").trim();
      out[f] = base && pre && !base.includes(".") && !base.includes(" ") ? pre + "." + base : base;
    } else out[f] = (a[f] || "").trim();
  }
  return out;
}
function variantsCard(e) {
  const L = labelsOf(e), dic = dictOf(e), sid = e.source_system_id;
  const vs = e.source_variants || [];
  const enumCols = e.attributes.filter((a) => a.type === "enum");
  const url = "api/entities/" + e.id;
  const body = h("div", { class: "card-body" });
  const card = h("div", { class: "card", id: "variants" },
    h("div", { class: "card-head" }, h("div", null, h("h2", null, "Source variants" + (vs.length ? " (" + vs.length + ")" : "")),
      h("div", { class: "hint" }, "When records come from different " + L.object.toLowerCase() + "s by a column's value, e.g. notes by parent type. The API stays one resource; only the mapping differs."))),
    body);
  if (!vs.length) {
    if (!enumCols.length) { body.appendChild(h("div", { class: "muted small" }, "Needs an enum column whose value picks the source, e.g. parent.type with service-request, contact, activity.")); return card; }
    const sel = selectEl(enumCols.map((a) => [a.id, colPath(e, a) + "  (" + a.enum.map((v) => v.code).join(", ") + ")"]), enumCols[0].id, { "data-k": "vary-pick", style: "width:auto;min-width:260px" });
    body.appendChild(h("div", { class: "row-actions" }, h("span", { class: "small" }, "Source varies by"), sel,
      h("button", { class: "btn btn-sm", onclick: async () => {
        const a = e.attributes.find((x) => x.id === sel.value);
        const list = a.enum.map((v) => ({ code: v.code, source_object: "", source_io: "", location_prefix: "", description: "" }));
        try { await patch(url, { varies_by: a.id, source_variants: list }); } catch (err) { toast(err.message, true); }
      } }, "Set up a variant per value")));
    return card;
  }
  const by = e.attributes.find((a) => a.id === e.varies_by);
  const codes = by ? by.enum.map((v) => v.code) : [];
  const save = (list) => patch(url, { source_variants: list }, true).catch((err) => toast(err.message, true));
  const vsel = selectEl([["", "—"]].concat(enumCols.map((a) => [a.id, colPath(e, a)])), e.varies_by || "", { "data-k": "vary-by", style: "width:auto;min-width:200px" });
  editable(vsel, e.varies_by || "", (val) => patch(url, { varies_by: val }));
  body.appendChild(h("div", { class: "row-actions", style: "margin-bottom:10px" }, h("span", { class: "small" }, "Source varies by"), vsel,
    h("button", { class: "btn btn-sm btn-danger", onclick: async () => {
      if (!confirm("Remove the source variants and every column's per-variant mapping?")) return;
      try { await patch(url, { varies_by: "", source_variants: [] }); } catch (err) { toast(err.message, true); }
    } }, "Remove variants")));
  const rows = vs.map((v, i) => {
    const upd = (k) => (val) => save(vs.map((x, j) => (j === i ? Object.assign({}, x, { [k]: val }) : x)));
    const opts = [["", "—"]].concat(codes.map((c) => [c, c]));
    if (v.code && !codes.includes(v.code)) opts.push([v.code, v.code + " (not a value)"]);
    const code = selectEl(opts, v.code, { "data-k": "sv-code-" + v.id }); editable(code, v.code, upd("code"));
    const inp = (k, ph, mono) => { const x = h("input", { type: "text", class: mono ? "mono" : null, value: v[k] || "", placeholder: ph, "data-k": "sv-" + k + "-" + v.id }); editable(x, v[k], upd(k)); return x; };
    const bc = inp("source_object", L.ph.object || L.object);
    return h("tr", null, h("td", null, code), h("td", null, dic ? suggest(bc, bcOptions(sid)) : bc), h("td", null, inp("source_io", L.container)),
      h("td", null, inp("location_prefix", L.location === "Table.column" ? "e.g. S_NOTE_SR" : "optional", true)), h("td", null, inp("description", "")),
      h("td", null, h("button", { class: "icon-btn danger", title: "Remove this variant", onclick: () => save(vs.filter((_, j) => j !== i)) }, "✕")));
  });
  body.appendChild(h("div", { class: "table-wrap" }, h("table", { class: "cols-table", style: "min-width:0" },
    h("thead", null, h("tr", null, [(by ? colPath(e, by) : "Value"), L.object, L.container, L.location === "Table.column" ? "Table" : "Location prefix", "Description", ""].map((x) => h("th", null, x)))),
    h("tbody", null, rows))));
  const missing = codes.filter((c) => !vs.some((v) => v.code === c));
  if (missing.length) body.appendChild(h("button", { class: "btn btn-sm", style: "margin-top:8px", onclick: () =>
    save(vs.concat(missing.map((c) => ({ code: c, source_object: "", source_io: "", location_prefix: "", description: "" })))) }, "+ Variant for " + missing.join(", ")));

  body.appendChild(h("div", { class: "section-label", style: "margin-top:16px" }, "Column mapping by variant"));
  body.appendChild(h("div", { class: "muted small", style: "margin-bottom:8px" },
    "Grey comes from the column's own mapping (a " + L.location.toLowerCase() + " without a table gets the variant's table, so NOTE becomes S_NOTE_SR.NOTE). " +
    "Black is set for that variant only. Click a cell to change it, or mark the field not applicable."));
  const iss = S.issues.filter((x) => x.message.includes(" mapped for "));
  const gridRows = e.attributes.map((a) => {
    const warn = iss.find((x) => x.object_id === a.id);
    return h("tr", { class: warn ? "has-warn" : null, title: warn ? warn.message : null },
      h("td", null, h("div", null, a.business_name), h("div", { class: "mono small muted" }, colPath(e, a))),
      vs.map((v) => {
        const eff = effVariant(e, a, v), own = (a.variant_sources || {})[v.id];
        const cls = (f) => (eff.inherited.includes(f) ? "inh" : "own");
        const content = eff.not_applicable ? h("span", { class: "muted small" }, "not applicable")
          : h("div", null,
            eff.source_field ? h("div", { class: cls("source_field") }, eff.source_field) : (eff.transform ? null : h("div", { class: "lvl-warning small" }, "not mapped")),
            eff.source_column ? h("div", { class: "mono small " + cls("source_column") }, eff.source_column) : null,
            eff.transform ? h("div", { class: "small " + cls("transform"), title: eff.transform }, "ƒ " + (eff.transform.length > 46 ? eff.transform.slice(0, 44) + "…" : eff.transform)) : null,
            eff.source_object && eff.source_object !== v.source_object ? h("div", { class: "small " + cls("source_object") }, "from " + eff.source_object) : null);
        return h("td", { class: "vcell" + (own ? " vown" : ""), title: "Change for " + (v.code || "this variant"), onclick: () => variantCellModal(e, a, v) }, content);
      }));
  });
  body.appendChild(h("div", { class: "table-wrap" }, h("table", { class: "cols-table vgrid", style: "min-width:0" },
    h("thead", null, h("tr", null, h("th", null, "Column"), vs.map((v) => h("th", null, h("div", null, v.code || "—"),
      h("div", { class: "small muted", style: "text-transform:none;letter-spacing:0;font-weight:400" }, [v.source_object, v.location_prefix].filter(Boolean).join(" · ")))))),
    h("tbody", null, gridRows))));
  return card;
}
function variantCellModal(e, a, v) {
  const L = labelsOf(e), dic = dictOf(e), sid = e.source_system_id;
  const vm = Object.assign({}, (a.variant_sources || {})[v.id] || {});
  const base = effVariant(e, a, v, true);    // what it takes from the column's own mapping
  const f = {};
  const inp = (k, mono, area) => {
    const x = h(area ? "textarea" : "input", { type: "text", class: mono ? "mono" : null, value: vm[k] || "", placeholder: base[k] || "", "data-k": "vm-" + k });
    if (area) x.value = vm[k] || "";
    f[k] = x; return x;
  };
  const fld = (label, el, help) => h("div", { class: "field" }, h("label", null, label), el, help ? h("div", { class: "help" }, help) : null);
  inp("source_object"); inp("source_field"); inp("source_column", true); inp("dd_reference", true); inp("data_item"); inp("transform", false, true);
  const bcNow = () => f.source_object.value.trim() || base.source_object;
  if (dic) {
    f.source_field.addEventListener("change", async () => {
      const r = await dictField(sid, bcNow(), f.source_field.value.trim());
      if (!r) return;
      if (!f.source_column.value) f.source_column.value = dictLocation(r);
      if (!f.dd_reference.value && r.dd && r.dd !== base.dd_reference) f.dd_reference.value = r.dd;
      if (!f.data_item.value && r.item && r.item !== base.data_item) f.data_item.value = r.item;
    });
  }
  const fieldOpts = async (q) => (await dictFields(sid, { bc: bcNow(), q, by: "field", limit: 60 })).map((r) => [r.field, [dictLocation(r), r.dd, r.item].filter(Boolean).join(" · ")]);
  const na = h("input", { type: "checkbox", "data-k": "vm-na" }); na.checked = !!vm.not_applicable;
  const body = h("div", null,
    h("p", { class: "muted small", style: "margin-top:0" }, "For " + (v.code || "this variant") + " (" + (v.source_object || "no " + L.object.toLowerCase()) + "). Leave a field blank to use what is shown in grey: the column's own mapping."),
    h("label", { class: "checks", style: "margin-bottom:10px" }, na, " Not applicable: " + (v.source_object || "this source") + " has no such field"),
    h("div", { class: "grid" },
      fld(L.object, dic ? suggest(f.source_object, bcOptions(sid)) : f.source_object, "Only when this column comes from another " + L.object.toLowerCase()),
      fld(L.field, dic ? suggest(f.source_field, fieldOpts) : f.source_field),
      fld(L.location, f.source_column),
      fld("DD attribute", f.dd_reference),
      fld("Data item", f.data_item),
      h("div", { class: "field wide" }, h("label", null, "Transformation"), f.transform)));
  const saveBtn = h("button", { class: "btn btn-primary", onclick: async () => {
    const next = Object.assign({}, a.variant_sources || {});
    const o = { not_applicable: na.checked };
    VFIELDS.forEach((k) => { o[k] = f[k].value.trim(); });
    if (VFIELDS.some((k) => o[k]) || o.not_applicable) next[v.id] = o; else delete next[v.id];
    try { await patch("api/attributes/" + a.id, { variant_sources: next }); closeModal(); } catch (err) { toast(err.message, true); }
  } }, "Save");
  const clearBtn = h("button", { class: "btn", onclick: async () => {
    const next = Object.assign({}, a.variant_sources || {}); delete next[v.id];
    try { await patch("api/attributes/" + a.id, { variant_sources: next }); closeModal(); } catch (err) { toast(err.message, true); }
  } }, "Use the column's mapping");
  openModal(e.business_name + " › " + a.business_name + " · " + (v.code || "variant"), body, [h("button", { class: "btn", onclick: closeModal }, "Cancel"), clearBtn, saveBtn]);
}

/* ---------- data dictionary (e.g. the Siebel dictionary) ---------- */
function dictOf(e) { const sid = e && e.source_system_id; return sid ? (S.dicts || []).find((d) => d.system_id === sid && d.rows) : null; }
function dictLocation(r) { return r.table && r.column ? r.table + "." + r.column : r.table || r.column || ""; }
function colBC(e, a) { return ((a && a.source_object) || e.source_object || "").trim(); }
function qs(o) { return Object.entries(o).filter(([, v]) => v !== "" && v != null).map(([k, v]) => k + "=" + encodeURIComponent(v)).join("&"); }
function dictFields(sid, params) { return api("GET", "api/dictionary/" + sid + "/fields?" + qs(params)); }
async function dictField(sid, bc, field) {
  if (!bc || !field) return null;
  const rows = await dictFields(sid, { bc, q: field, by: "field", limit: 20 });
  return rows.find((r) => r.field.toLowerCase() === field.toLowerCase()) || null;
}
let dlSeq = 0;
// an input with a datalist filled as the user types; opts(q) resolves to [value, label] pairs
function suggest(input, opts) {
  const dl = h("datalist", { id: "dl-" + (++dlSeq) });
  input.setAttribute("list", dl.id);
  input.setAttribute("autocomplete", "off");
  let timer = null, last = null;
  const fill = async () => {
    const q = input.value.trim();
    if (q === last) return;
    last = q;
    try {
      const pairs = await opts(q);
      dl.innerHTML = "";
      pairs.forEach(([v, l]) => dl.appendChild(h("option", { value: v, label: l || null }, l || null)));
    } catch (err) { /* suggestions are optional */ }
  };
  input.addEventListener("focus", fill);
  input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(fill, 180); });
  return h("span", { class: "suggest" }, input, dl);
}
const bcOptions = (sid) => async (q) => (await api("GET", "api/dictionary/" + sid + "/bcs?" + qs({ q, limit: 40 })))
  .map((b) => [b.bc, b.fields + " fields" + (b.tables.length ? " · " + b.tables.slice(0, 3).join(", ") : "")]);
const fieldOptions = (e, a) => async (q) => (await dictFields(e.source_system_id, { bc: colBC(e, a), q, by: "field", limit: 60 }))
  .map((r) => [r.field, [dictLocation(r), r.dd, r.item].filter(Boolean).join(" · ")]);
const ddOptions = (e, a) => async (q) => {
  const own = await dictFields(e.source_system_id, { bc: colBC(e, a), q, by: "dd", limit: 40 });
  const all = q ? await dictFields(e.source_system_id, { q, by: "dd", limit: 40 }) : [];
  const seen = new Set();
  return own.concat(all).filter((r) => r.dd && !seen.has(r.dd) && seen.add(r.dd)).map((r) => [r.dd, r.bc + " · " + r.field + (r.item ? " · " + r.item : "")]);
};
// what a dictionary row sets on a column; the BC is kept only when it is not the entity's
function dictPatch(e, r) {
  return { source_field: r.field, source_column: dictLocation(r), dd_reference: r.dd, data_item: r.item,
    source_object: r.bc.toLowerCase() === (e.source_object || "").toLowerCase() ? "" : r.bc };
}
function fieldInput(e, a) {
  const inp = h("input", { type: "text", class: "w-src", value: a.source_field || "", "data-k": a.id + "source_field", placeholder: labelsOf(e).field });
  return editable(inp, a.source_field, async (v) => {
    const row = await dictField(e.source_system_id, colBC(e, a), v.trim());
    const body = { source_field: v };
    if (row) Object.assign(body, { source_column: dictLocation(row), dd_reference: row.dd || a.dd_reference, data_item: row.item || a.data_item });
    await patch("api/attributes/" + a.id, body, true);
    if (row) toast("Filled " + labelsOf(e).location + " and DD ref from the dictionary");
  });
}
function ddInput(e, a) {
  const inp = h("input", { type: "text", class: "mono w-dd", value: a.dd_reference || "", "data-k": a.id + "dd_reference", placeholder: "DD-…" });
  return editable(inp, a.dd_reference, async (v) => {
    const val = v.trim();
    let row = null;
    if (val) {
      const own = await dictFields(e.source_system_id, { bc: colBC(e, a), q: val, by: "dd", limit: 10 });
      const any = own.length ? own : await dictFields(e.source_system_id, { q: val, by: "dd", limit: 10 });
      row = any.find((r) => r.dd.toLowerCase() === val.toLowerCase()) || null;
    }
    await patch("api/attributes/" + a.id, row ? Object.assign({ dd_reference: val }, dictPatch(e, row)) : { dd_reference: val }, true);
    if (row) toast("Filled " + labelsOf(e).field + " " + row.field + (row.bc.toLowerCase() !== (e.source_object || "").toLowerCase() ? " (" + row.bc + ")" : "") + " from the dictionary");
  });
}
async function fillFromDictionary(e) {
  try {
    const r = await api("POST", "api/entities/" + e.id + "/fill-from-dictionary");
    await reload(); render();
    const n = Object.keys(r.changed).length;
    toast(n ? "Filled " + n + " column" + (n === 1 ? "" : "s") + " from the dictionary" + (r.not_found.length ? "; not found: " + r.not_found.join(", ") : "")
      : "Nothing to fill" + (r.not_found.length ? "; not found: " + r.not_found.join(", ") : ""));
  } catch (err) { toast(err.message, true); }
}
// search the dictionary and apply one row to a column
function lookupModal(e, a) {
  const L = labelsOf(e), sid = e.source_system_id, bc0 = colBC(e, a);
  const q = h("input", { type: "search", placeholder: "BC field, DD attribute, table, column or data item", value: a.source_field || "", "data-k": "lk-q" });
  const scope = selectEl([["bc", bc0 ? "In " + bc0 : "No " + L.object.toLowerCase() + " set"], ["all", "All " + L.object.toLowerCase() + "s"]], bc0 ? "bc" : "all", { "data-k": "lk-scope" });
  const by = selectEl([["any", "Anything"], ["field", L.field], ["dd", "DD attribute"], ["table", "Physical table"], ["column", "Physical column"], ["item", "Data item"]], "any", { "data-k": "lk-by" });
  const out = h("div", { class: "table-wrap", style: "max-height:380px" });
  const run = async () => {
    try {
      const rows = await dictFields(sid, { bc: scope.value === "bc" ? bc0 : "", q: q.value.trim(), by: by.value, limit: 200 });
      out.innerHTML = "";
      if (!rows.length) { out.appendChild(h("div", { class: "empty" }, "Nothing matches.")); return; }
      out.appendChild(h("table", null, h("thead", null, h("tr", null, [L.object, L.field, L.location, "DD attribute", "Data item", ""].map((x) => h("th", null, x)))),
        h("tbody", null, rows.map((r) => h("tr", null, h("td", { class: "small" }, r.bc), h("td", null, r.field), h("td", { class: "mono small" }, dictLocation(r)),
          h("td", { class: "mono small" }, r.dd), h("td", { class: "small" }, r.item),
          h("td", null, h("button", { class: "btn btn-sm btn-primary", onclick: async () => {
            try { await patch("api/attributes/" + a.id, dictPatch(e, r), true); closeModal(); toast(a.business_name + " ← " + r.bc + " › " + r.field); }
            catch (err) { toast(err.message, true); }
          } }, "Use")))))));
    } catch (err) { out.innerHTML = ""; out.appendChild(h("div", { class: "empty lvl-error" }, err.message)); }
  };
  let t = null;
  q.addEventListener("input", () => { clearTimeout(t); t = setTimeout(run, 200); });
  scope.onchange = run; by.onchange = run;
  openModal("Look up " + a.business_name + " in the dictionary", h("div", null,
    h("div", { class: "grid" }, h("div", { class: "field wide" }, h("label", null, "Search"), q),
      h("div", { class: "field" }, h("label", null, "Where"), scope), h("div", { class: "field" }, h("label", null, "Search in"), by)),
    h("div", { class: "muted small", style: "margin:8px 0" }, "Use fills " + L.field + ", " + L.location + ", DD attribute and data item. A row from another " + L.object.toLowerCase() + " also sets the column's own " + L.object.toLowerCase() + "."),
    out), [h("button", { class: "btn", onclick: closeModal }, "Close")]);
  run(); setTimeout(() => q.focus(), 50);
}
// pick fields of a business component (the entity's, or another) and add them as columns
function addFromDictionaryModal(e) {
  const L = labelsOf(e), sid = e.source_system_id;
  const bcIn = h("input", { type: "text", value: e.source_object || "", placeholder: L.object, "data-k": "fd-bc" });
  const q = h("input", { type: "search", placeholder: "Filter by " + L.field.toLowerCase() + ", DD, table or data item", "data-k": "fd-q" });
  const nameFrom = selectEl([["field", "Business names from the " + L.field.toLowerCase() + " name"], ["item", "Business names from the data item full name"]], "field", { "data-k": "fd-name" });
  const chosen = new Map();
  const count = h("span", { class: "muted small" });
  const list = h("div", { class: "table-wrap", style: "max-height:360px" });
  const used = () => new Set(e.attributes.map((a) => (colBC(e, a) + "|" + (a.source_field || "")).toLowerCase()));
  const upd = () => { count.textContent = chosen.size + " selected"; };
  const run = async () => {
    const bc = bcIn.value.trim();
    list.innerHTML = "";
    if (!bc) { list.appendChild(h("div", { class: "empty" }, "Pick a " + L.object.toLowerCase() + ".")); return; }
    const rows = await dictFields(sid, { bc, q: q.value.trim(), limit: 2000 });
    if (!rows.length) { list.appendChild(h("div", { class: "empty" }, "No fields: is the " + L.object.toLowerCase() + " name right?")); return; }
    const u = used();
    list.appendChild(h("table", null, h("thead", null, h("tr", null, ["", L.field, L.location, "DD attribute", "Data item"].map((x) => h("th", null, x)))),
      h("tbody", null, rows.map((r) => {
        const key = (r.bc + "|" + r.field).toLowerCase(), have = u.has(key);
        const cb = h("input", { type: "checkbox", checked: have || chosen.has(key), disabled: have, "data-k": "fd-" + r.field });
        cb.onchange = () => { if (cb.checked) chosen.set(key, { bc: r.bc, field: r.field }); else chosen.delete(key); upd(); };
        return h("tr", { class: have ? "muted" : null, title: have ? "Already a column" : null }, h("td", null, cb), h("td", null, r.field), h("td", { class: "mono small" }, dictLocation(r)),
          h("td", { class: "mono small" }, r.dd), h("td", { class: "small" }, r.item));
      }))));
  };
  let t = null;
  const later = () => { clearTimeout(t); t = setTimeout(run, 200); };
  bcIn.addEventListener("input", later); bcIn.addEventListener("change", run); q.addEventListener("input", later);
  upd();
  openModal("Columns from the dictionary", h("div", null,
    h("div", { class: "grid" }, h("div", { class: "field" }, h("label", null, L.object), suggest(bcIn, bcOptions(sid)),
      h("div", { class: "help" }, "The entity's by default; pick another to add its fields with their own " + L.object.toLowerCase())),
      h("div", { class: "field" }, h("label", null, "Filter"), q), h("div", { class: "field" }, h("label", null, "Names"), nameFrom)),
    h("div", { style: "margin:8px 0" }, count), list),
    [h("button", { class: "btn", onclick: closeModal }, "Cancel"), h("button", { class: "btn btn-primary", "data-k": "fd-add", onclick: async () => {
      if (!chosen.size) { toast("Tick the fields to add", true); return; }
      try {
        const r = await api("POST", "api/entities/" + e.id + "/attributes/from-dictionary", { fields: Array.from(chosen.values()), business_name_from: nameFrom.value });
        await reload(); closeModal(); render();
        toast("Added " + r.added.length + " column" + (r.added.length === 1 ? "" : "s") + (r.skipped.length ? "; already there: " + r.skipped.join(", ") : ""));
      } catch (err) { toast(err.message, true); }
    } }, "Add columns")]);
  run();
}
// upload a dictionary for a source system: preview, then replace or merge
function dictionaryUploadModal(s) {
  const fileIn = h("input", { type: "file", accept: ".xlsx,.xlsm,.csv,.txt", "data-k": "dict-file" });
  const mode = selectEl([["replace", "Replace the current dictionary"], ["merge", "Add to the current dictionary"]], "replace", { "data-k": "dict-mode" });
  const out = h("div");
  let data = null, name = "";
  const preview = async () => {
    out.innerHTML = "";
    if (!data) return;
    try {
      const r = await api("POST", "api/dictionary/" + s.id + "?" + qs({ apply: "false", mode: mode.value, filename: name }), data, true);
      out.appendChild(h("div", { class: "stats", style: "margin:8px 0 12px" }, [["rows", "Fields"], ["bcs", "Business components"]].map(([k, l]) => h("div", { class: "stat" }, h("b", null, r[k]), h("span", null, l)))));
      if (r.notes.length) out.appendChild(h("div", { class: "callout info" }, h("ul", { class: "issue-list" }, r.notes.map((n) => h("li", null, n)))));
      out.appendChild(h("div", { class: "table-wrap" }, h("table", null, h("thead", null, h("tr", null, ["BC", "BC field", "Table", "Column", "DD attribute", "Data item"].map((x) => h("th", null, x)))),
        h("tbody", null, r.sample.map((x) => h("tr", null, [x.bc, x.field, x.table, x.column, x.dd, x.item].map((v) => h("td", { class: "small" }, v))))))));
      if (r.replaces) out.appendChild(h("div", { class: "muted small", style: "margin-top:6px" }, "Replaces the " + r.replaces + " fields loaded now."));
    } catch (err) { data = null; out.appendChild(h("div", { class: "callout err" }, err.message)); }
  };
  fileIn.onchange = async () => { const f = fileIn.files[0]; if (!f) return; data = await f.arrayBuffer(); name = f.name; preview(); };
  mode.onchange = preview;
  openModal("Data dictionary for " + s.name, h("div", null,
    h("p", { class: "muted small", style: "margin-top:0" }, "An Excel (.xlsx) or CSV export with the columns Business Component (BC) Name, BC Field Name, Physical Table Name, Physical Column Name, DD Attribute Reference and Data Item Full Name. The header row can be anywhere in the first 20 rows."),
    h("div", { class: "grid" }, h("div", { class: "field" }, h("label", null, "File"), fileIn), h("div", { class: "field" }, h("label", null, "Mode"), mode)), out),
    [h("button", { class: "btn", onclick: closeModal }, "Cancel"), h("button", { class: "btn btn-primary", "data-k": "dict-load", onclick: async () => {
      if (!data) { toast("Choose the dictionary file first", true); return; }
      try {
        const r = await api("POST", "api/dictionary/" + s.id + "?" + qs({ apply: "true", mode: mode.value, filename: name }), data, true);
        await reload(); closeModal(); render(); toast("Loaded " + r.summary.rows + " fields in " + r.summary.bcs + " business components");
      } catch (err) { toast(err.message, true); }
    } }, "Load dictionary")]);
}

function columnModal(eid, aid) {
  const e = entById(eid), a = e && e.attributes.find((x) => x.id === aid);
  if (!a) return;
  const L = labelsOf(e), sourced = !!srcOf(e);
  const f = {};
  const inp = (k, ph, mono) => (f[k] = h("input", { type: "text", value: a[k] == null ? "" : a[k], class: mono ? "mono" : null, placeholder: ph || null }));
  const fld = (label, el, wide) => h("div", { class: "field" + (wide ? " wide" : "") }, h("label", null, label), el);
  f.type = selectEl(TYPES.map((t) => [t, t]), a.type);
  f.max_length = h("input", { type: "number", min: "1", value: a.max_length || "" });
  f.description = h("textarea", null); f.description.value = a.description || "";
  const flags = h("div", { class: "checks" }, [["key", "Key"], ["required", "Required"], ["read_only", "Read only"], ["system", "System column (not in POST/PUT)"], ["pii", "Personal data"], ["money", "Money { amount, currency }"], ["derived", "Derived (calculated, or from another business component)"]].map(([k, l]) => {
    f[k] = h("input", { type: "checkbox", checked: a[k] }); return h("label", null, f[k], l);
  }));
  f.filter = selectEl([["", "Not a filter"], ["optional", "Optional query parameter"], ["required", "Required query parameter"]], !a.filterable ? "" : a.filter_required ? "required" : "optional");
  f.match = selectEl(MATCHES, a.filter_match || "equals");
  const matchHelp = h("div", { class: "help" });
  const showMatch = () => { matchHelp.textContent = matchExample(a.api_name, f.match.value); f.match.disabled = !f.filter.value; };
  f.match.onchange = showMatch; f.filter.onchange = showMatch; showMatch();
  let values = a.enum.map((v) => Object.assign({}, v));
  const enumStyle = (M().namespaces.find((x) => x.id === e.namespace_id) || {}).enum_style || "UPPER_SNAKE";
  const enumBox = h("div");
  const drawEnum = () => {
    enumBox.innerHTML = "";
    if (f.type.value !== "enum") { enumBox.appendChild(h("div", { class: "muted small" }, "Enum values apply when the type is enum.")); return; }
    enumBox.appendChild(h("div", { class: "enum-row muted small" }, h("span", null, "API code"), h("span", null, "Label"), h("span", null, sourced ? "Source value (" + L.code_list + ")" : "Source value"), h("span")));
    values.forEach((v, i) => {
      const c = h("input", { type: "text", class: "mono", value: v.code, placeholder: enumCode("Code", enumStyle) }); c.oninput = () => (v.code = c.value);
      const l = h("input", { type: "text", value: v.label, placeholder: "Label" });
      l.oninput = () => { const auto = !v.code || v.code === enumCode(v._prevLabel || "", enumStyle); v.label = l.value; if (auto) { v.code = enumCode(l.value, enumStyle); c.value = v.code; } v._prevLabel = l.value; };
      v._prevLabel = v.label;
      const s = h("input", { type: "text", value: v.source_value, placeholder: "as stored in source" }); s.oninput = () => (v.source_value = s.value);
      enumBox.appendChild(h("div", { class: "enum-row" }, c, l, s, h("button", { class: "icon-btn danger", title: "Remove", onclick: () => { values.splice(i, 1); drawEnum(); } }, "✕")));
    });
    enumBox.appendChild(h("button", { class: "btn btn-sm", onclick: () => { values.push({ code: "", label: "", source_value: "" }); drawEnum(); const ins = enumBox.querySelectorAll(".enum-row input"); if (ins.length) ins[ins.length - 2].focus(); } }, "+ Add value"));
  };
  f.type.onchange = drawEnum; drawEnum();
  const body = h("div", null,
    h("div", { class: "section-label" }, "Names"),
    h("div", { class: "grid" }, fld("Business name", withSimilar(inp("business_name"), () => ({ business: f.business_name.value, api: "", dd: f.dd_reference ? f.dd_reference.value : a.dd_reference, location: f.source_column ? f.source_column.value : a.source_column, exclude: a.id }),
        (g) => { f.business_name.value = g.business_name; f.api_name.value = g.api_name; })),
      fld("API name", withSimilar(inp("api_name", "", true), () => ({ business: "", api: f.api_name.value, dd: f.dd_reference ? f.dd_reference.value : a.dd_reference, location: f.source_column ? f.source_column.value : a.source_column, exclude: a.id }),
        (g) => { f.business_name.value = g.business_name; f.api_name.value = g.api_name; })),
      fld("DD attribute reference", inp("dd_reference", "Data dictionary attribute id", true))),
    h("div", { class: "section-label" }, "Type"),
    h("div", { class: "grid" }, fld("Type", f.type), fld("Format", inp("format", "email, uuid, double, int64…")), fld("Max length", f.max_length), fld("Example", inp("example"))),
    (() => {
      const PRESETS = [["", "Common patterns…"], ["^[0-9A-Z]+-[0-9A-Z]+$", "Siebel row id (1-7ABC12)"], ["^CS-[0-9]{8}$", "Case number (CS-10000002)"],
        ["^[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z][0-9]{6}[A-D]$", "National Insurance number (QQ123456C)"],
        ["^[0-9]{2}-?[0-9]{2}-?[0-9]{2}$", "Sort code (12-34-56)"], ["^[0-9]{8}$", "Bank account number (8 digits)"],
        ["^[A-Z]{1,2}[0-9][A-Z0-9]? ?[0-9][A-Z]{2}$", "UK postcode (LS1 4AB)"],
        ["^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", "UUID"], ["^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", "Email address"]];
      const pin = inp("pattern", "e.g. ^[0-9A-Z]+-[0-9A-Z]+$", true);
      const preset = selectEl(PRESETS, "", { "data-k": "pattern-preset" });
      const check = h("div", { class: "help" });
      const test = () => {
        const p = pin.value.trim(), ex = f.example.value.trim(), t = f.type.value;
        if (!p) { check.textContent = "Optional. Text columns only; IDs, codes and references usually have one."; check.className = "help"; return; }
        let rx; try { rx = new RegExp(p); } catch (err) { check.textContent = "Not a valid regular expression: " + err.message; check.className = "help lvl-error"; return; }
        if (t !== "string") { check.textContent = "Only applies to text columns."; check.className = "help lvl-warning"; return; }
        check.textContent = ex ? (rx.test(ex) ? "Example " + ex + " matches." : "Example " + ex + " does not match.") : "Valid. Add an example to test it.";
        check.className = "help" + (ex && !rx.test(ex) ? " lvl-warning" : "");
      };
      preset.onchange = () => { if (preset.value) { pin.value = preset.value; preset.value = ""; test(); } };
      pin.addEventListener("input", test); setTimeout(() => { f.example.addEventListener("input", test); f.type.addEventListener("change", test); test(); }, 0);
      return h("div", { class: "grid", style: "margin-top:8px" }, h("div", { class: "field wide" }, h("label", null, "Pattern (regular expression)"),
        h("div", { style: "display:flex;gap:8px" }, pin, preset), check));
    })(),
    (() => {
      const cur = (M().settings || {}).money_currency || "GBP";
      const help = h("div", { class: "help", style: "margin-top:6px" });
      const show = () => {
        const on = f.money && f.money.checked, t = f.type.value;
        help.textContent = !on ? "" : t === "integer" ? "Money: { \"" + a.api_name + "\": { \"amount\": 2999, \"currency\": \"" + cur + "\" } }, amount in pence. Write the example as 29.99 (pounds) or 2999 (pence)."
          : t === "number" ? "Money: { \"" + a.api_name + "\": { \"amount\": 29.99, \"currency\": \"" + cur + "\" } }, amount in pounds."
          : "Money needs the type integer (pence) or number (pounds).";
      };
      setTimeout(() => { if (f.money) f.money.addEventListener("change", show); show(); }, 0);
      f.type.addEventListener("change", show);
      return help;
    })(),
    h("div", { style: "margin-top:10px" }, flags),
    (e.column_groups || []).length ? h("div", { class: "grid", style: "margin-top:8px" }, fld("Column group",
      (f.group_id = selectEl([["", "— top level"]].concat(e.column_groups.map((g) => [g.id, g.api_name + (g.many ? " [ { … } ] list" : " { … } object")])), a.group_id || "")))) : null,
    h("div", { class: "help muted small", style: "margin-top:6px" }, "Read only: never accepted in a request. System column: set by the source system; left out of POST and PUT bodies, still allowed in PATCH unless also read only."),
    h("div", { class: "section-label" }, "Filter on GET lists"),
    h("div", { class: "grid" }, fld("Query parameter", f.filter), h("div", { class: "field" }, h("label", null, "Match"), f.match, matchHelp)),
    h("div", { class: "section-label" }, sourced ? "Source mapping — " + srcOf(e).name + " (optional)" : "Derivation"),
    (() => {
      const fBC = fld(L.object + " (blank: the entity's" + (e.source_object ? ", " + e.source_object : "") + ")", inp("source_object", e.source_object || L.ph.object));
      const fSF = fld(L.field, inp("source_field", L.ph.field)), fSC = fld(L.location, inp("source_column", L.ph.location, true));
      const fDI = fld("Data item", inp("data_item", "Data dictionary: data item full name"));
      const fCL = fld(L.code_list, inp("lov_type", L.ph.code_list, true));
      const fTR = fld(sourced ? "Transformation" : "Derivation / rule", inp("transform", sourced ? "e.g. Y/N to true/false" : "How the value is worked out"));
      return h("div", null, h("div", { class: "grid" }, sourced ? [fBC, fSF, fSC, fDI, fCL, fTR] : [fTR]),
        dictOf(e) ? h("button", { class: "btn btn-sm", style: "margin-top:8px", onclick: () => { closeModal(); lookupModal(e, a); } }, "🔍 Look up in the dictionary…") : null);
    })(),
    h("div", { class: "section-label" }, "Enum values"), enumBox,
    h("div", { class: "section-label" }, "Description"), f.description);
  const saveBtn = h("button", { class: "btn btn-primary", onclick: async () => {
    const body = {};
    for (const k of ["business_name", "api_name", "dd_reference", "format", "source_field", "source_column", "lov_type", "transform", "example", "pattern"]) body[k] = f[k].value;
    if (f.source_object) { const bc = f.source_object.value.trim(); body.source_object = bc && bc.toLowerCase() !== (e.source_object || "").toLowerCase() ? bc : ""; }
    if (f.data_item) body.data_item = f.data_item.value;
    if (f.group_id) body.group_id = f.group_id.value;
    for (const k of ["key", "required", "read_only", "system", "pii", "money", "derived"]) body[k] = f[k].checked;
    body.filterable = !!f.filter.value; body.filter_required = f.filter.value === "required"; body.filter_match = f.match.value;
    body.type = f.type.value; body.max_length = f.max_length.value ? parseInt(f.max_length.value, 10) : null;
    body.description = f.description.value;
    body.enum = body.type === "enum" ? values.filter((v) => v.code || v.label).map((v) => ({ code: v.code || enumCode(v.label, enumStyle), label: v.label, source_value: v.source_value })) : [];
    try { await patch("api/attributes/" + a.id, body); closeModal(); } catch (err) { toast(err.message, true); }
  } }, "Save column");
  openModal(e.business_name + " › " + a.business_name, body, [h("button", { class: "btn", onclick: closeModal }, "Cancel"), saveBtn]);
}

function cardinalityText(r, f, t) {
  const many = (c, side) => (side === "from" ? ["many-to-one", "many-to-many"] : ["one-to-many", "many-to-many"]).includes(c);
  return (many(r.cardinality, "from") ? "many " : "one ") + f.business_name + " → " + (many(r.cardinality, "to") ? "many " : "one ") + t.business_name;
}
const exposeLabel = (x) => ({ none: "not exposed", link: "link", embedded: "embedded", subresource: "sub-resource" }[x] || x);

function linksCard(e) {
  const rels = M().relationships.filter((r) => r.from_entity_id === e.id || r.to_entity_id === e.id);
  const body = h("div");
  if (!rels.length) body.appendChild(h("div", { class: "empty" }, "No linked entities yet."));
  for (const r of rels) {
    const f = entById(r.from_entity_id), t = entById(r.to_entity_id);
    if (!f || !t) continue;
    const iss = issuesOf(r.id);
    const nav = (ent, name, exp, many, ops, keyId, target) => {
      if (!name) return null;
      const parts = [];
      if (exp === "link" || exp === "embedded") parts.push(h("span", null, h("code", null, ent.api_name + "." + name + (many ? "[]" : "")), " ", h("span", { class: "pill on" }, exposeLabel(exp))));
      if (ops && ops.length) parts.push(h("span", null, h("code", null, subPaths(ent, target, name, ops, keyId, many).join("  ·  ")), " ", h("span", { class: "pill on" }, "sub-resource")));
      return parts.length ? parts : null;
    };
    const toMany = ["one-to-many", "many-to-many"].includes(r.cardinality), fromMany = ["many-to-one", "many-to-many"].includes(r.cardinality);
    body.appendChild(h("div", { class: "link-row" },
      h("div", null,
        h("div", { class: "link-line" },
          h("button", { class: "link-btn ent", onclick: () => go("entity", f.id) }, f.business_name),
          h("span", { class: "card-txt" }, r.cardinality),
          h("button", { class: "link-btn ent", onclick: () => go("entity", t.id) }, t.business_name),
          r.business_name ? h("span", { class: "muted" }, "· " + r.business_name) : null),
        h("div", { class: "link-meta" },
          nav(f, r.api_name, r.expose, toMany, r.sub_ops, r.sub_key, t), nav(t, r.inverse_api_name, r.inverse_expose, fromMany, r.inverse_sub_ops, r.inverse_sub_key, f),
          r.read_only ? h("span", { class: "pill" }, "set by system") : null,
          embWrite(r, "from", f, t), embWrite(r, "to", t, f),
          (r.expose === "none" && r.inverse_expose === "none" && !(r.sub_ops || []).length && !(r.inverse_sub_ops || []).length) ? h("span", null, "Not exposed in the API (diagram and mapping only)") : null,
          (r.join_from || r.join_to) ? h("span", null, "Join ", h("code", null, (r.join_from || "?") + " = " + (r.join_to || "?"))) : null,
          r.source_link ? h("span", null, "Source link ", h("code", null, r.source_link)) : null),
        iss.length ? h("div", { style: "margin-top:6px" }, issueCallout(iss)) : null),
      h("div", { class: "row-actions" },
        h("button", { class: "btn btn-sm", onclick: () => linkModal(r) }, "Edit"),
        h("button", { class: "btn btn-sm btn-danger", onclick: async () => {
          if (!confirm("Remove this link?")) return;
          await api("DELETE", "api/relationships/" + r.id); await reload(); render(); toast("Link removed");
        } }, "Remove"))));
  }
  return h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h2", null, "Linked entities (" + rels.length + ")"),
      h("button", { class: "btn btn-primary btn-sm", onclick: () => linkModal(null, e.id) }, "+ Link an entity")),
    body);
}

// paths a link's sub-resource operations generate, e.g. GET/PUT /changes/{changeId}/declarations/{partyRole}
function subPaths(owner, target, name, ops, keyId, many) {
  const k = keyAttr(owner);
  const base = resourcePath(owner) + "/{" + (k ? k.api_name : "id") + "}/" + kebab(name);
  const out = [];
  if (many) {
    const coll = ops.filter((o) => o === "list" || o === "create").map((o) => (o === "list" ? "GET" : "POST"));
    if (coll.length) out.push(coll.join("/") + " " + base);
    const item = ops.filter((o) => ["get", "update", "delete"].includes(o)).map((o) => ({ get: "GET", update: "PUT", delete: "DELETE" }[o]));
    if (item.length) {
      const ka = (target && (target.attributes.find((a) => a.id === keyId) || keyAttr(target))) || null;
      out.push(item.join("/") + " " + base + "/{" + (ka ? ka.api_name : "key") + "}");
    }
  } else {
    const item = ops.filter((o) => ["get", "update", "delete"].includes(o)).map((o) => ({ get: "GET", update: "PUT", delete: "DELETE" }[o]));
    if (item.length) out.push(item.join("/") + " " + base);
  }
  return out;
}

function linkModal(r, fromId) {
  const isNew = !r;
  r = Object.assign({ from_entity_id: fromId, to_entity_id: (M().entities.find((x) => x.id !== fromId) || {}).id, cardinality: "many-to-one", business_name: "", api_name: "", expose: "link",
    inverse_business_name: "", inverse_api_name: "", inverse_expose: "none", join_from: "", join_to: "", source_link: "", description: "",
    sub_ops: [], sub_key: "", inverse_sub_ops: [], inverse_sub_key: "", read_only: false, nested_create: false }, r || {});
  const subState = { from: { ops: (r.sub_ops || []).slice(), key: r.sub_key || "" }, to: { ops: (r.inverse_sub_ops || []).slice(), key: r.inverse_sub_key || "" } };
  const entOpts = M().entities.map((x) => [x.id, x.business_name + "  (" + x.api_name + ")"]);
  const f = {
    from: selectEl(entOpts, r.from_entity_id), to: selectEl(entOpts, r.to_entity_id), card: selectEl(CARDS, r.cardinality),
    bn: h("input", { type: "text", value: r.business_name, placeholder: "e.g. Account holder" }), api: h("input", { type: "text", class: "mono", value: r.api_name, placeholder: "accountHolder" }),
    exp: selectEl(EXPOSE, r.expose),
    ibn: h("input", { type: "text", value: r.inverse_business_name, placeholder: "e.g. Cases as account holder" }), iapi: h("input", { type: "text", class: "mono", value: r.inverse_api_name, placeholder: "casesAsAccountHolder" }),
    iexp: selectEl(EXPOSE, r.inverse_expose),
    jf: h("input", { type: "text", value: r.join_from, placeholder: "From-side field, e.g. Case.accountHolderId" }), jt: h("input", { type: "text", value: r.join_to, placeholder: "To-side field, e.g. Contact.Id" }),
    sl: h("input", { type: "text", value: r.source_link, placeholder: labelsOf(entById(r.from_entity_id)).ph.link || "Optional" }), desc: h("textarea", null),
  };
  f.desc.value = r.description;
  f.ro = h("input", { type: "checkbox", checked: r.read_only });
  f.nested = h("input", { type: "checkbox", checked: r.nested_create, "data-k": "rel-nested" });
  f.inested = h("input", { type: "checkbox", checked: !!r.inverse_nested_create, "data-k": "rel-inested" });
  // switching a side to embedded offers its objects in request bodies too (untick to keep them read-only)
  let wasEmb = r.expose === "embedded", wasIEmb = r.inverse_expose === "embedded";
  const isMany = (side) => (side === "from" ? ["one-to-many", "many-to-many"] : ["many-to-one", "many-to-many"]).includes(f.card.value);
  const subBox = (side) => {
    const wrap = h("div", { style: "margin-top:8px" });
    const draw = () => {
      wrap.innerHTML = "";
      const target = entById(side === "from" ? f.to.value : f.from.value);
      const many = isMany(side), st = subState[side];
      const opts = many ? SUB_OPS : SUB_OPS.filter(([o]) => ["get", "update", "delete"].includes(o));
      st.ops = st.ops.filter((o) => opts.some(([x]) => x === o));
      wrap.appendChild(h("div", { class: "small muted", style: "margin-bottom:4px" }, "Sub-resource paths under the owning record"));
      wrap.appendChild(h("div", { class: "checks" }, opts.map(([o, l]) => {
        const cb = h("input", { type: "checkbox", checked: st.ops.includes(o) });
        cb.onchange = () => { st.ops = cb.checked ? st.ops.concat([o]) : st.ops.filter((x) => x !== o); draw(); preview(); };
        return h("label", null, cb, l);
      })));
      if (many && target && st.ops.some((o) => ["get", "update", "delete"].includes(o))) {
        const tk = keyAttr(target);
        const sel = selectEl([["", "Key column" + (tk ? " (" + tk.api_name + ")" : ": none, pick one")]].concat(target.attributes.map((a) => [a.id, a.api_name + "  (" + a.business_name + ")"])), st.key);
        sel.onchange = () => { st.key = sel.value; preview(); };
        wrap.appendChild(h("div", { class: "field", style: "margin-top:8px;max-width:340px" }, h("label", null, "Path key for one item"), sel,
          h("div", { class: "help" }, "e.g. partyRole gives …/declarations/{partyRole}")));
      }
    };
    return { wrap, draw };
  };
  const subFrom = subBox("from"), subTo = subBox("to");
  let apiTouched = !!r.api_name, iapiTouched = !!r.inverse_api_name;
  f.bn.oninput = () => { if (!apiTouched) f.api.value = camel(f.bn.value); preview(); };
  f.api.oninput = () => { apiTouched = !!f.api.value; preview(); };
  f.ibn.oninput = () => { if (!iapiTouched) f.iapi.value = camel(f.ibn.value); preview(); };
  f.iapi.oninput = () => { iapiTouched = !!f.iapi.value; preview(); };
  const prev = h("div", { class: "side-box small" });
  const preview = () => {
    const fe = entById(f.from.value), te = entById(f.to.value); if (!fe || !te) return;
    const c = f.card.value, toMany = ["one-to-many", "many-to-many"].includes(c), fromMany = ["many-to-one", "many-to-many"].includes(c);
    const line = (ent, name, exp, many, target, side) => {
      const ops = subState[side].ops;
      if (exp === "none" && !ops.length) return h("div", { class: "muted" }, ent.business_name + ": not exposed");
      if (!name) return h("div", { class: "lvl-error" }, ent.business_name + ": needs an API name");
      const out = h("div");
      if (exp === "link") out.appendChild(h("div", null, h("code", null, ent.api_name + "." + name), " = " + (many ? "[ { id, href } ]" : "{ id, href }") + " → " + target.schema_name + (side === "from" && f.ro.checked ? " (set by the system)" : "")));
      if (exp === "embedded") out.appendChild(h("div", null, h("code", null, ent.api_name + "." + name), " = " + (many ? "[ " + target.schema_name + " ]" : target.schema_name)
        + ((side === "from" ? f.nested : f.inested).checked ? " in GET, POST, PUT and PATCH" : " in GET only (read-only)")));
      for (const p of subPaths(ent, target, name, ops, subState[side].key, many)) out.appendChild(h("div", null, h("code", null, p), " → " + target.schema_name));
      return out;
    };
    prev.innerHTML = "";
    prev.appendChild(h("div", { style: "font-weight:600;margin-bottom:4px" }, cardinalityText({ cardinality: c }, fe, te)));
    prev.appendChild(line(fe, f.api.value, f.exp.value, toMany, te, "from"));
    prev.appendChild(line(te, f.iapi.value, f.iexp.value, fromMany, fe, "to"));
  };
  [f.from, f.to, f.card].forEach((x) => (x.onchange = () => { subFrom.draw(); subTo.draw(); preview(); }));
  [f.ro, f.nested, f.inested].forEach((x) => (x.onchange = preview));
  const syncEmb = () => {
    const emb = f.exp.value === "embedded", iemb = f.iexp.value === "embedded";
    if (emb && !wasEmb) f.nested.checked = true;
    if (iemb && !wasIEmb) f.inested.checked = true;
    wasEmb = emb; wasIEmb = iemb;
    f.nested.disabled = !emb; f.inested.disabled = !iemb;
    preview();
  };
  f.exp.onchange = syncEmb; f.iexp.onchange = syncEmb;
  f.nested.disabled = r.expose !== "embedded"; f.inested.disabled = r.inverse_expose !== "embedded";
  subFrom.draw(); subTo.draw();
  const fld = (label, el, help) => h("div", { class: "field" }, h("label", null, label), el, help ? h("div", { class: "help" }, help) : null);
  const body = h("div", null,
    h("div", { class: "grid" }, fld("From entity", f.from), fld("Cardinality", f.card, "From side → to side"), fld("To entity", f.to)),
    h("div", { class: "section-label" }, "From side (how the 'from' entity shows the link)"),
    h("div", { class: "grid" }, fld("Business name", f.bn), fld("API name", f.api), fld("Exposed as", f.exp)),
    h("div", { class: "checks", style: "margin-top:8px" },
      h("label", { title: "For a link: the system sets it; it never appears in request bodies" }, f.ro, "Set by the system (link)"),
      h("label", { title: "Embedded objects can be sent in POST, PUT and PATCH bodies, not only returned by GET" }, f.nested, "Accept in POST, PUT and PATCH (embedded)")),
    subFrom.wrap,
    h("div", { class: "section-label" }, "Other side (optional reverse navigation)"),
    h("div", { class: "grid" }, fld("Business name", f.ibn), fld("API name", f.iapi), fld("Exposed as", f.iexp)),
    h("div", { class: "checks", style: "margin-top:8px" },
      h("label", { title: "Embedded objects can be sent in POST, PUT and PATCH bodies, not only returned by GET" }, f.inested, "Accept in POST, PUT and PATCH (embedded)")),
    subTo.wrap,
    h("div", { class: "section-label" }, "Source mapping"),
    h("div", { class: "grid" }, fld("Join from", f.jf), fld("Join to", f.jt), fld("Source link", f.sl)),
    h("div", { class: "field", style: "margin-top:10px" }, h("label", null, "Description"), f.desc),
    h("div", { class: "section-label" }, "What the API gets"), prev);
  preview();
  const saveBtn = h("button", { class: "btn btn-primary", onclick: async () => {
    const payload = { from_entity_id: f.from.value, to_entity_id: f.to.value, cardinality: f.card.value, business_name: f.bn.value, api_name: f.api.value, expose: f.exp.value,
      inverse_business_name: f.ibn.value, inverse_api_name: f.iapi.value, inverse_expose: f.iexp.value, join_from: f.jf.value, join_to: f.jt.value, source_link: f.sl.value, description: f.desc.value,
      sub_ops: subState.from.ops, sub_key: subState.from.key, inverse_sub_ops: subState.to.ops, inverse_sub_key: subState.to.key,
      read_only: f.ro.checked, nested_create: f.nested.checked, inverse_nested_create: f.inested.checked };
    try {
      if (isNew) await api("POST", "api/relationships", payload); else await api("PATCH", "api/relationships/" + r.id, payload);
      await reload(); closeModal(); render(); toast(isNew ? "Link added" : "Link saved");
    } catch (err) { toast(err.message, true); }
  } }, isNew ? "Add link" : "Save link");
  openModal(isNew ? "Link an entity" : "Edit link", body, [h("button", { class: "btn", onclick: closeModal }, "Cancel"), saveBtn]);
}

const KIND_LABEL = { operation: "", "sub-resource": "sub-resource", action: "action" };
const STATUS_TEXT = { "200": "200 OK", "201": "201 Created", "202": "202 Accepted", "204": "204 No content" };
const SUB_OPS = [["list", "GET list"], ["create", "POST add"], ["get", "GET one"], ["update", "PUT replace"], ["delete", "DELETE remove"]];
const ERRORS = [["BadRequest", "400"], ["NotFound", "404"], ["Conflict", "409"], ["PreconditionFailed", "412"], ["ValidationFailed", "422"]];

function previewCard(e, n) {
  const eps = h("div", { class: "table-wrap" }, h("div", { class: "empty" }, "Loading…"));
  const ex = h("pre", { class: "code", style: "max-height:420px" }, "");
  const wrap = h("div");
  wrap.appendChild(h("div", { class: "card" },
    h("div", { class: "card-head" }, h("div", null, h("h2", null, "API operations"),
      h("div", { class: "hint" }, "Everything this entity's settings generate: its own operations, sub-resources from its links, and actions. Edit to change a summary, request, response or status.")),
      h("div", { class: "row-actions" },
        h("button", { class: "btn btn-primary btn-sm", onclick: () => actionModal(e, null) }, "+ Add action"),
        n ? h("button", { class: "btn btn-sm", onclick: () => { S.apiv.ns = n.key; go("api"); } }, "Full definition") : null)),
    eps));
  wrap.appendChild(h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h2", null, "Example " + e.schema_name), h("span", { class: "hint" }, "From the column examples")), ex));
  if (!n) return wrap;
  Promise.all([api("GET", "api/openapi/" + n.key + ".json?view=full"), api("GET", "api/endpoints")]).then(([doc, all]) => {
    const sch = doc.components.schemas[e.schema_name];
    ex.innerHTML = highlightJson(JSON.stringify(sch ? sch.example : {}, null, 2));
    const mine = all.filter((x) => x.entity_id === e.id);
    eps.innerHTML = "";
    if (e.operations.length && !e.operations.includes("list") && e.get_by !== "query" && !isEmbedded(e))
      eps.appendChild(h("div", { class: "callout info", style: "margin:10px 14px" }, "No GET list: ",
        h("code", null, "GET " + resourcePath(e)), " needs ", h("b", null, "List"), " ticked under Operations. (An action always adds its own path segment, so it cannot be the list.) ",
        h("button", { class: "btn btn-sm", "data-k": "tick-list", onclick: () => patch("api/entities/" + e.id, { operations: OPS.map((o) => o[0]).filter((o) => o === "list" || e.operations.includes(o)) }) }, "Tick List")));
    eps.appendChild(mine.length ? operationsTable(e, mine) : h("div", { class: "empty" }, e.operations.length ? "No operations generated." : isEmbedded(e) ? "No operations: " + e.business_name + " only appears inside linked entities." : "No operations yet: schema only. Tick operations above, add a sub-resource on a link, or add an action."));
  }).catch((err) => { eps.innerHTML = ""; eps.appendChild(h("div", { class: "empty lvl-error" }, err.message)); });
  return wrap;
}

function endpointTable(list) {
  return h("table", null, h("thead", null, h("tr", null, ["Method", "Path", "Operation", "Request", "Returns", "Status"].map((x) => h("th", null, x)))),
    h("tbody", null, list.map((x) => h("tr", null, h("td", null, h("span", { class: "method m-" + x.method }, x.method)), h("td", { class: "mono" }, x.path),
      h("td", { class: "mono small" }, x.operation_id), h("td", { class: "mono small muted" }, x.request || "—"),
      h("td", { class: "mono small muted" }, x.response || "—"), h("td", { class: "mono small" }, x.status || "")))));
}

function overrideOf(row) {
  if (row.kind === "operation") return (entById(row.entity_id).op_overrides || {})[row.op] || {};
  if (row.kind === "sub-resource") {
    const r = M().relationships.find((x) => x.id === row.rel_id) || {};
    return ((row.side === "from" ? r.sub_overrides : r.inverse_sub_overrides) || {})[row.op] || {};
  }
  return {};
}

function operationsTable(e, rows) {
  return h("table", null, h("thead", null, h("tr", null, ["Method", "Path", "Summary", "Request", "Response", "Status", ""].map((x) => h("th", null, x)))),
    h("tbody", null, rows.map((x) => {
      const ov = overrideOf(x);
      const changed = Object.values(ov).some(Boolean);
      return h("tr", null,
        h("td", null, h("span", { class: "method m-" + x.method }, x.method)),
        h("td", { class: "mono small" }, x.path, KIND_LABEL[x.kind] ? h("span", { class: "pill", style: "margin-left:6px" }, KIND_LABEL[x.kind]) : null,
          x.query ? h("div", { class: "muted", style: "margin-top:2px" }, "?" + x.query.split(", ").join("  ·  ")) : null,
          x.header_params ? h("div", { class: "muted", style: "margin-top:2px" }, "header: " + x.header_params.split(", ").join("  ·  ")) : null),
        h("td", { class: "small" }, x.summary, changed ? h("span", { class: "pill on", style: "margin-left:6px", title: "Has custom settings" }, "custom") : null),
        h("td", { class: "mono small muted" }, x.request || "—"),
        h("td", { class: "mono small muted" }, x.response || "—"),
        h("td", { class: "mono small" }, x.status),
        h("td", { style: "white-space:nowrap" },
          x.kind === "action"
            ? h("button", { class: "btn btn-sm", onclick: () => actionModal(e, (e.actions || []).find((a) => a.id === x.action_id)) }, "Edit")
            : h("button", { class: "btn btn-sm", onclick: () => overrideModal(x) }, "Edit")));
    })));
}

// for an embedded side: whether POST/PUT/PATCH accept it, with a one-click switch
function embWrite(r, side, owner, target) {
  const emb = side === "from" ? r.expose === "embedded" : r.inverse_expose === "embedded";
  if (!emb) return null;
  const on = side === "from" ? !!r.nested_create : !!r.inverse_nested_create;
  const key = side === "from" ? "nested_create" : "inverse_nested_create";
  const name = side === "from" ? r.api_name : r.inverse_api_name;
  return h("button", { class: "pill" + (on ? " on" : ""), style: "border:0;cursor:pointer", "data-k": "emb-" + r.id + "-" + side,
    title: on ? "Click to make " + owner.api_name + "." + name + " read-only (GET only)" : "Click to accept " + owner.api_name + "." + name + " in POST, PUT and PATCH",
    onclick: async () => { try { await patch("api/relationships/" + r.id, { [key]: !on }, true); toast(on ? name + ": GET only" : name + ": accepted in POST, PUT and PATCH"); } catch (err) { toast(err.message, true); } } },
    owner.api_name + "." + name + (on ? ": in POST/PUT/PATCH" : ": GET only, click to accept in POST/PUT"));
}

function entityOptions(defaultLabel, extra) {
  return [["", defaultLabel]].concat(extra || [], M().entities.map((x) => [x.id, x.schema_name + "  (" + x.business_name + ")"]));
}

const MATCHES = [["equals", "Equals: ?x=value"], ["in", "One of several: ?x=a,b"], ["range", "Range: ?xFrom=&xTo="], ["contains", "Contains text: ?x=text"]];
function matchExample(api, m) {
  return { equals: "?" + api + "=value", in: "?" + api + "=A,B", range: "?" + api + "From=…&" + api + "To=…", contains: "?" + api + "=text" }[m] || "";
}
function columnFilterSpecs(ent) {
  return (ent ? ent.attributes : []).filter((a) => a.filterable).map((a) => ({ attr_id: a.id, required: !!a.filter_required, match: a.filter_match || "equals", name: "" }));
}
// edits a list of query parameters over an entity's columns. initial null = use defaults (columns' filters for lists)
// write: parameters of a POST/PUT/PATCH/DELETE: no Match, an In column (query or header)
// write may be a function (an action whose method changes); extra() lists more entities whose columns can be used
function queryEditor(initial, ent, isList, allowDefault, keyMode, writeArg, extra) {
  const isWriteNow = () => (typeof writeArg === "function" ? writeArg() : !!writeArg);
  const keyId = ent && keyAttr(ent) ? keyAttr(ent).id : null;
  const keyFixed = () => (keyMode && keyId ? keyMode() : null);   // "path" or "query": the key is not a free choice
  const allAttrs = {}; M().entities.forEach((x) => x.attributes.forEach((a) => (allAttrs[a.id] = { ent: x, a })));
  const useDefault = h("input", { type: "checkbox", checked: allowDefault && initial === null });
  const start = initial === null || initial === undefined ? (isList ? columnFilterSpecs(ent) : []) : initial;
  const state = {}; start.forEach((sp) => (state[sp.attr_id] = { use: sp.required ? "required" : "optional", match: sp.match || "equals", name: sp.name || "", loc: sp.location || "query" }));
  const box = h("div");
  const draw = () => {
    box.innerHTML = "";
    if (allowDefault && useDefault.checked) {
      const d = isList ? columnFilterSpecs(ent) : [];
      box.appendChild(h("div", { class: "muted small" }, d.length ? "From the columns: " + d.map((sp) => allAttrs[sp.attr_id].a.api_name + (sp.required ? " (required)" : "") + (sp.match !== "equals" ? " (" + sp.match + ")" : "")).join(", ")
        : isList ? "No columns are marked as filters." : "None."));
      return;
    }
    const write = isWriteNow();
    const more = (extra ? extra() : []).filter((x) => x && (!ent || x.id !== ent.id));
    const ids0 = (ent ? ent.attributes.map((a) => a.id) : []).concat(...more.map((x) => x.attributes.map((a) => a.id)));
    const ids = ids0.concat(Object.keys(state).filter((id) => !ids0.includes(id)));
    const rows = ids.filter((id) => allAttrs[id]).map((id) => {
      const { ent: owner, a } = allAttrs[id];
      if (id === keyId && keyFixed()) {
        return h("tr", null, h("td", { class: "mono small" }, a.api_name), h("td", { class: "small muted" }, a.type),
          h("td", { colspan: 3, class: "small muted" }, keyFixed() === "path" ? "In the path: /{" + a.api_name + "}. Set Get one by to Query to send it as ?" + a.api_name + "= instead."
            : "Required: ?" + a.api_name + "= (Get one by: Query)"));
      }
      const st = state[id] || { use: "", match: a.filter_match || "equals", name: "", loc: "query" };
      const use = selectEl([["", "—"], ["optional", "Optional"], ["required", "Required"]], st.use, { "data-k": "qp-" + a.api_name });
      const match = selectEl(MATCHES.map(([k]) => [k, k]), write ? "equals" : st.match);
      const loc = selectEl([["query", "Query ?" + a.api_name + "="], ["header", "Header"]], st.loc || "query", { "data-k": "qloc-" + a.api_name });
      const name = h("input", { type: "text", class: "mono", value: st.name, placeholder: a.api_name, style: "width:140px" });
      const upd = () => { if (use.value) state[id] = { use: use.value, match: write ? (state[id] || st).match || "equals" : match.value, name: name.value.trim(), loc: loc.value }; else delete state[id]; match.disabled = name.disabled = loc.disabled = !use.value; };
      use.onchange = upd; match.onchange = upd; name.oninput = upd; loc.onchange = upd; match.disabled = name.disabled = loc.disabled = !st.use;
      const inBody = write && a.read_only ? h("span", { class: "muted small", title: "Read-only: never in the body anyway" }, " (read-only)") : null;
      return h("tr", null, h("td", { class: "mono small" }, (ent && owner.id === ent.id ? "" : owner.schema_name + ".") + a.api_name, inBody), h("td", { class: "small muted" }, a.type),
        h("td", null, use), h("td", null, write ? loc : match), h("td", null, name));
    });
    box.appendChild(h("div", { class: "table-wrap", style: "max-height:260px" }, h("table", { class: "cols-table", style: "min-width:0" },
      h("thead", null, h("tr", null, ["Column", "Type", write ? "Parameter" : "Query parameter", write ? "Sent in" : "Match", "Name"].map((x) => h("th", null, x)))), h("tbody", null, rows))));
  };
  useDefault.onchange = () => { if (!useDefault.checked && !Object.keys(state).length && isList) columnFilterSpecs(ent).forEach((sp) => (state[sp.attr_id] = { use: sp.required ? "required" : "optional", match: sp.match, name: "" })); draw(); };
  draw();
  const wrap = h("div", null, allowDefault ? h("div", { class: "checks", style: "margin-bottom:6px" }, h("label", null, useDefault, isList ? "Use the columns' filter settings" : isWriteNow() ? "No parameters: everything in the body" : "No query parameters")) : null, box);
  return { wrap, redraw: draw, value: () => (allowDefault && useDefault.checked ? null
    : Object.entries(state).filter(([attr_id]) => !(attr_id === keyId && keyFixed()))
      .map(([attr_id, v]) => ({ attr_id, required: v.use === "required", match: v.match, name: v.name, location: v.loc || "query" }))) };
}

// what a response body holds: whole record, only its key (row id), chosen fields, or nothing
const CONTENT = [["full", "Full record"], ["key", "Key only (row id)"], ["fields", "Selected fields"], ["none", "No body"]];
function defaultContent(row) {
  const st = M().settings || {};
  if (row.op === "create" && row.method === "POST") return st.post_response || "key";
  if (row.op === "update" && row.method === "PUT") return st.put_response || "full";
  return "full";
}
function contentLabel(mode) { return (CONTENT.find((c) => c[0] === mode) || ["", mode])[1]; }
// a content picker plus field checkboxes for an entity chosen elsewhere in the dialog
function contentPicker(initialMode, initialFields, entityOf, defaultLabel) {
  const opts = (defaultLabel ? [["", "Default: " + defaultLabel]] : []).concat(CONTENT);
  const sel = selectEl(opts, initialMode || "");
  let fields = (initialFields || []).slice();
  const box = h("div", { style: "margin-top:6px" });
  const draw = () => {
    box.innerHTML = "";
    if (sel.value !== "fields") return;
    const ent = entityOf();
    if (!ent) { box.appendChild(h("div", { class: "muted small" }, "Pick a response entity first.")); return; }
    box.appendChild(h("div", { class: "checks" }, ent.attributes.map((a) => {
      const cb = h("input", { type: "checkbox", checked: fields.includes(a.id) });
      cb.onchange = () => { fields = cb.checked ? fields.concat([a.id]) : fields.filter((x) => x !== a.id); };
      return h("label", null, cb, h("span", { class: "mono small" }, a.api_name), a.key ? h("span", { class: "key-mark", style: "margin-left:4px" }, "PK") : null);
    })));
  };
  sel.addEventListener("change", draw); draw();
  return { sel, box, draw, value: () => ({ mode: sel.value, fields: sel.value === "fields" ? fields : [] }) };
}

// columns of the request entity to send in the body, each optionally required
function requestFieldsPicker(fields0, required0, entityOf) {
  let fields = (fields0 || []).slice(), required = (required0 || []).slice();
  const box = h("div", { style: "margin-top:6px" });
  const draw = (show) => {
    box.innerHTML = "";
    if (!show) return;
    const ent = entityOf();
    if (!ent) { box.appendChild(h("div", { class: "muted small" }, "Pick the request entity first.")); return; }
    box.appendChild(h("div", { class: "table-wrap", style: "max-height:220px" }, h("table", { class: "cols-table", style: "min-width:0" },
      h("thead", null, h("tr", null, ["Send", "Column", "Required"].map((x) => h("th", null, x)))),
      h("tbody", null, ent.attributes.map((a) => {
        const cb = h("input", { type: "checkbox", checked: fields.includes(a.id), "data-k": "rf-" + a.api_name });
        const rq = h("input", { type: "checkbox", checked: required.includes(a.id), disabled: !fields.includes(a.id), "data-k": "rq-" + a.api_name });
        cb.onchange = () => { fields = cb.checked ? fields.concat([a.id]) : fields.filter((x) => x !== a.id); if (!cb.checked) { required = required.filter((x) => x !== a.id); rq.checked = false; } rq.disabled = !cb.checked; };
        rq.onchange = () => { required = rq.checked ? required.concat([a.id]) : required.filter((x) => x !== a.id); };
        return h("tr", { class: a.read_only ? "muted" : null, title: a.read_only ? "Read only: normally never in a request" : null },
          h("td", { class: "c" }, cb), h("td", null, a.business_name, " ", h("code", { class: "small" }, a.api_name)), h("td", { class: "c" }, rq));
      })))));
  };
  return { box, draw, value: () => ({ fields: fields.slice(), required: required.filter((x) => fields.includes(x)) }) };
}

function overrideModal(row) {
  const ov = overrideOf(row);
  const hasBody = ["POST", "PUT", "PATCH"].includes(row.method);
  const owner = entById(row.entity_id);
  const rel = row.kind === "sub-resource" ? M().relationships.find((x) => x.id === row.rel_id) : null;
  const defaultEnt = rel ? entById(row.side === "from" ? rel.to_entity_id : rel.from_entity_id) : owner;
  const isList = row.method === "GET" && row.op === "list" && ["operation", "sub-resource"].includes(row.kind);
  const listDefault = (M().settings || {}).list_response || "page";
  const f = {
    summary: h("input", { type: "text", value: ov.summary || "", placeholder: row.summary }),
    opid: h("input", { type: "text", class: "mono", value: ov.operation_id || "", placeholder: row.operation_id, "data-k": "op-id" }),
    desc: h("textarea", { placeholder: "What this operation does, rules, when it returns an error…" }),
    req: selectEl(entityOptions("Default"), ov.request_entity_id || ""),
    resp: selectEl(entityOptions("Default (" + (defaultEnt ? defaultEnt.schema_name : "") + ")"), ov.response_entity_id === "none" ? "" : (ov.response_entity_id || "")),
    status: selectEl([["", "Default"]].concat(Object.entries(STATUS_TEXT)), ov.status || ""),
  };
  f.desc.value = ov.description || "";
  const listEnt = () => entById(f.resp.value) || defaultEnt;
  const LIST_SHAPE = { page: "Page: { items, nextCursor }, paged", array: "Array: [ … ], no paging",
    wrapped: "Object: { " + (listEnt() ? listEnt().api_name : "name") + ": [ … ] }" };
  const shapeSel = selectEl([["", "Default: " + LIST_SHAPE[listDefault]], ["page", LIST_SHAPE.page], ["array", LIST_SHAPE.array], ["wrapped", LIST_SHAPE.wrapped]],
    ["page", "array", "wrapped"].includes(ov.response_mode) ? ov.response_mode : "", { "data-k": "list-shape" });
  const propIn = h("input", { type: "text", class: "mono", value: ov.list_property || "", placeholder: listEnt() ? listEnt().api_name : "", "data-k": "list-property", style: "margin-top:6px" });
  const shapeHelp = h("div", { class: "help" });
  const syncShape = () => {
    const shape = shapeSel.value || listDefault, nm = propIn.value.trim() || (listEnt() ? listEnt().api_name : "items");
    propIn.style.display = shape === "wrapped" ? "" : "none";
    shapeHelp.textContent = shape === "wrapped" ? "Returns { \"" + nm + "\": [ … ] }. Property name (blank: " + (listEnt() ? listEnt().api_name : "") + ")"
      : shape === "array" ? "Returns [ … ]: every match in one response" : "Returns { \"items\": [ … ], \"nextCursor\": … } with limit and cursor";
  };
  shapeSel.addEventListener("change", syncShape); propIn.addEventListener("input", syncShape); f.resp.addEventListener("change", syncShape); syncShape();
  const reqMode = selectEl([["", "Its create form (PATCH: update form)"], ["fields", "Selected fields only"]], ov.request_mode || "", { "data-k": "req-mode" });
  const reqPick = requestFieldsPicker(ov.request_fields, ov.request_required, () => entById(f.req.value) || defaultEnt);
  reqMode.addEventListener("change", () => reqPick.draw(reqMode.value === "fields"));
  f.req.addEventListener("change", () => reqPick.draw(reqMode.value === "fields"));
  reqPick.draw(reqMode.value === "fields");
  const picker = contentPicker(ov.response_entity_id === "none" ? "none" : (ov.response_mode || ""), ov.response_fields || [],
    () => entById(f.resp.value) || defaultEnt, contentLabel(defaultContent(row)));
  f.resp.addEventListener("change", picker.draw);
  const isGet = row.method === "GET" && ["list", "get"].includes(row.op);
  // reading one record of the entity itself: choose path /things/{id} or query /things?id=
  const getOne = row.kind === "operation" && owner && keyAttr(owner)
    && (row.op === "get" || (row.op === "list" && owner.operations.includes("get") && owner.get_by === "query"));
  const kname = getOne ? keyAttr(owner).api_name : "";
  const getBy = getOne ? selectEl([["path", "Path: " + row.path.replace(/\?.*$/, "").replace(/\/\{[^}]+\}$/, "") + "/{" + kname + "}"],
    ["query", "Query: " + row.path.replace(/\?.*$/, "").replace(/\/\{[^}]+\}$/, "") + "?" + kname + "="]], owner.get_by || "path") : null;
  const dropList = h("input", { type: "checkbox", checked: false });
  const getByHelp = h("div", { class: "help" });
  const isWrite = ["POST", "PUT", "PATCH", "DELETE"].includes(row.method) && ["operation", "sub-resource"].includes(row.kind);
  const qed = isGet ? queryEditor(ov.query_params === undefined ? null : ov.query_params, defaultEnt, row.op === "list", true,
    getOne && row.op === "get" ? () => getBy.value : null)
    : isWrite ? queryEditor(ov.query_params === undefined || (ov.query_params && !ov.query_params.length) ? null : ov.query_params,
      entById(ov.request_entity_id) || defaultEnt, false, true, null, true) : null;
  const listOn = getOne && row.op === "get" && owner.operations.includes("list");
  const syncGetBy = () => {
    if (!getOne) return;
    getByHelp.innerHTML = "";
    if (getBy.value === "query" && listOn) {
      getByHelp.appendChild(h("div", null, "List is ticked, so this is the list's GET with ?" + kname + "= and it returns a page holding that record."));
      getByHelp.appendChild(h("label", { class: "checks", style: "margin-top:4px" }, dropList, "Untick List, so this GET returns the record itself"));
    } else if (row.op === "list") getByHelp.appendChild(h("div", null, getBy.value === "query" ? "One " + owner.business_name.toLowerCase() + " is read through this list with ?" + kname + "=." : "Adds GET …/{" + kname + "} for one record."));
    else if (getBy.value === "query") getByHelp.appendChild(h("div", null, "Removes /{" + kname + "} from the GET. PUT, PATCH and DELETE keep it in the path."));
    if (qed) qed.redraw();
  };
  if (getBy) { getBy.addEventListener("change", syncGetBy); syncGetBy(); }
  const fld = (l, el, help) => h("div", { class: "field" }, h("label", null, l), el, help ? h("div", { class: "help" }, help) : null);
  const body = h("div", null,
    h("div", { class: "side-box small", style: "margin-bottom:12px" }, h("span", { class: "method m-" + row.method }, row.method), " ", h("code", null, row.path),
      h("div", { class: "muted", style: "margin-top:4px" }, "Now: request " + (row.request || "none") + " · response " + (row.response || "none") + " · " + (STATUS_TEXT[row.status] || row.status))),
    getOne ? h("div", { class: "field", style: "margin-bottom:10px" }, h("label", null, "Get one by"), getBy, getByHelp) : null,
    h("div", { class: "grid" }, fld("Summary", f.summary), fld("Status", f.status),
      fld("Operation ID", f.opid, "Name of the operation in the OpenAPI and in generated client code. Blank: " + row.operation_id)),
    h("div", { class: "field", style: "margin-top:10px" }, h("label", null, "Description"), f.desc),
    h("div", { class: "section-label" }, "Request and response"),
    h("div", { class: "grid" },
      hasBody ? fld("Request body", f.req, "Another entity's create form, e.g. a decision") : null,
      hasBody ? h("div", { class: "field" }, h("label", null, "Request content"), reqMode) : null,
      hasBody ? h("div", { class: "field wide" }, reqPick.box) : null,
      fld("Response entity", f.resp),
      isList ? h("div", { class: "field" }, h("label", null, "Response shape"), shapeSel, propIn, shapeHelp)
        : h("div", { class: "field" }, h("label", null, "Response content"), picker.sel, picker.box,
        h("div", { class: "help" }, "POST normally returns the row id of the created record"))),
    qed ? h("div", null, h("div", { class: "section-label" }, isWrite ? "Parameters instead of the body" : "Query parameters"),
      isWrite ? h("div", { class: "muted small", style: "margin:-2px 0 6px" }, "Columns marked here are sent as query or header parameters and left out of the request body, e.g. "
        + row.method + " " + row.path.replace(/\?.*$/, "") + "?" + ((entById(ov.request_entity_id) || defaultEnt || { attributes: [] }).attributes.find((a) => !a.key) || { api_name: "personId" }).api_name + "=…") : null,
      qed.wrap) : null);
  const save = async (clear) => {
    const c = picker.value();
    const next = clear ? {} : { operation_id: f.opid.value.trim(), summary: f.summary.value.trim(), description: f.desc.value.trim(), request_entity_id: hasBody ? f.req.value : "",
      response_entity_id: f.resp.value, response_mode: isList ? shapeSel.value : c.mode, response_fields: isList ? [] : c.fields, status: f.status.value };
    if (isList && !clear && shapeSel.value === "wrapped" && propIn.value.trim()) next.list_property = propIn.value.trim();
    if (hasBody && !clear && reqMode.value === "fields") { const rv = reqPick.value(); next.request_mode = "fields"; next.request_fields = rv.fields; next.request_required = rv.required; }
    if (qed && !clear) { next.query_params = qed.value(); if ((row.op === "get" || isWrite) && next.query_params && !next.query_params.length) next.query_params = null; }
    const empty = !Object.entries(next).some(([k, v]) => (k === "query_params" ? v !== null && v !== undefined : Array.isArray(v) ? v.length : Boolean(v)));
    try {
      if (row.kind === "operation") {
        const all = Object.assign({}, entById(row.entity_id).op_overrides || {});
        if (empty) delete all[row.op]; else all[row.op] = next;
        const patch = { op_overrides: all };
        if (getOne && getBy.value !== (owner.get_by || "path")) patch.get_by = getBy.value;
        if (getOne && getBy.value === "query" && listOn && dropList.checked) patch.operations = owner.operations.filter((o) => o !== "list");
        await api("PATCH", "api/entities/" + row.entity_id, patch);
      } else {
        const r = M().relationships.find((x) => x.id === row.rel_id);
        const key = row.side === "from" ? "sub_overrides" : "inverse_sub_overrides";
        const all = Object.assign({}, r[key] || {});
        if (empty) delete all[row.op]; else all[row.op] = next;
        await api("PATCH", "api/relationships/" + r.id, { [key]: all });
      }
      await reload(); closeModal(); render(); toast(empty ? "Back to defaults" : "Operation saved");
    } catch (err) { toast(err.message, true); }
  };
  openModal("Operation settings", body, [
    h("button", { class: "btn", style: "margin-right:auto", onclick: () => save(true) }, "Reset to default"),
    h("button", { class: "btn", onclick: closeModal }, "Cancel"),
    h("button", { class: "btn btn-primary", onclick: () => save(false) }, "Save")]);
}

function actionModal(e, act) {
  const isNew = !act;
  act = Object.assign({ name: "", method: "POST", scope: "item", path: "", summary: "", description: "", request_entity_id: "", request_variant: "create",
    response_entity_id: "self", status: "200", if_match: false, errors: ["NotFound", "Conflict", "ValidationFailed"] }, act || {});
  const f = {
    name: h("input", { type: "text", class: "mono", value: act.name, placeholder: "e.g. decision" }),
    path: h("input", { type: "text", class: "mono", value: act.path, placeholder: "e.g. decision" }),
    opid: h("input", { type: "text", class: "mono", value: act.operation_id || "", "data-k": "act-op-id" }),
    method: selectEl(["POST", "PUT", "PATCH", "GET", "DELETE"].map((x) => [x, x]), act.method),
    scope: selectEl([["item", "One record: …/{id}/name"], ["collection", "The collection: …/name"]], act.scope),
    summary: h("input", { type: "text", value: act.summary, placeholder: "e.g. Record the decision on a change" }),
    desc: h("textarea", null),
    req: selectEl(entityOptions("No body"), act.request_entity_id || ""),
    variant: selectEl([["create", "Its create form"], ["update", "Its update form (merge patch)"], ["full", "The full schema"], ["fields", "Selected fields only"]], act.request_variant || "create", { "data-k": "act-variant" }),
    resp: selectEl([["self", "This entity (" + e.schema_name + ")"], ["", "No body"]].concat(M().entities.filter((x) => x.id !== e.id).map((x) => [x.id, x.schema_name + "  (" + x.business_name + ")"])), act.response_entity_id),
    status: selectEl(Object.entries(STATUS_TEXT), act.status),
    ifm: h("input", { type: "checkbox", checked: act.if_match }),
  };
  const actReq = requestFieldsPicker(act.request_fields, act.request_required, () => entById(f.req.value));
  const syncReq = () => actReq.draw(!!f.req.value && f.variant.value === "fields");
  f.variant.addEventListener("change", syncReq); f.req.addEventListener("change", syncReq);
  const picker = contentPicker(act.response_mode || "full", act.response_fields || [],
    () => (f.resp.value === "self" ? e : entById(f.resp.value)), "");
  picker.sel.querySelector('option[value="none"]').remove();
  // a list of records, e.g. GET /agreements/changes returning the changes
  const respEnt = () => (f.resp.value === "self" ? e : entById(f.resp.value));
  [["array", "List: [ … ]"], ["wrapped", "List in an object: { name: [ … ] }"], ["page", "List: page { items, nextCursor }"]]
    .forEach(([v, l]) => picker.sel.appendChild(h("option", { value: v }, l)));
  if (["array", "wrapped", "page"].includes(act.response_mode)) picker.sel.value = act.response_mode;
  const propA = h("input", { type: "text", class: "mono", value: act.list_property || "", "data-k": "act-list-property", style: "margin-top:6px" });
  const propHelp = h("div", { class: "help" });
  const syncList = () => {
    const ent = respEnt(), nm = propA.value.trim() || (ent ? ent.api_name : "items");
    propA.placeholder = ent ? ent.api_name : "";
    propA.style.display = picker.sel.value === "wrapped" ? "" : "none";
    propHelp.textContent = picker.sel.value === "wrapped" ? "Returns { \"" + nm + "\": [ … ] }. Property name (blank: " + (ent ? ent.api_name : "") + ")"
      : picker.sel.value === "array" ? "Returns [ … ] of " + (ent ? ent.schema_name : "records") + ", no paging"
      : picker.sel.value === "page" ? "Returns { items, nextCursor } with limit and cursor" : "";
  };
  picker.sel.addEventListener("change", syncList); propA.addEventListener("input", syncList);
  picker.sel.setAttribute("data-k", "act-content");
  const contentFld = h("div", { class: "field" }, h("label", null, "Response content"), picker.sel, picker.box, propA, propHelp);
  const qedA = queryEditor(act.query_params || [], e, false, false, null, () => f.method.value !== "GET" && !!f.req.value,
    () => [entById(f.resp.value), entById(f.req.value)]);
  const showContent = () => { contentFld.style.display = f.resp.value ? "" : "none"; picker.draw(); };
  f.resp.addEventListener("change", showContent); f.resp.addEventListener("change", syncList); showContent(); syncList(); syncReq();
  [f.method, f.resp, f.req].forEach((x) => x.addEventListener("change", () => qedA.redraw()));
  f.desc.value = act.description;
  let pathTouched = !!act.path && act.path !== kebab(act.name);
  f.path.oninput = () => (pathTouched = !!f.path.value);
  const k = keyAttr(e);
  const where = h("code");
  const showWhere = () => { f.opid.placeholder = camel(e.schema_name + " " + (f.name.value || "action")); if (!pathTouched) f.path.value = kebab(f.name.value); where.textContent = f.method.value + " " + resourcePath(e) + (f.scope.value === "item" ? "/{" + (k ? k.api_name : "id") + "}" : "") + "/" + (f.path.value || "…"); };
  [f.name, f.path].forEach((x) => x.addEventListener("input", showWhere)); [f.method, f.scope].forEach((x) => (x.onchange = showWhere)); showWhere();
  const errBoxes = ERRORS.map(([code, num]) => { const cb = h("input", { type: "checkbox", checked: (act.errors || []).includes(code) }); cb.dataset.code = code; return h("label", null, cb, num + " " + code.replace(/([a-z])([A-Z])/g, "$1 $2").toLowerCase()); });
  const fld = (l, el, help) => h("div", { class: "field" }, h("label", null, l), el, help ? h("div", { class: "help" }, help) : null);
  const body = h("div", null,
    h("div", { class: "side-box small", style: "margin-bottom:12px" }, where),
    h("div", { class: "grid" }, fld("Action name", f.name, "camelCase"), fld("Path segment", f.path), fld("Method", f.method), fld("Applies to", f.scope)),
    h("div", { class: "grid", style: "margin-top:10px" }, fld("Summary", f.summary),
      fld("Operation ID", f.opid, "Blank: generated from the schema and action name")),
    h("div", { class: "field", style: "margin-top:10px" }, h("label", null, "Description"), f.desc),
    h("div", { class: "section-label" }, "Request and response"),
    h("div", { class: "grid" }, fld("Request body", f.req), h("div", { class: "field" }, h("label", null, "Request form"), f.variant,
        h("div", { class: "help" }, "Create form leaves out read-only and system columns; Selected fields sends only the ticked columns, e.g. a reason")),
      h("div", { class: "field wide" }, actReq.box),
      fld("Response body", f.resp), contentFld, fld("Status", f.status)),
    h("div", { class: "checks", style: "margin-top:10px" }, h("label", null, f.ifm, "Require If-Match (ETag)")),
    h("div", { class: "section-label" }, "Parameters"), h("div", { class: "muted small", style: "margin-bottom:6px" }, "E.g. a GET lookup: /" + e.plural + "/lookup?customerNumber=, or GET /" + e.plural + "/changes filtered on the response entity's columns. With a request body, the columns marked here are sent as parameters and left out of the body."), qedA.wrap,
    h("div", { class: "section-label" }, "Error responses"), h("div", { class: "checks" }, errBoxes));
  const foot = [h("button", { class: "btn", onclick: closeModal }, "Cancel"), h("button", { class: "btn btn-primary", onclick: async () => {
    const payload = { name: f.name.value.trim(), path: f.path.value.trim(), operation_id: f.opid.value.trim(), method: f.method.value, scope: f.scope.value, summary: f.summary.value.trim(), description: f.desc.value.trim(),
      request_entity_id: f.req.value, request_variant: f.variant.value, response_entity_id: f.resp.value, status: f.status.value, if_match: f.ifm.checked,
      response_mode: f.resp.value ? picker.value().mode : "", response_fields: f.resp.value ? picker.value().fields : [],
      request_fields: f.variant.value === "fields" ? actReq.value().fields : [], request_required: f.variant.value === "fields" ? actReq.value().required : [],
      list_property: f.resp.value && picker.value().mode === "wrapped" ? propA.value.trim() : "",
      query_params: qedA.value(),
      errors: errBoxes.map((l) => l.querySelector("input")).filter((c) => c.checked).map((c) => c.dataset.code) };
    if (!payload.name) { f.name.focus(); return; }
    try {
      if (isNew) await api("POST", "api/entities/" + e.id + "/actions", payload); else await api("PATCH", "api/actions/" + act.id, payload);
      await reload(); closeModal(); render(); toast(isNew ? "Action added" : "Action saved");
    } catch (err) { toast(err.message, true); }
  } }, isNew ? "Add action" : "Save action")];
  if (!isNew) foot.unshift(h("button", { class: "btn btn-danger", style: "margin-right:auto", onclick: async () => {
    if (!confirm("Delete action " + act.name + "?")) return;
    try { await api("DELETE", "api/actions/" + act.id); await reload(); closeModal(); render(); toast("Action deleted"); } catch (err) { toast(err.message, true); }
  } }, "Delete"));
  openModal(isNew ? "Add an action to " + e.business_name : "Edit action " + act.name, body, foot);
}

function newEntityModal() {
  if (!M().namespaces.length) { toast("Create a namespace first", true); go("namespaces"); return; }
  const defNs = (S.route.view === "entity" && entById(S.route.id)) ? entById(S.route.id).namespace_id : M().namespaces[0].id;
  const cur = S.route.view === "entity" ? entById(S.route.id) : null;
  const defSys = cur ? cur.source_system_id || "" : (M().source_systems[0] ? M().source_systems[0].id : "");
  const f = {
    ns: selectEl(M().namespaces.map((x) => [x.id, x.key + " — " + x.title]), defNs),
    bn: h("input", { type: "text", placeholder: "e.g. Contact" }), api: h("input", { type: "text", class: "mono", placeholder: "e.g. customer" }),
    pl: h("input", { type: "text", class: "mono" }), sch: h("input", { type: "text", class: "mono" }),
    ss: selectEl(sysOptions(), defSys), so: h("input", { type: "text" }), io: h("input", { type: "text" }),
    key: h("input", { type: "checkbox", checked: true }),
  };
  const touched = {};
  ["api", "pl", "sch"].forEach((k) => f[k].addEventListener("input", () => (touched[k] = !!f[k].value)));
  const derive = () => {
    const apiName = touched.api ? f.api.value : camel(f.bn.value);
    if (!touched.api) f.api.value = apiName;
    if (!touched.pl) f.pl.value = kebab(plural(apiName));
    if (!touched.sch) f.sch.value = pascal(apiName);
  };
  f.bn.oninput = derive; f.api.addEventListener("input", derive);
  const opBoxes = OPS.map(([op, label]) => { const cb = h("input", { type: "checkbox", checked: ["list", "get", "create", "patch"].includes(op) }); cb.dataset.op = op; return h("label", null, cb, label); });
  const fld = (label, el, help) => h("div", { class: "field" }, h("label", null, label), el, help ? h("div", { class: "help" }, help) : null);
  const soF = fld("", f.so), ioF = fld("", f.io, "Optional");
  const srcLabels = () => {
    const K = kindOf(srcById(f.ss.value));
    soF.querySelector("label").textContent = K.object; f.so.placeholder = K.ph.object || "";
    ioF.querySelector("label").textContent = K.container; f.io.placeholder = K.ph.container || "";
    soF.style.display = ioF.style.display = f.ss.value ? "" : "none";
  };
  f.ss.onchange = srcLabels; srcLabels();
  const body = h("div", null,
    h("div", { class: "grid" }, fld("Namespace", f.ns), fld("Business name", f.bn, "What the business calls it"), fld("API name", f.api, "What the API calls it (singular)"),
      fld("Path segment", f.pl), fld("Schema name", f.sch), fld("Source system", f.ss), soF, ioF),
    h("div", { class: "section-label" }, "Operations"), h("div", { class: "checks" }, opBoxes),
    h("div", { class: "checks", style: "margin-top:12px" }, h("label", null, f.key, "Add a key column (<API name>Id)")));
  const create = async () => {
    if (!f.bn.value.trim()) { f.bn.focus(); return; }
    const ops = opBoxes.map((l) => l.querySelector("input")).filter((c) => c.checked).map((c) => c.dataset.op);
    try {
      const e = await api("POST", "api/entities?key_column=" + f.key.checked, { namespace_id: f.ns.value, business_name: f.bn.value, api_name: f.api.value, plural: f.pl.value,
        schema_name: f.sch.value, source_system_id: f.ss.value, source_object: f.so.value, source_io: f.io.value, operations: ops });
      await reload(); closeModal(); go("entity", e.id); toast("Created " + e.business_name);
    } catch (err) { toast(err.message, true); }
  };
  openModal("New entity", body, [h("button", { class: "btn", onclick: closeModal }, "Cancel"), h("button", { class: "btn btn-primary", onclick: create }, "Create entity")]);
}

/* ---------- namespaces ---------- */
function viewNamespaces() {
  const root = h("div");
  root.appendChild(h("div", { class: "page-head" },
    h("div", null, h("h1", null, "Namespaces"), h("div", { class: "sub" }, "Each namespace becomes one OpenAPI definition with its own base path, version, security and servers.")),
    h("button", { class: "btn btn-primary", onclick: newNamespaceModal }, "+ New namespace")));
  const grid = h("div", { class: "ns-grid" });
  for (const n of M().namespaces) {
    const url = "api/namespaces/" + n.id;
    const sv = (k) => (v) => patch(url, { [k]: v });
    const ents = M().entities.filter((e) => e.namespace_id === n.id);
    const servers = h("div");
    const drawServers = () => {
      servers.innerHTML = "";
      n.servers.forEach((s, i) => {
        const u = h("input", { type: "text", class: "mono", value: s.url, "data-k": n.id + "su" + i });
        const d = h("input", { type: "text", value: s.description, placeholder: "Environment", "data-k": n.id + "sd" + i });
        const saveAll = () => patch(url, { servers: n.servers.map((x, j) => (j === i ? { url: u.value, description: d.value } : x)) });
        editable(u, s.url, saveAll); editable(d, s.description, saveAll);
        servers.appendChild(h("div", { class: "server-row" }, u, d, h("button", { class: "icon-btn danger", title: "Remove server", onclick: () => patch(url, { servers: n.servers.filter((_, j) => j !== i) }) }, "✕")));
      });
      servers.appendChild(h("button", { class: "btn btn-sm", onclick: () => patch(url, { servers: n.servers.concat([{ url: "https://", description: "" }]) }) }, "+ Add server"));
    };
    drawServers();
    const first = (n.servers[0] || { url: "https://api.example" }).url.replace(/\/$/, "");
    const exampleUrl = n.base_path_in === "server" ? "server " + first + n.base_path + " + path /" + (ents[0] ? ents[0].plural : "items")
      : "server " + first + " + path " + n.base_path + "/" + (ents[0] ? ents[0].plural : "items");
    grid.appendChild(h("div", { class: "card" },
      h("div", { class: "card-head" }, h("h2", null, h("span", { class: "mono", style: "color:var(--accent)" }, n.key), "  ", n.title),
        h("div", { class: "row-actions" },
          h("button", { class: "btn btn-sm", onclick: () => { S.apiv.ns = n.key; go("api"); } }, "OpenAPI"),
          h("a", { class: "btn btn-sm", href: "preview/" + n.key, target: "_blank", rel: "noopener" }, "Swagger ↗"),
          h("button", { class: "btn btn-sm btn-danger", onclick: async () => {
            if (!confirm("Delete namespace " + n.key + "?")) return;
            try { await api("DELETE", url); await reload(); render(); toast("Namespace deleted"); } catch (err) { toast(err.message, true); }
          } }, "Delete"))),
      h("div", { class: "card-body" },
        issueCallout(issuesOf(n.id)),
        h("div", { class: "grid" },
          textField("Key", n.id + "key", n.key, sv("key"), { mono: true, help: "Short name, e.g. cm" }),
          textField("Title", n.id + "title", n.title, sv("title")),
          textField("Base path", n.id + "bp", n.base_path, sv("base_path"), { mono: true, help: "e.g. /cm/v1" }),
          textField("API version", n.id + "ver", n.version, sv("version"), { mono: true, help: "info.version, e.g. 1.0.0" }),
          selectField("Base path goes in", n.id + "bpi", [["paths", "Paths (/cm/v1/cases)"], ["server", "Server URL"]], n.base_path_in || "paths", sv("base_path_in"), exampleUrl),
          selectField("Security", n.id + "sec", SECURITY, n.security, sv("security")),
          textField("Owner", n.id + "own", n.owner, sv("owner")),
          selectField("Enum code style", n.id + "enst", ENUM_STYLES, n.enum_style || "UPPER_SNAKE", sv("enum_style"),
            "How enum values are written in this API, e.g. ?parentType=service-request with kebab-case"),
          selectField("Source mapping in the OpenAPI", n.id + "srcoa", [["yes", "Include (x-source-…, DD, transformation)"], ["no", "Leave out: contract only"]],
            n.source_in_openapi === false ? "no" : "yes", (v) => patch(url, { source_in_openapi: v === "yes" }),
            "Leave out for an API whose consumers should not see the source system's tables and fields"),
          textField("Description", n.id + "desc", n.description, sv("description"), { wide: true, area: true })),
        h("div", { class: "section-label", style: "margin-top:16px" }, "Servers"), servers,
        h("div", { class: "section-label" }, "Entities (" + ents.length + ")"),
        h("div", null, ents.length ? ents.map((e) => h("button", { class: "btn btn-sm", style: "margin:0 6px 6px 0", onclick: () => go("entity", e.id) }, e.business_name + " → " + e.api_name)) : h("span", { class: "muted small" }, "None yet")))));
  }
  root.appendChild(grid);
  return root;
}

function newNamespaceModal() {
  const f = { key: h("input", { type: "text", class: "mono", placeholder: "e.g. payments" }), title: h("input", { type: "text", placeholder: "Payments API" }),
    bp: h("input", { type: "text", class: "mono", placeholder: "/payments/v1" }), ver: h("input", { type: "text", class: "mono", value: "1.0.0" }), sec: selectEl(SECURITY, "bearer") };
  let bpTouched = false;
  f.bp.oninput = () => (bpTouched = !!f.bp.value);
  f.key.oninput = () => { if (!bpTouched) f.bp.value = f.key.value ? "/" + kebab(f.key.value) + "/v1" : ""; };
  const fld = (l, el) => h("div", { class: "field" }, h("label", null, l), el);
  openModal("New namespace", h("div", { class: "grid" }, fld("Key", f.key), fld("Title", f.title), fld("Base path", f.bp), fld("API version", f.ver), fld("Security", f.sec)),
    [h("button", { class: "btn", onclick: closeModal }, "Cancel"), h("button", { class: "btn btn-primary", onclick: async () => {
      try {
        const servers = (M().namespaces[0] || { servers: [] }).servers;
        await api("POST", "api/namespaces", { key: f.key.value.trim(), title: f.title.value, base_path: f.bp.value, version: f.ver.value, security: f.sec.value, servers });
        await reload(); closeModal(); render(); toast("Namespace created");
      } catch (err) { toast(err.message, true); }
    } }, "Create namespace")]);
}

/* ---------- import an ERD ---------- */
function erdImportModal() {
  let text = "", fileName = "";
  const fileIn = h("input", { type: "file", accept: ".html,.htm,.mmd,.md,.txt", style: "display:none" });
  const fileLbl = h("span", { class: "muted small" }, "No file chosen");
  const paste = h("textarea", { placeholder: "…or paste Mermaid erDiagram text here", style: "min-height:110px;font-family:var(--mono);font-size:12px" });
  const nsSel = selectEl([["per-area", "One namespace per subject area on the page"]]
    .concat(M().namespaces.map((n) => ["ns:" + n.id, "All into " + n.key + " — " + n.title]), [["single", "All into a new namespace 'model'"]]), "per-area");
  const opsSel = selectEl([["none", "None: schema only, choose later"], ["read", "List and get"], ["crud", "List, get, create, update, delete"]], "none");
  const clashSel = selectEl([["link", "Link to the existing entity"], ["separate", "Import separately, with the subject area in its name"]], "link");
  const modeSel = selectEl([["merge", "Add to the current model"], ["replace", "Replace namespaces, entities and links"]], "merge");
  const tagsIn = h("input", { type: "text", placeholder: "e.g. Agreements, Case", list: "tag-list-erd", "data-k": "erd-tags" });
  const tagsDl = h("datalist", { id: "tag-list-erd" }, allTags().map((t) => h("option", { value: t.name })));
  const areaCb = h("input", { type: "checkbox", "data-k": "erd-area-tags" });
  const out = h("div");
  const params = () => {
    const ns = nsSel.value;
    return "mode=" + modeSel.value + "&operations=" + opsSel.value + "&clash=" + clashSel.value
      + (tagsIn.value.trim() ? "&tags=" + encodeURIComponent(tagsIn.value.trim()) : "") + (areaCb.checked ? "&area_tags=true" : "")
      + (ns === "per-area" ? "&namespaces=per-area" : "&namespaces=single" + (ns.startsWith("ns:") ? "&namespace_id=" + encodeURIComponent(ns.slice(3)) : ""));
  };
  const source = () => (paste.value.trim() ? paste.value : text);
  let previewed = false;
  const preview = async () => {
    out.innerHTML = ""; previewed = false;
    if (!source().trim()) { out.appendChild(h("div", { class: "callout warn" }, "Choose a file or paste Mermaid text first.")); return; }
    try {
      const r = await api("POST", "api/import/erd?apply=false&" + params(), source(), true);
      const sm = r.summary;
      out.appendChild(h("div", { class: "stats", style: "margin:4px 0 12px" },
        [["entities", "Entities"], ["columns", "Columns"], ["links", "Links"], ["diagrams", "Diagrams"]].map(([k, l]) => h("div", { class: "stat" }, h("b", null, sm[k]), h("span", null, l)))));
      if (r.new_namespaces.length) out.appendChild(h("div", { class: "small", style: "margin-bottom:8px" }, "New namespaces: ", r.new_namespaces.map((n) => h("code", { style: "margin-right:6px" }, n.key)), h("span", { class: "muted" }, " " + r.new_namespaces.map((n) => n.title).join(" · "))));
      if (r.notes.length) out.appendChild(h("div", { class: "callout info" }, h("ul", { class: "issue-list" }, r.notes.map((n) => h("li", null, n)))));
      out.appendChild(r.errors ? h("div", { class: "callout err" }, r.errors + " error(s) after import:", h("ul", { class: "issue-list" }, r.error_samples.map((x) => h("li", null, x))))
        : h("div", { class: "muted small" }, "No errors after import" + (r.warnings ? "; " + r.warnings + " warning(s)" : "") + ". Imported entities are not sourced and their links are not exposed in the API until you choose to."));
      previewed = true;
    } catch (err) { out.appendChild(h("div", { class: "callout err" }, err.message)); }
  };
  fileIn.onchange = async () => { const f = fileIn.files[0]; if (!f) return; text = await f.text(); fileName = f.name; fileLbl.textContent = f.name; paste.value = ""; preview(); };
  [nsSel, opsSel, clashSel, modeSel].forEach((x) => (x.onchange = () => { if (source().trim()) preview(); }));
  paste.oninput = () => { previewed = false; out.innerHTML = ""; };
  const fld = (l, el, help) => h("div", { class: "field" }, h("label", null, l), el, help ? h("div", { class: "help" }, help) : null);
  const body = h("div", null,
    h("p", { class: "muted small", style: "margin-top:0" }, "Bring in a logical model: a Mermaid erDiagram, Markdown with mermaid blocks, or a saved page such as the sample logical data model. Entities, columns, keys, enum values, links and catalogue notes come across; nothing is exposed in the API until you choose."),
    h("div", { class: "row-actions", style: "margin-bottom:8px" }, h("button", { class: "btn", onclick: () => fileIn.click() }, "Choose file…"), fileLbl, fileIn),
    paste,
    h("div", { class: "grid", style: "margin-top:12px" },
      fld("Where entities go", nsSel), fld("Operations", opsSel),
      fld("If a name already exists", clashSel), fld("Import mode", modeSel, "The current model is backed up first either way"),
      h("div", { class: "field" }, h("label", null, "Tag imported entities"), tagsIn, tagsDl,
        h("label", { class: "checks", style: "margin-top:6px" }, areaCb, "Also tag each with its subject area (e.g. Party)"),
        h("div", { class: "help" }, "Comma separated. Existing entities the import links to get these tags too."))),
    h("div", { class: "section-label", style: "margin-top:14px" }, "Preview"), out);
  openModal("Import ERD", body, [
    h("button", { class: "btn", onclick: closeModal }, "Cancel"),
    h("button", { class: "btn", onclick: preview }, "Preview"),
    h("button", { class: "btn btn-primary", onclick: async () => {
      if (!previewed) { await preview(); if (!previewed) return; }
      try {
        const r = await api("POST", "api/import/erd?apply=true&" + params(), source(), true);
        await reload(); closeModal(); S.diag.fitted = false; go("diagram"); render();
        toast("Imported " + r.summary.entities + " entities" + (fileName ? " from " + fileName : "") + ". Previous model backed up.");
      } catch (err) { toast(err.message, true); }
    } }, "Import")]);
}

/* ---------- standard headers ---------- */
const HMETHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"];
function viewHeaders() {
  const root = h("div");
  const hs = M().headers || [];
  const std = ["request-id", "date-time", "correlation-id", "journey-instance-id", "instigating-system"];
  const missing = std.filter((x) => !hs.some((y) => y.name.toLowerCase() === x));
  root.appendChild(h("div", { class: "page-head" },
    h("div", null, h("h1", null, "Headers"),
      h("div", { class: "sub" }, "Standard headers added to every matching operation in the generated definitions. A request header is sent by the caller; a response header comes back; 'both' does both.")),
    h("div", { class: "row-actions" },
      missing.length ? h("button", { class: "btn", onclick: async () => { await api("POST", "api/headers/standard"); await reload(); render(); toast("Added " + missing.join(", ")); } }, "Add standard headers (" + missing.length + ")") : null,
      h("button", { class: "btn btn-primary", onclick: async () => {
        let nm = "new-header", i = 2;
        while (hs.some((x) => x.name.toLowerCase() === nm)) nm = "new-header-" + i++;
        try { await api("POST", "api/headers", { name: nm, required: false }); await reload(); render(); toast("Header " + nm + " added: rename it in the table"); }
        catch (err) { toast(err.message, true); }
      } }, "+ Add header"))));
  const tbody = h("tbody");
  for (const hd of hs) {
    const url = "api/headers/" + hd.id;
    const sv = (k) => (v) => patch(url, { [k]: v }, true);
    const iss = issuesOf(hd.id);
    const methods = h("div", { class: "checks" }, HMETHODS.map((m_) => {
      const cb = editable(h("input", { type: "checkbox", checked: (hd.methods || []).includes(m_), "data-k": hd.id + "m" + m_ }), (hd.methods || []).includes(m_),
        (on) => patch(url, { methods: HMETHODS.filter((x) => (x === m_ ? on : (hd.methods || []).includes(x))) }, true));
      return h("label", { class: "small" }, cb, m_);
    }));
    const nss = h("div", { class: "checks" }, M().namespaces.map((n) => {
      const on0 = (hd.namespace_ids || []).includes(n.id);
      const cb = editable(h("input", { type: "checkbox", checked: on0, "data-k": hd.id + "n" + n.id }), on0,
        (on) => patch(url, { namespace_ids: M().namespaces.map((x) => x.id).filter((x) => (x === n.id ? on : (hd.namespace_ids || []).includes(x))) }, true));
      return h("label", { class: "small mono" }, cb, n.key);
    }));
    tbody.appendChild(h("tr", { class: iss.some((x) => x.level === "error") ? "has-err" : iss.length ? "has-warn" : null, title: iss.map((x) => x.message).join("\n") || null },
      h("td", null, editable(h("input", { type: "text", class: "mono", value: hd.name, "data-k": hd.id + "hname", style: "min-width:170px" }), hd.name, sv("name"))),
      h("td", null, editable(selectEl([["request", "Request"], ["response", "Response"], ["both", "Both: sent and returned"]], hd.direction, { "data-k": hd.id + "dir" }), hd.direction, sv("direction"))),
      h("td", { class: "c" }, editable(h("input", { type: "checkbox", checked: hd.required, "data-k": hd.id + "req" }), hd.required, sv("required"))),
      h("td", null, editable(selectEl([["string", "string"], ["integer", "integer"]], hd.type, { "data-k": hd.id + "type" }), hd.type, sv("type"))),
      h("td", null, editable(selectEl([["", "—"], ["uuid", "uuid"], ["date-time", "date-time"], ["date", "date"]], hd.format || "", { "data-k": hd.id + "fmt" }), hd.format, sv("format"))),
      h("td", null, editable(h("input", { type: "text", class: "mono", value: hd.example || "", "data-k": hd.id + "ex", style: "min-width:180px" }), hd.example, sv("example"))),
      h("td", null, methods, (hd.methods || []).length ? null : h("div", { class: "muted small" }, "all methods")),
      h("td", null, nss, (hd.namespace_ids || []).length ? null : h("div", { class: "muted small" }, "all namespaces")),
      h("td", null, editable(h("input", { type: "text", value: hd.description || "", "data-k": hd.id + "desc", style: "min-width:240px" }), hd.description, sv("description"))),
      h("td", null, h("button", { class: "icon-btn danger", title: "Delete header", onclick: async () => {
        if (!confirm("Delete header " + hd.name + "?")) return; await api("DELETE", url); await reload(); render(); toast("Header deleted");
      } }, "🗑"))));
  }
  const secText = M().namespaces.map((n) => n.key + ": " + ((SECURITY.find((x) => x[0] === n.security) || ["", n.security])[1])).join(" · ");
  tbody.appendChild(h("tr", null,
    h("td", { class: "mono", style: "padding-left:10px" }, "Authorization"), h("td", { class: "muted small" }, "Request"), h("td", { class: "c muted" }, "✓"),
    h("td", { colspan: "6", class: "muted small" }, "From each namespace's security setting, not listed here, because OpenAPI tools ignore an Authorization header parameter. ", secText),
    h("td", null, h("button", { class: "btn btn-sm", onclick: () => go("namespaces") }, "Namespaces"))));
  root.appendChild(h("div", { class: "card" }, h("div", { class: "table-wrap" }, h("table", { class: "cols-table", style: "min-width:1300px" },
    h("thead", null, h("tr", null, ["Name", "Direction", "Req", "Type", "Format", "Example", "Methods", "Namespaces", "Description", ""].map((x) => h("th", null, x)))), tbody))));
  // what a request looks like
  const sample = ["POST /cm/v1/cases/1-9XYZ78/agreement-changes HTTP/1.1"];
  const n0 = M().namespaces[0];
  if (n0 && n0.security === "bearer") sample.push("Authorization: Bearer eyJhbGciOi…");
  if (n0 && n0.security === "apiKey") sample.push("X-API-Key: …");
  for (const hd of hs) if (hd.direction !== "response" && (!hd.methods.length || hd.methods.includes("POST"))) sample.push(hd.name + ": " + (hd.example || "…"));
  sample.push("Content-Type: application/json", "", "HTTP/1.1 201 Created");
  for (const hd of hs) if (hd.direction !== "request" && (!hd.methods.length || hd.methods.includes("POST"))) sample.push(hd.name + ": " + (hd.example || "…"));
  sample.push("Location: /cm/v1/agreement-changes/1-SR9932", "", '{ "changeId": "1-SR9932" }');
  root.appendChild(h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", null, "How a request and response look"),
    h("span", { class: "hint" }, "With the default POST response: the row id of the created record")), h("pre", { class: "code" }, sample.join("\n"))));
  return root;
}

/* ---------- worked examples ---------- */
async function exampleModal(name, title) {
  try {
    const p = await api("POST", "api/examples/" + name + "?apply=false");
    const body = h("div", null,
      h("p", { class: "muted small", style: "margin-top:0" }, "Adds a worked example to your current model, linked to your existing entities where the names match. Your model is backed up first."),
      p.added.length ? h("div", null, h("div", { class: "section-label" }, "Adds " + p.added.length + " entities"), h("div", null, p.added.map((x) => h("span", { class: "pill", style: "margin:0 6px 6px 0" }, x))))
        : h("div", { class: "callout info" }, "Everything in this example is already in your model."),
      p.notes.length ? h("div", { class: "callout info", style: "margin-top:10px" }, h("ul", { class: "issue-list" }, p.notes.map((x) => h("li", null, x)))) : null,
      h("div", { class: "muted small", style: "margin-top:8px" }, p.errors + " errors, " + p.warnings + " warnings after adding it (warnings include source fields still to map)."));
    openModal("Add example: " + title, body, [h("button", { class: "btn", onclick: closeModal }, "Cancel"), h("button", { class: "btn btn-primary", onclick: async () => {
      try {
        await api("POST", "api/examples/" + name + "?apply=true"); await reload(); closeModal();
        const ent = M().entities.find((x) => x.schema_name === "AgreementChange");
        if (ent) go("entity", ent.id); else render();
        toast("Example added. Previous model backed up.");
      } catch (err) { toast(err.message, true); }
    } }, p.added.length ? "Add to model" : "OK")]);
  } catch (err) { toast(err.message, true); }
}

/* ---------- source systems ---------- */
function viewSources() {
  const root = h("div");
  root.appendChild(h("div", { class: "page-head" },
    h("div", null, h("h1", null, "Source systems"),
      h("div", { class: "sub" }, "The applications data comes from: Siebel, other services, databases, files. Each entity picks one, and its source fields use that system type's names. All source fields are optional; pick none for derived or new data.")),
    h("button", { class: "btn btn-primary", onclick: newSourceModal }, "+ New source system")));
  const grid = h("div", { class: "ns-grid" });
  for (const s of M().source_systems) {
    const url = "api/source-systems/" + s.id;
    const sv = (k) => (v) => patch(url, { [k]: v });
    const K = kindOf(s);
    const ents = M().entities.filter((e) => e.source_system_id === s.id);
    const names = [["Source object", "object"], ["Source container", "container"], ["Source field", "field"], ["Source location", "location"], ["Code list", "code_list"], ["Link", "link"]];
    grid.appendChild(h("div", { class: "card", style: "border-top:4px solid " + sysColour(s) },
      h("div", { class: "card-head" }, h("h2", null, h("span", { class: "mono", style: "color:" + sysColour(s) }, s.key), "  ", s.name),
        h("button", { class: "btn btn-sm btn-danger", onclick: () => deleteSource(s, ents) }, "Delete")),
      h("div", { class: "card-body" },
        issueCallout(issuesOf(s.id)),
        h("div", { class: "grid" },
          textField("Key", s.id + "key", s.key, sv("key"), { mono: true, help: "Short name, e.g. siebel" }),
          textField("Name", s.id + "name", s.name, sv("name")),
          selectField("Type", s.id + "kind", Object.entries(S.kinds).map(([k, v]) => [k, v.label]), s.kind, sv("kind"), "Sets the source field names below"),
          textField("Owner", s.id + "own", s.owner, sv("owner")),
          textField("Internal name tokens", s.id + "tok", (s.internal_tokens || []).join(", "),
            (v) => patch(url, { internal_tokens: v.split(",").map((t) => t.trim()).filter(Boolean) }),
            { mono: true, wide: true, placeholder: "none", help: "API names containing these are flagged as exposing " + s.name + " internals. A token ending in _ must be a prefix. Blank switches the check off." }),
          textField("Description", s.id + "desc", s.description, sv("description"), { wide: true, area: true })),
        h("div", { class: "section-label", style: "margin-top:16px" }, "Source field names for " + K.label),
        h("table", { class: "kv" }, h("tbody", null, names.map(([g, k]) => h("tr", null, h("td", { class: "muted small" }, g), h("td", null, K[k]),
          h("td", { class: "mono small muted" }, K.ph[k] ? "e.g. " + K.ph[k] : ""))))),
        h("div", { class: "section-label" }, "Data dictionary"),
        (() => {
          const d = (S.dicts || []).find((x) => x.system_id === s.id && x.rows);
          return h("div", null,
            d ? h("div", { class: "small", style: "margin-bottom:8px" }, h("b", null, d.rows), " fields in ", h("b", null, d.bcs), " business components, ", h("b", null, d.tables), " tables",
              h("span", { class: "muted" }, " · " + (d.file || "uploaded") + (d.loaded ? " · " + d.loaded.replace("T", " ") : "")))
              : h("div", { class: "muted small", style: "margin-bottom:8px" }, "None loaded. Upload the dictionary export to look up business components, fields and DD attributes when mapping columns."),
            h("div", { class: "row-actions" },
              h("button", { class: "btn btn-sm" + (d ? "" : " btn-primary"), "data-k": s.id + "dict-upload", onclick: () => dictionaryUploadModal(s) }, d ? "Upload again…" : "Upload dictionary…"),
              d ? h("button", { class: "btn btn-sm btn-danger", onclick: async () => {
                if (!confirm("Remove the data dictionary for " + s.name + "? Columns keep their values.")) return;
                try { await api("DELETE", "api/dictionary/" + s.id); await reload(); render(); toast("Dictionary removed"); } catch (err) { toast(err.message, true); }
              } }, "Remove") : null));
        })(),
        h("div", { class: "section-label" }, "Entities (" + ents.length + ")"),
        h("div", null, ents.length ? ents.map((e) => h("button", { class: "btn btn-sm", style: "margin:0 6px 6px 0", onclick: () => go("entity", e.id) }, e.business_name + " → " + e.api_name))
          : h("span", { class: "muted small" }, "None yet")))));
  }
  const uns = M().entities.filter((e) => !srcOf(e));
  grid.appendChild(h("div", { class: "card", style: "border-top:4px solid " + sysColour(null) },
    h("div", { class: "card-head" }, h("h2", null, "Not sourced"), h("span", { class: "hint" }, "Derived, calculated or new data")),
    h("div", { class: "card-body" },
      h("p", { class: "muted small", style: "margin-top:0" }, "Entities with no source system have no source fields to map, and no source-mapping warnings. Columns get a Derivation / rule field instead."),
      h("div", null, uns.length ? uns.map((e) => h("button", { class: "btn btn-sm", style: "margin:0 6px 6px 0", onclick: () => go("entity", e.id) }, e.business_name + " → " + e.api_name))
        : h("span", { class: "muted small" }, "None")))));
  root.appendChild(grid);
  return root;
}

async function deleteSource(s, ents) {
  const msg = ents.length ? s.name + " is used by " + ents.map((e) => e.business_name).join(", ") + ". Delete it and mark those entities as not sourced? Their source field values are kept." : "Delete source system " + s.name + "?";
  if (!confirm(msg)) return;
  try { await api("DELETE", "api/source-systems/" + s.id + (ents.length ? "?detach=true" : "")); await reload(); render(); toast("Source system deleted"); }
  catch (err) { toast(err.message, true); }
}

function newSourceModal() {
  const f = { name: h("input", { type: "text", placeholder: "e.g. Payments Service" }), key: h("input", { type: "text", class: "mono", placeholder: "e.g. payments" }),
    kind: selectEl(Object.entries(S.kinds).map(([k, v]) => [k, v.label]), "rest"), owner: h("input", { type: "text" }) };
  let keyTouched = false;
  f.key.oninput = () => (keyTouched = !!f.key.value);
  f.name.oninput = () => { if (!keyTouched) f.key.value = kebab(f.name.value); };
  const fld = (l, el, help) => h("div", { class: "field" }, h("label", null, l), el, help ? h("div", { class: "help" }, help) : null);
  openModal("New source system", h("div", { class: "grid" }, fld("Name", f.name), fld("Key", f.key), fld("Type", f.kind, "Sets the source field names"), fld("Owner", f.owner)),
    [h("button", { class: "btn", onclick: closeModal }, "Cancel"), h("button", { class: "btn btn-primary", onclick: async () => {
      if (!f.name.value.trim() && !f.key.value.trim()) { f.name.focus(); return; }
      try {
        await api("POST", "api/source-systems", { key: f.key.value.trim() || kebab(f.name.value), name: f.name.value.trim(), kind: f.kind.value, owner: f.owner.value });
        await reload(); closeModal(); go("sources"); render(); toast("Source system created");
      } catch (err) { toast(err.message, true); }
    } }, "Create source system")]);
}

/* ---------- API definition ---------- */
function highlightJson(s) {
  return esc(s).replace(/(&quot;(?:[^&]|&(?!quot;))*?&quot;)(\s*:)?|\b(-?\d+(?:\.\d+)?)\b|\b(true|false|null)\b/g, (m, str, colon, num, lit) =>
    str ? (colon ? '<span class="k">' + str + "</span>" + colon : '<span class="s">' + str + "</span>") : num ? '<span class="n">' + num + "</span>" : '<span class="n">' + lit + "</span>");
}
function highlightYaml(s) {
  return s.split("\n").map((line) => {
    const m = line.match(/^(\s*(?:- )?)([^:#'"][^:]*?|'[^']*'|"[^"]*")(:)(\s.*)?$/);
    if (!m) return esc(line).replace(/^(\s*- )(.*)$/, (x, a, b) => a + '<span class="s">' + b + "</span>");
    const val = m[4] || "";
    const v = val.trim();
    const vh = !v ? "" : /^-?\d+(\.\d+)?$|^(true|false|null)$/.test(v) ? ' <span class="n">' + esc(v) + "</span>" : ' <span class="s">' + esc(v) + "</span>";
    return esc(m[1]) + '<span class="k">' + esc(m[2]) + "</span>:" + vh;
  }).join("\n");
}

function viewApi() {
  const root = h("div");
  if (!M().namespaces.length) return h("div", { class: "empty" }, "No namespaces.");
  if (!S.apiv.ns || !M().namespaces.some((n) => n.key === S.apiv.ns)) S.apiv.ns = M().namespaces[0].key;
  const n = M().namespaces.find((x) => x.key === S.apiv.ns);
  const nsSeg = h("div", { class: "seg" }, M().namespaces.map((x) => h("button", { class: x.key === S.apiv.ns ? "on" : null, onclick: () => { S.apiv.ns = x.key; render(); } }, x.key)));
  const fmtSeg = h("div", { class: "seg" }, ["yaml", "json"].map((x) => h("button", { class: x === S.apiv.fmt ? "on" : null, onclick: () => { S.apiv.fmt = x; render(); } }, x.toUpperCase())));
  const pubLabel = n.source_in_openapi === false ? "As published (contract only)" : "As published (with source mapping)";
  const viewSeg = h("div", { class: "seg", title: "Source mapping: x-source-…, DD attribute, data item, transformation" },
    [["", pubLabel], ["full", "With source mapping"], ["contract", "Contract only"]].map(([v, l]) => h("button", { class: (S.apiv.view || "") === v ? "on" : null, onclick: () => { S.apiv.view = v; render(); } }, l)));
  const vq = S.apiv.view ? "view=" + S.apiv.view : "";
  const errs = S.issues.filter((i) => i.level === "error");
  root.appendChild(h("div", { class: "page-head" },
    h("div", null, h("h1", null, "API definition"), h("div", { class: "sub" }, "OpenAPI 3.0.3, generated from the model each time. Business names and source mapping travel as title and x-… extensions.")),
    h("div", { class: "row-actions" }, nsSeg, viewSeg, fmtSeg)));
  if (errs.length) root.appendChild(h("div", { class: "callout err" }, errs.length + " error(s) in the model may make this definition incomplete. ", h("button", { class: "link-btn", onclick: () => go("issues") }, "See issues")));
  const eps = h("div", { class: "table-wrap" });
  const code = h("pre", { class: "code" }, "Loading…");
  const fp = h("span", { class: "hint mono" });
  root.appendChild(h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", null, "Operations — " + n.title)), eps));
  root.appendChild(h("div", { class: "card" },
    h("div", { class: "card-head" }, h("div", null, h("h2", null, n.key + "-openapi." + S.apiv.fmt), fp),
      h("div", { class: "row-actions" },
        h("button", { class: "btn btn-sm", onclick: async () => { try { await navigator.clipboard.writeText(code.textContent); toast("Copied"); } catch (_) { toast("Copy not allowed here; use download", true); } } }, "Copy"),
        h("button", { class: "btn btn-sm", onclick: () => download("api/openapi/" + n.key + ".yaml?download=true" + (vq ? "&" + vq : "")) }, "Download YAML"),
        h("button", { class: "btn btn-sm", onclick: () => download("api/openapi/" + n.key + ".json?download=true" + (vq ? "&" + vq : "")) }, "Download JSON"),
        h("a", { class: "btn btn-sm", href: "preview/" + n.key, target: "_blank", rel: "noopener", title: "Swagger UI loads from cdn.jsdelivr.net" }, "Swagger preview ↗"))),
    code));
  api("GET", "api/endpoints").then((all) => { eps.innerHTML = ""; const mine = all.filter((x) => x.namespace === n.key); eps.appendChild(mine.length ? endpointTable(mine) : h("div", { class: "empty" }, "No operations yet.")); });
  fetch("api/openapi/" + n.key + "." + S.apiv.fmt + (vq ? "?" + vq : "")).then((r) => r.text()).then((t) => {
    code.innerHTML = S.apiv.fmt === "json" ? highlightJson(t) : highlightYaml(t);
    const m = t.match(/x-model-fingerprint["']?:\s*["']?([0-9a-f]+)/); if (m) fp.textContent = "  fingerprint " + m[1];
  });
  return root;
}

/* ---------- mapping sheet ---------- */
function viewMapping() {
  const root = h("div");
  const fileIn = h("input", { type: "file", accept: ".xlsx", style: "display:none", onchange: async () => {
    const file = fileIn.files[0]; if (!file) return;
    const buf = await file.arrayBuffer();
    try { const res = await api("POST", "api/import/mapping?apply=false", buf, true); importPreviewModal(res, buf, file.name); }
    catch (err) { toast(err.message, true); }
    fileIn.value = "";
  } });
  root.appendChild(h("div", { class: "page-head" },
    h("div", null, h("h1", null, "Mapping sheet"), h("div", { class: "sub" }, "Business name ↔ API name ↔ source field for every column. Download it, edit it in Excel, and import it back.")),
    h("div", { class: "row-actions" },
      h("button", { class: "btn btn-primary", onclick: () => download("api/export/mapping.xlsx") }, "Download Excel"),
      h("button", { class: "btn", onclick: () => download("api/export/mapping.csv") }, "Download CSV"),
      h("button", { class: "btn", onclick: () => fileIn.click() }, "Import Excel…"), fileIn)));
  const filter = h("input", { type: "text", placeholder: "Filter by entity, name, field…", value: S.mapFilter, "data-k": "map-filter", style: "max-width:320px" });
  const tbodyHolder = h("div", { class: "table-wrap", style: "max-height:62vh" });
  const draw = (rows) => {
    const q = S.mapFilter.toLowerCase();
    const shown = rows.filter((r) => !q || Object.values(r).join(" ").toLowerCase().includes(q));
    tbodyHolder.innerHTML = "";
    const cols = [["entity", "Entity"], ["json_path", "JSON path"], ["business_name", "Business name"], ["api_name", "API name"], ["dd_reference", "DD attribute"], ["type", "Type"], ["key", "Key"], ["required", "Req"],
      ["read_only", "RO"], ["system", "Sys"], ["pii", "PII"], ["enum_values", "Enum values"], ["source_system", "Source system"], ["source_object", "Source object"], ["source_field", "Source field"], ["source_column", "Source location"], ["lov_type", "Code list / LOV"], ["transform", "Transformation"]];
    tbodyHolder.appendChild(h("table", { class: "nowrap" }, h("thead", null, h("tr", null, cols.map(([, l]) => h("th", null, l)))),
      h("tbody", null, shown.map((r) => h("tr", null, cols.map(([k]) => h("td", { class: ["json_path", "api_name", "dd_reference", "source_column", "lov_type", "enum_values"].includes(k) ? "mono small" : null, style: k === "json_path" ? "white-space:pre" : null }, r[k] == null ? "" : String(r[k]))))))));
    if (!shown.length) tbodyHolder.appendChild(h("div", { class: "empty" }, "No rows match."));
  };
  const linksHolder = h("div", { class: "table-wrap" });
  api("GET", "api/mapping").then((d) => {
    draw(d.columns);
    filter.oninput = () => { S.mapFilter = filter.value; draw(d.columns); };
    const lc = [["from_business", "From"], ["cardinality", "Cardinality"], ["to_business", "To"], ["business_name", "Business name"], ["api_name", "API name"], ["expose", "Exposed as"],
      ["inverse_api_name", "Inverse API name"], ["inverse_expose", "Inverse exposed as"], ["join_from", "Join from"], ["join_to", "Join to"], ["source_link", "Source link"]];
    linksHolder.appendChild(d.links.length ? h("table", { class: "nowrap" }, h("thead", null, h("tr", null, lc.map(([, l]) => h("th", null, l)))),
      h("tbody", null, d.links.map((r) => h("tr", null, lc.map(([k]) => h("td", { class: ["api_name", "inverse_api_name"].includes(k) ? "mono small" : null }, r[k] || "")))))) : h("div", { class: "empty" }, "No links."));
  });
  root.appendChild(h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", null, "Columns"), filter), tbodyHolder));
  root.appendChild(h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", null, "Linked entities")), linksHolder));
  root.appendChild(h("div", { class: "callout info" }, "The Excel file has sheets for Namespaces, Entities, Mapping, Linked entities, Enum values and Endpoints. Dark blue headers are read back on import; grey headers are generated. Importing replaces the model, and the previous model is backed up first."));
  return root;
}

function importPreviewModal(res, buf, name) {
  const errs = res.issues.filter((i) => i.level === "error"), warns = res.issues.length - errs.length;
  const s = res.summary;
  const body = h("div", null,
    h("div", { class: "stats", style: "margin-bottom:14px" },
      [["namespaces", "Namespaces"], ["entities", "Entities"], ["columns", "Columns"], ["links", "Links"]].map(([k, l]) => h("div", { class: "stat" }, h("b", null, s[k]), h("span", null, l)))),
    res.notes.length ? h("div", { class: "callout warn" }, h("ul", { class: "issue-list" }, res.notes.map((x) => h("li", null, x)))) : null,
    errs.length ? h("div", { class: "callout err" }, errs.length + " error(s) after import:", h("ul", { class: "issue-list" }, errs.slice(0, 8).map((i) => h("li", null, i.where + ": " + i.message)))) : null,
    h("div", { class: "muted small" }, warns + " warning(s). Importing replaces the current model; the current model is backed up first."));
  openModal("Import " + name, body, [h("button", { class: "btn", onclick: closeModal }, "Cancel"), h("button", { class: "btn btn-primary", onclick: async () => {
    try { const r = await api("POST", "api/import/mapping?apply=true", buf, true); await reload(); closeModal(); render(); toast("Imported. Previous model saved as " + r.backup); }
    catch (err) { toast(err.message, true); }
  } }, "Replace model with this sheet")]);
}

/* ---------- issues ---------- */
function viewIssues() {
  const root = h("div");
  root.appendChild(h("div", { class: "page-head" }, h("div", null, h("h1", null, "Issues"),
    h("div", { class: "sub" }, "Errors make the definition wrong or incomplete. Warnings are design advice: naming style, missing source mapping, names that leak the source system."))));
  if (!S.issues.length) { root.appendChild(h("div", { class: "card" }, h("div", { class: "empty" }, "✓ No issues. The model is consistent."))); return root; }
  const sorted = S.issues.slice().sort((a, b) => (a.level === b.level ? 0 : a.level === "error" ? -1 : 1));
  root.appendChild(h("div", { class: "card" }, h("div", { class: "table-wrap" }, h("table", null,
    h("thead", null, h("tr", null, ["Level", "Where", "Issue", ""].map((x) => h("th", null, x)))),
    h("tbody", null, sorted.map((i) => {
      const owner = ownerOfObject(i.object_id); const isNs = M().namespaces.some((n) => n.id === i.object_id);
      return h("tr", null, h("td", { class: "lvl-" + i.level }, i.level), h("td", null, i.where), h("td", null, i.message),
        h("td", null, owner ? h("button", { class: "btn btn-sm", onclick: () => go("entity", owner.id) }, "Open") : isNs ? h("button", { class: "btn btn-sm", onclick: () => go("namespaces") }, "Open") : null));
    }))))));
  return root;
}

/* ---------- settings ---------- */
function viewSettings() {
  const st = M().settings;
  const sv = (k) => (v) => patch("api/settings", Object.assign({}, st, { [k]: v }));
  const fileIn = h("input", { type: "file", accept: ".json", style: "display:none", onchange: async () => {
    const file = fileIn.files[0]; if (!file) return;
    try {
      const data = JSON.parse(await file.text());
      if (!confirm("Replace the current model with " + file.name + "? The current model is backed up first.")) return;
      await api("PUT", "api/model", data); await reload(); render(); toast("Model imported");
    } catch (err) { toast(err.message, true); }
    fileIn.value = "";
  } });
  return h("div", null,
    h("div", { class: "page-head" }, h("div", null, h("h1", null, "Settings & model file"), h("div", { class: "sub" }, "The whole design is one model.json file, which you can keep in version control."))),
    h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", null, "Catalogue")),
      h("div", { class: "card-body" }, h("div", { class: "grid" },
        textField("Title", "s-title", st.title, sv("title")), textField("Organisation", "s-org", st.organisation, sv("organisation")),
        selectField("Default POST response", "s-post", [["key", "Key only: row id of the created record"], ["full", "Full record"]],
          st.post_response || "key", sv("post_response"), "Each operation can choose otherwise"),
        selectField("Default PUT response", "s-put", [["full", "Full record"], ["key", "Key only (row id)"], ["none", "No body"]],
          st.put_response || "full", sv("put_response")),
        textField("Default currency", "s-cur", st.money_currency || "GBP", (v) => patch("api/settings", Object.assign({}, st, { money_currency: v.trim().toUpperCase() })),
          { mono: true, help: "ISO 4217, e.g. GBP; used in money examples" }),
        h("div", { class: "field" }, h("label", null, "Amount columns"),
          h("button", { class: "btn", "data-k": "money-cands", onclick: moneyCandidatesModal }, "Use money for amount columns…"),
          h("div", { class: "help" }, "Finds number columns named like amount, price, liability, balance…")),
        selectField("Default GET list response", "s-list", [["page", "Page: { items, nextCursor } with limit and cursor"], ["array", "Array of records, no paging"],
          ["wrapped", "Object holding the array, e.g. { agreements: [ … ] }, no paging"]],
          st.list_response || "page", sv("list_response"), "Each list can choose otherwise in its Edit dialog"),
        h("div", { class: "field" }, h("label", null, "Source systems"),
          h("button", { class: "btn", onclick: () => go("sources") }, "Manage source systems (" + M().source_systems.length + ")"),
          h("div", { class: "help" }, "Each entity picks one; recorded as x-source-system in the definitions"))))),
    h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", null, "Model file")),
      h("div", { class: "card-body" },
        h("div", { class: "row-actions" },
          h("button", { class: "btn", onclick: () => download("api/model/download") }, "Download model.json"),
          h("button", { class: "btn", onclick: () => fileIn.click() }, "Import model.json…"), fileIn,
          h("button", { class: "btn", onclick: erdImportModal }, "Import ERD…"),
          h("button", { class: "btn", onclick: () => exampleModal("agreements", "Service agreements API") }, "Add service agreements example…"),
          h("button", { class: "btn", onclick: () => exampleModal("notes", "Notes API") }, "Add notes example…"),
          h("button", { class: "btn", onclick: () => download("api/export/bundle.zip") }, "Download all outputs (.zip)"),
          h("button", { class: "btn btn-danger", onclick: async () => {
            if (!confirm("Replace the model with the starter example (Contact as customer, Case, Address)? The current model is backed up first.")) return;
            await api("POST", "api/reset-sample"); await reload(); go("entity", "en_contact"); render(); toast("Starter example loaded");
          } }, "Reset to starter example")),
        h("p", { class: "muted small", style: "margin-bottom:0" }, "Command line: ", h("code", null, "api-definition-studio generate --out generated"), " writes the same outputs without the UI, e.g. in a pipeline."))),
    h("div", { class: "muted small" }, "API Definition Studio ", h("span", { id: "app-version" }, "")));
}

/* ---------- data model diagram ---------- */
const BOX_W = 272, HEAD_H = 46, ROW_H = 22, TAG_H = 24;
function tagTop(e) { return HEAD_H + ((e.tags || []).length ? TAG_H : 0); }
function diagEntities() {
  // each filter keeps matching entities; with "linked" on, also whatever they link to (cross-area links)
  const D = S.diag;
  let ids = new Set(M().entities.map((e) => e.id));
  const keep = (pred) => {
    const base = new Set(M().entities.filter((e) => ids.has(e.id) && pred(e)).map((e) => e.id)), out = new Set(base);
    if (D.linked) M().relationships.forEach((r) => {
      if (base.has(r.from_entity_id) && ids.has(r.to_entity_id)) out.add(r.to_entity_id);
      if (base.has(r.to_entity_id) && ids.has(r.from_entity_id)) out.add(r.from_entity_id);
    });
    ids = out;
  };
  const ns = D.ns && M().namespaces.find((n) => n.key === D.ns);
  if (ns) keep((e) => e.namespace_id === ns.id);
  if (D.src) keep((e) => (e.source_system_id || "-") === D.src);
  if (D.tags.length) keep((e) => D.tags.some((t) => hasTag(e, t)));
  return M().entities.filter((e) => ids.has(e.id));
}
function sourceLegend() {
  const list = M().source_systems.filter((s) => M().entities.some((e) => e.source_system_id === s.id));
  const items = list.map((s) => '<span style="display:inline-block;width:14px;height:4px;border-radius:2px;background:' + sysColour(s) + ';vertical-align:middle"></span> ' + esc(s.name));
  if (M().entities.some((e) => !srcOf(e))) items.push('<span style="display:inline-block;width:14px;height:4px;border-radius:2px;background:' + sysColour(null) + ';vertical-align:middle"></span> not sourced');
  return items.length ? '<div style="margin-top:4px">Source: ' + items.join(" &nbsp; ") + "</div>" : "";
}
function lbl(business, apiName, mode) { return mode === "api" ? apiName : mode === "both" ? (business === apiName ? business : business + " · " + apiName) : business; }
function boxRows(e) { const c = S.diag.cols; return c === "none" ? [] : c === "keys" ? e.attributes.filter((a) => a.key || a.required) : e.attributes; }
// business components the shown rows are derived from, listed under the box so a picture says it too
function derivedFoot(e, rows) { return [...new Set(rows.map((a) => derivedOf(e, a).from).filter(Boolean))]; }
const FOOT_H = 20;
// rows as drawn: a column group's header, then its columns indented, where its first column stands
function displayItems(e, rows) {
  const groups = {}; (e.column_groups || []).forEach((g) => (groups[g.id] = g));
  const out = [], done = new Set();
  rows.forEach((a) => {
    const g = groups[a.group_id];
    if (!g) { out.push({ a }); return; }
    if (done.has(g.id)) return;
    done.add(g.id);
    out.push({ g });
    rows.filter((x) => x.group_id === g.id).forEach((x) => out.push({ a: x, indent: true }));
  });
  return out;
}
function boxes(list) {
  return list.map((e, i) => {
    const rows = boxRows(e), items = displayItems(e, rows);
    const top = tagTop(e), foot = derivedFoot(e, rows);
    return { e, rows, items, top, foot, x: e.x != null ? e.x : 60 + (i % 3) * 400, y: e.y != null ? e.y : 60 + Math.floor(i / 3) * 380, w: BOX_W,
      h: top + items.length * ROW_H + (items.length ? 8 : 0) + (foot.length ? FOOT_H : 0) };
  });
}
function clipText(t, px, size) { const max = Math.floor(px / (size * 0.56)); return t.length > max ? t.slice(0, Math.max(1, max - 1)) + "…" : t; }

/* logical groups: a labelled frame round each group's boxes */
const GROUP_COLOURS = [["#EEF5FC", "#2F6DB0"], ["#EEF8F1", "#2E7D4F"], ["#FDF3EA", "#B0601C"], ["#F4F0FB", "#6B46C1"], ["#FCEFF3", "#A63A62"], ["#ECF7F7", "#1F7373"]];
function groupColour(g) { let x = 0; for (const c of g.toLowerCase()) x = (x * 31 + c.charCodeAt(0)) >>> 0; return GROUP_COLOURS[x % GROUP_COLOURS.length]; }
const FRAME_PAD = 24, FRAME_HEAD = 44;
function groupFrames(bxs) {
  if (!S.diag.groups) return [];
  const by = {};
  bxs.forEach((b) => { if (b.e.group) (by[b.e.group] = by[b.e.group] || []).push(b); });
  return Object.entries(by).map(([g, list]) => {
    const x = Math.min(...list.map((b) => b.x)) - FRAME_PAD, y = Math.min(...list.map((b) => b.y)) - FRAME_HEAD;
    const x2 = Math.max(...list.map((b) => b.x + b.w)) + FRAME_PAD, y2 = Math.max(...list.map((b) => b.y + b.h)) + FRAME_PAD;
    return { g, list, x, y, w: x2 - x, h: y2 - y };
  });
}
function allGroups() {
  const m = {};
  M().entities.forEach((e) => { if (e.group) { const k = e.group.toLowerCase(); (m[k] = m[k] || { name: e.group, count: 0 }).count++; } });
  return Object.values(m).sort((a, b) => a.name.localeCompare(b.name));
}

function svgContent(bxs, opts) {
  opts = opts || {};
  const mode = S.diag.labels;
  const byId = {}; bxs.forEach((b) => (byId[b.e.id] = b));
  const rels = M().relationships.filter((r) => byId[r.from_entity_id] && byId[r.to_entity_id]);
  const pairCount = {}, pairIdx = {};
  rels.forEach((r) => { const k = [r.from_entity_id, r.to_entity_id].sort().join("|"); pairCount[k] = (pairCount[k] || 0) + 1; });
  let edges = "", labels = "";
  const font = 'font-family="-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif"';
  let frames = "";
  for (const fr of groupFrames(bxs)) {
    const [bg, fg] = groupColour(fr.g);
    frames += '<g class="grp-frame" data-group="' + esc(fr.g) + '" style="cursor:move">';
    frames += '<rect x="' + fr.x + '" y="' + fr.y + '" width="' + fr.w + '" height="' + fr.h + '" rx="14" fill="' + bg + '" fill-opacity="0.75" stroke="' + fg + '" stroke-width="1.4" stroke-dasharray="7 5"/>';
    frames += '<text x="' + (fr.x + 16) + '" y="' + (fr.y + 27) + '" font-size="14" font-weight="700" fill="' + fg + '" ' + font + ">" + esc(fr.g) + "</text>";
    frames += '<text x="' + (fr.x + fr.w - 14) + '" y="' + (fr.y + 27) + '" text-anchor="end" font-size="11" fill="' + fg + '" ' + font + ">" + fr.list.length + " entit" + (fr.list.length === 1 ? "y" : "ies") + "</text>";
    frames += "</g>";
  }
  for (const r of rels) {
    const A = byId[r.from_entity_id], B = byId[r.to_entity_id];
    const k = [r.from_entity_id, r.to_entity_id].sort().join("|");
    const idx = pairIdx[k] = (pairIdx[k] || 0) + 1;
    const off = (idx - 1 - (pairCount[k] - 1) / 2) * 26;
    const fromMany = ["many-to-one", "many-to-many"].includes(r.cardinality), toMany = ["one-to-many", "many-to-many"].includes(r.cardinality);
    const text = lbl(r.business_name || r.api_name, r.api_name || r.business_name, mode);
    if (A === B) {
      const x = A.x + A.w, y1 = A.y + 14 + idx * 10, y2 = A.y + HEAD_H + 10 + idx * 10;
      edges += '<path d="M' + x + " " + y1 + " C " + (x + 60) + " " + (y1 - 20) + ", " + (x + 60) + " " + (y2 + 20) + ", " + x + " " + y2 + '" fill="none" stroke="#5B6B7F" stroke-width="1.4"/>';
      labels += '<text x="' + (x + 64) + '" y="' + (y1 + y2) / 2 + '" font-size="11" fill="#1F3A5F" ' + font + ' paint-order="stroke" stroke="#fbfcfd" stroke-width="4">' + esc(text) + "</text>";
      continue;
    }
    const ax = A.x + A.w / 2, ay = A.y + A.h / 2, bx = B.x + B.w / 2, by = B.y + B.h / 2;
    let dx = bx - ax, dy = by - ay; const len = Math.hypot(dx, dy) || 1; const ux = dx / len, uy = dy / len;
    const sgn = r.from_entity_id < r.to_entity_id ? 1 : -1; // keep parallel offsets consistent whichever way the link points
    const px = -uy * off * sgn, py = ux * off * sgn;
    const exit = (b, cx, cy, vx, vy) => { const tx = vx ? (b.w / 2 - 1) / Math.abs(vx) : Infinity, ty = vy ? (b.h / 2 - 1) / Math.abs(vy) : Infinity; const t = Math.min(tx, ty); return [cx + vx * t, cy + vy * t]; };
    const [x1, y1] = exit(A, ax + px, ay + py, ux, uy), [x2, y2] = exit(B, bx + px, by + py, -ux, -uy);
    edges += '<line x1="' + x1 + '" y1="' + y1 + '" x2="' + x2 + '" y2="' + y2 + '" stroke="#5B6B7F" stroke-width="1.4"/>';
    edges += endMark(x1, y1, -ux, -uy, fromMany) + endMark(x2, y2, ux, uy, toMany);
    const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
    if (text) labels += '<text x="' + mx + '" y="' + (my - 4) + '" text-anchor="middle" font-size="11.5" fill="#1F3A5F" font-weight="600" ' + font + ' paint-order="stroke" stroke="#fbfcfd" stroke-width="5" stroke-linejoin="round">' + esc(text) + "</text>";
  }
  let boxSvg = "";
  for (const b of bxs) {
    const e = b.e, n = nsById(e.namespace_id), src = srcOf(e);
    const title = mode === "api" ? e.api_name : e.business_name;
    const sub = mode === "api" ? "Business: " + e.business_name : mode === "both" ? "API: " + e.api_name + (e.operations.length ? "  " + resourcePath(e) : "  (" + noPathLabel(e) + ")")
      : (e.business_name.toLowerCase() !== e.api_name.toLowerCase() ? "API: " + e.api_name + "  " : "") + (e.operations.length ? resourcePath(e) : noPathLabel(e));
    let g = '<g class="ent-box" data-id="' + e.id + '" transform="translate(' + b.x + "," + b.y + ')">';
    g += '<rect x="1.5" y="3" width="' + b.w + '" height="' + b.h + '" rx="9" fill="#14213d" opacity="0.07"/>';
    g += '<rect width="' + b.w + '" height="' + b.h + '" rx="8" fill="#ffffff" stroke="#1F3A5F" stroke-width="1.2"/>';
    g += '<path d="M0 8 a8 8 0 0 1 8 -8 h' + (b.w - 16) + " a8 8 0 0 1 8 8 v" + (HEAD_H - 8) + " h-" + b.w + ' z" fill="#1F3A5F"/>';
    g += '<text x="12" y="20" font-size="13.5" font-weight="700" fill="#ffffff" ' + font + ">" + esc(clipText(title, b.w - 60, 13.5)) + "</text>";
    g += '<rect x="0" y="' + (HEAD_H - 4) + '" width="' + b.w + '" height="4" fill="' + sysColour(src) + '"/>';
    g += '<text x="' + (b.w - 10) + '" y="20" text-anchor="end" font-size="10" fill="#9fb3cc" font-family="Menlo,Consolas,monospace">' + esc((n ? n.key + " · " : "") + (src ? src.key : "not sourced")) + "</text>";
    g += '<text x="12" y="37" font-size="10.5" fill="#c9d6e8" font-family="Menlo,Consolas,monospace">' + esc(clipText(sub, b.w - 20, 10.5)) + "</text>";
    if ((e.tags || []).length) {
      let tx = 10;
      for (const t of e.tags) {
        const label = clipText(t, 120, 10.5), w = Math.round(label.length * 6.1) + 14;
        if (tx + w > b.w - 8) { g += '<text x="' + tx + '" y="' + (HEAD_H + 16) + '" font-size="10.5" fill="#5B6B7F" ' + font + '>+' + (e.tags.length - e.tags.indexOf(t)) + "</text>"; break; }
        const [bg, fg] = tagColour(t);
        g += '<rect x="' + tx + '" y="' + (HEAD_H + 5) + '" width="' + w + '" height="16" rx="8" fill="' + bg + '"/>';
        g += '<text x="' + (tx + 7) + '" y="' + (HEAD_H + 16.5) + '" font-size="10.5" fill="' + fg + '" ' + font + ">" + esc(label) + "</text>";
        tx += w + 5;
      }
    }
    b.items.forEach((it, i) => {
      const y = b.top + 4 + i * ROW_H + 15;
      if (it.g) {   // a column group: bankDetails { } or bankDetails [ ]
        const G = it.g;
        g += '<rect x="1" y="' + (b.top + 4 + i * ROW_H) + '" width="' + (b.w - 2) + '" height="' + ROW_H + '" fill="#EEF3F8"/>';
        g += '<text x="12" y="' + y + '" font-size="11.5" font-weight="700" fill="#1F3A5F" font-family="Menlo,Consolas,monospace">' + esc(clipText((mode === "business" ? G.business_name || G.api_name : G.api_name), b.w - 90, 11.5)) + "</text>";
        g += '<text x="' + (b.w - 10) + '" y="' + y + '" text-anchor="end" font-size="10.5" fill="#1F3A5F" font-family="Menlo,Consolas,monospace">' + (G.many ? "list [ { } ]" : "object { }") + "</text>";
        return;
      }
      const a = it.a, ix = it.indent ? 14 : 0;
      if (it.indent) g += '<line x1="' + 18 + '" y1="' + (b.top + 4 + i * ROW_H) + '" x2="18" y2="' + (b.top + 4 + (i + 1) * ROW_H) + '" stroke="#9DB1C7" stroke-width="1.2"/>';
      if (i % 2 === 1) g += '<rect x="1" y="' + (b.top + 4 + i * ROW_H) + '" width="' + (b.w - 2) + '" height="' + ROW_H + '" fill="#f6f8fa" fill-opacity="0.8"/>';
      if (a.key) g += '<rect x="10" y="' + (y - 11) + '" width="20" height="14" rx="3" fill="#1F3A5F"/><text x="20" y="' + (y - 1) + '" text-anchor="middle" font-size="8.5" font-weight="700" fill="#fff" font-family="Menlo,Consolas,monospace">PK</text>';
      const name = lbl(a.business_name, a.api_name, mode) + (a.required && !a.key ? " *" : "");
      const tt = (a.type === "date-time" ? "datetime" : a.type) + (a.money ? " £" : "");
      const sysCol = a.system && !a.key;
      const dv = derivedOf(e, a);
      if (dv.derived) {
        g += '<g><title>' + esc(a.business_name + (dv.from ? ": derived from " + dv.from : ": derived (calculated or looked up)")) + '</title>';
        g += '<rect x="1" y="' + (b.top + 4 + i * ROW_H) + '" width="' + (b.w - 2) + '" height="' + ROW_H + '" fill="#F3EEFC"/>';
        g += '<rect x="1" y="' + (b.top + 4 + i * ROW_H) + '" width="3" height="' + ROW_H + '" fill="' + DRV + '"/>';
        if (!a.key) g += '<rect x="8" y="' + (y - 11) + '" width="24" height="14" rx="3" fill="#fff" stroke="' + DRV + '" stroke-width="1"/><text x="20" y="' + (y - 1.5) + '" text-anchor="middle" font-size="7.5" font-weight="700" fill="' + DRV + '" font-family="Menlo,Consolas,monospace">DRV</text>';
        g += "</g>";
      }
      const fill = dv.derived ? DRV : sysCol ? "#5B6B7F" : "#14213d";
      g += '<text x="' + (36 + ix) + '" y="' + y + '" font-size="12" fill="' + fill + '" ' + (a.key ? 'font-weight="600" ' : "") + (sysCol || dv.derived ? 'font-style="italic" ' : "") + font + ">" + esc(clipText(name, b.w - 110 - ix, 12)) + "</text>";
      g += '<text x="' + (b.w - 10) + '" y="' + y + '" text-anchor="end" font-size="10.5" fill="#5B6B7F" font-family="Menlo,Consolas,monospace">' + esc(tt) + (a.pii ? " ●" : "") + "</text>";
    });
    if (!b.rows.length && S.diag.cols !== "none") g += '<text x="12" y="' + (b.top + 16) + '" font-size="11" fill="#5B6B7F" ' + font + ">no columns</text>";
    if (b.foot.length) {
      const fy = b.h - FOOT_H;
      g += '<line x1="8" y1="' + (fy + 2) + '" x2="' + (b.w - 8) + '" y2="' + (fy + 2) + '" stroke="' + DRV + '" stroke-dasharray="3 3" stroke-width="0.8"/>';
      g += '<text x="12" y="' + (fy + 15) + '" font-size="10.5" fill="' + DRV + '" font-style="italic" ' + font + ">" + esc(clipText("Derived from " + b.foot.join(", "), b.w - 24, 10.5)) + "</text>";
    }
    g += "</g>";
    boxSvg += g;
  }
  return '<g class="frames">' + frames + '</g><g class="edges">' + edges + "</g>" + boxSvg + '<g class="labels">' + labels + "</g>";
}
function endMark(x, y, ux, uy, many) {
  // (x,y) on the box edge; (ux,uy) points from the line into the box
  const qx = -ux, qy = -uy, nx = -uy, ny = ux; let s = "";
  const L = (x1, y1, x2, y2) => '<line x1="' + x1 + '" y1="' + y1 + '" x2="' + x2 + '" y2="' + y2 + '" stroke="#1F3A5F" stroke-width="1.4"/>';
  if (many) {
    const bx = x + qx * 13, by = y + qy * 13;
    s += L(bx, by, x + nx * 7, y + ny * 7) + L(bx, by, x, y) + L(bx, by, x - nx * 7, y - ny * 7);
    s += '<circle cx="' + (x + qx * 19) + '" cy="' + (y + qy * 19) + '" r="4" fill="#fff" stroke="#1F3A5F" stroke-width="1.3"/>';
  } else {
    for (const d of [8, 13]) s += L(x + qx * d + nx * 7, y + qy * d + ny * 7, x + qx * d - nx * 7, y + qy * d - ny * 7);
  }
  return s;
}
function bbox(bxs) {
  if (!bxs.length) return { x: 0, y: 0, w: 400, h: 300 };
  const fr = groupFrames(bxs);
  if (fr.length) {
    const all = bxs.concat(fr.map((f) => ({ x: f.x, y: f.y, w: f.w - 110 + 40, h: f.h })));
    const x0 = Math.min(...all.map((b) => b.x)) - 40, y0 = Math.min(...all.map((b) => b.y)) - 40;
    const x1 = Math.max(...all.map((b) => b.x + b.w)) + 110, y1 = Math.max(...all.map((b) => b.y + b.h)) + 40;
    return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
  }
  const x0 = Math.min(...bxs.map((b) => b.x)) - 40, y0 = Math.min(...bxs.map((b) => b.y)) - 40;
  const x1 = Math.max(...bxs.map((b) => b.x + b.w)) + 110, y1 = Math.max(...bxs.map((b) => b.y + b.h)) + 40;
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

function viewDiagram() {
  const D = S.diag;
  const seg = (opts, cur, set) => h("div", { class: "seg" }, opts.map(([v, l]) => h("button", { class: cur === v ? "on" : null, onclick: () => { set(v); render(); } }, l)));
  const nsSel = selectEl([["", "All namespaces"]].concat(M().namespaces.map((n) => [n.key, n.key + " — " + n.title])), D.ns);
  nsSel.onchange = () => { D.ns = nsSel.value; D.fitted = false; render(); };
  if (D.src && D.src !== "-" && !srcById(D.src)) D.src = "";
  const srcSel = selectEl([["", "All source systems"]].concat(M().source_systems.map((s) => [s.id, s.name]), [["-", "Not sourced"]]), D.src || "");
  srcSel.onchange = () => { D.src = srcSel.value; D.fitted = false; render(); };
  // drop tags that no longer exist
  D.tags = D.tags.filter((t) => allTags().some((x) => x.name.toLowerCase() === t.toLowerCase()));
  const filtering = D.ns || D.src || D.tags.length;
  const q = "labels=" + D.labels + (D.ns ? "&ns=" + encodeURIComponent(D.ns) : "") + (D.src ? "&src=" + encodeURIComponent(D.src) : "")
    + (D.tags.length ? "&tags=" + encodeURIComponent(D.tags.join(",")) : "") + (filtering ? "&linked=" + (D.linked ? "true" : "false") : "");
  const toggleTag = (t) => { const on = D.tags.some((x) => x.toLowerCase() === t.toLowerCase()); D.tags = on ? D.tags.filter((x) => x.toLowerCase() !== t.toLowerCase()) : D.tags.concat([t]); D.fitted = false; render(); };
  const tagBar = h("div", { class: "diagram-tags" }, h("span", { class: "muted small" }, "Tags"),
    allTags().length ? allTags().map((t) => tagChip(t.name, { count: t.count, off: D.tags.length && !D.tags.some((x) => x.toLowerCase() === t.name.toLowerCase()),
      onclick: () => toggleTag(t.name), title: "Show entities tagged " + t.name + " (click again to remove)" }))
      : h("span", { class: "muted small" }, "none yet: tag entities to filter the diagram by subject area"),
    D.tags.length ? h("button", { class: "link-btn", onclick: () => { D.tags = []; D.fitted = false; render(); } }, "Clear") : null,
    D.tags.length > 1 ? h("span", { class: "muted small" }, "showing entities with any of these") : null,
    h("button", { class: "btn btn-sm", style: "margin-left:auto", onclick: tagsModal }, "Manage tags…"));
  return h("div", null,
    h("div", { class: "page-head" }, h("div", null, h("h1", null, "Data model diagram"),
      h("div", { class: "sub" }, "Drag boxes to arrange (positions are saved). Drag the background to pan, scroll to zoom, double-click a box to edit it."))),
    h("div", { class: "card" },
      h("div", { class: "diagram-tools" },
        h("div", { class: "grp" }, h("span", { class: "muted small" }, "Names"), seg([["business", "Business"], ["api", "API"], ["both", "Both"]], D.labels, (v) => (D.labels = v)),
          h("span", { class: "muted small", style: "margin-left:6px" }, "Columns"), seg([["all", "All"], ["keys", "Key & required"], ["none", "None"]], D.cols, (v) => (D.cols = v)), nsSel, srcSel,
          filtering ? h("span", { class: "muted small", style: "margin-left:6px" }, "Linked from elsewhere") : null,
          filtering ? seg([["hide", "Hide"], ["show", "Show"]], D.linked ? "show" : "hide", (v) => { D.linked = v === "show"; D.fitted = false; }) : null),
        h("div", { class: "grp" },
          h("span", { class: "muted small" }, "Groups"), seg([["show", "Show"], ["hide", "Hide"]], D.groups ? "show" : "hide", (v) => { D.groups = v === "show"; }),
          h("button", { class: "btn btn-sm", "data-k": "groups-btn", onclick: groupsModal }, "Groups…"),
          h("button", { class: "btn btn-sm", onclick: erdImportModal }, "Import ERD…"),
          h("button", { class: "btn btn-sm", onclick: autoLayout }, "Tidy layout"),
          h("button", { class: "btn btn-sm", onclick: () => exportSvg(false) }, "SVG"),
          h("button", { class: "btn btn-sm", onclick: () => exportSvg(true) }, "PNG"),
          h("button", { class: "btn btn-sm", onclick: () => download("api/export/diagram.mmd?download=true&" + q) }, "Mermaid"),
          h("button", { class: "btn btn-sm", onclick: () => download("api/export/diagram.drawio?" + q) }, "draw.io"))),
      tagBar,
      h("div", { class: "canvas", id: "canvas" },
        h("div", { class: "legend", html: '<svg width="34" height="14"><line x1="0" y1="7" x2="34" y2="7" stroke="#5B6B7F"/><line x1="26" y1="0" x2="26" y2="14" stroke="#1F3A5F"/><line x1="21" y1="0" x2="21" y2="14" stroke="#1F3A5F"/></svg> one &nbsp; <svg width="34" height="14"><line x1="0" y1="7" x2="34" y2="7" stroke="#5B6B7F"/><circle cx="15" cy="7" r="3.5" fill="#fff" stroke="#1F3A5F"/><line x1="21" y1="7" x2="34" y2="0" stroke="#1F3A5F"/><line x1="21" y1="7" x2="34" y2="14" stroke="#1F3A5F"/></svg> zero or many &nbsp; <b style="color:#1F3A5F">PK</b> key &nbsp; * required &nbsp; ● personal data &nbsp; <i>italic</i> system column &nbsp; <span style="display:inline-block;border:1px solid #6B46C1;color:#6B46C1;border-radius:3px;font:700 8px Menlo,monospace;padding:0 2px;background:#fff">DRV</span> <span style="color:#6B46C1;font-style:italic">derived: another business component or calculated</span>' + sourceLegend() }),
        h("div", { class: "zoom" }, h("button", { title: "Zoom in", onclick: () => zoomBy(1.2) }, "+"), h("button", { title: "Zoom out", onclick: () => zoomBy(1 / 1.2) }, "−"), h("button", { title: "Fit", onclick: () => { D.fitted = false; drawDiagram(); } }, "⤢")))));
}

let dragState = null;
function drawDiagram() {
  const canvas = document.getElementById("canvas"); if (!canvas) return;
  const D = S.diag;
  const bxs = boxes(diagEntities());
  if (!D.fitted) {
    const bb = bbox(bxs), cw = canvas.clientWidth || 900, ch = canvas.clientHeight || 600;
    D.z = Math.min(1.25, Math.max(0.3, Math.min(cw / bb.w, ch / bb.h)));
    D.px = (cw - bb.w * D.z) / 2 - bb.x * D.z; D.py = (ch - bb.h * D.z) / 2 - bb.y * D.z; D.fitted = true;
  }
  let svg = canvas.querySelector("svg.diagram");
  if (!svg) {
    svg = document.createElementNS("http://www.w3.org/2000/svg", "svg"); svg.setAttribute("class", "diagram");
    canvas.insertBefore(svg, canvas.firstChild);
    wireDiagram(canvas, svg);
  }
  svg.innerHTML = '<g id="vp" transform="translate(' + D.px + "," + D.py + ") scale(" + D.z + ')">' + svgContent(bxs) + "</g>";
  if (!bxs.length) svg.innerHTML = '<text x="40" y="60" font-size="14" fill="#5B6B7F">No entities to show.</text>';
}
function zoomBy(f, cx, cy) {
  const D = S.diag, canvas = document.getElementById("canvas");
  if (cx === undefined) { cx = canvas.clientWidth / 2; cy = canvas.clientHeight / 2; }
  const nz = Math.min(3, Math.max(0.2, D.z * f));
  D.px = cx - (cx - D.px) * (nz / D.z); D.py = cy - (cy - D.py) * (nz / D.z); D.z = nz; drawDiagram();
}
function wireDiagram(canvas, svg) {
  const D = S.diag;
  svg.addEventListener("pointerdown", (ev) => {
    const g = ev.target.closest(".ent-box");
    const fg = !g && ev.target.closest(".grp-frame");
    const rect = canvas.getBoundingClientRect();
    if (fg) {
      const list = boxes(diagEntities()).filter((b) => b.e.group === fg.dataset.group);
      dragState = { kind: "group", list: list.map((b) => ({ e: b.e, ox: b.x, oy: b.y })), sx: ev.clientX, sy: ev.clientY, moved: false };
    } else if (g) {
      const e = entById(g.dataset.id); const b = boxes(diagEntities()).find((x) => x.e.id === e.id);
      dragState = { kind: "box", e, sx: ev.clientX, sy: ev.clientY, ox: b.x, oy: b.y, moved: false };
    } else dragState = { kind: "pan", sx: ev.clientX, sy: ev.clientY, ox: D.px, oy: D.py, rect };
    svg.setPointerCapture(ev.pointerId);
    if (dragState.kind === "pan") canvas.classList.add("panning");
  });
  svg.addEventListener("pointermove", (ev) => {
    if (!dragState) return;
    const dx = ev.clientX - dragState.sx, dy = ev.clientY - dragState.sy;
    if (dragState.kind === "pan") { D.px = dragState.ox + dx; D.py = dragState.oy + dy; drawDiagram(); return; }
    if (dragState.kind === "group") {
      if (Math.abs(dx) + Math.abs(dy) > 2) dragState.moved = true;
      dragState.list.forEach((m) => { m.e.x = Math.round((m.ox + dx / D.z) / 10) * 10; m.e.y = Math.round((m.oy + dy / D.z) / 10) * 10; });
      drawDiagram(); return;
    }
    if (Math.abs(dx) + Math.abs(dy) > 2) dragState.moved = true;
    dragState.e.x = Math.round((dragState.ox + dx / D.z) / 10) * 10; dragState.e.y = Math.round((dragState.oy + dy / D.z) / 10) * 10;
    drawDiagram();
  });
  const end = async () => {
    const st = dragState; dragState = null; canvas.classList.remove("panning");
    if (st && st.kind === "group" && st.moved) {
      const pos = {}; st.list.forEach((m) => (pos[m.e.id] = { x: m.e.x, y: m.e.y }));
      try { await api("POST", "api/positions", pos); } catch (err) { toast(err.message, true); }
    }
    if (st && st.kind === "box" && st.moved) {
      try { await api("POST", "api/positions", { [st.e.id]: { x: st.e.x, y: st.e.y } }); } catch (err) { toast(err.message, true); }
    }
  };
  svg.addEventListener("pointerup", end); svg.addEventListener("pointercancel", end);
  svg.addEventListener("dblclick", (ev) => {
    // hit-test by position: pointer capture can retarget the event to the svg itself
    const r = canvas.getBoundingClientRect();
    const x = (ev.clientX - r.left - D.px) / D.z, y = (ev.clientY - r.top - D.py) / D.z;
    const hit = boxes(diagEntities()).reverse().find((b) => x >= b.x && x <= b.x + b.w && y >= b.y && y <= b.y + b.h);
    if (hit) go("entity", hit.e.id);
  });
  svg.addEventListener("wheel", (ev) => { ev.preventDefault(); const r = canvas.getBoundingClientRect(); zoomBy(ev.deltaY < 0 ? 1.1 : 1 / 1.1, ev.clientX - r.left, ev.clientY - r.top); }, { passive: false });
}
async function autoLayout() {
  // small diagrams: most-linked entities in the middle column, the rest around them.
  // large ones (12+): one band per namespace, four columns, so subject areas stay together.
  const list = diagEntities().slice();
  const deg = (e) => M().relationships.filter((r) => r.from_entity_id === e.id || r.to_entity_id === e.id).length;
  const pos = {};
  const hgt = (e) => tagTop(e) + displayItems(e, boxRows(e)).length * ROW_H + 8 + (derivedFoot(e, boxRows(e)).length ? FOOT_H : 0);
  // on a filtered view, lay the set out from where it already starts, so other areas are not covered
  const placed = list.filter((e) => e.x != null && e.y != null);
  const filtered = (S.diag.ns || S.diag.src || S.diag.tags.length) && list.length < M().entities.length;
  const ox = filtered && placed.length ? Math.min(...placed.map((e) => e.x)) : 60;
  const oy = filtered && placed.length ? Math.min(...placed.map((e) => e.y)) : 60;
  if (S.diag.groups && list.some((e) => e.group)) {
    // one cluster per logical group (framed), then the ungrouped entities; clusters left to right, wrapping
    const clusters = {};
    list.forEach((e) => { const k = e.group || "\u0000"; (clusters[k] = clusters[k] || []).push(e); });
    const keys = Object.keys(clusters).sort((a, b) => (a === "\u0000") - (b === "\u0000") || clusters[b].length - clusters[a].length);
    let cx = ox, rowY = oy, rowH = 0;
    for (const k of keys) {
      const ms = clusters[k].sort((a, b) => deg(b) - deg(a));
      const cols = ms.length <= 2 ? ms.length : ms.length <= 4 ? 2 : 3;
      const width = cols * (BOX_W + 70) - 70;
      if (cx > ox && cx + width > ox + 2600) { cx = ox; rowY += rowH + 140; rowH = 0; }
      const top = rowY + (k === "\u0000" ? 0 : FRAME_HEAD);
      const colY = Array(cols).fill(top);
      ms.forEach((e) => {
        const c = colY.indexOf(Math.min(...colY));
        e.x = cx + c * (BOX_W + 70); e.y = colY[c]; colY[c] += hgt(e) + 60; pos[e.id] = { x: e.x, y: e.y };
      });
      rowH = Math.max(rowH, Math.max(...colY) - rowY);
      cx += width + 140;
    }
  } else if (list.length < 12) {
    list.sort((a, b) => deg(b) - deg(a));
    const colsN = list.length <= 2 ? list.length : list.length <= 4 ? 2 : 3;
    const order = colsN === 3 ? [1, 0, 2] : [0, 1];
    const colY = [oy, oy, oy];
    list.forEach((e, i) => {
      const c = order[i % colsN];
      e.x = ox + c * (BOX_W + 150); e.y = colY[c]; colY[c] += hgt(e) + 70; pos[e.id] = { x: e.x, y: e.y };
    });
  } else {
    let y = oy;
    for (const n of M().namespaces) {
      const band = list.filter((e) => e.namespace_id === n.id).sort((a, b) => deg(b) - deg(a));
      if (!band.length) continue;
      const colY = [y, y, y, y];
      band.forEach((e) => {
        const c = colY.indexOf(Math.min(...colY));
        e.x = ox + c * (BOX_W + 90); e.y = colY[c]; colY[c] += hgt(e) + 60; pos[e.id] = { x: e.x, y: e.y };
      });
      y = Math.max(...colY) + 120;
    }
  }
  await api("POST", "api/positions", pos); S.diag.fitted = false; drawDiagram(); toast("Layout tidied");
}
function exportSvg(png) {
  const bxs = boxes(diagEntities()); const bb = bbox(bxs);
  const title = M().settings.title + " — data model";
  const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="' + bb.w + '" height="' + (bb.h + 30) + '" viewBox="' + bb.x + " " + (bb.y - 30) + " " + bb.w + " " + (bb.h + 30) + '">' +
    '<rect x="' + bb.x + '" y="' + (bb.y - 30) + '" width="' + bb.w + '" height="' + (bb.h + 30) + '" fill="#ffffff"/>' +
    '<text x="' + (bb.x + 20) + '" y="' + (bb.y - 4) + '" font-size="15" font-weight="700" fill="#1F3A5F" font-family="-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">' + esc(title) + "</text>" +
    svgContent(bxs).replace(/stroke="#fbfcfd"/g, 'stroke="#ffffff"') + "</svg>";
  const blob = new Blob([svg], { type: "image/svg+xml" });
  if (!png) { saveBlob(blob, "data-model.svg"); return; }
  const img = new Image(); const u = URL.createObjectURL(blob);
  img.onload = () => {
    const c = document.createElement("canvas"); c.width = bb.w * 2; c.height = (bb.h + 30) * 2;
    const ctx = c.getContext("2d"); ctx.scale(2, 2); ctx.drawImage(img, 0, 0); URL.revokeObjectURL(u);
    c.toBlob((b) => saveBlob(b, "data-model.png"), "image/png");
  };
  img.src = u;
}

/* ---------- start ---------- */
(async function start() {
  try { await reload(); S.route = parseHash(); render(); }
  catch (err) { document.getElementById("main").appendChild(h("div", { class: "callout err" }, "Could not load the model: " + err.message)); }
})();
