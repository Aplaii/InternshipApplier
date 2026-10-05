// Boîtes de dialogue : recherche d'offres, ajout par lien, paramètres et documents.

import { api } from "./api.js";
import { el, icon, toast, confirmDialog, formatSize, hydrateIcons } from "./util.js";

const $ = (id) => document.getElementById(id);

let ctx = { onSearch: () => {}, onChange: () => {}, onOpenOffer: () => {} };
let settings = null;

const SEARCH_SOURCES = [
  { id: "hellowork", label: "HelloWork" },
  { id: "linkedin", label: "LinkedIn" },
  { id: "wttj", label: "Welcome to the Jungle" },
];

// Champs du formulaire de paramètres (hors recherche, gérée par l'autre dialogue).
const TEXT_KEYS = [
  "full_name", "email", "phone", "links", "education", "internship", "cv_text", "signature",
  "llm_base_url", "llm_model", "llm_language", "llm_extra_instructions",
  "smtp_host", "smtp_security", "smtp_username", "smtp_from_name", "smtp_from_email",
];
const int = (v) => parseInt(v, 10);
const NUMBER_KEYS = {
  llm_temperature: parseFloat, llm_max_tokens: int, smtp_port: int, auto_interval_hours: parseFloat, auto_draft_max: int,
};
const BOOL_KEYS = ["llm_disable_thinking", "smtp_bcc_self", "ai_only", "auto_enabled", "auto_fetch_details", "auto_draft"];
const SECRET_KEYS = ["llm_api_key", "smtp_password"];

export function getSettings() {
  return settings;
}

export async function loadSettings() {
  settings = await api("/api/settings");
  return settings;
}

function showFormError(id, message) {
  const box = $(id);
  box.textContent = message || "";
  box.hidden = !message;
}

function closeOnCancel(dialog) {
  for (const button of dialog.querySelectorAll("[data-close]")) {
    button.addEventListener("click", () => dialog.close());
  }
}

// --------------------------------------------------------------------------- recherche

export function openFindDialog() {
  const form = $("find-form");
  const s = settings || {};
  form.elements.keywords.value = s.search_keywords || "";
  form.elements.location.value = s.search_location ?? "France";
  form.elements.recency.value = s.search_recency || "week";
  form.elements.max_per_source.value = s.search_max_per_source || 60;
  form.elements.ai_only.checked = s.ai_only !== false;
  const chosen = new Set(s.search_sources || SEARCH_SOURCES.map((x) => x.id));
  $("find-sources").replaceChildren(...SEARCH_SOURCES.map((source) => {
    const input = el("input", { type: "checkbox", name: "sources", value: source.id });
    input.checked = chosen.has(source.id);
    return el("label", { class: "check" }, [input, el("span", { class: `src-dot src-${source.id}` }), source.label]);
  }));
  showFormError("find-error", "");
  $("find-dialog").showModal();
  form.elements.keywords.focus();
}

function bindFindDialog() {
  const dialog = $("find-dialog");
  closeOnCancel(dialog);
  $("find-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const form = event.target;
    const sources = [...form.querySelectorAll("input[name=sources]:checked")].map((input) => input.value);
    if (!sources.length) {
      showFormError("find-error", "Choisissez au moins un site.");
      return;
    }
    const max = parseInt(form.elements.max_per_source.value, 10);
    if (!Number.isFinite(max) || max < 10 || max > 300) {
      showFormError("find-error", "Le nombre d'offres par site doit être compris entre 10 et 300.");
      return;
    }
    dialog.close();
    ctx.onSearch({
      keywords: form.elements.keywords.value.trim(),
      location: form.elements.location.value.trim(),
      sources,
      recency: form.elements.recency.value,
      max_per_source: max,
      ai_only: form.elements.ai_only.checked,
    });
  });
}

// --------------------------------------------------------------------------- ajout par lien

export function openAddDialog() {
  const form = $("add-form");
  form.reset();
  showFormError("add-error", "");
  $("add-info").hidden = true;
  $("add-dialog").showModal();
  form.elements.url.focus();
}

function showAddInfo(children) {
  const box = $("add-info");
  box.replaceChildren(...children);
  box.hidden = !children.length;
}

function existingOfferNotice(id) {
  return [
    el("span", { text: "Cette offre est déjà dans votre liste. " }),
    el("button", {
      type: "button",
      class: "link-btn",
      text: "L'ouvrir",
      onclick: () => {
        $("add-dialog").close();
        ctx.onOpenOffer(id);
      },
    }),
  ];
}

function bindAddDialog() {
  const dialog = $("add-dialog");
  const form = $("add-form");
  closeOnCancel(dialog);
  $("add-read").addEventListener("click", async () => {
    const url = form.elements.url.value.trim();
    showFormError("add-error", "");
    if (!url) {
      showFormError("add-error", "Collez d'abord le lien de l'offre.");
      return;
    }
    const button = $("add-read");
    button.disabled = true;
    showAddInfo([el("span", { class: "spinner small" }), " Lecture de l'offre…"]);
    try {
      const data = await api("/api/offers/import", { method: "POST", json: { url } });
      for (const key of ["title", "company", "location", "contract", "description"]) {
        if (data[key] && !form.elements[key].value.trim()) form.elements[key].value = data[key];
      }
      if (data.existing_id) showAddInfo(existingOfferNotice(data.existing_id));
      else if (data.error) showAddInfo([el("span", { text: `Lecture automatique impossible (${data.error}). Complétez les champs à la main.` })]);
      else showAddInfo([el("span", { text: `Offre lue sur ${data.source_label} : vérifiez les champs puis cliquez sur « Ajouter ».` })]);
    } catch (error) {
      showAddInfo([]);
      showFormError("add-error", error.message);
    } finally {
      button.disabled = false;
    }
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    showFormError("add-error", "");
    const payload = Object.fromEntries(["url", "title", "company", "location", "contract", "description"]
      .map((key) => [key, form.elements[key].value.trim()]));
    if (payload.url && !payload.title && $("add-info").hidden) {
      $("add-read").click(); // Entrée dans le champ du lien : on lit d'abord l'offre
      return;
    }
    if (!payload.url || !payload.title) {
      showFormError("add-error", "Le lien et l'intitulé du poste sont obligatoires.");
      return;
    }
    try {
      const offer = await api("/api/offers", { method: "POST", json: payload });
      dialog.close();
      toast("Offre ajoutée à la boîte de réception.");
      await ctx.onChange();
      ctx.onOpenOffer(offer.id);
    } catch (error) {
      if (error.status === 409 && error.data?.detail?.id) showAddInfo(existingOfferNotice(error.data.detail.id));
      else showFormError("add-error", error.message);
    }
  });
}

// --------------------------------------------------------------------------- paramètres

function selectTab(name) {
  for (const tab of document.querySelectorAll("#settings-dialog [role=tab]")) {
    tab.setAttribute("aria-selected", String(tab.dataset.tab === name));
  }
  for (const panel of document.querySelectorAll("#settings-dialog .tab-panel")) {
    panel.hidden = panel.dataset.panel !== name;
  }
}

function fillSettingsForm() {
  const form = $("settings-form");
  for (const key of TEXT_KEYS) form.elements[key].value = settings[key] ?? "";
  for (const key of Object.keys(NUMBER_KEYS)) form.elements[key].value = settings[key] ?? "";
  for (const key of BOOL_KEYS) form.elements[key].checked = Boolean(settings[key]);
  for (const key of SECRET_KEYS) {
    form.elements[key].value = "";
    form.elements[key].placeholder = settings[`has_${key}`] ? "•••••••• enregistré (laisser vide pour le garder)" : "";
  }
  form.elements.signature.placeholder = settings.effective_signature
    ? `Signature actuelle :\n${settings.effective_signature}`
    : "";
  $("clear-smtp-password").hidden = !settings.has_smtp_password;
}

function collectSettings() {
  const form = $("settings-form");
  const data = {};
  for (const key of TEXT_KEYS) data[key] = form.elements[key].value;
  for (const [key, parse] of Object.entries(NUMBER_KEYS)) {
    const raw = form.elements[key].value.trim();
    if (raw === "") continue;
    const value = parse(raw);
    if (!Number.isFinite(value)) throw new Error(`Valeur numérique invalide : ${form.elements[key].closest("label").querySelector("span").textContent}`);
    data[key] = value;
  }
  for (const key of BOOL_KEYS) data[key] = form.elements[key].checked;
  for (const key of SECRET_KEYS) {
    if (form.elements[key].value) data[key] = form.elements[key].value;
  }
  return data;
}

async function saveSettings() {
  showFormError("settings-error", "");
  let payload;
  try {
    payload = collectSettings();
  } catch (error) {
    showFormError("settings-error", error.message);
    return false;
  }
  try {
    settings = await api("/api/settings", { method: "PUT", json: payload });
  } catch (error) {
    showFormError("settings-error", error.message);
    return false;
  }
  fillSettingsForm();
  return true;
}

async function runTest(buttonId, resultId, path) {
  const button = $(buttonId);
  const result = $(resultId);
  button.disabled = true;
  result.className = "test-result";
  result.textContent = "Test en cours…";
  try {
    if (!(await saveSettings())) {
      result.textContent = "";
      return;
    }
    const data = await api(path, { method: "POST" });
    result.className = `test-result ${data.ok ? "ok" : "ko"}`;
    result.textContent = data.message;
  } catch (error) {
    result.className = "test-result ko";
    result.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

export async function openSettings(tab = "profile") {
  try {
    await loadSettings();
  } catch (error) {
    toast(error.message, { error: true });
    return;
  }
  fillSettingsForm();
  showFormError("settings-error", "");
  $("test-llm-result").textContent = "";
  $("test-smtp-result").textContent = "";
  selectTab(tab);
  await Promise.all([refreshDocuments(), refreshAutomation().catch(() => null)]);
  $("settings-dialog").showModal();
}

// --------------------------------------------------------------------------- automatisation

const RUNNING_LABELS = {
  search: "Recherche en cours sur les sites…",
  details: "Lecture des nouvelles offres…",
  drafts: "Rédaction des brouillons par l'IA…",
};

function relativeTime(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const minutes = Math.round((date - Date.now()) / 60000);
  const rtf = new Intl.RelativeTimeFormat("fr-FR", { numeric: "auto" });
  if (Math.abs(minutes) < 60) return rtf.format(minutes, "minute");
  const hours = Math.round(minutes / 60);
  if (Math.abs(hours) < 48) return rtf.format(hours, "hour");
  return rtf.format(Math.round(hours / 24), "day");
}

function summaryText(summary) {
  if (!summary) return "";
  const parts = [`${summary.new_total} nouvelle${summary.new_total > 1 ? "s" : ""} offre${summary.new_total > 1 ? "s" : ""}`];
  if (summary.filtered_total) parts.push(`${summary.filtered_total} hors IA écartée${summary.filtered_total > 1 ? "s" : ""}`);
  if (summary.details) parts.push(`${summary.details} description${summary.details > 1 ? "s" : ""} lue${summary.details > 1 ? "s" : ""}`);
  if (summary.drafted) parts.push(`${summary.drafted} brouillon${summary.drafted > 1 ? "s" : ""} préparé${summary.drafted > 1 ? "s" : ""}`);
  return parts.join(" · ");
}

/** Met à jour l'indicateur de la barre latérale et l'onglet Automatisation. Renvoie l'état. */
export async function refreshAutomation() {
  const status = await api("/api/automation");
  const indicator = $("auto-indicator");
  indicator.classList.toggle("on", status.enabled);
  indicator.classList.toggle("running", Boolean(status.running));
  let label = "Recherche automatique désactivée";
  if (status.running) label = RUNNING_LABELS[status.running] || "Recherche en cours…";
  else if (status.enabled && status.next_run) label = `Auto : ${relativeTime(status.next_run)}`;
  $("auto-indicator-text").textContent = label;
  indicator.title = `${label} — cliquer pour régler la recherche automatique`;

  const summary = status.last_summary;
  const lines = [];
  if (status.running) lines.push(el("p", {}, [el("span", { class: "spinner small" }), ` ${label}`]));
  if (status.last_run) {
    lines.push(el("p", {}, [
      el("strong", { text: `Dernière recherche ${relativeTime(status.last_run)}` }),
      summary?.trigger === "manual" ? " (lancée à la main)" : "",
      summary ? ` : ${summaryText(summary)}.` : "",
    ]));
    for (const error of summary?.errors || []) lines.push(el("p", { class: "error-text", text: error }));
    if (summary?.draft_error) lines.push(el("p", { class: "error-text", text: `Brouillons IA : ${summary.draft_error}` }));
  } else {
    lines.push(el("p", { class: "muted", text: "Aucune recherche automatique pour l'instant." }));
  }
  if (status.enabled && status.next_run && !status.running) {
    lines.push(el("p", { class: "muted", text: `Prochaine recherche ${relativeTime(status.next_run)} (si l'application est ouverte).` }));
  }
  $("auto-status").replaceChildren(...lines);
  $("auto-run").disabled = Boolean(status.running);
  return status;
}

async function runAutomationNow() {
  if (!(await saveSettings())) return;
  try {
    await api("/api/automation/run", { method: "POST" });
  } catch (error) {
    showFormError("settings-error", error.message);
    return;
  }
  let status = await refreshAutomation();
  while (status.running) {
    await new Promise((resolve) => { setTimeout(resolve, 2000); });
    try {
      status = await refreshAutomation();
    } catch {
      break;
    }
  }
  const n = status.last_summary?.new_total || 0;
  toast(n ? `${n} nouvelle${n > 1 ? "s" : ""} offre${n > 1 ? "s" : ""} en IA.` : "Recherche terminée : aucune nouvelle offre.", { timeout: 6000 });
  await ctx.onChange();
}

// --------------------------------------------------------------------------- documents

async function refreshDocuments() {
  let docs = [];
  try {
    docs = await api("/api/documents");
  } catch (error) {
    toast(error.message, { error: true });
  }
  const list = $("docs-list");
  list.replaceChildren(...(docs.length ? docs.map((doc) => el("li", { class: "doc" }, [
    icon("attach_file"),
    el("span", { class: "doc-name", text: doc.name }),
    el("span", { class: "muted small-text", text: formatSize(doc.size) }),
    el("button", {
      type: "button",
      class: "icon-btn small",
      title: `Supprimer ${doc.name}`,
      "aria-label": `Supprimer ${doc.name}`,
      onclick: async () => {
        const ok = await confirmDialog({ title: "Supprimer ce document ?", text: doc.name, okLabel: "Supprimer", danger: true });
        if (!ok) return;
        try {
          await api(`/api/documents/${encodeURIComponent(doc.name)}`, { method: "DELETE" });
        } catch (error) {
          toast(error.message, { error: true });
        }
        refreshDocuments();
      },
    }, icon("delete")),
  ])) : [el("li", { class: "muted", text: "Aucun document pour l'instant." })]));

  const select = $("cv-import-select");
  const importable = docs.filter((doc) => /\.(pdf|txt)$/i.test(doc.name));
  select.replaceChildren(...(importable.length
    ? importable.map((doc) => el("option", { value: doc.name, text: doc.name }))
    : [el("option", { value: "", text: "Aucun PDF dans Documents" })]));
  select.disabled = !importable.length;
  $("cv-import-btn").disabled = !importable.length;
}

async function uploadFiles(files) {
  for (const file of files) {
    try {
      await api(`/api/documents/${encodeURIComponent(file.name)}`, {
        method: "PUT",
        body: file,
        headers: { "Content-Type": "application/octet-stream" },
      });
      toast(`${file.name} ajouté.`, { timeout: 3000 });
    } catch (error) {
      toast(`${file.name} : ${error.message}`, { error: true });
    }
  }
  await refreshDocuments();
}

function bindSettingsDialog() {
  const dialog = $("settings-dialog");
  const form = $("settings-form");
  closeOnCancel(dialog);
  for (const tab of dialog.querySelectorAll("[role=tab]")) {
    tab.addEventListener("click", () => selectTab(tab.dataset.tab));
  }
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (await saveSettings()) {
      dialog.close();
      toast("Paramètres enregistrés.", { timeout: 3000 });
      ctx.onChange();
    }
  });
  $("test-llm").addEventListener("click", () => runTest("test-llm", "test-llm-result", "/api/settings/test-llm"));
  $("test-smtp").addEventListener("click", () => runTest("test-smtp", "test-smtp-result", "/api/settings/test-smtp"));
  $("auto-run").addEventListener("click", runAutomationNow);
  $("auto-indicator").addEventListener("click", () => openSettings("auto"));
  $("preset-gmail").addEventListener("click", () => {
    form.elements.smtp_host.value = "smtp.gmail.com";
    form.elements.smtp_port.value = 587;
    form.elements.smtp_security.value = "starttls";
    if (!form.elements.smtp_username.value) form.elements.smtp_username.value = form.elements.email.value;
    form.elements.smtp_password.focus();
  });
  $("clear-smtp-password").addEventListener("click", async () => {
    try {
      settings = await api("/api/settings", { method: "PUT", json: { smtp_password: "" } });
      fillSettingsForm();
      toast("Mot de passe SMTP effacé.", { timeout: 3000 });
    } catch (error) {
      showFormError("settings-error", error.message);
    }
  });
  $("docs-upload").addEventListener("change", async (event) => {
    const files = [...event.target.files];
    event.target.value = "";
    await uploadFiles(files);
  });
  $("cv-import-btn").addEventListener("click", async () => {
    const name = $("cv-import-select").value;
    if (!name) return;
    const area = form.elements.cv_text;
    if (area.value.trim()) {
      const ok = await confirmDialog({
        title: "Remplacer le texte du CV ?",
        text: `Le texte actuel sera remplacé par celui extrait de « ${name} ».`,
        okLabel: "Remplacer",
      });
      if (!ok) return;
    }
    try {
      const { text } = await api(`/api/documents/${encodeURIComponent(name)}/text`, { method: "POST" });
      area.value = text.slice(0, 30000);
      toast("Texte importé : relisez-le, puis cliquez sur « Enregistrer ».", { timeout: 6000 });
    } catch (error) {
      toast(error.message, { error: true });
    }
  });
}

export function initDialogs(options) {
  ctx = { ...ctx, ...options };
  hydrateIcons(document.getElementById("settings-dialog"));
  bindFindDialog();
  bindAddDialog();
  bindSettingsDialog();
}
