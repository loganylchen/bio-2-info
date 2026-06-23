"use strict";
// 每日生信资讯 — 客户端归档站。零依赖，fetch ./data/papers.json 后本地渲染。

const BUCKET_LABELS = {
  nanopore_drs: "纳米孔直读",
  rna_mod: "RNA 修饰",
  core: "核心方法",
  ai_bioinfo: "AI 生信",
  ai_application: "AI 应用",
};
const PRIORITY_ORDER = ["🥇", "🥈", "🥉"];

const state = { papers: [], updated: "", view: "latest" };

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const esc = (s) =>
  String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// 只放行 http/https，挡掉 javascript: 等危险 scheme（数据来自 LLM/外部 feed）。
const safeUrl = (u) => {
  const s = String(u == null ? "" : u).trim();
  return /^https?:\/\//i.test(s) ? s : "";
};
const bucketLabel = (b) => BUCKET_LABELS[b] || b || "其他";

function init() {
  $$(".tab").forEach((t) =>
    t.addEventListener("click", () => switchView(t.dataset.view)));
  ["q", "f-bucket", "f-priority", "f-journal"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("input", renderArchive);
  });
  fetch("./data/papers.json", { cache: "no-store" })
    .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then((data) => {
      state.papers = Array.isArray(data.papers) ? data.papers : [];
      state.updated = data.updated || "";
      onLoaded();
    })
    .catch((err) => {
      $("#empty").hidden = false;
      $("#empty").textContent = "数据加载失败：" + err.message;
    });
}

function onLoaded() {
  $("#data-meta").textContent =
    `共 ${state.papers.length} 篇 · 更新于 ${state.updated || "—"}`;
  populateFilters();
  switchView("latest");
}

function switchView(view) {
  state.view = view;
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === view));
  $$(".view").forEach((v) => (v.hidden = true));
  const sec = document.getElementById("view-" + view);
  if (sec) sec.hidden = false;
  if (view === "latest") renderLatest();
  else if (view === "archive") renderArchive();
  else if (view === "timeline") renderTimeline();
  else if (view === "stats") renderStats();
}

// 按 pushed_date 分组，最新一天的论文。
function latestGroup() {
  if (!state.papers.length) return { date: "", items: [] };
  const dates = state.papers.map((p) => p.pushed_date || "").filter(Boolean);
  const newest = dates.sort().reverse()[0] || "";
  return { date: newest, items: state.papers.filter((p) => (p.pushed_date || "") === newest) };
}

function priRank(p) {
  const i = PRIORITY_ORDER.indexOf(p.priority);
  return i === -1 ? 99 : i;
}

function cardHTML(p) {
  const url = safeUrl(p.link);
  const titleInner = url
    ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(p.title)}</a>`
    : esc(p.title);
  const sub = [];
  if (p.journal) sub.push(`<span>${esc(p.journal)}</span>`);
  if (p.date) sub.push(`<span>${esc(p.date)}</span>`);
  if (p.bucket) sub.push(`<span class="tag">${esc(bucketLabel(p.bucket))}</span>`);
  if (p.doi) sub.push(`<span><a href="https://doi.org/${esc(p.doi)}" target="_blank" rel="noopener">DOI</a></span>`);
  return `<article class="card">
    <h3><span class="pri">${esc(p.priority || "")}</span>${titleInner}</h3>
    <p class="sub">${sub.join("")}</p>
    ${p.summary_cn ? `<p class="summary">${esc(p.summary_cn)}</p>` : ""}
    ${p.relevance_cn ? `<p class="rel">${esc(p.relevance_cn)}</p>` : ""}
  </article>`;
}

function renderLatest() {
  const { date, items } = latestGroup();
  $("#latest-date").textContent = date ? `（${date}）` : "";
  const sorted = items.slice().sort((a, b) => priRank(a) - priRank(b));
  $("#latest-list").innerHTML = sorted.map(cardHTML).join("");
}

function populateFilters() {
  const buckets = [...new Set(state.papers.map((p) => p.bucket).filter(Boolean))];
  const journals = [...new Set(state.papers.map((p) => p.journal).filter(Boolean))].sort();
  const priorities = PRIORITY_ORDER.filter((pr) =>
    state.papers.some((p) => p.priority === pr));
  fillSelect("f-bucket", buckets.map((b) => [b, bucketLabel(b)]));
  fillSelect("f-priority", priorities.map((p) => [p, p]));
  fillSelect("f-journal", journals.map((j) => [j, j]));
}

function fillSelect(id, pairs) {
  const el = document.getElementById(id);
  if (!el) return;
  const first = el.querySelector("option");
  el.innerHTML = "";
  if (first) el.appendChild(first);
  pairs.forEach(([val, label]) => {
    const o = document.createElement("option");
    o.value = val; o.textContent = label;
    el.appendChild(o);
  });
}

function renderArchive() {
  const q = ($("#q").value || "").trim().toLowerCase();
  const fb = $("#f-bucket").value, fp = $("#f-priority").value, fj = $("#f-journal").value;
  let rows = state.papers.filter((p) => {
    if (fb && p.bucket !== fb) return false;
    if (fp && p.priority !== fp) return false;
    if (fj && p.journal !== fj) return false;
    if (q) {
      const hay = [p.title, p.summary_cn, p.relevance_cn, p.journal, p.doi]
        .join(" ").toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
  rows = rows.sort((a, b) =>
    (b.pushed_date || "").localeCompare(a.pushed_date || "") ||
    priRank(a) - priRank(b));
  $("#archive-count").textContent = `匹配 ${rows.length} 篇`;
  $("#archive-list").innerHTML =
    rows.length ? rows.map(cardHTML).join("") : `<p class="empty">无匹配结果。</p>`;
}

function renderTimeline() {
  const byDay = new Map();
  state.papers.forEach((p) => {
    const d = p.pushed_date || "未知日期";
    if (!byDay.has(d)) byDay.set(d, []);
    byDay.get(d).push(p);
  });
  const days = [...byDay.keys()].sort().reverse();
  $("#timeline-list").innerHTML = days.map((d) => {
    const items = byDay.get(d).slice().sort((a, b) => priRank(a) - priRank(b));
    const lis = items.map((p) => {
      const url = safeUrl(p.link);
      const t = url
        ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(p.title)}</a>`
        : esc(p.title);
      return `<li><span class="pri">${esc(p.priority || "")}</span>${t}</li>`;
    }).join("");
    return `<div class="tl-day"><h4>${esc(d)} <span class="muted">· ${items.length} 篇</span></h4><ul>${lis}</ul></div>`;
  }).join("");
}

function barRows(pairs, total) {
  const max = pairs.reduce((m, [, n]) => Math.max(m, n), 0) || 1;
  return pairs.map(([label, n]) => {
    const pct = Math.round((n / max) * 100);
    return `<div class="bar-row">
      <span class="label" title="${esc(label)}">${esc(label)}</span>
      <span class="track"><span class="fill" style="width:${pct}%"></span></span>
      <span class="num">${n}</span>
    </div>`;
  }).join("");
}

function countBy(keyFn) {
  const m = new Map();
  state.papers.forEach((p) => {
    const k = keyFn(p);
    if (k == null || k === "") return;
    m.set(k, (m.get(k) || 0) + 1);
  });
  return m;
}

function renderStats() {
  $("#stat-total").textContent = state.papers.length;

  const buckets = [...countBy((p) => p.bucket).entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([b, n]) => [bucketLabel(b), n]);
  $("#stat-buckets").innerHTML = barRows(buckets);

  const months = [...countBy((p) => (p.pushed_date || "").slice(0, 7)).entries()]
    .sort((a, b) => a[0].localeCompare(b[0]));
  $("#stat-months").innerHTML = barRows(months);

  const pri = PRIORITY_ORDER
    .map((pr) => [pr, state.papers.filter((p) => p.priority === pr).length])
    .filter(([, n]) => n > 0);
  $("#stat-priority").innerHTML = barRows(pri);

  const journals = [...countBy((p) => p.journal).entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 10);
  $("#stat-journals").innerHTML = barRows(journals);
}

document.addEventListener("DOMContentLoaded", init);
