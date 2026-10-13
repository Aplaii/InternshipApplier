// Profil actif (domaine de stage, zone géographique, textes) : IA par défaut, FLE dans fle/.
// Les valeurs viennent de /api/meta ; les éléments [data-profile] du HTML sont mis à jour.

export const PROFILE = {
  id: "ia",
  brand: "Stages",
  domain_label: "stages en IA",
  domain_short: "IA",
  filter_label: "",
  keywords_placeholder: "",
  location_placeholder: "",
  region_label: "",
  default_queries: [],
};

export function setProfile(profile) {
  Object.assign(PROFILE, profile || {});
  for (const node of document.querySelectorAll("[data-profile]")) {
    const value = PROFILE[node.dataset.profile];
    if (value) node.textContent = value;
  }
  for (const node of document.querySelectorAll("[data-profile-placeholder]")) {
    const value = PROFILE[node.dataset.profilePlaceholder];
    if (value) node.placeholder = value;
  }
  for (const node of document.querySelectorAll("[data-profile-region]")) {
    node.hidden = !PROFILE.region_label;
  }
  document.body.dataset.profile = PROFILE.id;
}

/** « 3 offres hors IA », « 1 offre en FLE »… */
export function plural(n, singular, pluralForm = `${singular}s`) {
  return `${n} ${n > 1 ? pluralForm : singular}`;
}
