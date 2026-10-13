// Onglet « Universités » (profil FLE) : établissements où l'on apprend le français comme
// langue étrangère ou seconde, hors cursus français. « Suivre » crée une candidature spontanée.

import { api } from "./api.js";
import { el, icon, toast, openExternal } from "./util.js";
import { PROFILE } from "./profile.js";

const $ = (id) => document.getElementById(id);

const ZONES = [
  { id: "all", label: "Tout" },
  { id: "fr", label: "France", test: (i) => i.country_code === "FR" },
  { id: "europe", label: "Europe (hors France)", test: (i) => i.region === "europe" && i.country_code !== "FR" },
  { id: "canada", label: "Canada", test: (i) => i.region === "canada" },
];

// Filtres gardés d'un affichage à l'autre (la vue est redessinée après chaque modification).
const filters = { zone: "all", q: "", trackedOnly: false };
let ctx = { onOpen: () => {}, onChange: () => {} };
let items = [];

function fold(text) {
  return (text || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

function visibleItems() {
  const zone = ZONES.find((z) => z.id === filters.zone);
  const terms = fold(filters.q).split(/\s+/).filter(Boolean);
  return items.filter((item) => {
    if (zone?.test && !zone.test(item)) return false;
    if (filters.trackedOnly && !item.offer_id) return false;
    const haystack = fold(`${item.name} ${item.unit} ${item.city} ${item.country} ${item.kind}`);
    return terms.every((t) => haystack.includes(t));
  });
}

async function track(item, button) {
  button.disabled = true;
  try {
    const offer = await api(`/api/institutions/${encodeURIComponent(item.id)}/track`, { method: "POST" });
    item.offer_id = offer.id;
    toast(`${item.name} ajoutée à vos offres (candidature spontanée).`, {
      action: { label: "Ouvrir", onClick: () => ctx.onOpen(offer.id) },
    });
    ctx.onChange();
    renderList();
  } catch (error) {
    toast(error.message, { error: true });
    button.disabled = false;
  }
}

function row(item) {
  const tracked = Boolean(item.offer_id);
  const action = tracked
    ? el("button", { type: "button", class: "btn small", onclick: () => ctx.onOpen(item.offer_id) }, [icon("check"), "Suivie"])
    : el("button", { type: "button", class: "btn tonal small" }, [icon("add_link"), "Suivre"]);
  if (!tracked) action.addEventListener("click", () => track(item, action));
  return el("li", { class: `inst${tracked ? " tracked" : ""}` }, [
    el("div", { class: "inst-main" }, [
      el("div", { class: "inst-name", text: item.name }),
      el("div", { class: "inst-unit", text: item.unit }),
      el("div", { class: "inst-meta muted" }, [
        el("span", { text: `${item.city}, ${item.country}` }),
        el("span", { class: "chip topic-chip", text: item.kind }),
      ]),
    ]),
    el("div", { class: "inst-actions" }, [
      el("button", {
        type: "button", class: "icon-btn", title: "Site de l'université", "aria-label": `Site de ${item.name}`,
        onclick: () => openExternal(item.url),
      }, icon("open_in_new")),
      action,
    ]),
  ]);
}

function renderList() {
  const shown = visibleItems();
  const tracked = items.filter((i) => i.offer_id).length;
  $("inst-count").textContent = `${shown.length} établissement${shown.length > 1 ? "s" : ""} · ${tracked} suivi${tracked > 1 ? "s" : ""}`;
  $("inst-list").replaceChildren(...(shown.length
    ? shown.map(row)
    : [el("li", { class: "muted inst-empty", text: "Aucun établissement ne correspond à ces filtres." })]));
}

export async function renderInstitutions(options) {
  ctx = { ...ctx, ...options };
  const box = $("institutions-view");
  if (!items.length) box.replaceChildren(el("p", { class: "muted stats-loading" }, [el("span", { class: "spinner small" }), " Chargement…"]));
  try {
    items = await api("/api/institutions");
  } catch (error) {
    box.replaceChildren(el("p", { class: "error-text", text: error.message }));
    return;
  }
  const search = el("input", {
    type: "search", class: "inst-search", placeholder: "Filtrer (ville, pays, université…)", "aria-label": "Filtrer les établissements",
  });
  search.value = filters.q;
  search.addEventListener("input", () => { filters.q = search.value; renderList(); });
  const tracked = el("input", { type: "checkbox" });
  tracked.checked = filters.trackedOnly;
  tracked.addEventListener("change", () => { filters.trackedOnly = tracked.checked; renderList(); });
  const zones = el("div", { class: "inst-zones", role: "group", "aria-label": "Zone" }, ZONES.map((zone) => {
    const button = el("button", {
      type: "button", class: `status-option${filters.zone === zone.id ? " active status-option-sent" : ""}`,
      "aria-pressed": String(filters.zone === zone.id), text: zone.label,
    });
    button.addEventListener("click", () => { filters.zone = zone.id; renderInstitutions(); });
    return button;
  }));
  box.replaceChildren(
    el("div", { class: "stats-head" }, [
      el("h2", { text: PROFILE.institutions_label || "Universités" }),
      el("span", { class: "scope-chip", text: "Hors cursus français" }),
    ]),
    el("p", { class: "muted inst-intro", text: PROFILE.institutions_intro }),
    el("div", { class: "inst-filters" }, [
      zones,
      search,
      el("label", { class: "check inst-check" }, [tracked, "Suivies seulement"]),
      el("span", { id: "inst-count", class: "muted small-text" }),
    ]),
    el("ul", { id: "inst-list", class: "inst-list" }),
    el("p", { class: "muted small-text", text: "Liste indicative : vérifiez sur le site de chaque université le service exact et la personne à contacter." }),
  );
  renderList();
}
