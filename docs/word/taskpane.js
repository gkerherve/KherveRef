/* KherveRef panel for Word.
 *
 * Citations are content controls tagged "KREF:key1;key2"; the
 * bibliography is one tagged "KREF-BIB". "Refresh all" reads them in
 * document order, asks KherveRef (running on this computer) to format
 * them in the chosen style, and writes the results back — so numbering
 * and the reference list always follow the document.
 */
"use strict";

const API = "http://127.0.0.1:23120/api";
const CITE_TAG = "KREF:";
const BIB_TAG = "KREF-BIB";
const STYLE_SETTING = "kherveref-style";

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
    $("style").innerHTML = s.styles.map(
      (st) => `<option value="${st.id}">${st.label}</option>`).join("");
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
    li.ondblclick = () => { if (!selected.includes(it.key)) toggle(it.key, li); insertCitation(); };
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

async function insertBibliography() {
  await Word.run(async (ctx) => {
    const existing = ctx.document.contentControls.getByTag(BIB_TAG);
    existing.load("items");
    await ctx.sync();
    if (existing.items.length) {
      existing.items[0].getRange().select();
      await ctx.sync();
      return;
    }
    const cc = ctx.document.getSelection().insertContentControl();
    cc.tag = BIB_TAG;
    cc.title = "KherveRef bibliography";
    cc.appearance = "BoundingBox";
    cc.insertText("Bibliography", "Replace");
    await ctx.sync();
  });
  await refreshAll();
}

async function refreshAll() {
  status("Updating…");
  try {
    await Word.run(async (ctx) => {
      const all = ctx.document.contentControls;
      all.load("items/tag");
      await ctx.sync();
      const cites = all.items.filter((c) => (c.tag || "").startsWith(CITE_TAG));
      const bibs = all.items.filter((c) => c.tag === BIB_TAG);
      const clusters = cites.map((c) => c.tag.slice(CITE_TAG.length).split(";").filter(Boolean));
      const f = await api("/format", { clusters, style: currentStyle() });
      cites.forEach((c, i) => c.insertHtml(f.citations[i] || "?", "Replace"));
      const html = f.bibliography.map((b) => `<p>${b}</p>`).join("");
      bibs.forEach((b) => b.insertHtml(html || "<p>(no citations yet)</p>", "Replace"));
      await ctx.sync();
      const missing = f.missing.length ? ` · not in the library: ${f.missing.join(", ")}` : "";
      status(`${cites.length} citation${cites.length === 1 ? "" : "s"} updated${missing}`);
    });
  } catch (e) {
    status("Could not update: " + e.message);
  }
}

Office.onReady(() => {
  $("search").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(search, 150);
  });
  $("cite").onclick = insertCitation;
  $("bibliography").onclick = insertBibliography;
  $("refresh").onclick = refreshAll;
  $("retry").onclick = connect;
  $("style").onchange = () => {
    settings.set(STYLE_SETTING, currentStyle());
    if (typeof Word !== "undefined" && Office.context && Office.context.document) refreshAll();
  };
  connect();
});
