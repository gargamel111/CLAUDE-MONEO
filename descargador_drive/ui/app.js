"use strict";

const $ = (s) => document.querySelector(s);
const api = () => window.pywebview.api;
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const icon = (id) => `<svg class="i"><use href="#i-${id}"/></svg>`;
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const URLS = {
  project: "https://console.cloud.google.com/projectcreate",
  driveApi: "https://console.cloud.google.com/apis/library/drive.googleapis.com",
  credentials: "https://console.cloud.google.com/apis/credentials",
};

let state = { has_key: false, key_mask: "", dest: "", workers: 3, running: false };

/* ---------------- Navegación con píldora deslizante ---------------- */

function moveSlider() {
  const cur = document.querySelector("nav.pills button[aria-current]");
  const slider = document.querySelector("nav.pills .slider");
  if (!cur) return;
  slider.style.width = cur.offsetWidth + "px";
  slider.style.transform = `translateX(${cur.offsetLeft}px)`;
}

function showView(name) {
  document.querySelectorAll("nav.pills button").forEach((b) => {
    if (b.dataset.view === name) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  });
  moveSlider();
  document.querySelectorAll(".view").forEach((v) => {
    const on = v.id === "view-" + name;
    v.hidden = !on;
    if (on) { v.classList.remove("enter"); void v.offsetWidth; v.classList.add("enter"); }
  });
}

/* ---------------- Números que cuentan hasta su valor ---------------- */

const tweens = new Map();
function tweenNumber(el, to, fmt) {
  const from = parseFloat(el.dataset.v || "0") || 0;
  el.dataset.v = to;
  if (reduceMotion || from === to) { el.innerHTML = fmt(to); return; }
  const t0 = performance.now(), dur = 650;
  cancelAnimationFrame(tweens.get(el));
  const step = (t) => {
    const k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 3);
    el.innerHTML = fmt(from + (to - from) * e);
    if (k < 1) tweens.set(el, requestAnimationFrame(step));
  };
  tweens.set(el, requestAnimationFrame(step));
}
const fmtPercent = (v) => `${v >= 100 ? 100 : v.toFixed(v < 10 ? 1 : 1).replace(".", ",")}<sup>%</sup>`;

function splitSize(h) {
  // "14.96 GB" -> ["14,96", "GB"]
  const m = String(h || "").match(/^([\d.]+)\s*(\S+)/);
  return m ? [m[1].replace(".", ","), m[2]] : ["0", ""];
}

/* ---------------- Estado general ---------------- */

function applyState() {
  $("#dest").textContent = state.dest;
  $("#s-dest").textContent = state.dest;
  $("#s-key").textContent = state.key_mask || "Sin API key";
  $("#key-dot").classList.toggle("off", !state.has_key);
  $("#workers").textContent = state.workers;
}

async function refreshState() {
  state = await api().get_state();
  applyState();
}

/* ---------------- Descarga ---------------- */

let logCount = 0;
let polling = null;
let lastResult = null;
let shownRecent = new Set();

function setChip(text, kind) {
  const c = $("#p-chip");
  c.textContent = text;
  c.className = "chip" + (kind ? " " + kind : "");
}

function setCard(mode) {
  const card = $("#progress-card");
  card.classList.toggle("idle", mode === "idle");
  card.classList.toggle("finished", mode === "done");
}

function actions(html) {
  $("#p-actions").innerHTML = html;
}

async function start() {
  const input = $("#link");
  $("#link-error").textContent = "";
  input.classList.remove("bad");
  const res = await api().start(input.value);
  if (!res.ok) {
    if (res.need_key) return openWizard(KEY_STEP, true);
    $("#link-error").textContent = res.error;
    void input.offsetWidth; input.classList.add("bad");
    input.focus();
    return;
  }
  // Nueva descarga: limpia la vista
  logCount = 0; lastResult = null; shownRecent = new Set();
  $("#log").textContent = "";
  $("#recent").querySelectorAll(".rcard").forEach((n) => n.remove());
  $("#recent-empty").hidden = false;
  $("#p-percent").classList.remove("done");
  $("#p-percent").dataset.v = 0;
  $("#p-percent").innerHTML = fmtPercent(0);
  $("#p-bar").style.width = "0%";
  setCard("run");
  setChip("Buscando archivos", "run");
  $("#p-sub").textContent = "Revisando la carpeta y sus subcarpetas…";
  actions(`<button class="btn small" id="stop">${icon("pause")}Pausar</button>`);
  $("#stop").onclick = stop;
  $("#start").disabled = true;
  $("#pick-dest").disabled = true;
  startPolling();
}

async function stop() {
  const b = $("#stop");
  if (b) { b.disabled = true; b.innerHTML = `${icon("pause")}Pausando…`; }
  await api().stop();
}

function startPolling() {
  clearInterval(polling);
  polling = setInterval(poll, 500);
  poll();
}

async function poll() {
  let s;
  try { s = await api().poll(logCount); } catch (e) { return; }
  if (s.log && s.log.length) {
    const log = $("#log");
    const atEnd = log.scrollTop + log.clientHeight >= log.scrollHeight - 30;
    log.textContent += s.log.join("\n") + "\n";
    if (log.textContent.length > 300000) log.textContent = log.textContent.slice(-200000);
    if (atEnd) log.scrollTop = log.scrollHeight;
  }
  logCount = s.log_count;
  if (s.phase === undefined) return;

  if (s.root) $("#p-folder").textContent = s.root;
  tweenNumber($("#p-percent"), s.percent, fmtPercent);
  $("#p-bar").style.width = s.percent + "%";

  tweenNumber($("#t-files"), s.done_files, (v) => Math.round(v));
  $("#t-files-of").textContent = `de ${s.total_files}`;
  const [sp, spu] = splitSize(s.speed_h);
  $("#t-speed").textContent = s.running ? sp : "0";
  $("#t-speed-u").textContent = spu || "MB/s";
  const [by, byu] = splitSize(s.done_h);
  $("#t-bytes").textContent = by;
  $("#t-bytes-u").textContent = byu;
  $("#t-bytes-of").textContent = s.total_h ? `de ${s.total_h.replace(".", ",")}` : "";
  if (s.eta) {
    const m = s.eta.match(/^(\d+)\s*(.*)$/);
    $("#t-eta").textContent = m ? m[1] : s.eta;
    $("#t-eta-u").textContent = m ? m[2] : "";
  } else {
    $("#t-eta").textContent = "—";
    $("#t-eta-u").textContent = s.running && !s.sizes_known ? "calculando" : "";
  }

  if (s.running) {
    if (s.phase === "Descargando") {
      setChip("Descargando", "run");
      $("#p-sub").textContent = `${s.done_files} de ${s.total_files} archivos` +
        (s.failed ? ` · ${s.failed} con error` : "");
    } else if (s.phase.startsWith("Listando")) {
      setChip("Buscando archivos", "run");
      $("#p-sub").textContent = s.phase.replace("Listando…", "Encontrados:");
    } else {
      $("#p-sub").textContent = s.phase;
    }
  }
  renderStack(s.running ? s.active || [] : []);
  renderRecent(s.completed || []);

  if (!s.running && s.result && s.result !== lastResult) {
    lastResult = s.result;
    finished(s);
  }
}

function finished(s) {
  clearInterval(polling);
  $("#start").disabled = false;
  $("#pick-dest").disabled = false;
  const r = s.result;
  if (r.kind === "done" && !r.failed.length) {
    setCard("done");
    setChip("Listo", "ok");
    $("#p-percent").classList.add("done");
    tweenNumber($("#p-percent"), 100, fmtPercent);
    $("#p-bar").style.width = "100%";
    $("#p-sub").textContent = `¡Todo descargado! ${s.total_files} archivos · ${s.done_h}`;
    actions(`<button class="btn small" id="open-done">${icon("folder")}Abrir carpeta</button>`);
    $("#open-done").onclick = () => api().open_dest();
  } else if (r.kind === "done") {
    setCard("run");
    setChip(`${r.failed.length} con error`, "err");
    $("#p-sub").textContent = `Terminó, pero ${r.failed.length} archivo(s) no se pudieron bajar (sin permiso o borrados). Dale otra vez para reintentarlos.`;
    actions(`<button class="btn small" id="retry">Reintentar</button><button class="btn ghost small" id="see-log">${icon("list")}Ver detalles</button>`);
    $("#retry").onclick = start;
    $("#see-log").onclick = openLog;
  } else if (r.kind === "paused") {
    setCard("run");
    setChip("En pausa");
    $("#p-sub").textContent = "Pausado. Lo ya bajado queda guardado.";
    actions(`<button class="btn small" id="resume">Seguir descargando</button>`);
    $("#resume").onclick = start;
  } else {
    setCard("idle");
    setChip("No se pudo", "err");
    $("#p-sub").textContent = r.message;
    actions(`<button class="btn small" id="retry">Reintentar</button><button class="btn ghost small" id="see-log">${icon("list")}Ver detalles</button>`);
    $("#retry").onclick = start;
    $("#see-log").onclick = openLog;
  }
}

/* Tarjetas de archivos en curso (máximo 3, apiladas) */
function renderStack(active) {
  const stack = $("#stack");
  $("#stack-empty").hidden = active.length > 0;
  const want = active.slice(0, 3);
  const keys = new Set(want.map((a) => a.name));
  stack.querySelectorAll(".fcard").forEach((el) => {
    if (!keys.has(el.dataset.k) && !el.classList.contains("out")) {
      el.classList.add("out");
      setTimeout(() => el.remove(), 380);
    }
  });
  want.forEach((a, i) => {
    let el = [...stack.querySelectorAll(".fcard")].find((n) => n.dataset.k === a.name && !n.classList.contains("out"));
    if (!el) {
      el = document.createElement("div");
      el.className = "fcard in";
      el.dataset.k = a.name;
      el.innerHTML = `<div class="f-meta"><span></span><span></span></div><div class="f-name"></div>
        <div class="f-bar"><div class="bar"><i></i></div><span class="f-pct"></span></div>`;
      stack.appendChild(el);
    }
    el.dataset.pos = i;
    const folder = a.folder ? a.folder.split("/").pop() : "Carpeta principal";
    el.querySelector(".f-meta span:first-child").textContent = `${String(i + 1).padStart(2, "0")}. ${folder}`;
    el.querySelector(".f-meta span:last-child").textContent = a.total_h ? `${a.done_h} de ${a.total_h}` : a.done_h;
    el.querySelector(".f-name").textContent = a.name;
    el.querySelector(".bar i").style.width = (a.pct ?? 0) + "%";
    el.querySelector(".f-pct").textContent = a.pct == null ? "…" : `${Math.round(a.pct)}%`;
  });
}

/* Últimos archivos terminados (estilo agenda, colores alternados) */
const COLORS = ["blue", "", "red"];
let recentIndex = 0;
function renderRecent(list) {
  const box = $("#recent");
  const fresh = list.filter((c) => !shownRecent.has(c.folder + "/" + c.name)).reverse();
  if (!fresh.length) return;
  $("#recent-empty").hidden = true;
  fresh.forEach((c) => {
    shownRecent.add(c.folder + "/" + c.name);
    const el = document.createElement("div");
    el.className = `rcard in ${COLORS[recentIndex++ % COLORS.length]}`;
    el.innerHTML = `<div><div class="r-name"></div><span class="r-tag"></span></div><div class="r-size"></div>`;
    el.querySelector(".r-name").textContent = c.name;
    el.querySelector(".r-tag").textContent = c.folder ? c.folder.split("/").pop() : "Listo";
    el.querySelector(".r-size").textContent = c.size_h;
    box.insertBefore(el, box.querySelector(".rcard") || null);
  });
  box.querySelectorAll(".rcard").forEach((n, i) => { if (i >= 30) n.remove(); });
}

function openLog() {
  $("#log-wrap").hidden = false;
  const log = $("#log");
  log.scrollTop = log.scrollHeight;
}

/* ---------------- Paso a paso de la API key ---------------- */

const tap = (n, html) => `<span class="tap" data-n="${n}">${html}</span>`;
const browser = (pane) => `
  <div class="mock" aria-hidden="true">
    <div class="chrome"><i style="background:#FF5F57"></i><i style="background:#FEBC2E"></i><i style="background:#28C840"></i><div class="url">console.cloud.google.com</div></div>
    <div class="gbar"><span class="glogo"><i style="background:#4285F4"></i><i style="background:#EA4335"></i><i style="background:#FBBC04"></i><i style="background:#34A853"></i></span>Google Cloud
      ${pane.proj ? tap(pane.proj, `<span class="proj">descargador ▾</span>`) : `<span class="proj">descargador ▾</span>`}</div>
    <div class="pane">${pane.html}</div>
  </div>
  <div class="caption">Así se ve más o menos. Puede cambiar un poco según tu cuenta.</div>`;

const DRIVE_SVG = `<svg class="drive" viewBox="0 0 34 30"><path d="M11 0h12l11 19H22z" fill="#FBBC04"/><path d="M11 0 0 19l6 11 11-19z" fill="#0F9D58"/><path d="M6 30h22l6-11H12z" fill="#4285F4"/></svg>`;

const STEPS = [
  {
    rail: "Bienvenida",
    render: () => `
      <div class="eyebrow">Antes de empezar</div>
      <h1 id="wz-title">Necesitas una API key de Google</h1>
      <p class="lead">Es un pase gratis que deja al programa ver las carpetas completas de Drive, sin límite de archivos. Se saca una sola vez y queda guardada solo en esta compu. No da acceso a tu correo ni a tus archivos.</p>
      <div class="overview">
        <div class="tile"><div class="t-label">Crear un proyecto</div><div class="t-value"><b>01</b></div></div>
        <div class="tile mesh"><div class="t-label">Activar Drive API</div><div class="t-value"><b>02</b></div></div>
        <div class="tile red"><div class="t-label">Crear la clave</div><div class="t-value"><b>03</b></div></div>
        <div class="tile blue"><div class="t-label">Pegarla aquí</div><div class="t-value"><b>04</b></div></div>
      </div>`,
    next: "Empezar",
  },
  {
    rail: "Crear proyecto",
    render: () => `
      <div class="eyebrow">Paso 1 de 6</div>
      <h1 id="wz-title">Crea un proyecto</h1>
      <div class="wz-row"><ol>
        <li><span>Dale a <b>Abrir Google Cloud</b> y entra con tu cuenta de Google.</span></li>
        <li><span>Ponle cualquier nombre, por ejemplo <b>descargador</b>.</span></li>
        <li><span>Dale a <b>Crear</b> y espera unos segundos.</span></li>
      </ol><button class="btn" data-url="project">Abrir Google Cloud <span class="arrow">${icon("arrow")}</span></button></div>
      ${browser({ html: `<h4>Nuevo proyecto</h4><div class="small">Nombre del proyecto *</div>
        ${tap(2, `<div class="gfield">descargador</div>`)}<div style="height:18px"></div>${tap(3, `<span class="gbtn">CREAR</span>`)}` })}
      <div class="hint">${icon("info")}<span>Si es tu primera vez, Google te pide aceptar sus términos. Acéptalos y sigue.</span></div>`,
    next: "Ya lo creé",
  },
  {
    rail: "Activar Drive API",
    render: () => `
      <div class="eyebrow">Paso 2 de 6</div>
      <h1 id="wz-title">Activa Google Drive API</h1>
      <div class="wz-row"><ol>
        <li><span>Dale a <b>Abrir Google Drive API</b>.</span></li>
        <li><span>Arriba, revisa que esté elegido tu proyecto <b>descargador</b>.</span></li>
        <li><span>Dale al botón azul <b>Habilitar</b>.</span></li>
      </ol><button class="btn" data-url="driveApi">Abrir Drive API <span class="arrow">${icon("arrow")}</span></button></div>
      ${browser({ proj: 2, html: `<div style="display:flex;gap:14px;align-items:center">${DRIVE_SVG}<div><h4 style="margin:0">Google Drive API</h4><div class="small" style="margin:2px 0 0">Google Enterprise API</div></div></div>
        <div style="margin:18px 0 0 48px;display:flex;gap:14px">${tap(3, `<span class="gbtn">HABILITAR</span>`)}<span class="gbtn flat">PROBAR ESTA API</span></div>` })}
      <div class="hint">${icon("info")}<span>Si en vez de «Habilitar» dice «Administrar», ya está activada. Sigue.</span></div>`,
    next: "Ya la activé",
  },
  {
    rail: "Crear la clave",
    render: () => `
      <div class="eyebrow">Paso 3 de 6</div>
      <h1 id="wz-title">Crea tu API key</h1>
      <div class="wz-row"><ol>
        <li><span>Dale a <b>Abrir Credenciales</b>.</span></li>
        <li><span>Arriba, dale a <b>+ Crear credenciales</b> y elige <b>Clave de API</b>.</span></li>
        <li><span>Si te pregunta qué API va a usar, elige <b>Google Drive API</b>.</span></li>
      </ol><button class="btn" data-url="credentials">Abrir Credenciales <span class="arrow">${icon("arrow")}</span></button></div>
      ${browser({ html: `<div style="display:flex;align-items:center;gap:26px"><h4 style="margin:0">Credenciales</h4>${tap(2, `<span style="color:#1A73E8;font-size:13px;font-weight:700;padding:6px 4px">+ CREAR CREDENCIALES</span>`)}</div>
        <div style="margin-left:150px">${`<div class="menu"><div class="sel">${tap(3, `<span style="padding:0 2px">Clave de API</span>`)}</div><div>ID de cliente de OAuth</div><div>Cuenta de servicio</div></div>`}</div>` })}`,
    next: "Siguiente",
  },
  {
    rail: "Copiar la clave",
    render: () => `
      <div class="eyebrow">Paso 4 de 6</div>
      <h1 id="wz-title">Copia tu API key</h1>
      <p class="lead">Google te muestra la clave en una ventanita. Dale al botón de <b>copiar</b> que está a la derecha de la clave.</p>
      ${browser({ html: `<h4>Se creó la clave de API</h4><div class="small">Tu clave de API</div>
        <div class="keyline"><code>AIzaSyB3x••••••••••••••••••••••••Qk</code>${tap(4, `<span class="copy"><svg class="i"><use href="#i-paste"/></svg></span>`)}</div>
        <div style="margin-top:16px"><span class="gbtn flat">CERRAR</span></div>` })}
      <div class="hint">${icon("info")}<span>La clave empieza con «AIza» (con i mayúscula). Si cerraste la ventanita, la ves de nuevo en Credenciales → «Mostrar clave».</span></div>`,
    next: "Ya la copié",
  },
  {
    rail: "Protegerla",
    render: () => `
      <div class="eyebrow">Paso 5 de 6 · recomendado</div>
      <h1 id="wz-title">Protégela</h1>
      <div class="wz-row"><ol>
        <li><span>En Credenciales, haz clic en el nombre de tu clave.</span></li>
        <li><span>En <b>Restricciones de API</b>, elige <b>Restringir clave</b>.</span></li>
        <li><span>Marca solo <b>Google Drive API</b> y dale a <b>Guardar</b>.</span></li>
      </ol><button class="btn" data-url="credentials">Abrir Credenciales <span class="arrow">${icon("arrow")}</span></button></div>
      ${browser({ html: `<h4>Restricciones de API</h4>
        <div style="display:flex;gap:40px;align-items:center"><div><div class="radio"><i></i>No restringir clave</div><div style="height:10px"></div>${tap(2, `<div class="radio on" style="margin:4px 6px"><i></i>Restringir clave</div>`)}</div>
        ${tap(3, `<span class="check"><i></i>Google Drive API</span>`)}</div>
        <div style="margin-top:16px">${tap(4, `<span class="gbtn">GUARDAR</span>`)}</div>` })}
      <div class="hint">${icon("info")}<span>Así, aunque alguien viera tu clave, solo le serviría para Google Drive.</span></div>`,
    next: "Ya la protegí",
    skip: true,
  },
  {
    rail: "Pegarla aquí",
    render: () => `
      <div class="eyebrow">Paso 6 de 6</div>
      <h1 id="wz-title">Pega tu API key</h1>
      <p class="lead">La voy a comprobar con Google antes de guardarla.</p>
      <div class="keybox">
        <input class="input" id="key" type="text" spellcheck="false" autocomplete="off" placeholder="AIza…" aria-label="API key">
        <button class="circle" id="paste-key" title="Pegar" aria-label="Pegar API key" style="width:58px;height:58px;background:var(--bg);box-shadow:none">${icon("paste")}</button>
      </div>
      <div class="msg" id="key-msg" role="status"></div>`,
    next: "Comprobar y guardar",
  },
  {
    rail: "¡Listo!",
    render: (extra) => `
      <div class="success">
        <div class="ring"><svg viewBox="0 0 24 24"><path d="m5 12.5 4.5 4.5L19 7.5" stroke-linecap="round" stroke-linejoin="round"/></svg></div>
        <h1 id="wz-title">¡Tu API key funciona!</h1>
        <p class="lead">Quedó guardada en esta compu. Ahora pega el link de una carpeta de Drive y dale a descargar.</p>
        ${extra ? `<div class="hint" style="text-align:left">${icon("info")}<span>${esc(extra)}</span></div>` : ""}
      </div>`,
    next: "Empezar a descargar",
  },
];
const KEY_STEP = 6;
let step = 0, canCancel = false, checking = false, keyWarning = "";

function openWizard(start = 0, cancellable = false) {
  step = start; canCancel = cancellable; keyWarning = "";
  $("#wizard").hidden = false;
  renderStep("next");
}

function closeWizard() {
  $("#wizard").hidden = true;
  refreshState();
}

function renderStep(dir) {
  const s = STEPS[step];
  const body = $("#wz-body");
  body.innerHTML = s.render(keyWarning);
  body.classList.remove("next", "prev"); void body.offsetWidth; body.classList.add(dir);
  body.scrollTop = 0;
  body.querySelectorAll("[data-url]").forEach((b) => (b.onclick = () => api().open_url(URLS[b.dataset.url])));

  $("#rail-steps").innerHTML = STEPS.map((x, i) => {
    const cls = i === step ? "now" : i < step ? "done" : "";
    const check = `<svg class="i" style="width:15px;height:15px;stroke-width:2.6"><use href="#i-check"/></svg>`;
    const n = i < step || i === STEPS.length - 1 ? check : i === 0 ? icon("info") : i;
    return `<div class="rstep ${cls}"><span class="n">${n}</span>${x.rail}</div>`;
  }).join("");

  const last = step === STEPS.length - 1;
  $("#wz-back").hidden = step === 0 || last;
  $("#wz-cancel").hidden = !(canCancel && step === 0) && !(canCancel && step === KEY_STEP);
  $("#wz-skip").hidden = !s.skip;
  setNext(s.next);

  if (step === KEY_STEP) {
    const k = $("#key");
    k.focus();
    k.addEventListener("keydown", (e) => { if (e.key === "Enter") next(); });
    $("#paste-key").onclick = async () => { k.value = await api().paste(); k.focus(); };
  }
}

function setNext(text, busy) {
  const b = $("#wz-next");
  b.disabled = !!busy;
  b.innerHTML = busy ? `<span class="spinner"></span>${text}` : `${text} <span class="arrow">${icon("right")}</span>`;
}

async function next() {
  if (checking) return;
  if (step === KEY_STEP) return checkKey();
  if (step === STEPS.length - 1) { closeWizard(); showView("home"); $("#link").focus(); return; }
  step++; renderStep("next");
}

function back() {
  if (checking || step === 0) return;
  step--; renderStep("prev");
}

async function checkKey() {
  const k = $("#key"), msg = $("#key-msg");
  msg.className = "msg"; msg.textContent = "";
  k.classList.remove("bad");
  checking = true;
  setNext("Comprobando con Google…", true);
  const res = await api().check_key(k.value);
  checking = false;
  if (!res.ok) {
    setNext(STEPS[KEY_STEP].next);
    msg.className = "msg err"; msg.textContent = res.reason;
    void k.offsetWidth; k.classList.add("bad");
    k.focus();
    return;
  }
  keyWarning = res.warning || "";
  step = STEPS.length - 1; renderStep("next");
  refreshState();
}

/* ---------------- Arranque ---------------- */

function wire() {
  document.querySelectorAll("nav.pills button").forEach((b) => (b.onclick = () => showView(b.dataset.view)));
  $("#start").onclick = start;
  $("#link").addEventListener("keydown", (e) => { if (e.key === "Enter") start(); });
  $("#link").addEventListener("input", () => { $("#link-error").textContent = ""; $("#link").classList.remove("bad"); });
  $("#paste-link").onclick = async () => { $("#link").value = await api().paste(); $("#link").focus(); };
  $("#pick-dest").onclick = async () => { state.dest = await api().pick_folder(); applyState(); };
  $("#s-pick").onclick = $("#pick-dest").onclick;
  $("#top-folder").onclick = () => api().open_dest();
  $("#top-key").onclick = () => openWizard(state.has_key ? KEY_STEP : 0, state.has_key);
  $("#s-change-key").onclick = () => openWizard(KEY_STEP, true);
  $("#w-down").onclick = async () => { state.workers = await api().set_workers(state.workers - 1); applyState(); };
  $("#w-up").onclick = async () => { state.workers = await api().set_workers(state.workers + 1); applyState(); };
  $("#open-log").onclick = openLog;
  $("#close-log").onclick = () => ($("#log-wrap").hidden = true);
  $("#log-wrap").addEventListener("click", (e) => { if (e.target.id === "log-wrap") e.target.hidden = true; });
  $("#wz-next").onclick = next;
  $("#wz-back").onclick = back;
  $("#wz-skip").onclick = () => { step++; renderStep("next"); };
  $("#wz-cancel").onclick = closeWizard;
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (!$("#log-wrap").hidden) $("#log-wrap").hidden = true;
      else if (!$("#wizard").hidden && canCancel) closeWizard();
    }
  });
  window.addEventListener("resize", moveSlider);
}

/* La ventana es de tamaño fijo (1180×740). En pantallas más chicas todo se
   achica en la misma proporción, así nunca se corta nada. */
const BASE_W = 1180, BASE_H = 740;
function fit() {
  const z = Math.min(1, window.innerWidth / BASE_W, window.innerHeight / BASE_H);
  document.body.classList.toggle("scaled", z < 1);
  document.body.style.zoom = z < 1 ? z : "";
}

async function boot() {
  fit();
  window.addEventListener("resize", fit);
  wire();
  await document.fonts.ready;
  moveSlider();
  await refreshState();
  showView("home");
  if (!state.has_key) openWizard(0, false);
  if (state.running) startPolling();
  window.__ready = true;
}

if (window.pywebview && window.pywebview.api) boot();
else window.addEventListener("pywebviewready", boot);
