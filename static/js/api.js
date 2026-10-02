// Appels à l'API locale. L'en-tête X-Requested-With est exigé par le serveur pour
// toute modification (protection contre les requêtes venant d'autres sites).

const HEADERS = { "X-Requested-With": "InternshipApplier" };

export class ApiError extends Error {
  constructor(message, status = 0, data = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.data = data;
  }
}

function errorMessage(data, status) {
  const detail = data && data.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        const field = Array.isArray(item.loc) ? item.loc[item.loc.length - 1] : "";
        return field ? `${field} : ${item.msg}` : item.msg;
      })
      .join(" ; ");
  }
  if (detail && typeof detail === "object" && detail.message) return detail.message;
  return `Erreur du serveur (HTTP ${status})`;
}

async function parseError(response) {
  const text = await response.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = null; }
  return new ApiError(errorMessage(data, response.status), response.status, data);
}

export async function api(path, { method = "GET", json, body, headers = {}, signal } = {}) {
  const options = { method, headers: { ...HEADERS, ...headers }, signal };
  if (json !== undefined) {
    options.body = JSON.stringify(json);
    options.headers["Content-Type"] = "application/json";
  } else if (body !== undefined) {
    options.body = body;
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new ApiError("Serveur injoignable : l'application est-elle toujours lancée ?");
  }
  if (!response.ok) throw await parseError(response);
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}

/** Lit une réponse NDJSON (un objet JSON par ligne) au fil de l'eau. */
export async function* ndjson(path, payload, signal) {
  let response;
  try {
    response = await fetch(path, {
      method: "POST",
      headers: { ...HEADERS, "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal,
    });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new ApiError("Serveur injoignable : l'application est-elle toujours lancée ?");
  }
  if (!response.ok) throw await parseError(response);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let index;
    while ((index = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, index).trim();
      buffer = buffer.slice(index + 1);
      if (line) yield JSON.parse(line);
    }
  }
  buffer += decoder.decode();
  if (buffer.trim()) yield JSON.parse(buffer.trim());
}
