// Nessuna API key, token o credenziale è presente in questo file: tutte le
// chiamate sensibili verso Crustdata avvengono lato backend (vedi backend/).

const API_BASE = (window.APP_CONFIG && window.APP_CONFIG.API_BASE_URL) || "";
const GIORNI_IT = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"];

// --- Accesso con password condivisa (opzionale, vedi backend APP_PASSWORD) ---
const PASSWORD_STORAGE_KEY = "linkedin_post_finder_password";

function storedPassword() {
  return sessionStorage.getItem(PASSWORD_STORAGE_KEY) || "";
}

function authHeaders() {
  const pwd = storedPassword();
  return pwd ? { "X-App-Password": pwd } : {};
}

async function checkPassword(pwd) {
  const resp = await fetch(`${API_BASE}/api/login`, {
    method: "POST",
    headers: { "X-App-Password": pwd },
  });
  return resp.ok;
}

function showApp() {
  el("login-overlay").classList.add("hidden");
  el("app-main").classList.remove("hidden");
}

function showLogin(onSuccess) {
  const overlay = el("login-overlay");
  const input = el("login-password");
  const btn = el("login-btn");
  const err = el("login-error");
  overlay.classList.remove("hidden");

  const attempt = async () => {
    err.classList.add("hidden");
    const ok = await checkPassword(input.value);
    if (ok) {
      sessionStorage.setItem(PASSWORD_STORAGE_KEY, input.value);
      onSuccess();
    } else {
      err.textContent = "Password errata.";
      err.classList.remove("hidden");
    }
  };

  btn.addEventListener("click", attempt);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") attempt();
  });
}

async function initAuth() {
  const health = await fetch(`${API_BASE}/api/health`).then((r) => r.json()).catch(() => ({}));
  if (!health.password_protected) {
    showApp();
    return;
  }
  const stored = storedPassword();
  if (stored && (await checkPassword(stored))) {
    showApp();
    return;
  }
  showLogin(showApp);
}

const el = (id) => document.getElementById(id);

const fileInput = el("file-input");
const periodSelect = el("period-select");
const customStart = el("custom-start");
const customEnd = el("custom-end");
const startBtn = el("start-btn");
const formError = el("form-error");

const progressCard = el("progress-card");
const progressBar = el("progress-bar");
const progressLog = el("progress-log");

const summaryCard = el("summary-card");
const summaryGrid = el("summary-grid");

const resultsCard = el("results-card");
const weeklyTable = el("weekly-table");
const detailedTable = el("detailed-table");

let currentJobId = null;
let currentResults = null;

periodSelect.addEventListener("change", () => {
  const isCustom = periodSelect.value === "custom";
  el("custom-range-fields").classList.toggle("hidden", !isCustom);
  el("custom-range-fields-2").classList.toggle("hidden", !isCustom);
});

document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    const tab = btn.dataset.tab;
    el("tab-weekly").classList.toggle("hidden", tab !== "weekly");
    el("tab-detailed").classList.toggle("hidden", tab !== "detailed");
  });
});

function showError(msg) {
  formError.textContent = msg;
  formError.classList.remove("hidden");
}
function clearError() {
  formError.classList.add("hidden");
  formError.textContent = "";
}

function logLine(text, cls = "") {
  const line = document.createElement("div");
  line.className = cls;
  line.textContent = text;
  progressLog.appendChild(line);
  progressLog.scrollTop = progressLog.scrollHeight;
}

startBtn.addEventListener("click", async () => {
  clearError();
  const file = fileInput.files[0];
  if (!file) {
    showError("Seleziona un file .xlsx prima di procedere.");
    return;
  }
  const period = periodSelect.value;
  if (period === "custom" && (!customStart.value || !customEnd.value)) {
    showError("Inserisci entrambe le date per l'intervallo personalizzato.");
    return;
  }

  startBtn.disabled = true;
  progressCard.classList.remove("hidden");
  summaryCard.classList.add("hidden");
  resultsCard.classList.add("hidden");
  progressLog.innerHTML = "";
  progressBar.style.width = "0%";

  try {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("period", period);
    if (period === "custom") {
      formData.append("custom_start", customStart.value);
      formData.append("custom_end", customEnd.value);
    }

    logLine("Caricamento file e creazione job...");
    const createResp = await fetch(`${API_BASE}/api/jobs`, { method: "POST", body: formData, headers: authHeaders() });
    if (!createResp.ok) {
      const errBody = await createResp.json().catch(() => ({}));
      throw new Error(errBody.detail || `Errore nella creazione del job (HTTP ${createResp.status}).`);
    }
    const { job_id, total_emails, date_from, date_to } = await createResp.json();
    currentJobId = job_id;
    logLine(`Job creato: ${total_emails} email da elaborare. Periodo: ${date_from} → ${date_to}.`, "ok");

    await runStream(job_id, total_emails);
  } catch (err) {
    showError(err.message || String(err));
    logLine(`Errore: ${err.message || err}`, "error");
  } finally {
    startBtn.disabled = false;
  }
});

function runStream(jobId, total) {
  return new Promise((resolve, reject) => {
    // EventSource non supporta header custom: la password (se impostata) va
    // passata come query param, unica eccezione tra le chiamate API.
    const pwd = storedPassword();
    const streamUrl = `${API_BASE}/api/jobs/${jobId}/stream` + (pwd ? `?password=${encodeURIComponent(pwd)}` : "");
    const es = new EventSource(streamUrl);

    es.addEventListener("progress", (ev) => {
      const data = JSON.parse(ev.data);
      const pct = Math.round((data.index / data.total) * 100);
      progressBar.style.width = `${pct}%`;
      let cls = "";
      if (/non trovato/i.test(data.message)) cls = "warn";
      if (/errore/i.test(data.message)) cls = "error";
      if (/completata/i.test(data.message)) cls = "ok";
      logLine(`[${data.index}/${data.total}] ${data.email} — ${data.message}`, cls);
    });

    es.addEventListener("summary", (ev) => {
      const s = JSON.parse(ev.data);
      renderSummary(s);
    });

    es.addEventListener("result", (ev) => {
      const data = JSON.parse(ev.data);
      currentResults = data;
      renderTables(data);
      logLine("Elaborazione completata.", "ok");
      es.close();
      resolve();
    });

    es.onerror = () => {
      es.close();
      reject(new Error("Connessione al backend interrotta durante l'elaborazione. Riprova."));
    };
  });
}

function renderSummary(s) {
  summaryCard.classList.remove("hidden");
  const stats = [
    ["Email elaborate", s.processed],
    ["Profili trovati", s.profiles_found],
    ["Profili non trovati", s.profiles_not_found],
    ["Match non verificati", s.unverified_matches],
    ["Errori", s.errors],
    ["Post totali trovati", s.total_posts],
  ];
  summaryGrid.innerHTML = stats
    .map(([label, n]) => `<div class="summary-stat"><div class="n">${n}</div><div class="l">${label}</div></div>`)
    .join("");
}

function statusPill(text) {
  let cls = "";
  if (/non trovato/i.test(text)) cls = "not-found";
  else if (/non verificato/i.test(text)) cls = "unverified";
  else if (/nessun post/i.test(text)) cls = "no-posts";
  else if (/errore/i.test(text)) cls = "error";
  return `<span class="status-pill ${cls}">${escapeHtml(text)}</span>`;
}

function escapeHtml(str) {
  return String(str ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function renderTables(data) {
  resultsCard.classList.remove("hidden");

  // Vista settimanale
  const wHead = `<thead><tr><th>Email</th><th>Nome profilo LinkedIn</th><th>URL profilo LinkedIn</th>${GIORNI_IT.map((g) => `<th>${g}</th>`).join("")}</tr></thead>`;
  const wBody = data.weekly_rows
    .map((row) => {
      const nameCell = row.profile_name ? escapeHtml(row.profile_name) : (row.status_label ? statusPill(row.status_label) : "");
      const urlCell = row.profile_url
        ? `<a href="${row.profile_url}" target="_blank" rel="noopener">${escapeHtml(row.profile_url)}</a>`
        : row.status_label
        ? statusPill(row.status_label)
        : "";
      const dayCells = GIORNI_IT.map((g) => {
        const links = (row.giorni && row.giorni[g]) || [];
        if (!links.length) return "<td></td>";
        return `<td>${links.map((l) => `<a href="${l}" target="_blank" rel="noopener">${escapeHtml(l)}</a>`).join("")}</td>`;
      }).join("");
      return `<tr><td>${escapeHtml(row.email)}</td><td>${nameCell}</td><td>${urlCell}</td>${dayCells}</tr>`;
    })
    .join("");
  weeklyTable.innerHTML = wHead + `<tbody>${wBody}</tbody>`;

  // Vista dettagliata
  const dHead = `<thead><tr><th>Email</th><th>Nome profilo LinkedIn</th><th>URL profilo LinkedIn</th><th>Data del post</th><th>Giorno della settimana</th><th>Link al post</th></tr></thead>`;
  const dBody = data.detailed_rows
    .map((row) => {
      const urlCell = row.profile_url ? `<a href="${row.profile_url}" target="_blank" rel="noopener">${escapeHtml(row.profile_url)}</a>` : "";
      const isLink = row.post_link && row.post_link.startsWith("http");
      const linkCell = isLink
        ? `<a href="${row.post_link}" target="_blank" rel="noopener">${escapeHtml(row.post_link)}</a>`
        : row.post_link
        ? statusPill(row.post_link)
        : "";
      return `<tr><td>${escapeHtml(row.email)}</td><td>${escapeHtml(row.profile_name)}</td><td>${urlCell}</td><td>${escapeHtml(row.post_date)}</td><td>${escapeHtml(row.day_of_week)}</td><td>${linkCell}</td></tr>`;
    })
    .join("");
  detailedTable.innerHTML = dHead + `<tbody>${dBody}</tbody>`;
}

el("export-weekly-btn").addEventListener("click", () => downloadExport("weekly"));
el("export-detailed-btn").addEventListener("click", () => downloadExport("detailed"));

async function downloadExport(type) {
  if (!currentJobId) return;
  const resp = await fetch(`${API_BASE}/api/jobs/${currentJobId}/export?type=${type}`, { headers: authHeaders() });
  if (!resp.ok) {
    showError(`Impossibile generare il file Excel (HTTP ${resp.status}).`);
    return;
  }
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = type === "weekly" ? "linkedin_post_settimanale.xlsx" : "linkedin_post_dettagliato.xlsx";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

initAuth();
