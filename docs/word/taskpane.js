/* KherveRef panel for Word.
 *
 * Citations are content controls tagged "KREF:key1;key2"; the
 * bibliography is one tagged "KREF-BIB". "Refresh all" reads them in
 * document order, asks KherveRef (running on this computer) to format
 * them in the chosen style, and writes the results back — so numbering
 * and the reference list always follow the document.
 *
 * The first citation also puts a "References" heading and the list at
 * the end of the document. Only once per document: a list the user
 * deleted stays deleted (Insert bibliography brings it back, at the
 * cursor; pressed when there is one already, it moves it there).
 */
"use strict";

// Served by KherveRef itself (Word for Mac) the API is this page's own
// origin; served from GitHub Pages it is KherveRef's loopback address.
const API = (location.hostname === "127.0.0.1" || location.hostname === "localhost"
  ? location.origin : "http://127.0.0.1:23120") + "/api";
const CITE_TAG = "KREF:";
const BIB_TAG = "KREF-BIB";
const BIB_HEAD_TAG = "KREF-BIB-HEADING";
const STYLE_SETTING = "kherveref-style";
const AUTO_BIB_SETTING = "kherveref-bibliography-added";

const $ = (id) => document.getElementById(id);
let selected = [];          // keys picked in the list, in click order
let searchTimer = null;

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body) };
  const r = await fetch(API + path, opts);
  if (!r.ok) throw new Error(`KherveRef answered ${r.status}`);
  return r.json();
}

function status(text) { $("status").textContent = text || ""; }

// Word's errors say little on screen ("GeneralException"); KherveRef keeps
// the details in word-panel.log for whoever has to fix it.
function report(what, e) {
  const info = e && e.debugInfo ? JSON.stringify(e.debugInfo) : "";
  const msg = `${what}: ${e && (e.name || "")} ${e && e.message} ${info} | Word ${
    Office.context && Office.context.diagnostics ? Office.context.diagnostics.version : "?"}`;
  fetch(API + "/log", { method: "POST", headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ message: msg }) }).catch(() => {});
}

async function guarded(what, fn) {
  try { await fn(); } catch (e) {
    report(what, e);
    const where = e.debugInfo && e.debugInfo.errorLocation;
    status(`Could not ${what}: ${e.message}${where ? ` (${where})` : ""}`);
  }
}

function currentStyle() { return $("style").value || "apa"; }

// Inside Word the style is saved with the document; opened in a plain
// browser (for testing) there is no document, so remember it locally.
const settings = {
  get(k) {
    const d = Office.context && Office.context.document;
    return d ? d.settings.get(k) : localStorage.getItem(k);
  },
  set(k, v) {
    const d = Office.context && Office.context.document;
    if (d) { d.settings.set(k, v); d.settings.saveAsync(); }
    else localStorage.setItem(k, v);
  },
};

async function connect() {
  try {
    const s = await api("/status");
    $("offline").hidden = true;
    $("main").hidden = false;
    $("library").textContent = s.library
      ? `${s.library} · ${s.count} references` : "no library open in KherveRef";
    const saved = settings.get(STYLE_SETTING);
    const esc = (t) => String(t).replace(/[&<>"]/g, (c) => `&#${c.charCodeAt(0)};`);
    const groups = new Map();
    for (const st of s.styles) {
      const g = st.group || "Styles";
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push(`<option value="${esc(st.id)}">${esc(st.label)}</option>`);
    }
    $("style").innerHTML = [...groups].map(
      ([g, opts]) => `<optgroup label="${esc(g)}">${opts.join("")}</optgroup>`).join("");
    $("style").value = saved || s.default_style;
    search();
  } catch (e) {
    $("main").hidden = true;
    $("offline").hidden = false;
  }
}

function render(items) {
  const ul = $("results");
  ul.innerHTML = "";
  for (const it of items) {
    const li = document.createElement("li");
    li.dataset.key = it.key;
    li.className = selected.includes(it.key) ? "selected" : "";
    const t = document.createElement("div");
    t.className = "t";
    t.textContent = it.title || it.key;
    const m = document.createElement("div");
    m.className = "m";
    m.textContent = [it.authors, it.year, it.container].filter(Boolean).join(" · ");
    li.append(t, m);
    li.title = it.key;
    li.onclick = () => toggle(it.key, li);
    li.ondblclick = () => {
      if (!selected.includes(it.key)) toggle(it.key, li);
      guarded("insert the citation", insertCitation);
    };
    ul.append(li);
  }
  if (!items.length) ul.innerHTML = '<li class="m">No matching references.</li>';
}

function toggle(key, li) {
  const i = selected.indexOf(key);
  if (i >= 0) { selected.splice(i, 1); li.classList.remove("selected"); }
  else { selected.push(key); li.classList.add("selected"); }
  $("cite").disabled = !selected.length;
  $("selected").textContent = selected.length > 1 ? `${selected.length} selected` : "";
}

async function search() {
  try {
    const r = await api(`/search?limit=80&q=${encodeURIComponent($("search").value)}`);
    render(r.items);
  } catch (e) { connect(); }
}

async function insertCitation() {
  if (!selected.length) return;
  const keys = selected.slice();
  await Word.run(async (ctx) => {
    const range = ctx.document.getSelection();
    const cc = range.insertContentControl();
    cc.tag = CITE_TAG + keys.join(";");
    cc.title = "KherveRef citation";
    cc.appearance = "BoundingBox";
    cc.insertText("[" + keys.join("; ") + "]", "Replace");
    // Typing after the citation must not land inside it.
    cc.getRange("After").select();
    await ctx.sync();
  });
  selected = [];
  $("cite").disabled = true;
  $("selected").textContent = "";
  document.querySelectorAll("#results li.selected").forEach((li) => li.classList.remove("selected"));
  await refreshAll();
}

function newBibliography(cc) {
  cc.tag = BIB_TAG;
  cc.title = "KherveRef bibliography";
  cc.appearance = "BoundingBox";
  cc.insertText("Bibliography", "Replace");
}

// A "References" heading and the list: as the last thing in the
// document, or (where = a range) in place of that range.
function headedBibliography(ctx, where) {
  const body = ctx.document.body;
  const heading = where ? where.insertParagraph("References", "Before")
                        : body.insertParagraph("References", "End");
  heading.styleBuiltIn = Word.Style.heading1;
  const hc = heading.insertContentControl();
  hc.tag = BIB_HEAD_TAG;
  hc.title = "KherveRef bibliography heading";
  const p = heading.insertParagraph("Bibliography", "After");
  p.styleBuiltIn = Word.Style.normal;
  newBibliography(p.insertContentControl());
}

async function ensureBibliography(ctx, citeCount) {
  if (!citeCount || settings.get(AUTO_BIB_SETTING)) return false;
  const existing = ctx.document.contentControls.getByTag(BIB_TAG);
  existing.load("items");
  await ctx.sync();
  if (!existing.items.length) {
    headedBibliography(ctx, null);
    await ctx.sync();
  }
  settings.set(AUTO_BIB_SETTING, "1");
  return !existing.items.length;
}

async function insertBibliography() {
  await Word.run(async (ctx) => {
    const old = ctx.document.contentControls;
    old.load("items/tag");
    const sel = ctx.document.getSelection();
    const inside = sel.parentContentControlOrNullObject;
    inside.load("tag");
    await ctx.sync();
    if (!inside.isNullObject && [BIB_TAG, BIB_HEAD_TAG].includes(inside.tag)) {
      status("The cursor is in the bibliography already. Click where it should go instead.");
      return;
    }
    const mine = old.items.filter((c) => [BIB_TAG, BIB_HEAD_TAG].includes(c.tag));
    // The old heading and list go with the paragraphs they fill.
    const paras = mine.map((c) => {
      const ps = c.getRange("Whole").paragraphs;
      ps.load("items");
      return ps;
    });
    await ctx.sync();
    paras.forEach((ps) => ps.items.forEach((p) => p.delete()));
    headedBibliography(ctx, sel.paragraphs.getFirst());
    settings.set(AUTO_BIB_SETTING, "1");
    await ctx.sync();
    if (mine.length) status("Bibliography moved here.");
  });
  await refreshAll();
}

async function refreshAll() {
  status("Updating…");
  try {
    await Word.run(async (ctx) => {
      const all = ctx.document.contentControls;
      all.load("items/tag,items/font/name,items/font/size");
      const first = ctx.document.body.paragraphs.getFirst();
      first.load("font/name,font/size");
      await ctx.sync();
      // insertHtml brings Word's default HTML font; keep each control in
      // the font of the text it sits in (or the document's body font when
      // the control already mixes fonts).
      const keepFont = (c) => ({ name: c.font.name || first.font.name,
                                 size: c.font.size || first.font.size });
      let cites = all.items.filter((c) => (c.tag || "").startsWith(CITE_TAG));
      let bibs = all.items.filter((c) => c.tag === BIB_TAG);
      if (!bibs.length && await ensureBibliography(ctx, cites.length)) {
        all.load("items/tag,items/font/name,items/font/size");
        await ctx.sync();
        cites = all.items.filter((c) => (c.tag || "").startsWith(CITE_TAG));
        bibs = all.items.filter((c) => c.tag === BIB_TAG);
      }
      const clusters = cites.map((c) => c.tag.slice(CITE_TAG.length).split(";").filter(Boolean));
      const f = await api("/format", { clusters, style: currentStyle() });
      const fonts = new Map([...cites, ...bibs].map((c) => [c, keepFont(c)]));
      cites.forEach((c, i) => c.insertHtml(f.citations[i] || "?", "Replace"));
      const html = f.bibliography.map((b) => `<p>${b}</p>`).join("");
      bibs.forEach((b) => b.insertHtml(html || "<p>(no citations yet)</p>", "Replace"));
      for (const [c, font] of fonts) {
        if (font.name) c.font.name = font.name;
        if (font.size) c.font.size = font.size;
      }
      await ctx.sync();
      const missing = f.missing.length ? ` · not in the library: ${f.missing.join(", ")}` : "";
      status(`${cites.length} citation${cites.length === 1 ? "" : "s"} updated${missing}`);
    });
  } catch (e) {
    report("update", e);
    const where = e.debugInfo && e.debugInfo.errorLocation;
    status("Could not update: " + e.message + (where ? ` (${where})` : ""));
  }
}

Office.onReady(() => {
  $("search").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(search, 150);
  });
  $("cite").onclick = () => guarded("insert the citation", insertCitation);
  $("bibliography").onclick = () => guarded("insert the bibliography", insertBibliography);
  $("refresh").onclick = refreshAll;
  $("retry").onclick = connect;
  $("style").onchange = () => {
    settings.set(STYLE_SETTING, currentStyle());
    if (typeof Word !== "undefined" && Office.context && Office.context.document) refreshAll();
  };
  connect();
});
