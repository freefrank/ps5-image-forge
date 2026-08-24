/* Setup UI. The Python side owns all state; this file only renders it and
   polls, because pushing into the page from a worker thread is not safe on the
   WebView2 backend. */

const $ = (id) => document.getElementById(id);
const api = () => window.pywebview.api;

let MODE = 'install';
let busy = false;

/* ── boot ─────────────────────────────────────────────────── */
window.addEventListener('pywebviewready', async () => {
  const s = await api().state();
  MODE = s.mode;

  $('brand-ver').textContent = 'v' + s.version;
  $('f-version').textContent = s.installed && s.installed !== s.version
    ? `${s.installed}  →  ${s.version}`
    : s.version;
  setDir(s.dir);
  $('f-size').textContent = (s.size / 1048576).toFixed(1) + ' MB';

  if (MODE === 'uninstall') {
    $('mode-tag').textContent = 'UNINSTALL';
    $('ready-tag').textContent = 'REMOVE';
    $('ready-title').textContent = 'UNINSTALL';
    $('blurb').textContent =
      'This removes the program, its shortcuts and its Add/Remove Programs '
      + 'entry. Images you built and settings under %APPDATA% are left alone.';
    $('f-version').textContent = s.installed || s.version;
    $('f-size-label').textContent = 'ON DISK';
    $('f-size').textContent = s.on_disk
      ? (s.on_disk / 1048576).toFixed(1) + ' MB' : '—';
    $('opt-row').hidden = true;
    $('dest-row').hidden = true;      // the location is wherever it was installed
    const go = $('btn-go');
    go.textContent = 'UNINSTALL';
    go.classList.add('danger');
    $('done-tag').textContent = 'REMOVED';
    $('done-title').textContent = 'UNINSTALLED';
    $('btn-launch').hidden = true;
  }

  showRunning(s.running);
});

/* ── window drag ──────────────────────────────────────────── */
/* Same fix as the main app: pywebview moves the window with one synchronous
 * IPC call per mousemove, and a mouse reports 125-1000 times a second. Left
 * alone that saturates the UI thread — the window trails the cursor and the
 * clicks queue up behind the backlog. Throttle the moves, and stop painting
 * the sweep animation while dragging. */
(() => {
  const MIN_GAP_MS = 8;
  const root = document.documentElement;
  let dragging = false, last = 0;
  const stop = () => { dragging = false; root.classList.remove('dragging'); };

  document.querySelector('.pywebview-drag-region')
    .addEventListener('mousedown', () => {
      dragging = true; last = 0; root.classList.add('dragging');
    });
  window.addEventListener('mouseup', stop);
  window.addEventListener('blur', stop);

  // Capture phase, so this drops the event before pywebview's own window-level
  // handler turns it into an IPC call. Clock-gated rather than rAF: rAF stops
  // firing when the window is occluded and the gate would never reopen.
  window.addEventListener('mousemove', ev => {
    if (!dragging) return;
    const now = performance.now();
    if (now - last < MIN_GAP_MS) { ev.stopImmediatePropagation(); return; }
    last = now;
  }, true);
})();

/* ── ready stage ──────────────────────────────────────────── */
// The path renders RTL so a long one truncates at the front, where the
// uninteresting part is; the LTR mark keeps the drive letter from being
// reordered to the end.
function setDir(dir) {
  const el = $('f-dir');
  el.textContent = '‪' + dir;
  el.title = dir;
}

$('btn-browse').onclick = async () => {
  if (busy) return;
  setDir(await api().browse());
};

function showRunning(running) {
  $('running-warn').hidden = !running;
  $('btn-go').disabled = !!running;
  fitWindow();
}

$('chip-desktop').onclick = () => $('chip-desktop').classList.toggle('on');

$('btn-recheck').onclick = async () => {
  const note = $('recheck-note');
  const running = await api().recheck();
  showRunning(running);
  note.textContent = running ? 'Still running.' : '';
  note.classList.toggle('bad', running);
};

$('btn-cancel').onclick = () => api().close();
$('btn-finish').onclick = () => api().close();
$('btn-launch').onclick = async () => { await api().launch(); api().close(); };

$('btn-go').onclick = () => {
  stage('work');
  busy = true;
  document.querySelector('.bar').classList.remove('idle');
  api().start($('chip-desktop').classList.contains('on'));
  setTimeout(pump, 120);
};

/* ── worker polling ───────────────────────────────────────── */
async function pump() {
  const s = await api().poll();
  for (const ln of s.lines) {
    const el = document.createElement('div');
    el.className = 'ln' + (ln.kind ? ' ' + ln.kind : '');
    el.textContent = ln.text;
    $('log').appendChild(el);
  }
  if (s.lines.length) $('log').scrollTop = $('log').scrollHeight;
  $('pct').textContent = s.pct + '%';
  document.querySelector('.bar .fill').style.width = s.pct + '%';
  if (s.phase) $('phase').textContent = s.phase;

  if (s.done === null) { setTimeout(pump, 200); return; }

  busy = false;
  document.querySelector('.bar').classList.add('idle');
  if (s.done) { finish(true); return; }

  // Failure: the "app is running" case is recoverable, so drop back to the
  // ready stage with the warning up instead of dead-ending on an error page.
  if (s.error === 'running') {
    showRunning(true);
    stage('ready');
  } else {
    finish(false, s.error);
  }
}

function finish(ok, error) {
  if (!ok) {
    $('done-tag').textContent = 'FAILED';
    $('done-title').textContent = MODE === 'uninstall' ? 'UNINSTALL FAILED' : 'INSTALL FAILED';
    $('done-text').textContent = error || 'Unknown error.';
    $('btn-launch').hidden = true;
  } else if (MODE === 'uninstall') {
    $('done-text').textContent = 'PS5 Image Forge has been removed.';
  } else {
    $('done-text').textContent =
      'PS5 Image Forge is installed and pinned to the Start Menu.';
  }
  stage('done');
}

function stage(name) {
  for (const el of document.querySelectorAll('.stage')) el.classList.remove('on');
  $('stage-' + name).classList.add('on');
  fitWindow();
}

/* The stages differ by ~250px in height. #body is flex:1, so a fixed window
   leaves the short ones sitting in a large empty box — measure the live stage
   and let Python shrink-wrap the window around it. */
function fitWindow() {
  const on = document.querySelector('.stage.on');
  if (!on) return;
  const body = $('body'), foot = document.querySelector('footer');
  const pad = parseFloat(getComputedStyle(body).paddingTop)
            + parseFloat(getComputedStyle(body).paddingBottom);
  // +4: sub-pixel rounding here costs a scrollbar down the whole panel.
  const h = Math.ceil($('titlebar').getBoundingClientRect().height
                      + on.getBoundingClientRect().height + pad
                      + (foot ? foot.getBoundingClientRect().height : 0)) + 4;
  try { api().fit(h); } catch (e) { /* no bridge (browser preview) */ }
}

/* Frameless chrome. */
$('win-min').onclick = () => api().minimize();
$('win-close').onclick = () => { if (!busy) api().close(); };
