// Tableau de bord : indicateurs et graphiques (SVG dessinés à la main, sans bibliothèque).

import { api } from "./api.js";
import { el, STATUSES, SOURCE_SHORT } from "./util.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const $ = (id) => document.getElementById(id);

function svg(tag, attrs = {}, children = []) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value !== undefined && value !== null) node.setAttribute(key, String(value));
  }
  for (const child of [].concat(children)) if (child) node.append(child);
  return node;
}

const fmt = new Intl.NumberFormat("fr-FR");
const pct = new Intl.NumberFormat("fr-FR", { style: "percent", maximumFractionDigits: 0 });

/** Graduation « propre » (0, 5, 10…) couvrant `max`. */
function niceScale(max, ticks = 4) {
  if (max <= 0) return { top: 4, step: 1 };
  const raw = max / ticks;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= raw);
  const finalStep = Math.max(1, step);
  return { top: Math.ceil(max / finalStep) * finalStep, step: finalStep };
}

/** Chemin d'une barre arrondie (4px) côté donnée, carrée côté ligne de base. */
function barPath(x, y, w, h, horizontal) {
  const r = Math.min(4, horizontal ? w / 2 : h / 2, horizontal ? h / 2 : w / 2);
  if (w <= 0 || h <= 0) return "";
  if (horizontal) {
    return `M${x},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h - r}Q${x + w},${y + h} ${x + w - r},${y + h}H${x}Z`;
  }
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

// --------------------------------------------------------------------------- infobulle

let tip = null;

function showTip(event, lines) {
  if (!tip) {
    tip = el("div", { class: "chart-tip", role: "tooltip" });
    document.body.append(tip);
  }
  tip.replaceChildren(...lines.map((line, i) => (typeof line === "string"
    ? el("div", { class: i ? "chart-tip-row" : "chart-tip-title", text: line })
    : el("div", { class: "chart-tip-row" }, [
      el("span", { class: `chart-key ${line.key}` }),
      el("span", { class: "chart-tip-label", text: line.label }),
      el("strong", { text: fmt.format(line.value) }),
    ]))));
  tip.hidden = false;
  const { innerWidth: vw } = window;
  const rect = tip.getBoundingClientRect();
  let left = event.clientX + 14;
  if (left + rect.width > vw - 8) left = event.clientX - rect.width - 14;
  tip.style.left = `${Math.max(8, left)}px`;
  tip.style.top = `${Math.max(8, event.clientY - rect.height - 12)}px`;
}

function hideTip() {
  if (tip) tip.hidden = true;
}

function hoverable(node, lines) {
  node.addEventListener("pointermove", (event) => showTip(event, lines));
  node.addEventListener("pointerleave", hideTip);
  return node;
}

// --------------------------------------------------------------------------- composants

function card(title, subtitle, body, extra = null) {
  return el("section", { class: "stat-card" }, [
    el("header", { class: "stat-card-head" }, [
      el("h3", { text: title }),
      subtitle ? el("p", { class: "muted", text: subtitle }) : null,
    ]),
    body,
    extra,
  ]);
}

function tile(label, value, hint, accent = false) {
  return el("div", { class: `kpi${accent ? " kpi-accent" : ""}` }, [
    el("span", { class: "kpi-label", text: label }),
    el("span", { class: "kpi-value", text: value }),
    hint ? el("span", { class: "kpi-hint", text: hint }) : null,
  ]);
}

/**
 * Barres horizontales : une ligne par catégorie (pastille + libellé, barre, valeur).
 * rows = [{ label, value, key (classe de couleur), href?, title? }]
 */
function hbars(rows, { emptyText = "Aucune donnée pour l'instant." } = {}) {
  if (!rows.length || rows.every((r) => !r.value)) return el("p", { class: "muted chart-empty", text: emptyText });
  const max = Math.max(...rows.map((r) => r.value));
  return el("ul", { class: "hbars" }, rows.map((row) => {
    const width = max ? (row.value / max) * 100 : 0;
    const label = el("span", { class: "hbar-label" }, [
      row.dot ? el("span", { class: `chart-key ${row.key}` }) : null,
      row.href ? el("a", { href: row.href, text: row.label }) : el("span", { text: row.label }),
    ]);
    const bar = el("span", { class: "hbar-track" }, el("span", {
      class: `hbar-fill ${row.key}`,
      style: `width:${row.value ? Math.max(width, 1.5) : 0}%`,
    }));
    const item = el("li", { class: "hbar" }, [label, bar, el("span", { class: "hbar-value", text: fmt.format(row.value) })]);
    return hoverable(item, [row.title || row.label, { key: row.key, label: "Offres", value: row.value }]);
  }));
}

/** Colonnes groupées par semaine : offres trouvées / candidatures envoyées (même unité, un seul axe). */
const WEEKLY_SERIES = [
  { id: "found", label: "Offres trouvées", key: "series-1" },
  { id: "applied", label: "Candidatures envoyées", key: "series-2" },
];

/** Dessine le graphique à la largeur réelle de `holder` (texte des axes à taille constante). */
function drawWeekly(holder, weekly) {
  const series = WEEKLY_SERIES;
  const W = Math.max(280, Math.floor(holder.clientWidth || 640)), H = 220;
  const M = { top: 12, right: 8, bottom: 26, left: 32 };
  const innerW = W - M.left - M.right, innerH = H - M.top - M.bottom;
  const max = Math.max(0, ...weekly.flatMap((w) => series.map((s) => w[s.id])));
  const { top, step } = niceScale(max);
  const y = (v) => M.top + innerH - (v / top) * innerH;
  const band = innerW / weekly.length;
  const barW = Math.min(14, (band - 8) / 2);

  const root = svg("svg", {
    viewBox: `0 0 ${W} ${H}`, width: W, height: H, class: "chart-svg", role: "img", "aria-label": "Activité par semaine",
  });
  for (let v = 0; v <= top; v += step) {
    root.append(
      svg("line", { x1: M.left, x2: W - M.right, y1: y(v), y2: y(v), class: v === 0 ? "axis-base" : "axis-grid" }),
      svg("text", { x: M.left - 6, y: y(v) + 4, class: "axis-text", "text-anchor": "end" }, document.createTextNode(fmt.format(v))),
    );
  }
  weekly.forEach((week, i) => {
    const x0 = M.left + i * band + (band - (barW * 2 + 2)) / 2;
    const date = new Date(`${week.week}T12:00:00Z`);
    const label = date.toLocaleDateString("fr-FR", { day: "numeric", month: "short" });
    const group = svg("g", { class: "col-group" });
    group.append(svg("rect", { x: M.left + i * band, y: M.top, width: band, height: innerH, class: "col-hit" }));
    series.forEach((s, j) => {
      const value = week[s.id];
      const h = innerH - (y(value) - M.top);
      if (value) group.append(svg("path", { d: barPath(x0 + j * (barW + 2), y(value), barW, h, false), class: `mark ${s.key}` }));
    });
    const every = Math.ceil(70 / band); // une date toutes les N semaines selon la place
    if ((weekly.length - 1 - i) % every === 0) {
      root.append(svg("text", { x: M.left + i * band + band / 2, y: H - 8, class: "axis-text", "text-anchor": "middle" }, document.createTextNode(label)));
    }
    hoverable(group, [`Semaine du ${date.toLocaleDateString("fr-FR", { day: "numeric", month: "long" })}`,
      ...series.map((s) => ({ key: s.key, label: s.label, value: week[s.id] }))]);
    root.append(group);
  });
  holder.replaceChildren(root);
}

function weeklyChart(weekly) {
  const series = WEEKLY_SERIES;
  const holder = el("div", { class: "chart-holder" });
  requestAnimationFrame(() => drawWeekly(holder, weekly));
  if ("ResizeObserver" in window) {
    let width = 0;
    new ResizeObserver(([entry]) => {
      const next = Math.floor(entry.contentRect.width);
      if (next && next !== width) {
        width = next;
        drawWeekly(holder, weekly);
      }
    }).observe(holder);
  }
  const legend = el("div", { class: "chart-legend" }, series.map((s) => el("span", { class: "legend-item" }, [
    el("span", { class: `chart-key ${s.key}` }), s.label,
  ])));
  const table = el("details", { class: "chart-table" }, [
    el("summary", { text: "Voir les données" }),
    el("table", {}, [
      el("thead", {}, el("tr", {}, [el("th", { text: "Semaine du" }), ...series.map((s) => el("th", { text: s.label }))])),
      el("tbody", {}, weekly.map((w) => el("tr", {}, [
        el("td", { text: new Date(`${w.week}T12:00:00Z`).toLocaleDateString("fr-FR") }),
        ...series.map((s) => el("td", { text: fmt.format(w[s.id]) })),
      ]))),
    ]),
  ]);
  return el("div", { class: "chart" }, [legend, holder, table]);
}

/** Entonnoir : du nombre d'offres suivies jusqu'aux entretiens. */
function funnel(stats) {
  const steps = [
    { label: "Offres suivies", value: stats.total },
    { label: "Candidatures envoyées", value: stats.applied },
    { label: "Réponses reçues", value: stats.replies },
    { label: "Entretiens", value: stats.interviews },
  ];
  const max = Math.max(1, steps[0].value);
  return el("ol", { class: "funnel" }, steps.map((step, i) => {
    const prev = i ? steps[i - 1].value : null;
    const rate = prev ? pct.format(step.value / prev) : null;
    return el("li", { class: "funnel-step" }, [
      el("span", { class: "funnel-bar", style: `width:${Math.max((step.value / max) * 100, step.value ? 2 : 0)}%` }),
      el("span", { class: "funnel-text" }, [
        el("span", { text: step.label }),
        el("strong", { text: fmt.format(step.value) }),
        rate ? el("span", { class: "muted funnel-rate", text: `${rate} de l'étape précédente` }) : null,
      ]),
    ]);
  }));
}

// --------------------------------------------------------------------------- vue

export async function renderStats(sourceLabel) {
  const box = $("stats-view");
  box.replaceChildren(el("p", { class: "muted stats-loading" }, [el("span", { class: "spinner small" }), " Calcul des statistiques…"]));
  let stats;
  try {
    stats = await api("/api/stats");
  } catch (error) {
    box.replaceChildren(el("p", { class: "error-text", text: error.message }));
    return;
  }
  const scope = stats.ai_only ? "Stages en IA uniquement" : "Toutes les offres";
  const statusRows = STATUSES.map((s) => ({
    label: s.label, value: stats.by_status[s.id] || 0, key: `st-${s.id}`, dot: true, href: `#status/${s.id}`,
  }));
  const sourceRows = Object.entries(stats.by_source).map(([source, value]) => ({
    label: sourceLabel(source) || SOURCE_SHORT[source] || source,
    value,
    key: `src-fill-${["hellowork", "linkedin", "wttj"].includes(source) ? source : "manual"}`,
    dot: true,
    href: `#source/${source}`,
  }));
  const topicRows = stats.topics.map((t) => ({ label: t.topic, value: t.count, key: "series-1" }));
  const companies = stats.companies.length
    ? el("table", { class: "companies" }, [
      el("thead", {}, el("tr", {}, [el("th", { text: "Entreprise" }), el("th", { text: "Offres" }), el("th", { text: "Candidatures" })])),
      el("tbody", {}, stats.companies.map((c) => el("tr", {}, [
        el("td", {}, el("a", { href: `#search/${encodeURIComponent(c.name)}`, text: c.name })),
        el("td", { text: fmt.format(c.offers) }),
        el("td", { text: fmt.format(c.applied) }),
      ]))),
    ])
    : el("p", { class: "muted chart-empty", text: "Aucune entreprise pour l'instant." });

  box.replaceChildren(
    el("div", { class: "stats-head" }, [
      el("h2", { text: "Tableau de bord" }),
      el("span", { class: "scope-chip", text: scope }),
    ]),
    el("div", { class: "kpis" }, [
      tile("Offres suivies", fmt.format(stats.total), `${fmt.format(stats.active)} dans la boîte de réception`, true),
      tile("Candidatures envoyées", fmt.format(stats.applied), stats.total ? `${pct.format(stats.applied / stats.total)} des offres` : null),
      tile("Taux de réponse", stats.reply_rate === null ? "—" : pct.format(stats.reply_rate), `${fmt.format(stats.replies)} réponse${stats.replies > 1 ? "s" : ""}`),
      tile("Entretiens", fmt.format(stats.interviews), null),
    ]),
    el("div", { class: "stats-grid" }, [
      card("Activité des 12 dernières semaines", "Offres ajoutées et premières candidatures, par semaine", weeklyChart(stats.weekly)),
      card("Parcours des candidatures", "De l'offre repérée à l'entretien", funnel(stats)),
      card("Statut des offres", "Cliquez sur un statut pour voir les offres", hbars(statusRows)),
      card("Thèmes IA", "Une offre peut couvrir plusieurs thèmes", hbars(topicRows, { emptyText: "Aucune offre en IA pour l'instant." })),
      card("Sources", null, hbars(sourceRows)),
      card("Entreprises les plus présentes", null, companies),
    ]),
  );
}

window.addEventListener("scroll", hideTip, true);
