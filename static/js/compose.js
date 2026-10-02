// Fenêtre de rédaction façon Gmail : brouillon, génération par l'IA (llama.cpp), envoi.

import { api, ndjson, ApiError } from "./api.js";
import { el, icon, debounce, toast, confirmDialog, copyText, safeUrl, formatSize } from "./util.js";
import { getSettings } from "./dialogs.js";

const $ = (id) => document.getElementById(id);
const ATTACHMENTS_KEY = "internship-applier.attachments";

let ctx = { onChange: () => {} };
let current = null; // { offer, documents, lastSaved, hadDraft }
let generation = null; // { controller, prevSubject, prevBody, gotResult, done }

const fields = () => ({ to: $("compose-to"), subject: $("compose-subject"), body: $("compose-text") });

// --------------------------------------------------------------------------- brouillon

function draftPayload() {
  const { to, subject, body } = fields();
  return { draft_to: to.value, draft_subject: subject.value, draft_body: body.value };
}

function setSavedText(text) {
  $("compose-saved").textContent = text;
}

async function saveDraft({ keepalive = false } = {}) {
  if (!current) return;
  const payload = draftPayload();
  const key = JSON.stringify(payload);
  if (key === current.lastSaved) return;
  const offerId = current.offer.id;
  if (keepalive) {
    // Fermeture de l'onglet : la requête doit survivre à la page.
    fetch(`/api/offers/${offerId}`, {
      method: "PATCH",
      keepalive: true,
      headers: { "Content-Type": "application/json", "X-Requested-With": "InternshipApplier" },
      body: key,
    }).catch(() => {});
    current.lastSaved = key;
    return;
  }
  try {
    await api(`/api/offers/${offerId}`, { method: "PATCH", json: payload });
    if (current?.offer.id !== offerId) return;
    current.lastSaved = key;
    setSavedText("Brouillon enregistré");
    const hasDraft = Boolean(payload.draft_body);
    if (hasDraft !== current.hadDraft) {
      current.hadDraft = hasDraft;
      ctx.onChange();
    }
  } catch (error) {
    setSavedText(`Brouillon non enregistré : ${error.message}`);
  }
}

const saveDraftSoon = debounce(() => saveDraft(), 800);

// --------------------------------------------------------------------------- pièces jointes

function storedAttachmentChoice() {
  try {
    const value = JSON.parse(localStorage.getItem(ATTACHMENTS_KEY) || "null");
    return Array.isArray(value) ? value : null;
  } catch {
    return null;
  }
}

function renderAttachments() {
  const box = $("compose-attachments");
  const documents = current?.documents || [];
  if (!documents.length) {
    box.replaceChildren(el("span", { class: "muted small-text", text: "Aucun document joint. Ajoutez votre CV dans Paramètres > Documents." }));
    return;
  }
  const stored = storedAttachmentChoice();
  box.replaceChildren(
    ...documents.map((doc) => {
      const input = el("input", { type: "checkbox", value: doc.name });
      input.checked = stored ? stored.includes(doc.name) : true;
      input.addEventListener("change", () => {
        localStorage.setItem(ATTACHMENTS_KEY, JSON.stringify(selectedAttachments()));
      });
      return el("label", { class: "attachment", title: `${doc.name} (${formatSize(doc.size)})` }, [
        input,
        el("span", { class: "attachment-name", text: doc.name }),
        el("span", { class: "muted small-text", text: formatSize(doc.size) }),
      ]);
    }),
  );
}

function selectedAttachments() {
  return [...$("compose-attachments").querySelectorAll("input[type=checkbox]:checked")].map((input) => input.value);
}

// --------------------------------------------------------------------------- offre & suggestions

function renderSuggestions() {
  const box = $("compose-suggestions");
  const offer = current?.offer;
  if (!offer) {
    box.hidden = true;
    return;
  }
  const emails = offer.contact_emails || [];
  if (emails.length) {
    box.replaceChildren(
      el("span", { class: "muted small-text", text: "Trouvé dans l'offre :" }),
      ...emails.map((email) => el("button", {
        type: "button",
        class: "chip chip-button",
        text: email,
        title: "Utiliser cette adresse",
        onclick: () => {
          $("compose-to").value = email;
          saveDraftSoon();
        },
      })),
    );
  } else if (offer.details_fetched_at) {
    box.replaceChildren(el("span", {
      class: "muted small-text",
      text: "Aucune adresse email dans l'offre : postulez sur le site (le bouton Copier aide à coller votre texte) ou trouvez l'email du recruteur.",
    }));
  } else {
    box.replaceChildren(el("span", { class: "muted small-text", text: "Lecture de l'offre…" }));
  }
  box.hidden = false;
}

function renderDescription() {
  const box = $("compose-desc");
  const offer = current?.offer;
  if (!offer) return;
  if (offer.description) box.replaceChildren(el("div", { class: "desc-text", text: offer.description }));
  else if (offer.details_fetched_at) box.replaceChildren(el("p", { class: "muted", text: "Description vide." }));
  else box.replaceChildren(el("p", { class: "muted", text: "Chargement de la description…" }));
}

async function loadDetails(offerId) {
  try {
    const offer = await api(`/api/offers/${offerId}/details`, { method: "POST" });
    if (current?.offer.id !== offerId) return;
    Object.assign(current.offer, {
      description: offer.description,
      contact_emails: offer.contact_emails,
      details_fetched_at: offer.details_fetched_at,
    });
    if (!$("compose-to").value && offer.contact_emails?.length === 1) {
      $("compose-to").value = offer.contact_emails[0];
      saveDraftSoon();
    }
  } catch (error) {
    if (current?.offer.id !== offerId) return;
    current.offer.details_fetched_at = current.offer.details_fetched_at || "error";
    $("compose-desc").replaceChildren(el("p", { class: "error-text", text: `Description indisponible : ${error.message}` }));
    $("compose-suggestions").replaceChildren(el("span", { class: "muted small-text", text: "Description indisponible : l'IA s'appuiera sur le titre et l'entreprise." }));
    $("compose-suggestions").hidden = false;
    return;
  }
  renderSuggestions();
  renderDescription();
}

// --------------------------------------------------------------------------- ouverture / fermeture

export async function openCompose(offerId) {
  if (generation) {
    toast("Une rédaction est en cours : arrêtez-la avant d'ouvrir une autre offre.", { error: true });
    showWindow();
    return;
  }
  if (current && current.offer.id === offerId) {
    showWindow();
    return;
  }
  if (current) {
    saveDraftSoon.cancel();
    await saveDraft();
  }
  let offer;
  let documents = [];
  try {
    [offer, documents] = await Promise.all([api(`/api/offers/${offerId}`), api("/api/documents")]);
  } catch (error) {
    toast(error.message, { error: true });
    return;
  }
  current = { offer, documents, lastSaved: null, hadDraft: Boolean(offer.draft_body) };
  const { to, subject, body } = fields();
  to.value = offer.draft_to || (offer.contact_emails?.length === 1 ? offer.contact_emails[0] : "");
  subject.value = offer.draft_subject || "";
  body.value = offer.draft_body || "";
  current.lastSaved = JSON.stringify(draftPayload());
  $("ai-instructions").value = "";
  $("ai-language").value = getSettings()?.llm_language || "fr";
  $("compose-title").textContent = `Candidature · ${offer.company || offer.title}`;
  const link = $("compose-offer-link");
  link.textContent = offer.title;
  const url = safeUrl(offer.url);
  if (url) link.href = url;
  else link.removeAttribute("href");
  $("compose-offer-meta").textContent = [offer.company, offer.location, offer.source_label].filter(Boolean).join(" · ");
  $("compose-desc").hidden = true;
  $("compose-desc-toggle").textContent = "Voir l'offre";
  setStatus("");
  showError("");
  setSavedText(offer.draft_body ? "Brouillon repris" : "");
  renderAttachments();
  renderSuggestions();
  renderDescription();
  showWindow();
  (body.value ? body : $("ai-generate")).focus();
  if (!offer.is_read) {
    api(`/api/offers/${offerId}`, { method: "PATCH", json: { is_read: true } }).then(() => ctx.onChange()).catch(() => {});
  }
  if (!offer.details_fetched_at) loadDetails(offerId);
}

function showWindow() {
  const box = $("compose");
  box.hidden = false;
  box.classList.remove("minimized");
}

async function closeCompose() {
  if (generation) {
    generation.controller.abort();
    await generation.done; // laisse generate() remettre le texte en ordre avant d'enregistrer
  }
  saveDraftSoon.cancel();
  await saveDraft();
  hideWindow();
}

function setExpandIcon(expanded) {
  const button = $("compose-expand");
  button.replaceChildren(icon(expanded ? "close_fullscreen" : "open_in_full"));
  button.title = expanded ? "Taille normale" : "Agrandir";
  button.setAttribute("aria-label", button.title);
}

function hideWindow() {
  $("compose").hidden = true;
  $("compose").classList.remove("expanded", "minimized");
  setExpandIcon(false);
  current = null;
}

// --------------------------------------------------------------------------- IA

function setStatus(message, kind = "info") {
  const box = $("ai-status");
  box.textContent = message;
  box.className = `ai-status ai-status-${kind}`;
  box.hidden = !message;
}

function showError(message) {
  const box = $("compose-error");
  box.textContent = message;
  box.hidden = !message;
}

function setGenerating(on) {
  $("ai-generate").hidden = on;
  $("ai-stop").hidden = !on;
  $("compose-text").readOnly = on;
  $("compose-subject").readOnly = on;
  $("compose-send").disabled = on;
  $("compose-discard").disabled = on;
  $("compose").classList.toggle("generating", on);
}

function wordCount(text) {
  const words = text.trim().split(/\s+/).filter(Boolean);
  return words.length;
}

/** Version simplifiée du découpage serveur, pour un texte interrompu. */
function splitSubject(raw) {
  const lines = raw.replace(/\r\n/g, "\n").split("\n");
  for (let i = 0; i < Math.min(lines.length, 6); i += 1) {
    const match = lines[i].match(/^\s*[#>*_\s]*(objet|subject|sujet)\s*[*_]*\s*[:：]\s*(.*)$/i);
    if (match) return { subject: match[2].replace(/[*_]+$/, "").trim(), body: lines.slice(i + 1).join("\n").trim() };
  }
  return { subject: "", body: raw.trim() };
}

async function generate() {
  if (!current || generation) return;
  const { subject, body } = fields();
  if (body.value.trim()) {
    const ok = await confirmDialog({
      title: "Remplacer le message ?",
      text: "Le texte actuel sera remplacé par une nouvelle proposition de l'IA.",
      okLabel: "Remplacer",
    });
    if (!ok || !current) return;
  }
  const offerId = current.offer.id;
  const controller = new AbortController();
  let finish;
  generation = {
    controller,
    prevSubject: subject.value,
    prevBody: body.value,
    gotResult: false,
    done: new Promise((resolve) => { finish = resolve; }),
  };
  setGenerating(true);
  showError("");
  setStatus("Préparation…");
  body.value = "";
  let raw = "";
  try {
    const stream = ndjson(`/api/offers/${offerId}/generate`, {
      language: $("ai-language").value,
      instructions: $("ai-instructions").value,
      attachments: selectedAttachments(),
    }, controller.signal);
    for await (const event of stream) {
      if (event.type === "status") setStatus(event.message);
      else if (event.type === "notice") setStatus(event.message, "warn");
      else if (event.type === "reasoning") setStatus(`Le modèle réfléchit… (${event.chars} caractères)`);
      else if (event.type === "delta") {
        raw += event.text;
        body.value = raw;
        body.scrollTop = body.scrollHeight;
        setStatus(`Rédaction en cours… ${wordCount(raw)} mots`);
      } else if (event.type === "result") {
        subject.value = event.subject;
        body.value = event.body;
        body.scrollTop = 0;
        generation.gotResult = true;
        setStatus(
          event.warning || "Proposition prête : relisez-la et modifiez-la librement avant d'envoyer.",
          event.warning ? "warn" : "ok",
        );
      } else if (event.type === "error") {
        throw new ApiError(event.message);
      }
    }
    if (!generation.gotResult) throw new ApiError("La rédaction s'est interrompue avant la fin.");
    current.lastSaved = null;
    await saveDraft();
  } catch (error) {
    if (error.name === "AbortError") {
      const parts = splitSubject(raw);
      if (parts.subject) subject.value = parts.subject;
      body.value = parts.body || generation.prevBody;
      setStatus("Rédaction arrêtée : le début du texte est conservé.", "warn");
      if (current) saveDraftSoon();
    } else {
      subject.value = generation.prevSubject;
      body.value = generation.prevBody;
      setStatus(error.message, "error");
    }
  } finally {
    generation = null;
    setGenerating(false);
    finish();
  }
}

// --------------------------------------------------------------------------- envoi

async function send() {
  if (!current || generation) return;
  const { to, subject, body } = fields();
  const recipients = to.value.trim();
  showError("");
  if (!recipients) return showError("Indiquez l'adresse email du destinataire.");
  if (!subject.value.trim()) return showError("L'objet est vide.");
  if (!body.value.trim()) return showError("Le message est vide.");
  const settings = getSettings();
  if (settings && (!settings.smtp_host || !(settings.smtp_from_email || settings.email || settings.smtp_username))) {
    return showError("L'envoi n'est pas configuré : renseignez votre email et le serveur SMTP dans Paramètres > Envoi des emails. Vous pouvez aussi utiliser le bouton « Ouvrir dans ma messagerie ».");
  }
  const attachments = selectedAttachments();
  const placeholders = [...new Set(`${subject.value}\n${body.value}`.match(/\[[^\]\n]{1,40}\]/g) || [])]
    .filter((p) => p !== "[…]");
  const ok = await confirmDialog({
    title: "Envoyer la candidature ?",
    text: [
      `À : ${recipients}`,
      `Objet : ${subject.value.trim()}`,
      attachments.length ? `Pièces jointes : ${attachments.join(", ")}` : "Aucune pièce jointe.",
      placeholders.length ? el("p", { class: "error-text", text: `⚠ Champs à compléter encore présents : ${placeholders.join(", ")}` }) : null,
      "L'email part immédiatement depuis votre boîte mail.",
    ].filter(Boolean),
    okLabel: "Envoyer",
  });
  if (!ok || !current) return;
  const offerId = current.offer.id;
  const button = $("compose-send");
  button.disabled = true;
  button.textContent = "Envoi…";
  try {
    const result = await api(`/api/offers/${offerId}/send`, {
      method: "POST",
      json: { to: recipients, subject: subject.value.trim(), body: body.value, attachments },
    });
    saveDraftSoon.cancel();
    hideWindow();
    toast(result.refused?.length
      ? `Envoyé, mais refusé pour : ${result.refused.join(", ")}`
      : "Candidature envoyée ✓ Statut : « Candidature envoyée »", { timeout: 7000 });
    ctx.onChange();
  } catch (error) {
    showError(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Envoyer";
  }
}

function openMailto() {
  if (!current) return;
  const { to, subject, body } = fields();
  const offerId = current.offer.id;
  const address = to.value.trim().split(/[,;\s]+/).filter(Boolean).map(encodeURIComponent).join(",");
  const url = `mailto:${address}?subject=${encodeURIComponent(subject.value)}&body=${encodeURIComponent(body.value)}`;
  window.location.href = url;
  toast("Message ouvert dans votre messagerie : joignez-y votre CV avant de l'envoyer.", {
    timeout: 15000,
    action: {
      label: "Marquer comme envoyée",
      onClick: async () => {
        try {
          await api(`/api/offers/${offerId}`, { method: "PATCH", json: { status: "sent" } });
          ctx.onChange();
        } catch (error) {
          toast(error.message, { error: true });
        }
      },
    },
  });
}

async function copyAll() {
  const { subject, body } = fields();
  const text = subject.value ? `Objet : ${subject.value}\n\n${body.value}` : body.value;
  toast((await copyText(text)) ? "Objet et message copiés." : "Copie impossible : sélectionnez le texte à la main.", { timeout: 3000 });
}

async function discardDraft() {
  if (!current) return;
  const ok = await confirmDialog({ title: "Supprimer le brouillon ?", text: "Le message en cours sera effacé.", okLabel: "Supprimer", danger: true });
  if (!ok || !current) return;
  const { to, subject, body } = fields();
  subject.value = "";
  body.value = "";
  to.value = "";
  saveDraftSoon.cancel();
  await saveDraft();
  hideWindow();
  ctx.onChange();
}

// --------------------------------------------------------------------------- initialisation

export function initCompose(options) {
  ctx = { ...ctx, ...options };
  $("compose-close").addEventListener("click", closeCompose);
  $("compose-min").addEventListener("click", (event) => {
    event.stopPropagation();
    $("compose").classList.toggle("minimized");
  });
  $("compose-expand").addEventListener("click", (event) => {
    event.stopPropagation();
    const expanded = $("compose").classList.toggle("expanded");
    $("compose").classList.remove("minimized");
    setExpandIcon(expanded);
  });
  document.querySelector("#compose .compose-head").addEventListener("click", (event) => {
    if ($("compose").classList.contains("minimized") && !event.target.closest("button")) {
      $("compose").classList.remove("minimized");
    }
  });
  $("compose-desc-toggle").addEventListener("click", () => {
    const box = $("compose-desc");
    box.hidden = !box.hidden;
    $("compose-desc-toggle").textContent = box.hidden ? "Voir l'offre" : "Masquer l'offre";
  });
  for (const input of [$("compose-to"), $("compose-subject"), $("compose-text")]) {
    input.addEventListener("input", () => {
      setSavedText("");
      saveDraftSoon();
    });
  }
  $("ai-generate").addEventListener("click", generate);
  $("ai-stop").addEventListener("click", () => generation?.controller.abort());
  $("compose-send").addEventListener("click", send);
  $("compose-mailto").addEventListener("click", openMailto);
  $("compose-copy").addEventListener("click", copyAll);
  $("compose-discard").addEventListener("click", discardDraft);
  window.addEventListener("pagehide", () => {
    if (current) saveDraft({ keepalive: true });
  });
}
