// Page principale : liste des offres façon Gmail, barre latérale, sélection, recherche.

import { api } from "./api.js";
import {
  el, icon, hydrateIcons, debounce, shortDate, longDate, toast, showMenu, STATUSES, statusInfo,
  sourceBadge, openExternal, SOURCE_SHORT,
} from "./util.js";
import { initCompose, openCompose } from "./compose.js";
import { initDrawer, openDrawer, refreshDrawer } from "./drawer.js";
import {
  initDialogs, openFindDialog, openSettings, openAddDialog, getSettings, loadSettings, refreshAutomation,
} from "./dialogs.js";
import { renderStats } from "./stats.js";

const $ = (id) => document.getElementById(id);
const FOLDERS = ["inbox", "starred", "all", "archived", "unread"];

const state = {
  view: { folder: "inbox", status: null, source: null, key: "inbox" },
  q: "",
  page: 1,
  pageSize: 50,
  items: [],
  total: 0,
  counts: null,
  meta: null,
  selected: new Set(),
  requestSeq: 0,
  fetching: false,
  loadedOnce: false,
};

// --------------------------------------------------------------------------- navigation

function parseHash() {
  const hash = decodeURIComponent(window.location.hash.replace(/^#/, "")) || "inbox";
  const [kind, ...rest] = hash.split("/");
  const value = rest.join("/");
  if (kind === "stats") return { folder: "all", status: null, source: null, key: "stats", stats: true };
  if (kind === "status" && STATUSES.some((s) => s.id === value)) {
    return { folder: "all", status: value, source: null, key: `status/${value}` };
  }
  if (kind === "source" && value) return { folder: "inbox", status: null, source: value, key: `source/${value}` };
  if (FOLDERS.includes(kind)) return { folder: kind, status: null, source: null, key: kind };
  return { folder: "inbox", status: null, source: null, key: "inbox" };
}

function viewTitle() {
  const { folder, status, source } = state.view;
  if (state.view.stats) return "Tableau de bord";
  if (status) return statusInfo(status).label;
  if (source) return sourceLabel(source);
  return { inbox: "Boîte de réception", starred: "Favoris", all: "Toutes les offres", archived: "Archivées", unread: "Non lues" }[folder];
}

function sourceLabel(source) {
  if (source === "manual") return "Ajoutées par lien";
  return state.meta?.sources?.[source] || SOURCE_SHORT[source] || source;
}

function markActiveNav() {
  for (const link of document.querySelectorAll(".sidebar a[data-view]")) {
    const active = link.dataset.view === state.view.key;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  document.title = `${viewTitle()} · Stages`;
}

function renderNav() {
  const counts = state.counts;
  for (const node of document.querySelectorAll("[data-count]")) {
    const value = counts ? counts[node.dataset.count] : 0;
    node.textContent = value ? String(value) : "";
  }
  $("nav-status").replaceChildren(
    ...STATUSES.map((status) =>
      el("li", {}, el("a", { href: `#status/${status.id}`, dataset: { view: `status/${status.id}` } }, [
        el("span", { class: `dot status-dot-${status.id}` }),
        el("span", { class: "nav-label", text: status.label }),
        el("span", { class: "nav-count", text: counts?.by_status?.[status.id] || "" }),
      ])),
    ),
  );
  const known = state.meta?.search_sources || ["hellowork", "linkedin", "wttj"];
  const extra = Object.keys(counts?.by_source || {}).filter((s) => !known.includes(s));
  $("nav-source").replaceChildren(
    ...[...known, ...extra].map((source) =>
      el("li", {}, el("a", { href: `#source/${source}`, dataset: { view: `source/${source}` } }, [
        el("span", { class: `src-dot src-${source}` }),
        el("span", { class: "nav-label", text: sourceLabel(source) }),
        el("span", { class: "nav-count", text: counts?.by_source?.[source]?.unread || "" }),
      ])),
    ),
  );
  markActiveNav();
}

// --------------------------------------------------------------------------- chargement

/** Affiche soit la liste des offres, soit le tableau de bord. */
function showView() {
  const stats = Boolean(state.view.stats);
  $("stats-view").hidden = !stats;
  for (const id of ["list-toolbar", "offer-list"]) $(id).hidden = stats;
  if (stats) {
    $("empty-state").hidden = true;
    renderStats(sourceLabel);
  }
  return !stats;
}

async function loadOffers() {
  if (!showView()) return;
  const seq = ++state.requestSeq;
  const params = new URLSearchParams({ folder: state.view.folder, page: state.page, page_size: state.pageSize });
  if (state.view.status) params.set("status", state.view.status);
  if (state.view.source) params.set("source", state.view.source);
  if (state.q) params.set("q", state.q);
  try {
    const data = await api(`/api/offers?${params}`);
    if (seq !== state.requestSeq) return;
    if (!data.items.length && data.total > 0 && state.page > 1) {
      state.page = Math.max(1, Math.ceil(data.total / state.pageSize));
      await loadOffers();
      return;
    }
    state.items = data.items;
    state.total = data.total;
    state.loadedOnce = true;
    const visible = new Set(data.items.map((o) => o.id));
    for (const id of [...state.selected]) if (!visible.has(id)) state.selected.delete(id);
    renderList();
  } catch (error) {
    if (seq !== state.requestSeq) return;
    state.items = [];
    $("offer-list").replaceChildren();
    showEmpty([el("p", { class: "error-text", text: error.message })]);
  }
}

async function loadCounts() {
  try {
    state.counts = await api("/api/counts");
  } catch {
    state.counts = null;
  }
  renderNav();
  // Le message « liste vide » dépend des compteurs, qui peuvent arriver après la liste.
  if (state.loadedOnce && !state.items.length) renderList();
}

export async function refreshAll() {
  await Promise.all([loadOffers(), loadCounts()]);
  refreshDrawer();
}

// --------------------------------------------------------------------------- liste

function dateTooltip(offer) {
  const parts = [];
  if (offer.published_at) parts.push(`Publiée le ${longDate(offer.published_at)}`);
  parts.push(`Ajoutée le ${longDate(offer.first_seen_at, true)}`);
  return parts.join("\n");
}

function actionButton(action, iconName, label) {
  return el("button", { type: "button", class: "icon-btn row-action", dataset: { action }, title: label, "aria-label": label }, icon(iconName));
}

function renderRow(offer) {
  const status = statusInfo(offer.status);
  const selected = state.selected.has(offer.id);
  const row = el("li", {
    class: `row${offer.is_read ? "" : " unread"}${selected ? " selected" : ""}`,
    dataset: { id: offer.id, status: offer.status },
    tabindex: "0",
    "aria-label": `${offer.title}, ${offer.company || "entreprise non précisée"}, ${offer.source_label}`,
  });
  const check = el("input", { type: "checkbox", class: "row-check", dataset: { action: "select" }, "aria-label": "Sélectionner" });
  check.checked = selected;

  const chips = [];
  if (offer.status !== "new") {
    chips.push(el("span", {
      class: `chip status-chip status-chip-${offer.status}`,
      dataset: { action: "status" },
      title: `${status.label}${offer.status_changed_at ? ` depuis le ${longDate(offer.status_changed_at)}` : ""} (cliquer pour changer)`,
      text: status.short,
    }));
  }
  if (offer.has_draft) chips.push(el("span", { class: "chip draft-chip", text: "Brouillon", title: "Un brouillon d'email est enregistré" }));
  for (const topic of (offer.ai_topics || []).slice(0, 2)) {
    chips.push(el("span", { class: "chip topic-chip", text: topic, title: "Thème IA détecté dans l'offre" }));
  }
  if (offer.emails_count > 1) chips.push(el("span", { class: "chip count-chip", text: `${offer.emails_count} emails`, title: "Emails envoyés pour cette offre" }));

  const snippet = [offer.location, offer.contract].filter(Boolean).join(" · ");
  row.append(
    el("span", { class: "row-check-wrap" }, check),
    el("button", {
      type: "button",
      class: `row-star${offer.is_starred ? " on" : ""}`,
      dataset: { action: "star" },
      title: offer.is_starred ? "Retirer des favoris" : "Ajouter aux favoris",
      "aria-label": offer.is_starred ? "Retirer des favoris" : "Ajouter aux favoris",
      "aria-pressed": String(offer.is_starred),
    }, icon(offer.is_starred ? "star" : "star_border")),
    sourceBadge(offer),
    el("span", { class: "row-company", text: offer.company || "Entreprise non précisée", title: offer.company || "" }),
    el("span", { class: "row-main" }, [
      ...chips,
      el("span", { class: "row-title", text: offer.title }),
      snippet ? el("span", { class: "row-snippet", text: ` — ${snippet}` }) : null,
    ]),
    el("span", { class: "row-date", text: shortDate(offer.published_at || offer.first_seen_at), title: dateTooltip(offer) }),
    el("span", { class: "row-actions" }, [
      actionButton("compose", "edit", "Rédiger la candidature"),
      actionButton("details", "info", "Détails et suivi"),
      actionButton("status", "label", "Changer le statut"),
      offer.is_archived
        ? actionButton("unarchive", "unarchive", "Remettre dans la boîte de réception")
        : actionButton("archive", "archive", "Archiver"),
      offer.is_read
        ? actionButton("unread", "mark_email_unread", "Marquer comme non lu")
        : actionButton("read", "drafts", "Marquer comme lu"),
    ]),
  );
  return row;
}

function renderList() {
  $("offer-list").replaceChildren(...state.items.map(renderRow));
  updateToolbar();
  if (state.items.length) {
    $("empty-state").hidden = true;
    return;
  }
  const hidden = state.counts?.hidden_non_ai || 0;
  const hiddenNote = hidden
    ? el("p", { class: "muted", text: `${hidden} offre${hidden > 1 ? "s" : ""} hors IA ${hidden > 1 ? "sont masquées" : "est masquée"} (filtre « stages en IA uniquement » dans Paramètres > Automatisation).` })
    : null;
  if (state.q) {
    showEmpty([el("p", { text: `Aucune offre ne correspond à « ${state.q} » dans ce dossier.` }), hiddenNote]);
  } else if (state.counts && state.counts.total === 0) {
    showEmpty([
      el("p", { class: "empty-title", text: "Aucune offre pour l'instant" }),
      el("p", { class: "muted", text: "Lancez une recherche de stages en intelligence artificielle sur HelloWork, LinkedIn et Welcome to the Jungle, ou activez la recherche automatique. Pensez aussi à remplir votre profil dans les paramètres pour que l'IA rédige vos emails." }),
      hiddenNote,
      el("div", { class: "empty-actions" }, [
        el("button", { type: "button", class: "btn primary", text: "Chercher des offres", onclick: () => openFindDialog() }),
        el("button", { type: "button", class: "btn", text: "Paramètres", onclick: () => openSettings() }),
      ]),
    ]);
  } else {
    showEmpty([el("p", { class: "muted", text: "Rien ici pour le moment." }), hiddenNote]);
  }
}

function showEmpty(children) {
  const box = $("empty-state");
  box.replaceChildren(...children.filter(Boolean));
  box.hidden = false;
  updateToolbar();
}

function updateToolbar() {
  const visibleIds = state.items.map((o) => o.id);
  const selectedCount = visibleIds.filter((id) => state.selected.has(id)).length;
  const selectAll = $("select-all");
  selectAll.checked = selectedCount > 0 && selectedCount === visibleIds.length;
  selectAll.indeterminate = selectedCount > 0 && selectedCount < visibleIds.length;
  selectAll.disabled = visibleIds.length === 0;
  $("bulk-actions").hidden = selectedCount === 0;
  $("bulk-count").textContent = selectedCount ? `${selectedCount} sélectionnée${selectedCount > 1 ? "s" : ""}` : "";
  $("refresh-btn").hidden = selectedCount > 0;
  const first = state.total ? (state.page - 1) * state.pageSize + 1 : 0;
  const last = Math.min(state.page * state.pageSize, state.total);
  $("page-info").textContent = state.total ? `${first}–${last} sur ${state.total}` : "";
  $("prev-page").disabled = state.page <= 1;
  $("next-page").disabled = last >= state.total;
}

// --------------------------------------------------------------------------- actions

/** Modifie des offres ; `undoLabel` affiche une notification avec « Annuler ». */
export async function updateOffers(ids, changes, { message = null, undo = true } = {}) {
  const keys = Object.keys(changes);
  const previous = ids
    .map((id) => state.items.find((o) => o.id === id))
    .filter(Boolean)
    .map((offer) => ({ id: offer.id, values: Object.fromEntries(keys.map((k) => [k, offer[k]])) }));
  try {
    if (ids.length === 1) await api(`/api/offers/${ids[0]}`, { method: "PATCH", json: changes });
    else await api("/api/offers/bulk", { method: "POST", json: { ids, ...changes } });
  } catch (error) {
    toast(error.message, { error: true });
    return false;
  }
  await refreshAll();
  if (message) {
    toast(message, {
      action: undo && previous.length
        ? { label: "Annuler", onClick: () => restore(previous) }
        : null,
    });
  }
  return true;
}

async function restore(previous) {
  const groups = new Map();
  for (const { id, values } of previous) {
    const key = JSON.stringify(values);
    if (!groups.has(key)) groups.set(key, { values, ids: [] });
    groups.get(key).ids.push(id);
  }
  try {
    for (const { values, ids } of groups.values()) {
      await api("/api/offers/bulk", { method: "POST", json: { ids, ...values } });
    }
  } catch (error) {
    toast(error.message, { error: true });
  }
  await refreshAll();
}

function statusMenu(anchor, ids) {
  const items = ids.map((id) => state.items.find((o) => o.id === id)).filter(Boolean);
  const current = items.length === 1 ? items[0].status : null;
  showMenu(anchor, STATUSES.map((status) => ({
    label: status.label,
    color: status.id,
    checked: status.id === current,
    onClick: () => {
      const plural = ids.length > 1 ? `${ids.length} offres` : "Offre";
      updateOffers(ids, { status: status.id }, { message: `${plural} : « ${status.label} »` });
    },
  })));
}

async function openOffer(offer) {
  if (!openExternal(offer.url)) {
    toast("Lien de l'offre invalide.", { error: true });
    return;
  }
  if (!offer.is_read) {
    offer.is_read = true;
    document.querySelector(`.row[data-id="${offer.id}"]`)?.classList.remove("unread");
    try {
      await api(`/api/offers/${offer.id}`, { method: "PATCH", json: { is_read: true } });
    } catch { /* sans gravité */ }
    loadCounts();
  }
}

function handleRowAction(action, offer, anchor) {
  switch (action) {
    case "star":
      updateOffers([offer.id], { is_starred: !offer.is_starred });
      break;
    case "compose":
      openCompose(offer.id);
      break;
    case "details":
      openDrawer(offer.id);
      break;
    case "status":
      statusMenu(anchor, [offer.id]);
      break;
    case "archive":
      updateOffers([offer.id], { is_archived: true }, { message: "Offre archivée" });
      break;
    case "unarchive":
      updateOffers([offer.id], { is_archived: false }, { message: "Offre remise dans la boîte de réception" });
      break;
    case "read":
      updateOffers([offer.id], { is_read: true });
      break;
    case "unread":
      updateOffers([offer.id], { is_read: false });
      break;
    default:
      break;
  }
}

function bindList() {
  const list = $("offer-list");
  list.addEventListener("click", (event) => {
    const row = event.target.closest(".row");
    if (!row) return;
    const offer = state.items.find((o) => o.id === Number(row.dataset.id));
    if (!offer) return;
    const actionNode = event.target.closest("[data-action]");
    if (actionNode && row.contains(actionNode)) {
      event.stopPropagation();
      if (actionNode.dataset.action === "select") {
        if (actionNode.checked) state.selected.add(offer.id);
        else state.selected.delete(offer.id);
        row.classList.toggle("selected", actionNode.checked);
        updateToolbar();
        return;
      }
      handleRowAction(actionNode.dataset.action, offer, actionNode);
      return;
    }
    if (event.target.closest(".row-check-wrap")) return;
    openOffer(offer);
  });
  // Clic molette : ouvre aussi l'offre (comme un lien).
  list.addEventListener("auxclick", (event) => {
    if (event.button !== 1 || event.target.closest("[data-action]")) return;
    const row = event.target.closest(".row");
    const offer = row && state.items.find((o) => o.id === Number(row.dataset.id));
    if (offer) {
      event.preventDefault();
      openOffer(offer);
    }
  });
  list.addEventListener("mousedown", (event) => {
    if (event.button === 1 && event.target.closest(".row")) event.preventDefault(); // pas de défilement auto
  });
  list.addEventListener("keydown", (event) => {
    const row = event.target.classList?.contains("row") ? event.target : null;
    if (!row) return;
    const offer = state.items.find((o) => o.id === Number(row.dataset.id));
    if (!offer) return;
    if (event.key === "Enter") {
      event.preventDefault();
      openOffer(offer);
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const next = event.key === "ArrowDown" ? row.nextElementSibling : row.previousElementSibling;
      next?.focus();
    } else if (event.key === "x") {
      row.querySelector(".row-check").click(); // coche/décoche, comme dans Gmail
    }
  });
}

function bindToolbar() {
  $("select-all").addEventListener("change", (event) => {
    for (const offer of state.items) {
      if (event.target.checked) state.selected.add(offer.id);
      else state.selected.delete(offer.id);
    }
    renderList();
  });
  $("refresh-btn").addEventListener("click", () => {
    const settings = getSettings();
    if (!settings) return;
    runSearch({
      keywords: settings.search_keywords,
      location: settings.search_location,
      sources: settings.search_sources,
      recency: settings.search_recency,
      max_per_source: settings.search_max_per_source,
      ai_only: settings.ai_only,
    });
  });
  $("prev-page").addEventListener("click", () => {
    if (state.page > 1) {
      state.page -= 1;
      loadOffers();
    }
  });
  $("next-page").addEventListener("click", () => {
    if (state.page * state.pageSize < state.total) {
      state.page += 1;
      loadOffers();
    }
  });
  for (const button of document.querySelectorAll("[data-bulk]")) {
    button.addEventListener("click", () => {
      const ids = state.items.map((o) => o.id).filter((id) => state.selected.has(id));
      if (!ids.length) return;
      const n = ids.length;
      const label = n > 1 ? `${n} offres` : "1 offre";
      switch (button.dataset.bulk) {
        case "archive":
          state.selected.clear();
          updateOffers(ids, { is_archived: true }, { message: `${label} archivée${n > 1 ? "s" : ""}` });
          break;
        case "unarchive":
          state.selected.clear();
          updateOffers(ids, { is_archived: false }, { message: `${label} remise${n > 1 ? "s" : ""} dans la boîte de réception` });
          break;
        case "read":
          updateOffers(ids, { is_read: true });
          break;
        case "unread":
          updateOffers(ids, { is_read: false });
          break;
        case "status":
          statusMenu(button, ids);
          break;
        default:
          break;
      }
    });
  }
}

function bindSearch() {
  const input = $("search-input");
  const apply = () => {
    const q = input.value.trim();
    if (q === state.q) return;
    state.q = q;
    state.page = 1;
    state.selected.clear();
    loadOffers();
  };
  const applySoon = debounce(apply, 300);
  input.addEventListener("input", applySoon);
  $("search-form").addEventListener("submit", (event) => {
    event.preventDefault();
    applySoon.cancel();
    apply();
  });
  document.addEventListener("keydown", (event) => {
    const typing = event.target.closest?.("input, textarea, select, [contenteditable='true'], dialog");
    if (event.key === "/" && !typing) {
      event.preventDefault();
      input.focus();
      input.select();
    }
  });
}

/** Lien « #search/terme » (tableau de bord) : recherche le terme dans toutes les offres. */
function applySearchHash() {
  const hash = decodeURIComponent(window.location.hash.replace(/^#/, ""));
  if (!hash.startsWith("search/")) return false;
  const q = hash.slice("search/".length).trim();
  $("search-input").value = q;
  state.q = q;
  window.location.hash = "all"; // déclenche un nouveau hashchange qui charge la liste
  return true;
}

function bindSidebar() {
  const mobile = () => window.matchMedia("(max-width: 900px)").matches;
  const closeMobile = () => {
    document.body.classList.remove("sidebar-open");
    $("sidebar-backdrop").hidden = true;
  };
  $("menu-btn").addEventListener("click", () => {
    if (mobile()) {
      const open = !document.body.classList.contains("sidebar-open");
      document.body.classList.toggle("sidebar-open", open);
      $("sidebar-backdrop").hidden = !open;
    } else {
      document.body.classList.toggle("sidebar-collapsed");
    }
  });
  $("sidebar-backdrop").addEventListener("click", closeMobile);
  $("sidebar").addEventListener("click", (event) => {
    if (event.target.closest("a, button") && mobile()) closeMobile();
  });
  $("find-btn").addEventListener("click", () => openFindDialog());
  $("add-offer-btn").addEventListener("click", () => openAddDialog());
  $("settings-btn").addEventListener("click", () => openSettings());
  window.addEventListener("hashchange", () => {
    if (applySearchHash()) return;
    state.view = parseHash();
    state.page = 1;
    state.selected.clear();
    markActiveNav();
    loadOffers();
  });
}

// --------------------------------------------------------------------------- recherche en ligne

function showBanner(kind, title, lines = []) {
  const banner = $("fetch-banner");
  banner.className = `banner banner-${kind}`;
  const children = [
    kind === "loading" ? el("span", { class: "spinner", "aria-hidden": "true" }) : null,
    el("div", { class: "banner-text" }, [
      el("strong", { text: title }),
      ...lines.map((line) => el("div", { class: line.error ? "banner-line error-text" : "banner-line", text: line.text })),
    ]),
    kind === "loading" ? null : el("button", {
      type: "button", class: "icon-btn small", "aria-label": "Fermer", title: "Fermer",
      onclick: () => { banner.hidden = true; },
    }, icon("close")),
  ];
  banner.replaceChildren(...children.filter(Boolean)); // replaceChildren(null) écrirait « null »
  banner.hidden = false;
}

export async function runSearch(params) {
  if (state.fetching) {
    toast("Une recherche est déjà en cours.");
    return;
  }
  state.fetching = true;
  $("refresh-btn").classList.add("spinning");
  $("refresh-btn").disabled = true;
  const names = params.sources.map((s) => SOURCE_SHORT[s] || s).join(", ");
  const what = [params.keywords ? `« ${params.keywords} »` : "stages en IA", params.location].filter(Boolean).join(" · ");
  showBanner("loading", `Recherche en cours sur ${names}…`, what ? [{ text: what }] : []);
  try {
    const result = await api("/api/fetch", { method: "POST", json: params });
    await loadSettings();
    const kept = (r) => (result.ai_only ? `, dont ${r.kept} en IA` : "");
    const lines = result.results.map((r) => (r.error
      ? { text: `${r.label} : ${r.error}${r.found ? ` (${r.found} offres récupérées avant l'erreur${kept(r)}, ${r.new} nouvelles)` : ""}`, error: true }
      : { text: `${r.label} : ${r.found} offre${r.found > 1 ? "s" : ""} trouvée${r.found > 1 ? "s" : ""}${kept(r)}, ${r.new} nouvelle${r.new > 1 ? "s" : ""}` }));
    if (result.filtered_total) {
      lines.push({ text: `${result.filtered_total} offre${result.filtered_total > 1 ? "s" : ""} hors IA écartée${result.filtered_total > 1 ? "s" : ""}.` });
    }
    if (result.details) lines.push({ text: `Description lue pour ${result.details} nouvelle${result.details > 1 ? "s" : ""} offre${result.details > 1 ? "s" : ""}.` });
    const failed = result.results.some((r) => r.error);
    const n = result.new_total;
    showBanner(failed ? "warn" : "ok", n ? `${n} nouvelle${n > 1 ? "s" : ""} offre${n > 1 ? "s" : ""}` : "Aucune nouvelle offre", lines);
    if (!failed) {
      setTimeout(() => {
        if (!state.fetching && $("fetch-banner").classList.contains("banner-ok")) $("fetch-banner").hidden = true;
      }, 15000);
    }
    if (n && state.view.key !== "inbox" && !state.view.source) toast("Les nouvelles offres sont dans la boîte de réception.");
    await refreshAll();
  } catch (error) {
    showBanner("error", "La recherche a échoué", [{ text: error.message, error: true }]);
  } finally {
    state.fetching = false;
    $("refresh-btn").classList.remove("spinning");
    $("refresh-btn").disabled = false;
  }
}

// --------------------------------------------------------------------------- recherche automatique

let lastAutoRun = null;

/** Vérifie chaque minute si la recherche automatique a ajouté des offres. */
async function pollAutomation() {
  let status;
  try {
    status = await refreshAutomation();
  } catch {
    return;
  }
  const run = status.last_run;
  if (lastAutoRun !== null && run && run !== lastAutoRun) {
    const summary = status.last_summary || {};
    const n = summary.new_total || 0;
    const drafts = summary.drafted ? ` · ${summary.drafted} brouillon${summary.drafted > 1 ? "s" : ""} préparé${summary.drafted > 1 ? "s" : ""}` : "";
    if (n || summary.errors?.length) {
      toast(n
        ? `Recherche automatique : ${n} nouvelle${n > 1 ? "s" : ""} offre${n > 1 ? "s" : ""} en IA${drafts}`
        : `Recherche automatique : ${summary.errors.join(" ; ")}`, { error: !n, timeout: 10000 });
    }
    await refreshAll();
  }
  lastAutoRun = run || "";
}

// --------------------------------------------------------------------------- démarrage

async function init() {
  hydrateIcons();
  initCompose({ onChange: refreshAll });
  initDrawer({ onChange: refreshAll, onCompose: openCompose, updateOffers });
  initDialogs({ onSearch: runSearch, onChange: refreshAll, onOpenOffer: openDrawer });
  bindList();
  bindToolbar();
  bindSearch();
  bindSidebar();
  applySearchHash();
  state.view = parseHash();
  try {
    const [meta] = await Promise.all([api("/api/meta"), loadSettings()]);
    state.meta = meta;
  } catch (error) {
    showEmpty([el("p", { class: "error-text", text: error.message })]);
    return;
  }
  await refreshAll();
  pollAutomation();
  setInterval(pollAutomation, 60000);
}

init();
