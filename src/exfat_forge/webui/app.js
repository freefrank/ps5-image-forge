/* exFAT Forge — UI controller.
 *
 * Talks to Python through window.pywebview.api (see bridge.py). With no
 * bridge present the page runs in DEMO mode with synthetic data so the
 * whole interface can be previewed and styled in a plain browser.
 */
"use strict";

const $ = id => document.getElementById(id);
const $$ = sel => Array.from(document.querySelectorAll(sel));
const bridge = () => (window.pywebview && window.pywebview.api) || null;

let lang = "zh";
let scanRows = [], catItems = [];
const S = {                      // build page state
  mode: "exfat", verify: true, compress: true, keep: false,
  overwrite: false, follow: true, running: false, t0: 0,
  selDump: null, selImage: null,
};

/* ── i18n ─────────────────────────────────────────────── */
function t(key, vars) {
  const table = window.I18N[lang] || window.I18N.en;
  let s = table[key] || window.I18N.en[key] || key;
  if (vars) for (const k in vars) s = s.replace("{" + k + "}", vars[k]);
  return s;
}
function applyLang() {
  $$("[data-i18n]").forEach(el => {
    if (el.id === "phase") return;              // shows live state
    el.textContent = t(el.dataset.i18n);
  });
  $$("[data-i18n-ph]").forEach(el => el.placeholder = t(el.dataset.i18nPh));
  const ph = $("phase");
  ph.textContent = t("phase." + (ph.dataset.k || "standby"));
  $$("#lang-switch span").forEach(el =>
    el.classList.toggle("on", el.dataset.lang === lang));
  document.documentElement.lang = lang;
  renderHistory(lastHistory);
  if (scanRows.length) renderPorts();
  if (catItems.length) renderCatalog();
}

/* ── helpers ──────────────────────────────────────────── */
function gb(n) { return (n / 1073741824).toFixed(2) + " GB"; }
function human(n) {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(u.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  return (n / Math.pow(1024, i)).toFixed(i ? 1 : 0) + " " + u[i];
}
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function log(box, line, cls) {
  const el = typeof box === "string" ? $(box) : box;
  const div = document.createElement("div");
  div.className = "ln" + (cls ? " " + cls : "");
  div.textContent = line;
  el.appendChild(div);
  el.scrollTop = el.scrollHeight;
  while (el.childNodes.length > 600) el.removeChild(el.firstChild);
}
function chip(id, key, initial) {
  const el = $(id);
  if (!el) return;
  S[key] = initial;
  el.classList.toggle("on", initial);
  el.onclick = () => { S[key] = !S[key]; el.classList.toggle("on", S[key]); syncOpts(); };
}

/* ── navigation ───────────────────────────────────────── */
function goto(page) {
  $$(".nav").forEach(n => n.classList.toggle("on", n.dataset.page === page));
  $$(".page").forEach(p => p.classList.toggle("on", p.id === "page-" + page));
  if (page === "history") refreshHistory();
  if (page === "home") refreshHome();
}
$$(".nav").forEach(n => n.onclick = () => goto(n.dataset.page));
$$("[data-goto]").forEach(c => c.onclick = () => goto(c.dataset.goto));

/* ── window drag ──────────────────────────────────────── */
/* pywebview moves the window with one synchronous IPC call per mousemove.
 * A mouse reports 125-1000 times a second and each call costs about 1.3 ms,
 * so an untouched drag saturates the UI thread: the window lags behind the
 * cursor and clicks queue up behind the backlog. Two fixes here — throttle
 * the moves to one per frame, and stop painting animations while dragging. */
(() => {
  const MIN_GAP_MS = 8;          // 125 moves/s ceiling — still fluid, 8x cheaper
  const root = document.documentElement;
  let dragging = false, last = 0;
  const stop = () => { dragging = false; root.classList.remove("dragging"); };

  document.querySelector(".pywebview-drag-region")
    .addEventListener("mousedown", () => {
      dragging = true; last = 0; root.classList.add("dragging");
    });
  window.addEventListener("mouseup", stop);
  window.addEventListener("blur", stop);

  // Capture phase, so this runs before pywebview's own window-level handler
  // and can drop the event before it becomes an IPC call. Gated on a clock
  // rather than requestAnimationFrame: rAF stops firing when the window is
  // hidden or occluded, and a gate that never reopens would eat the drag.
  window.addEventListener("mousemove", ev => {
    if (!dragging) return;
    const now = performance.now();
    if (now - last < MIN_GAP_MS) { ev.stopImmediatePropagation(); return; }
    last = now;
  }, true);
})();

/* ── window buttons ───────────────────────────────────── */
$("win-min").onclick = () => { const b = bridge(); if (b) b.minimize(); };
$("win-max").onclick = () => { const b = bridge(); if (b) b.toggle_maximize(); };
$("win-close").onclick = () => { const b = bridge(); b ? b.close() : window.close(); };
$("lang-switch").onclick = e => {
  const l = e.target.dataset.lang;
  if (!l || l === lang) return;
  lang = l; applyLang();
  const b = bridge(); if (b) b.set_lang(l);
};

/* ── progress plumbing (shared by build/extract/upload) ── */
function setPhase(key) { const p = $("phase"); p.dataset.k = key; p.textContent = t("phase." + key); }

function onProgress(ev) {
  if (ev.phase === "upload") return onUploadProgress(ev);
  setPhase(ev.phase);
  const bar = document.querySelector("#page-build .bar");
  if (ev.total > 0) {
    const frac = ev.done / ev.total;
    $("pct").textContent = (frac * 100).toFixed(1) + "%";
    $("fill").style.width = (frac * 100) + "%";
    bar.classList.remove("idle");
    const el = (Date.now() - S.t0) / 1000;
    const sp = el > 0 ? ev.done / el : 0;
    if (["write", "verify", "extract"].includes(ev.phase) && sp > 0) {
      $("stats").textContent = `${gb(ev.done)} / ${gb(ev.total)} · ` +
        `${(sp / 1048576).toFixed(0)} MB/s · ${t("stats.eta")} ${((ev.total - ev.done) / sp).toFixed(0)}s`;
    } else $("stats").textContent = ev.detail || "";
  } else {
    $("stats").textContent = (ev.done ? ev.done.toLocaleString() + " " : "") + (ev.detail || "");
  }
}
function jobEnd(kind, msg) {
  S.running = false;
  document.querySelector("#page-build .bar").classList.add("idle");
  setPhase(kind);
  if (kind === "done") { $("pct").textContent = "100.0%"; $("fill").style.width = "100%"; }
  $("btn-run").disabled = false; $("btn-abort").disabled = true;
  $("ex-run").disabled = false; $("ex-abort").disabled = true;
  $("ftp-upload").disabled = false; $("ftp-abort").disabled = true;
  const box = activeLogBox();
  if (msg) log(box, msg, kind === "done" ? "ok" : kind === "error" ? "err" : "");
}
function activeLogBox() {
  const page = document.querySelector(".page.on").id;
  return page === "page-extract" ? "ex-log" : page === "page-ftp" ? "ftp-log" : "log";
}

window.forge = {
  onProgress,
  onLog: (m, cls) => log(activeLogBox(), m, cls),
  onDone: m => jobEnd("done", m),
  onError: m => jobEnd("error", t("err.prefix") + m),
  onCancelled: () => jobEnd("cancelled", t("phase.cancelled")),
  onKlog: lines => {
    // arrives as a batch; append in one pass so a busy console cannot
    // reflow the box once per line
    const el = $("kl-log");
    const frag = document.createDocumentFragment();
    for (const line of lines) {
      const div = document.createElement("div");
      div.className = "ln";
      div.textContent = line;
      frag.appendChild(div);
    }
    el.appendChild(frag);
    while (el.childNodes.length > 600) el.removeChild(el.firstChild);
    if (S.follow) el.scrollTop = el.scrollHeight;
  },
  onKlogError: m => {
    log("kl-log", t("err.prefix") + m, "err");
    setPill("kl-status", false, t("klog.idle"));
    $("kl-start").disabled = false; $("kl-stop").disabled = true;
  },
  onDownload: d => {
    const el = $("dl-" + d.id);
    if (el) el.textContent = d.total
      ? " " + Math.round(100 * d.done / d.total) + "%"
      : " " + human(d.done);
  },
  onDownloadDone: d => {
    const el = $("dl-" + d.id); if (el) el.textContent = " ✓";
    downloadEnded();
    log("pl-log", t("cat.done", { name: d.name }), "ok");
    scanPayloads();
  },
  onDownloadError: d => {
    const el = $("dl-" + d.id); if (el) el.textContent = "";
    downloadEnded();
    log("pl-log", t("err.prefix") + d.error, "err");
  },
  onPortResult: r => addPortRow(r),
  onScanDone: s => scanFinished(s),
  onScanError: m => scanFinished(null, m),
  setLang: l => { lang = l; applyLang(); },
};

/* ── BUILD page ───────────────────────────────────────── */
$$("#page-build .mode").forEach(m => m.onclick = () => {
  if (m.classList.contains("disabled")) return;
  $$("#page-build .mode").forEach(x => x.classList.remove("on"));
  m.classList.add("on"); S.mode = m.dataset.mode; syncOpts();
});
chip("opt-verify", "verify", true);
chip("opt-compress", "compress", true);
chip("opt-keep", "keep", false);
chip("ex-overwrite", "overwrite", false);
chip("kl-follow", "follow", true);

function syncOpts() {
  const pfs = S.mode === "pfs";
  $("opt-compress").classList.toggle("disabled", !pfs);
  $("opt-keep").classList.toggle("disabled", !pfs);
  $("inter-wrap").style.display = pfs ? "" : "none";
  $("lvl-wrap").style.opacity = (pfs && S.compress) ? 1 : .3;
  $("level").disabled = !(pfs && S.compress);
}
$("level").oninput = () => $("level-val").textContent = $("level").value;
$("set-level").oninput = () => $("set-level-val").textContent = $("set-level").value;

async function pickInto(inputId, kind, patterns) {
  const b = bridge();
  if (!b) { log(activeLogBox(), "demo: dialogs unavailable"); return; }
  const p = kind === "file" ? await b.pick_file(patterns || null) : await b.pick_folder();
  if (p) { $(inputId).value = p; if (inputId === "source") describeSource(p); }
}
$("pick-source").onclick = () => pickInto("source", "dir");
$("pick-output").onclick = () => pickInto("output", "dir");
$("ex-pick-image").onclick = () => pickInto("ex-image", "file");
$("ex-pick-dest").onclick = () => pickInto("ex-dest", "dir");
$("in-pick").onclick = () => pickInto("in-image", "file");
$("ftp-pick").onclick = () => pickInto("ftp-file", "file");
$("lib-add").onclick = async () => {
  const b = bridge(); if (!b) return;
  const p = await b.pick_folder();
  if (p) $("lib-folders").value = $("lib-folders").value ? $("lib-folders").value + ";" + p : p;
};
$("set-pick-output").onclick = () => pickInto("set-output", "dir");

async function describeSource(path) {
  const b = bridge(); if (!b) return;
  const info = await b.inspect_source(path);
  if (!info.ok) { $("source-info").textContent = ""; return; }
  const bits = [];
  if (info.title_id) bits.push(info.title_id);
  if (info.title) bits.push(info.title);
  if (info.version) bits.push("v" + info.version);
  if (!info.has_eboot) bits.push("⚠ " + t("msg.noeboot"));
  $("source-info").textContent = bits.join("  ·  ");
  if (!$("output").value) {
    const parent = path.replace(/[\\\/][^\\\/]*$/, "");
    $("output").value = parent;
  }
}
$("source").onchange = () => describeSource($("source").value.trim());

function startJob(logBox) {
  S.running = true; S.t0 = Date.now();
  $(logBox).innerHTML = "";
  $("pct").textContent = "0.0%"; $("fill").style.width = "0%";
  $("stats").textContent = "";
}

$("btn-run").onclick = () => {
  const src = $("source").value.trim();
  if (!src) { log("log", t("msg.need_source"), "err"); return; }
  startJob("log");
  $("btn-run").disabled = true; $("btn-abort").disabled = false;
  const opts = {
    source: src, output: $("output").value.trim(), mode: S.mode,
    intermediate: $("intermediate").value, verify: S.verify,
    compress: S.compress, level: +$("level").value, keep_intermediate: S.keep,
  };
  const b = bridge();
  if (b) b.start_build(opts); else demoBuild();
};
$("btn-abort").onclick = () => { const b = bridge(); b ? b.cancel() : jobEnd("cancelled"); };

/* ── EXTRACT page ─────────────────────────────────────── */
$("ex-run").onclick = () => {
  const img = $("ex-image").value.trim(), dest = $("ex-dest").value.trim();
  if (!img) { log("ex-log", t("msg.need_image"), "err"); return; }
  if (!dest) { log("ex-log", t("msg.need_dest"), "err"); return; }
  startJob("ex-log");
  $("ex-run").disabled = true; $("ex-abort").disabled = false;
  const b = bridge();
  if (b) b.start_extract(img, dest, S.overwrite);
  else { log("ex-log", "demo: extract " + img); setTimeout(() => jobEnd("done", "demo done"), 900); }
};
$("ex-abort").onclick = () => { const b = bridge(); if (b) b.cancel(); };

/* ── INSPECT page ─────────────────────────────────────── */
$("in-run").onclick = async () => {
  const img = $("in-image").value.trim();
  if (!img) { $("in-summary").textContent = t("msg.need_image"); return; }
  const b = bridge();
  if (!b) { $("in-summary").textContent = "demo mode"; return; }
  $("in-tree").innerHTML = "";
  const r = await b.inspect_image(img);
  if (!r.ok) { $("in-summary").textContent = t("err.prefix") + r.error; return; }
  if (r.fmt === "exfat") {
    $("in-summary").textContent = `exFAT · ${r.file_count.toLocaleString()} files · ${human(r.total)}`;
  } else {
    $("in-summary").textContent = Object.entries(r.info || {})
      .slice(0, 6).map(([k, v]) => `${k}: ${v}`).join("  ·  ");
  }
  (r.tree || []).forEach(line => log("in-tree", line));
};

/* ── LIBRARY page ─────────────────────────────────────── */
$("lib-scan").onclick = async () => {
  const b = bridge();
  const folders = $("lib-folders").value.split(";").map(s => s.trim()).filter(Boolean);
  if (!b) { renderLibrary(demoLibrary()); return; }
  renderLibrary(await b.scan_library(folders));
};
function renderLibrary(data) {
  const dumps = data.dumps || [], images = data.images || [];
  $("lib-dumps").innerHTML = dumps.map((d, i) => `<tr data-i="${i}">
    <td>${esc(d.title || d.name)}</td><td>${esc(d.title_id)}</td>
    <td class="num">${(d.file_count || 0).toLocaleString()}</td>
    <td class="num">${human(d.size_bytes)}</td></tr>`).join("");
  $("lib-images").innerHTML = images.map((m, i) => `<tr data-i="${i}">
    <td>${esc(m.name)}</td><td><span class="badge ${esc(m.fmt)}">${esc(m.fmt.toUpperCase())}</span></td>
    <td class="num">${human(m.size_bytes)}</td><td>${esc(m.modified)}</td></tr>`).join("");
  $("lib-dumps-empty").style.display = dumps.length ? "none" : "";
  $("lib-images-empty").style.display = images.length ? "none" : "";

  $$("#lib-dumps tr").forEach(tr => tr.onclick = () => {
    $$("#lib-dumps tr").forEach(x => x.classList.remove("sel"));
    tr.classList.add("sel");
    S.selDump = dumps[+tr.dataset.i];
    $("lib-build").disabled = false;
  });
  $$("#lib-images tr").forEach(tr => tr.onclick = () => {
    $$("#lib-images tr").forEach(x => x.classList.remove("sel"));
    tr.classList.add("sel");
    S.selImage = images[+tr.dataset.i];
    $("lib-upload").disabled = false;
  });
}
$("lib-build").onclick = () => {
  if (!S.selDump) return;
  $("source").value = S.selDump.path;
  goto("build"); describeSource(S.selDump.path);
};
$("lib-upload").onclick = () => {
  if (!S.selImage) return;
  $("ftp-file").value = S.selImage.path;
  goto("ftp");
};

/* ── HISTORY page ─────────────────────────────────────── */
let lastHistory = [];
async function refreshHistory() {
  const b = bridge();
  lastHistory = b ? await b.get_history() : demoHistory();
  renderHistory(lastHistory);
}
function renderHistory(rows) {
  if (!$("hist-body")) return;
  $("hist-body").innerHTML = (rows || []).map(h => `<tr>
    <td>${esc(h.timestamp)}</td>
    <td>${esc(h.title || h.title_id || h.source.split(/[\\\/]/).pop())}</td>
    <td><span class="badge ${esc(h.fmt)}">${esc((h.fmt || "").toUpperCase())}</span></td>
    <td class="num">${human(h.size_bytes)}</td>
    <td class="num">${Math.floor(h.duration_s / 60)}m ${String(Math.floor(h.duration_s % 60)).padStart(2, "0")}s</td>
    <td><span class="badge ${esc(h.status)}">${esc((h.status || "").toUpperCase())}</span></td>
  </tr>`).join("");
  $("hist-empty").style.display = (rows && rows.length) ? "none" : "";
}
$("hist-refresh").onclick = refreshHistory;
$("hist-clear").onclick = async () => {
  const b = bridge(); if (b) await b.clear_history();
  lastHistory = []; renderHistory([]);
};

/* ── HOME page ────────────────────────────────────────── */
async function refreshHome() {
  const b = bridge();
  const rows = (b ? await b.get_history() : demoHistory()).slice(0, 5);
  $("home-recent").innerHTML = rows.length ? `<table><tbody>${rows.map(h => `<tr>
    <td>${esc(h.timestamp)}</td>
    <td>${esc(h.title || h.title_id || "—")}</td>
    <td><span class="badge ${esc(h.fmt)}">${esc((h.fmt || "").toUpperCase())}</span></td>
    <td class="num">${human(h.size_bytes)}</td>
    <td><span class="badge ${esc(h.status)}">${esc((h.status || "").toUpperCase())}</span></td>
  </tr>`).join("")}</tbody></table>` : `<div class="empty">${t("empty.history")}</div>`;
}

/* ── PS5 MANAGER page ─────────────────────────────────── */
/* One console, the ports the homebrew scene actually uses. A hit is not
 * just a green dot: each row knows which page drives that service, so the
 * scan doubles as the way in. */
const PS5_TARGET = {                 // port → [page, host input, port input]
  2121: ["ftp", "ftp-host", "ftp-port"],
  1337: ["ftp", "ftp-host", "ftp-port"],
  3232: ["klog", "kl-host", "kl-port"],
  3233: ["klog", "kl-host", "kl-port"],
  9021: ["payload", "pl-host", "pl-port"],
  9020: ["payload", "pl-host", "pl-port"],
  9090: ["payload", "pl-host", "pl-port"],
};

function parsePorts(raw) {
  return raw.split(/[\s,;]+/).map(s => parseInt(s, 10))
            .filter(n => n >= 1 && n <= 65535);
}
function addPortRow(r) { scanRows.push(r); renderPorts(); }

function renderPorts() {
  // open ports first, then arrival order — same rule the backend sorts by
  const rows = scanRows.map((r, i) => [r, i])
    .sort((a, b) => (b[0].open - a[0].open) || (a[1] - b[1])).map(x => x[0]);
  $("ps5-rows").innerHTML = rows.map(r => {
    const jump = r.open && PS5_TARGET[r.port];
    return `<tr data-port="${r.port}" class="${r.open ? "open-row " : ""}${jump ? "clickable" : ""}"
      ${jump ? `title="${esc(t("ps5.jump"))}"` : ""}>
      <td class="num">${r.port}</td>
      <td>${esc(r.name)}</td>
      <td class="${r.open ? "ok" : "dim"}">${t(r.open ? "ps5.open" : "ps5.closed")}</td>
      <td class="num">${r.latency_ms == null ? "—" : r.latency_ms + " ms"}</td>
      <td class="dim">${esc(r.note)}</td></tr>`;
  }).join("");
  $("ps5-empty").style.display = rows.length ? "none" : "";
  $$("#ps5-rows tr.clickable").forEach(tr => tr.onclick = () => {
    const [page, hostId, portId] = PS5_TARGET[+tr.dataset.port];
    $(hostId).value = $("ps5-host").value.trim();
    $(portId).value = tr.dataset.port;
    goto(page);
  });
}
function scanFinished(summary, error) {
  $("ps5-scan").disabled = false;
  $("ps5-stop").disabled = true;
  if (error) { setPill("ps5-status", false, t("err.prefix") + error); return; }
  if (summary.cancelled) { setPill("ps5-status", null, t("ps5.cancelled")); return; }
  setPill("ps5-status", summary.open > 0,
    t("ps5.result", { open: summary.open, total: summary.total }));
}
$("ps5-scan").onclick = async () => {
  const host = $("ps5-host").value.trim();
  if (!host) { setPill("ps5-status", false, t("msg.need_host")); return; }
  $("ps5-rows").innerHTML = "";
  $("ps5-empty").style.display = "none";
  scanRows = [];
  setPill("ps5-status", null, t("ps5.scanning"));
  $("ps5-scan").disabled = true; $("ps5-stop").disabled = false;
  const b = bridge();
  if (!b) { demoScan(); return; }
  const ports = parsePorts($("ps5-ports").value);
  const r = await b.scan_ps5_ports(host, ports.length ? ports : null);
  if (!r.ok) scanFinished(null, r.error);
};
$("ps5-stop").onclick = () => { const b = bridge(); if (b) b.cancel_scan(); };

function demoScan() {
  const rows = [
    { port: 2121, name: "FTP (ftpsrv)", note: "GoldHEN / ftpsrv file server", open: true, latency_ms: 1.4 },
    { port: 9021, name: "ELF loader", note: "etaHEN elfldr — send .elf payloads here", open: true, latency_ms: 2.1 },
    { port: 3232, name: "Kernel log", note: "Kernel log stream", open: true, latency_ms: 1.9 },
    { port: 1337, name: "FTP (etaHEN)", note: "etaHEN's built-in FTP server", open: false, latency_ms: null },
    { port: 9090, name: "Payload (alt)", note: "Alternate loader port", open: false, latency_ms: null },
  ];
  rows.forEach((r, i) => setTimeout(() => addPortRow(r), 120 * (i + 1)));
  setTimeout(() => scanFinished({ open: 3, total: rows.length }), 120 * (rows.length + 1));
}

/* ── FTP page ─────────────────────────────────────────── */
function setPill(id, good, text) {
  const el = $(id);
  el.classList.toggle("good", !!good);
  el.classList.toggle("bad", good === false);
  el.querySelector("span:last-child").textContent = text;
}
$("ftp-probe").onclick = async () => {
  const host = $("ftp-host").value.trim();
  if (!host) { log("ftp-log", t("msg.need_host"), "err"); return; }
  const b = bridge(); if (!b) return;
  const r = await b.ps5_probe(host, +$("ftp-port").value);
  setPill("ftp-status", r.reachable,
    t(r.reachable ? "msg.probe_ok" : "msg.probe_bad", { d: r.detail }));
  log("ftp-log", `${host}:${$("ftp-port").value} — ${r.detail}`, r.reachable ? "ok" : "err");
};
$("ftp-list").onclick = async () => {
  const host = $("ftp-host").value.trim();
  if (!host) { log("ftp-log", t("msg.need_host"), "err"); return; }
  const b = bridge(); if (!b) return;
  const r = await b.ps5_list(host, +$("ftp-port").value, $("ftp-path").value.trim() || "/");
  if (!r.ok) { log("ftp-log", t("err.prefix") + r.error, "err"); return; }
  $("ftp-entries").innerHTML = r.entries.map(e => `<tr>
    <td>${e.is_dir ? "▸ " : ""}${esc(e.name)}</td>
    <td class="num">${e.is_dir ? "—" : human(e.size)}</td></tr>`).join("");
  $("ftp-empty").style.display = r.entries.length ? "none" : "";
  log("ftp-log", `${r.entries.length} entries`, "ok");
};
function onUploadProgress(ev) {
  const frac = ev.total ? ev.done / ev.total : 0;
  $("ftp-pct").textContent = (frac * 100).toFixed(1) + "%";
  $("ftp-fill").style.width = (frac * 100) + "%";
  document.querySelector("#page-ftp .bar").classList.remove("idle");
  const el = (Date.now() - S.t0) / 1000, sp = el > 0 ? ev.done / el : 0;
  $("ftp-stats").textContent = `${human(ev.done)} / ${human(ev.total)} · ${(sp / 1048576).toFixed(1)} MB/s`;
}
$("ftp-upload").onclick = () => {
  const host = $("ftp-host").value.trim(), file = $("ftp-file").value.trim();
  if (!host) { log("ftp-log", t("msg.need_host"), "err"); return; }
  if (!file) { log("ftp-log", t("msg.need_image"), "err"); return; }
  const b = bridge(); if (!b) return;
  S.t0 = Date.now();
  $("ftp-upload").disabled = true; $("ftp-abort").disabled = false;
  b.ps5_upload(host, +$("ftp-port").value, [file], $("ftp-path").value.trim());
};
$("ftp-abort").onclick = () => { const b = bridge(); if (b) b.cancel(); };

/* ── KLOG page ────────────────────────────────────────── */
$("kl-start").onclick = () => {
  const host = $("kl-host").value.trim();
  if (!host) { log("kl-log", t("msg.need_host"), "err"); return; }
  const b = bridge(); if (!b) return;
  b.klog_start(host, +$("kl-port").value);
  setPill("kl-status", true, t("msg.streaming"));
  $("kl-status").querySelector(".dot").classList.add("live");
  $("kl-start").disabled = true; $("kl-stop").disabled = false;
};
$("kl-stop").onclick = () => {
  const b = bridge(); if (b) b.klog_stop();
  setPill("kl-status", false, t("klog.idle"));
  $("kl-status").querySelector(".dot").classList.remove("live");
  $("kl-start").disabled = false; $("kl-stop").disabled = true;
};
$("kl-clear").onclick = () => $("kl-log").innerHTML = "";

/* ── PAYLOAD page ─────────────────────────────────────── */
let plItems = [], plSel = null;

$("pl-pick-dir").onclick = async () => {
  const b = bridge(); if (!b) return;
  const p = await b.pick_folder();
  if (p) { $("pl-dir").value = p; scanPayloads(); }
};
$("pl-scan").onclick = () => scanPayloads();

async function scanPayloads() {
  const b = bridge();
  if (!b) { plItems = demoPayloads(); renderPayloads(); return; }
  const r = await b.scan_payloads($("pl-dir").value.trim());
  if (!r.ok) { log("pl-log", t("msg.scan_failed") + r.error, "err"); return; }
  if (r.folder) $("pl-dir").value = r.folder;
  plItems = r.items || [];
  renderPayloads();
  log("pl-log", `${plItems.length} payload(s)`, "ok");
}

function renderPayloads() {
  $("pl-list").innerHTML = plItems.map((p, i) => `<tr data-i="${i}">
    <td>${esc(p.name)}${p.version ? ` <span class="pd-ver">${esc(p.version)}</span>` : ""}
        ${p.warning ? ' <span style="color:var(--warn)">⚠</span>' : ""}</td>
    <td><div class="caps">${(p.capabilities || []).slice(0, 3)
        .map(c => `<span class="cap ${esc(c)}">${esc(c)}</span>`).join("")}</div></td>
    <td class="num">${human(p.size_bytes)}</td></tr>`).join("");
  $("pl-empty").style.display = plItems.length ? "none" : "";
  $$("#pl-list tr").forEach(tr => tr.onclick = () => {
    $$("#pl-list tr").forEach(x => x.classList.remove("sel"));
    tr.classList.add("sel");
    selectPayload(plItems[+tr.dataset.i]);
  });
  if (!plItems.length) {
    plSel = null; $("pl-send").disabled = true;
    $("pl-detail").innerHTML = `<div class="empty">${t("payload.pick")}</div>`;
  }
}

function selectPayload(p) {
  plSel = p;
  $("pl-send").disabled = false;
  const kv = (k, v) => v ? `<dt>${t(k)}</dt><dd>${esc(v)}</dd>` : "";
  const srcLabel = p.source === "notes" ? t("pd.source.notes")
                 : p.source === "elf" ? t("pd.source.elf") : p.source;
  $("pl-detail").innerHTML = `
    <div class="pd-name">${esc(p.name)}${p.version ? `<span class="pd-ver">v${esc(p.version)}</span>` : ""}</div>
    ${p.warning ? `<div class="pd-warn">⚠ ${esc(p.warning)}</div>` : ""}
    <dl class="kv">
      ${kv("pd.file", p.filename)}
      ${kv("pd.size", human(p.size_bytes) + " · " + p.modified)}
      ${kv("pd.format", p.is_elf ? `${p.elf_class} ${p.elf_type} · ${p.machine} · ${p.osabi}` : "—")}
      ${kv("pd.entry", p.entry)}
      ${kv("pd.buildid", p.build_id)}
      ${kv("pd.toolchain", p.toolchain)}
    </dl>
    ${(p.capabilities || []).length ? `<dl class="kv"><dt>${t("pd.caps")}</dt>
      <dd><div class="caps">${p.capabilities.map(c =>
        `<span class="cap ${esc(c)}">${esc(c)}</span>`).join("")}</div></dd></dl>` : ""}
    <div class="pd-src">${t("pd.desc")} — ${esc(srcLabel)}</div>
    <div class="pd-desc">${esc(p.description || "—")}</div>
    <div class="pd-src" style="margin-top:8px">${t("pd.notes")}</div>
    <textarea id="pl-note" spellcheck="false">${esc(p.source === "notes" ? p.description : "")}</textarea>
    <div class="actions" style="margin-top:6px">
      <button id="pl-savenote">${t("btn.savenote")}</button>
    </div>
    ${(p.strings_sample || []).length ? `<div class="pd-src" style="margin-top:8px">${t("pd.strings")}</div>
      <div class="pd-strings">${p.strings_sample.map(esc).join("<br>")}</div>` : ""}`;

  $("pl-savenote").onclick = async () => {
    const b = bridge(); if (!b) return;
    await b.save_payload_note(p.path, $("pl-note").value);
    log("pl-log", t("msg.note_saved"), "ok");
    scanPayloads();
  };
}

/* ── payload catalog ──────────────────────────────────── */
/* Metadata ships with the app; binaries never do. "Get" pulls the file from
 * the project's own release into the user's payload folder, then rescans so
 * it shows up as an ordinary local payload. */
let catBusy = null;

$("cat-load").onclick = () => loadCatalog();
$("cat-filter").oninput = () => renderCatalog();
$("cat-cancel").onclick = () => { const b = bridge(); if (b) b.cancel_download(); };

async function loadCatalog() {
  const b = bridge();
  if (!b) { catItems = demoCatalog(); $("cat-source").textContent = "demo"; renderCatalog(); return; }
  const r = await b.payload_catalog();
  if (!r.ok) { log("pl-log", t("err.prefix") + r.error, "err"); return; }
  catItems = r.entries || [];
  $("cat-source").textContent = t("cat.source", { url: r.source });
  renderCatalog();
}

function renderCatalog() {
  const q = $("cat-filter").value.trim().toLowerCase();
  const rows = catItems.filter(e => !q ||
    (e.title + " " + e.author + " " + (e.firmwares || []).join(" ")).toLowerCase().includes(q));
  $("cat-list").innerHTML = rows.map(e => `<tr data-id="${esc(e.id)}">
    <td>${esc(e.title)}<div class="pd-strings">${esc(e.description)}</div></td>
    <td class="dim">${esc(e.author)}</td>
    <td class="num">${esc(e.version)}</td>
    <td class="dim">${e.firmwares ? esc(e.firmwares.join(" ")) : t("cat.allfw")}</td>
    <td><span class="lnk" data-act="${e.binary_url ? "get" : "open"}">${
      t(e.binary_url ? "cat.get" : "cat.page")}</span>
      <span class="dl" id="dl-${esc(e.id)}"></span></td></tr>`).join("");
  $("cat-empty").style.display = rows.length ? "none" : "";
  $$("#cat-list .lnk").forEach(el => el.onclick = () => {
    const e = catItems.find(x => x.id === el.closest("tr").dataset.id);
    el.dataset.act === "get" ? getPayload(e) : openPage(e);
  });
}

async function openPage(e) {
  const b = bridge();
  const url = e.page_url || e.project_url;
  if (b) await b.open_url(url); else log("pl-log", "demo: " + url);
}

async function getPayload(e) {
  const b = bridge();
  if (!b) { log("pl-log", "demo: would fetch " + e.binary_url); return; }
  if (!$("pl-dir").value.trim()) { log("pl-log", t("msg.need_folder"), "err"); return; }
  catBusy = e.id;
  $("cat-cancel").disabled = false;
  log("pl-log", t("cat.fetching", { name: e.file, url: e.binary_url }));
  const r = await b.download_catalog_payload(e.id, $("pl-dir").value.trim());
  if (!r.ok) { downloadEnded(); log("pl-log", t("err.prefix") + r.error, "err"); }
}

function downloadEnded() { catBusy = null; $("cat-cancel").disabled = true; }

function demoCatalog() {
  return [
    { id: "ftpsrv-ps5", title: "ftpsrv", file: "ftpsrv-ps5.elf", author: "ps5-payload-dev",
      version: "0.21.1", description: "FTP server payload.", firmwares: null, port: 9021,
      project_url: "https://github.com/ps5-payload-dev/ftpsrv",
      binary_url: "https://github.com/ps5-payload-dev/ftpsrv/releases/download/v0.21.1/ftpsrv-ps5.elf",
      page_url: null },
    { id: "kstuff-toggle", title: "kstuff-toggle", file: "kstuff-toggle.elf", author: "EchoStretch",
      version: "0.2", description: "Published as a CI artifact — opens the page.",
      firmwares: ["3.", "4.", "5."], port: 9021,
      project_url: "https://github.com/EchoStretch/kstuff-toggle",
      binary_url: null, page_url: "https://github.com/EchoStretch/kstuff-toggle/actions/runs/15086245462" },
  ];
}

$("pl-send").onclick = async () => {
  const host = $("pl-host").value.trim();
  if (!host) { log("pl-log", t("msg.need_host"), "err"); return; }
  if (!plSel) { log("pl-log", t("payload.pick"), "err"); return; }
  const b = bridge(); if (!b) return;
  const r = await b.ps5_send_payload(host, +$("pl-port").value, plSel.path);
  log("pl-log", r.ok ? `✓ ${plSel.name} — ${r.sent.toLocaleString()} bytes`
                     : t("err.prefix") + r.error, r.ok ? "ok" : "err");
};

function demoPayloads() {
  return [
    { path: "D:\payloads\goldhen.elf", name: "GoldHEN", version: "2.4.2",
      filename: "goldhen.elf", size_bytes: 1_204_000, modified: "2026-08-01 12:00",
      is_elf: true, elf_class: "ELF64", elf_type: "DYN", machine: "x86-64",
      osabi: "FreeBSD", entry: "0x40", build_id: "a1b2c3d4e5f6",
      toolchain: "clang version 18.1.3", capabilities: ["ftp", "mount", "debug"],
      description: "Homebrew enabler with FTP and debug support.", source: "elf",
      strings_sample: ["/mnt/sandbox/app0", "ftpsrv listening on 2121"], warning: "" },
    { path: "D:\payloads\ps5-backpork.elf", name: "backpork", version: "",
      filename: "ps5-backpork.elf", size_bytes: 99_800, modified: "2026-08-22 05:54",
      is_elf: true, elf_class: "ELF64", elf_type: "DYN", machine: "x86-64",
      osabi: "FreeBSD", entry: "0x40", build_id: "d5d731ddd171e9be",
      toolchain: "Ubuntu clang version 18.1.3", capabilities: ["mount", "backport", "net"],
      description: "ELF64 DYN · x86-64 · FreeBSD · capabilities: mount, backport, net",
      source: "elf", strings_sample: ["libSceFsInternalForVsh.sprx", "/user/homebrew/lib/%s"], warning: "" },
  ];
}

/* ── SETTINGS page ────────────────────────────────────── */
async function loadSettings() {
  const b = bridge(); if (!b) return;
  const s = await b.get_settings();
  $("set-output").value = s.output_dir || "";
  $("set-libdirs").value = (s.library_dirs || []).join(";");
  $("set-cluster").value = String(s.cluster_size == null ? 65536 : s.cluster_size);
  $("set-level").value = s.pfs_level || 9;
  $("set-level-val").textContent = s.pfs_level || 9;
  $("set-threads").value = s.pfs_threads || 0;
  $("set-ffblock").value = s.ffpkg_block || 65536;
  $("set-fffrag").value = s.ffpkg_frag || 65536;
  $("set-ps5host").value = s.ps5_host || "";
  $("set-ps5path").value = s.ps5_ftp_path || "";
  $("set-verify").classList.toggle("on", s.verify_after_build !== false);
  $("set-compress").classList.toggle("on", s.pfs_compress !== false);
  // seed the working pages from saved defaults
  if (s.output_dir && !$("output").value) $("output").value = s.output_dir;
  if (s.library_dirs && s.library_dirs.length) $("lib-folders").value = s.library_dirs.join(";");
  ["ftp-host", "kl-host", "pl-host"].forEach(id => { if (s.ps5_host) $(id).value = s.ps5_host; });
  if (s.ps5_ftp_path) $("ftp-path").value = s.ps5_ftp_path;
  if (s.ps5_ftp_port) $("ftp-port").value = s.ps5_ftp_port;
  if (s.ps5_klog_port) $("kl-port").value = s.ps5_klog_port;
  if (s.ps5_payload_port) $("pl-port").value = s.ps5_payload_port;
  if (s.payload_dir) { $("pl-dir").value = s.payload_dir; scanPayloads(); }
}
$("set-verify").onclick = () => $("set-verify").classList.toggle("on");
$("set-compress").onclick = () => $("set-compress").classList.toggle("on");
$("set-save").onclick = async () => {
  const b = bridge(); if (!b) return;
  await b.save_settings({
    output_dir: $("set-output").value.trim(),
    library_dirs: $("set-libdirs").value.split(";").map(s => s.trim()).filter(Boolean),
    cluster_size: +$("set-cluster").value,
    verify_after_build: $("set-verify").classList.contains("on"),
    pfs_compress: $("set-compress").classList.contains("on"),
    pfs_level: +$("set-level").value,
    pfs_threads: +$("set-threads").value,
    ffpkg_block: +$("set-ffblock").value,
    ffpkg_frag: +$("set-fffrag").value,
    ps5_host: $("set-ps5host").value.trim(),
    ps5_ftp_path: $("set-ps5path").value.trim(),
    lang,
  });
  log("log", t("msg.saved"), "ok");
  loadSettings();
};

/* ── demo data (browser preview, no bridge) ───────────── */
function demoHistory() {
  return [
    { timestamp: "2026-08-22 05:12:44", title: "ASTRO BOT", title_id: "PPSA21564",
      fmt: "pfs", size_bytes: 105_690_000_000, duration_s: 842, status: "ok", source: "E:\\PPSA21564-app0" },
    { timestamp: "2026-08-21 19:03:49", title: "Yakuza Kiwami", title_id: "PPSA31334",
      fmt: "exfat", size_bytes: 41_200_000_000, duration_s: 311, status: "ok", source: "D:\\PS5" },
    { timestamp: "2026-08-21 17:48:57", title: "Returnal", title_id: "PPSA01234",
      fmt: "ffpkg", size_bytes: 58_900_000_000, duration_s: 402, status: "failed", source: "D:\\PS5" },
  ];
}
function demoLibrary() {
  return {
    dumps: [
      { path: "E:\\PPSA21564-app0", name: "PPSA21564-app0", title: "ASTRO BOT",
        title_id: "PPSA21564", file_count: 156250, size_bytes: 159_500_000_000 },
      { path: "E:\\PPSA31334", name: "PPSA31334", title: "Yakuza Kiwami",
        title_id: "PPSA31334", file_count: 24310, size_bytes: 41_800_000_000 },
    ],
    images: [
      { path: "D:\\PS5\\PPSA21564.ffpfsc", name: "PPSA21564.ffpfsc", fmt: "pfs",
        size_bytes: 105_690_000_000, modified: "2026-08-22 05:12" },
      { path: "D:\\PS5\\PPSA31334.exfat", name: "PPSA31334.exfat", fmt: "exfat",
        size_bytes: 41_200_000_000, modified: "2026-08-21 19:03" },
    ],
  };
}
function demoBuild() {
  const phases = [
    { phase: "scan", steps: 10, detail: "156,250 files" },
    { phase: "write", steps: 45, total: 164501651456 },
    { phase: "verify", steps: 22, total: 164501651456 },
    { phase: "pfs", steps: 18, total: 164501651456 },
  ];
  let pi = 0, si = 0;
  log("log", "demo: scanning E:\\PPSA21564-app0 …");
  const tick = setInterval(() => {
    if (!S.running) { clearInterval(tick); return; }
    const p = phases[pi]; si++;
    onProgress({ phase: p.phase, done: (p.total || p.steps) * si / p.steps,
                 total: p.total || 0, detail: p.detail || "" });
    if (si >= p.steps) {
      log("log", `demo: ${p.phase} complete`);
      pi++; si = 0; S.t0 = Date.now();
      if (pi >= phases.length) {
        clearInterval(tick);
        jobEnd("done", "demo: PPSA21564.ffpfsc · 98.41 GB · 14m 02s");
      }
    }
  }, 110);
}

/* ── init ─────────────────────────────────────────────── */
async function init() {
  const b = bridge();
  if (b) {
    try { lang = (await b.get_lang()) || lang; } catch (e) {}
    try {
      const env = await b.environment();
      $("pill-version").querySelector("span:last-child").textContent = "v" + env.version;
      $("about-version").textContent = "v" + env.version;
      $("foot-left").textContent = `exFAT FORGE v${env.version} // GPL-3.0`;
      const ff = env.ffpkg || {};
      setPill("pill-ffpkg", ff.available, ff.available ? "ffpkg · " + ff.detail : t("msg.ffpkg_missing"));
      $("set-ffpkg-status").textContent = ff.detail || "";
      if (!ff.available) {
        const m = document.querySelector('#page-build .mode[data-mode="ffpkg"]');
        if (m) { m.classList.add("disabled"); m.title = t("msg.ffpkg_missing"); }
      }
    } catch (e) {}
    await loadSettings();
    await refreshHome();
  } else {
    $("demo-badge").style.display = "";
    lang = (navigator.language || "en").startsWith("zh") ? "zh" : "en";
    setPill("pill-ffpkg", true, "ffpkg · demo");
    $("pill-version").querySelector("span:last-child").textContent = "v0.3.0";
    renderLibrary(demoLibrary());
    plItems = demoPayloads(); renderPayloads();
    lastHistory = demoHistory();
    refreshHome();
  }
  applyLang();
  syncOpts();
  log("log", t("msg.hello"));
}
if (window.pywebview) init();
else window.addEventListener("pywebviewready", init, { once: true });
setTimeout(() => { if (!bridge() && !$("log").hasChildNodes()) init(); }, 350);
