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

document.querySelectorAll(".main-tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".main-tab-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    const tab = btn.dataset.mainTab;
    el("main-tab-email").classList.toggle("hidden", tab !== "email");
    el("main-tab-company").classList.toggle("hidden", tab !== "company");
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
    showError("Seleziona un file .xlsx, .xls o .docx prima di procedere.");
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

// --- Ricerca Soci/Partner (Cluster MINIT): elenco .docx, generale o per tematica ---

const companyFileInput = el("company-file-input");
const companyModeSelect = el("company-mode-select");
const companyPlanField = el("company-plan-field");
const companyPlanInput = el("company-plan-input");
const companyPeriodSelect = el("company-period-select");
const companyCustomStart = el("company-custom-start");
const companyCustomEnd = el("company-custom-end");
const companyStartBtn = el("company-start-btn");
const companyFormError = el("company-form-error");

const companyProgressCard = el("company-progress-card");
const companyProgressBar = el("company-progress-bar");
const companyProgressLog = el("company-progress-log");

const companySummaryCard = el("company-summary-card");
const companySummaryGrid = el("company-summary-grid");

const companyResultsCard = el("company-results-card");
const companyWeeklyTable = el("company-weekly-table");
const companyDetailedTable = el("company-detailed-table");
const companyTopicFilterField = el("company-topic-filter-field");
const companyTopicFilter = el("company-topic-filter");

let currentCompanyJobId = null;
let currentCompanyResults = null;

companyModeSelect.addEventListener("change", () => {
  companyPlanField.classList.toggle("hidden", companyModeSelect.value !== "topic");
});

companyPeriodSelect.addEventListener("change", () => {
  const isCustom = companyPeriodSelect.value === "custom";
  el("company-custom-range-fields").classList.toggle("hidden", !isCustom);
  el("company-custom-range-fields-2").classList.toggle("hidden", !isCustom);
});

document.querySelectorAll(".company-tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".company-tab-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    const tab = btn.dataset.companyTab;
    el("company-tab-weekly").classList.toggle("hidden", tab !== "weekly");
    el("company-tab-detailed").classList.toggle("hidden", tab !== "detailed");
  });
});

function showCompanyError(msg) {
  companyFormError.textContent = msg;
  companyFormError.classList.remove("hidden");
}
function clearCompanyError() {
  companyFormError.classList.add("hidden");
  companyFormError.textContent = "";
}

function companyLogLine(text, cls = "") {
  const line = document.createElement("div");
  line.className = cls;
  line.textContent = text;
  companyProgressLog.appendChild(line);
  companyProgressLog.scrollTop = companyProgressLog.scrollHeight;
}

companyStartBtn.addEventListener("click", async () => {
  clearCompanyError();
  const file = companyFileInput.files[0];
  if (!file) {
    showCompanyError("Seleziona un file .docx, .xlsx o .xls con l'elenco soci/partner.");
    return;
  }
  const mode = companyModeSelect.value;
  const planFile = companyPlanInput.files[0];
  if (mode === "topic" && !planFile) {
    showCompanyError("La ricerca per tematica richiede il piano editoriale (.xlsx).");
    return;
  }
  const period = companyPeriodSelect.value;
  if (period === "custom" && (!companyCustomStart.value || !companyCustomEnd.value)) {
    showCompanyError("Inserisci entrambe le date per l'intervallo personalizzato.");
    return;
  }

  companyStartBtn.disabled = true;
  companyProgressCard.classList.remove("hidden");
  companySummaryCard.classList.add("hidden");
  companyResultsCard.classList.add("hidden");
  companyProgressLog.innerHTML = "";
  companyProgressBar.style.width = "0%";

  try {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("mode", mode);
    formData.append("period", period);
    if (period === "custom") {
      formData.append("custom_start", companyCustomStart.value);
      formData.append("custom_end", companyCustomEnd.value);
    }
    if (mode === "topic") {
      formData.append("plan_file", planFile);
    }

    companyLogLine("Caricamento file e creazione job...");
    const createResp = await fetch(`${API_BASE}/api/company-jobs`, { method: "POST", body: formData, headers: authHeaders() });
    if (!createResp.ok) {
      const errBody = await createResp.json().catch(() => ({}));
      throw new Error(errBody.detail || `Errore nella creazione del job (HTTP ${createResp.status}).`);
    }
    const { job_id, total_companies, date_from, date_to } = await createResp.json();
    currentCompanyJobId = job_id;
    companyLogLine(`Job creato: ${total_companies} soci/partner da elaborare. Periodo: ${date_from} → ${date_to}.`, "ok");

    await runCompanyStream(job_id, total_companies);
  } catch (err) {
    showCompanyError(err.message || String(err));
    companyLogLine(`Errore: ${err.message || err}`, "error");
  } finally {
    companyStartBtn.disabled = false;
  }
});

function runCompanyStream(jobId, total) {
  return new Promise((resolve, reject) => {
    const pwd = storedPassword();
    const streamUrl = `${API_BASE}/api/jobs/${jobId}/stream` + (pwd ? `?password=${encodeURIComponent(pwd)}` : "");
    const es = new EventSource(streamUrl);

    es.addEventListener("progress", (ev) => {
      const data = JSON.parse(ev.data);
      const pct = Math.round((data.index / data.total) * 100);
      companyProgressBar.style.width = `${pct}%`;
      let cls = "";
      if (/non trovat/i.test(data.message)) cls = "warn";
      if (/errore/i.test(data.message)) cls = "error";
      if (/completata/i.test(data.message)) cls = "ok";
      const label = data.email ? `${data.email} — ` : "";
      companyLogLine(`[${data.index}/${data.total}] ${label}${data.message}`, cls);
    });

    es.addEventListener("summary", (ev) => {
      const s = JSON.parse(ev.data);
      renderCompanySummary(s);
    });

    es.addEventListener("result", (ev) => {
      const data = JSON.parse(ev.data);
      currentCompanyResults = data;
      renderCompanyTables(data);
      if (data.classify_error) {
        companyLogLine(`Errore classificazione tematica: ${data.classify_error}`, "error");
      }
      companyLogLine("Elaborazione completata.", "ok");
      es.close();
      resolve();
    });

    es.onerror = () => {
      es.close();
      reject(new Error("Connessione al backend interrotta durante l'elaborazione. Riprova."));
    };
  });
}

function renderCompanySummary(s) {
  companySummaryCard.classList.remove("hidden");
  const stats = [
    ["Soci/partner elaborati", s.processed],
    ["Pagine LinkedIn trovate", s.profiles_found],
    ["Pagine non trovate", s.profiles_not_found],
    ["Match non verificati", s.unverified_matches],
    ["Errori", s.errors],
    ["Post totali trovati", s.total_posts],
  ];
  companySummaryGrid.innerHTML = stats
    .map(([label, n]) => `<div class="summary-stat"><div class="n">${n}</div><div class="l">${label}</div></div>`)
    .join("");
}

function renderCompanyTables(data) {
  companyResultsCard.classList.remove("hidden");
  const includeTopic = data.mode === "topic";

  // Vista settimanale
  const wHead = `<thead><tr><th>Azienda/Socio</th><th>Nome pagina LinkedIn</th><th>URL pagina LinkedIn</th>${GIORNI_IT.map((g) => `<th>${g}</th>`).join("")}</tr></thead>`;
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
  companyWeeklyTable.innerHTML = wHead + `<tbody>${wBody}</tbody>`;

  // Vista dettagliata
  const topicHeader = includeTopic ? "<th>Area Tematica</th>" : "";
  const dHead = `<thead><tr><th>Azienda/Socio</th><th>Nome pagina LinkedIn</th><th>URL pagina LinkedIn</th><th>Data del post</th><th>Giorno della settimana</th><th>Link al post</th>${topicHeader}</tr></thead>`;
  const dBody = data.detailed_rows
    .map((row) => {
      const urlCell = row.profile_url ? `<a href="${row.profile_url}" target="_blank" rel="noopener">${escapeHtml(row.profile_url)}</a>` : "";
      const isLink = row.post_link && row.post_link.startsWith("http");
      const linkCell = isLink
        ? `<a href="${row.post_link}" target="_blank" rel="noopener">${escapeHtml(row.post_link)}</a>`
        : row.post_link
        ? statusPill(row.post_link)
        : "";
      const topic = row.topic || "";
      const topicCell = includeTopic ? `<td>${escapeHtml(topic)}</td>` : "";
      const topicAttr = includeTopic ? ` data-topic="${escapeHtml(topic)}"` : "";
      return `<tr${topicAttr}><td>${escapeHtml(row.email)}</td><td>${escapeHtml(row.profile_name)}</td><td>${urlCell}</td><td>${escapeHtml(row.post_date)}</td><td>${escapeHtml(row.day_of_week)}</td><td>${linkCell}</td>${topicCell}</tr>`;
    })
    .join("");
  companyDetailedTable.innerHTML = dHead + `<tbody>${dBody}</tbody>`;

  setupCompanyTopicFilter(data, includeTopic);
}

function setupCompanyTopicFilter(data, includeTopic) {
  companyTopicFilterField.classList.toggle("hidden", !includeTopic);
  companyTopicFilter.value = "";
  if (!includeTopic) return;

  const topics = (data.topics && data.topics.length ? data.topics : []).slice();
  // Aggiunge anche eventuali etichette diagnostiche assegnate dal classificatore
  // (es. "Nessuna tematica", "Errore classificazione") se presenti nei risultati.
  const extra = new Set();
  data.detailed_rows.forEach((row) => {
    if (row.topic && !topics.includes(row.topic)) extra.add(row.topic);
  });
  const allTopics = topics.concat([...extra]);

  companyTopicFilter.innerHTML =
    `<option value="">Tutte le tematiche</option>` +
    allTopics.map((t) => `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`).join("");
}

companyTopicFilter.addEventListener("change", () => {
  const selected = companyTopicFilter.value;
  companyDetailedTable.querySelectorAll("tbody tr").forEach((tr) => {
    const rowTopic = tr.dataset.topic || "";
    tr.classList.toggle("hidden", !!selected && rowTopic !== selected);
  });
});

el("company-export-weekly-btn").addEventListener("click", () => downloadCompanyExport("weekly"));
el("company-export-detailed-btn").addEventListener("click", () => downloadCompanyExport("detailed"));

async function downloadCompanyExport(type) {
  if (!currentCompanyJobId) return;
  const resp = await fetch(`${API_BASE}/api/jobs/${currentCompanyJobId}/export?type=${type}`, { headers: authHeaders() });
  if (!resp.ok) {
    showCompanyError(`Impossibile generare il file Excel (HTTP ${resp.status}).`);
    return;
  }
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = type === "weekly" ? "soci_post_settimanale.xlsx" : "soci_post_dettagliato.xlsx";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

initAuth();
