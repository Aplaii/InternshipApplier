// Volet de droite : détails d'une offre, suivi de la candidature, notes, emails envoyés.

import { api } from "./api.js";
import {
  el, icon, debounce, toast, confirmDialog, longDate, STATUSES, statusInfo, openExternal, SOURCE_SHORT,
} from "./util.js";

const $ = (id) => document.getElementById(id);

let ctx = { onChange: () => {}, onCompose: () => {}, updateOffers: async () => false };
let currentId = null;
let currentOffer = null;
let loadSeq = 0;
const detailRequests = new Map(); // id -> requête en cours (évite les doublons)

function requestDetails(id, refresh) {
  if (!refresh && detailRequests.has(id)) return detailRequests.get(id);
  const request = api(`/api/offers/${id}/details${refresh ? "?refresh=true" : ""}`, { method: "POST" })
    .finally(() => { if (detailRequests.get(id) === request) detailRequests.delete(id); });
  detailRequests.set(id, request);
  return request;
}

// --------------------------------------------------------------------------- notes

let pendingNotes = null; // { id, text }

async function saveNotes() {
  if (!pendingNotes) return;
  const { id, text } = pendingNotes;
  pendingNotes = null;
  try {
    await api(`/api/offers/${id}`, { method: "PATCH", json: { notes: text } });
    if (id === currentId) {
      const status = document.querySelector("#drawer .notes-status");
      if (status) status.textContent = "Enregistré";
      if (currentOffer) currentOffer.notes = text;
    }
  } catch (error) {
    toast(`Notes non enregistrées : ${error.message}`, { error: true });
  }
}

const saveNotesSoon = debounce(saveNotes, 700);

// --------------------------------------------------------------------------- rendu

function section(title, ...children) {
  return el("section", { class: "drawer-section" }, [el("h3", { text: title }), ...children]);
}

function statusPicker(offer) {
  const current = statusInfo(offer.status);
  return el("div", {}, [
    el("div", { class: "status-picker", role: "radiogroup", "aria-label": "Statut de la candidature" },
      STATUSES.map((status) => el("button", {
        type: "button",
        role: "radio",
        "aria-checked": String(status.id === offer.status),
        class: `status-option status-option-${status.id}${status.id === offer.status ? " active" : ""}`,
        onclick: () => setStatus(offer, status.id),
      }, [el("span", { class: `dot status-dot-${status.id}` }), el("span", { text: status.label })]))),
    offer.status_changed_at
      ? el("p", { class: "muted small-text", text: `« ${current.label} » depuis le ${longDate(offer.status_changed_at, true)}` })
      : null,
  ]);
}

async function setStatus(offer, status) {
  if (status === offer.status) return;
  await ctx.updateOffers([offer.id], { status }, { message: `Statut : « ${statusInfo(status).label} »` });
}

function notesBlock(offer) {
  const area = el("textarea", {
    class: "notes",
    rows: "4",
    maxlength: "20000",
    placeholder: "Contact, date d'entretien, relance prévue…",
    "aria-label": "Notes",
  });
  area.value = offer.notes || "";
  const status = el("span", { class: "muted small-text notes-status" });
  area.addEventListener("input", () => {
    pendingNotes = { id: offer.id, text: area.value };
    status.textContent = "";
    saveNotesSoon();
  });
  area.addEventListener("blur", () => saveNotesSoon.flush());
  return el("div", {}, [area, status]);
}

function emailsBlock(offer) {
  if (!offer.emails?.length) {
    return el("p", { class: "muted", text: "Aucun email envoyé depuis l'application pour cette offre." });
  }
  return el("ul", { class: "sent-list" }, offer.emails.map((mail) => el("li", {}, el("details", {}, [
    el("summary", {}, [
      el("strong", { text: longDate(mail.sent_at, true) }),
      el("span", { class: "muted", text: ` · à ${mail.to_addr}` }),
      el("div", { class: "sent-subject", text: mail.subject }),
    ]),
    mail.attachments?.length ? el("p", { class: "muted small-text", text: `Pièces jointes : ${mail.attachments.join(", ")}` }) : null,
    el("div", { class: "desc-text", text: mail.body }),
  ]))));
}

function descriptionBlock(offer) {
  const box = el("div", { class: "desc-block" });
  const render = () => {
    box.classList.remove("editing");
    const parts = [];
    if (offer.contact_emails?.length) {
      parts.push(el("p", { class: "small-text" }, [
        el("span", { class: "muted", text: "Adresse(s) trouvée(s) dans l'offre : " }),
        el("strong", { text: offer.contact_emails.join(", ") }),
      ]));
    }
    if (offer.description) {
      parts.push(el("div", { class: "desc-text", text: offer.description }));
    } else if (offer.details_fetched_at) {
      parts.push(el("p", { class: "muted", text: "Description vide." }));
    } else {
      parts.push(el("p", { class: "muted", text: "La description n'a pas encore été récupérée." }));
    }
    parts.push(el("div", { class: "row-buttons" }, [
      el("button", { type: "button", class: "btn small", text: offer.details_fetched_at ? "Actualiser depuis le site" : "Charger depuis le site", onclick: () => fetchDescription(true) }),
      el("button", { type: "button", class: "btn small", text: "Modifier à la main", onclick: editManually }),
    ]));
    box.replaceChildren(...parts);
  };
  const fetchDescription = async (refresh) => {
    box.replaceChildren(el("p", { class: "muted" }, [el("span", { class: "spinner small" }), " Lecture de l'offre en ligne…"]));
    try {
      const updated = await requestDetails(offer.id, refresh);
      Object.assign(offer, updated);
    } catch (error) {
      render();
      box.prepend(el("p", { class: "error-text", text: error.message }));
      return;
    }
    render();
  };
  const editManually = () => {
    const area = el("textarea", { rows: "10", class: "notes", maxlength: "100000", "aria-label": "Description de l'offre" });
    area.value = offer.description || "";
    box.classList.add("editing");
    box.replaceChildren(
      el("p", { class: "muted small-text", text: "Collez ici le texte de l'offre : l'IA s'en servira pour personnaliser l'email." }),
      area,
      el("div", { class: "row-buttons" }, [
        el("button", { type: "button", class: "btn small", text: "Annuler", onclick: render }),
        el("button", {
          type: "button",
          class: "btn small primary",
          text: "Enregistrer",
          onclick: async () => {
            try {
              const updated = await api(`/api/offers/${offer.id}`, { method: "PATCH", json: { description: area.value } });
              Object.assign(offer, updated);
              render();
              toast("Description enregistrée.");
            } catch (error) {
              toast(error.message, { error: true });
            }
          },
        }),
      ]),
    );
    area.focus();
  };
  render();
  if (!offer.details_fetched_at) fetchDescription(false);
  return box;
}

function render(offer) {
  currentOffer = offer;
  const badge = $("drawer-source");
  badge.className = `src src-${offer.source}`;
  badge.textContent = SOURCE_SHORT[offer.source] || offer.source_label;
  badge.title = `Source : ${offer.source_label}`;

  const dates = [];
  if (offer.published_at) dates.push(`Publiée le ${longDate(offer.published_at)}`);
  dates.push(`ajoutée le ${longDate(offer.first_seen_at)}`);

  $("drawer-body").replaceChildren(
    el("h2", { class: "drawer-title", text: offer.title }),
    el("div", { class: "drawer-company", text: offer.company || "Entreprise non précisée" }),
    el("div", { class: "muted", text: [offer.location, offer.contract].filter(Boolean).join(" · ") }),
    el("div", { class: "muted small-text", text: dates.join(", ") }),
    el("div", { class: "drawer-actions" }, [
      el("button", { type: "button", class: "btn primary", onclick: () => openExternal(offer.url) }, [icon("open_in_new"), "Voir l'offre"]),
      el("button", { type: "button", class: "btn tonal", onclick: () => ctx.onCompose(offer.id) }, [icon("edit"), "Rédiger"]),
      el("button", {
        type: "button",
        class: `icon-btn${offer.is_starred ? " star-on" : ""}`,
        title: offer.is_starred ? "Retirer des favoris" : "Ajouter aux favoris",
        "aria-label": offer.is_starred ? "Retirer des favoris" : "Ajouter aux favoris",
        onclick: () => ctx.updateOffers([offer.id], { is_starred: !offer.is_starred }),
      }, icon(offer.is_starred ? "star" : "star_border")),
      el("button", {
        type: "button",
        class: "icon-btn",
        title: offer.is_archived ? "Remettre dans la boîte de réception" : "Archiver",
        "aria-label": offer.is_archived ? "Désarchiver" : "Archiver",
        onclick: () => ctx.updateOffers([offer.id], { is_archived: !offer.is_archived }, {
          message: offer.is_archived ? "Offre remise dans la boîte de réception" : "Offre archivée",
        }),
      }, icon(offer.is_archived ? "unarchive" : "archive")),
    ]),
    section("Suivi de la candidature", statusPicker(offer)),
    section("Notes", notesBlock(offer)),
    section(`Emails envoyés${offer.emails?.length ? ` (${offer.emails.length})` : ""}`, emailsBlock(offer)),
    section("Description de l'offre", descriptionBlock(offer)),
    el("div", { class: "drawer-footer" }, el("button", {
      type: "button",
      class: "btn danger-text",
      text: "Supprimer cette offre",
      onclick: () => deleteOffer(offer),
    })),
  );
}

async function deleteOffer(offer) {
  const scraped = offer.source !== "manual";
  const ok = await confirmDialog({
    title: "Supprimer cette offre ?",
    text: [
      `« ${offer.title} » sera supprimée avec ses notes et l'historique de ses emails.`,
      scraped ? "Si elle est toujours en ligne, une prochaine recherche pourra la réajouter : pour simplement la masquer, archivez-la." : "",
    ].filter(Boolean),
    okLabel: "Supprimer",
    danger: true,
  });
  if (!ok) return;
  try {
    await api(`/api/offers/${offer.id}`, { method: "DELETE" });
  } catch (error) {
    toast(error.message, { error: true });
    return;
  }
  closeDrawer();
  toast("Offre supprimée.");
  ctx.onChange();
}

// --------------------------------------------------------------------------- ouverture

export async function openDrawer(id) {
  if (currentId !== id) saveNotesSoon.flush();
  currentId = id;
  const seq = ++loadSeq;
  $("drawer").hidden = false;
  document.body.classList.add("drawer-open");
  if (!currentOffer || currentOffer.id !== id) {
    $("drawer-body").replaceChildren(el("p", { class: "muted" }, [el("span", { class: "spinner small" }), " Chargement…"]));
  }
  try {
    const offer = await api(`/api/offers/${id}`);
    if (seq !== loadSeq || currentId !== id) return;
    render(offer);
    if (!offer.is_read) {
      await api(`/api/offers/${id}`, { method: "PATCH", json: { is_read: true } });
      ctx.onChange();
    }
  } catch (error) {
    if (seq === loadSeq) $("drawer-body").replaceChildren(el("p", { class: "error-text", text: error.message }));
  }
}

/** Recharge le volet s'il est ouvert (après une modification ailleurs). */
export async function refreshDrawer() {
  if (currentId === null || $("drawer").hidden) return;
  if (pendingNotes || $("drawer").querySelector(".editing")) return; // ne pas écraser une saisie en cours
  const focused = document.activeElement;
  if (focused && $("drawer").contains(focused) && focused.tagName === "TEXTAREA") return;
  const id = currentId;
  const seq = ++loadSeq;
  try {
    const offer = await api(`/api/offers/${id}`);
    if (seq !== loadSeq || currentId !== id) return;
    render(offer);
  } catch (error) {
    if (error.status === 404) closeDrawer();
  }
}

export function closeDrawer() {
  saveNotesSoon.flush();
  $("drawer").hidden = true;
  document.body.classList.remove("drawer-open");
  currentId = null;
  currentOffer = null;
}

export function initDrawer(options) {
  ctx = { ...ctx, ...options };
  $("drawer-close").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || $("drawer").hidden) return;
    if (document.querySelector("dialog[open]") || document.querySelector(".menu")) return;
    closeDrawer();
  });
}
