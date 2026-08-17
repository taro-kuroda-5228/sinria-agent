const SENSITIVE_PATTERN = /(pass(word)?|secret|token|api[-_ ]?key|auth|credential|credit|card|cc-|cvv|cvc|ssn|social|medical|patient|health)/i;
const SAFE_REF = /^e[1-9]\d*$/;

export function isSensitiveField(field = {}) {
  const fingerprint = [field.type, field.name, field.id, field.autocomplete, field.placeholder, field.ariaLabel]
    .filter(Boolean)
    .join(" ");
  return String(field.type || "").toLowerCase() === "password" || SENSITIVE_PATTERN.test(fingerprint);
}

export function isWritableField(field = {}) {
  const tag = String(field.tag || "").toLowerCase();
  if (tag === "textarea") return true;
  if (String(field.contenteditable || "").toLowerCase() === "true") return true;
  if (tag !== "input") return false;
  return ["", "text", "search", "email", "url", "tel", "number"].includes(String(field.type || "").toLowerCase());
}

export function validateAction(action) {
  if (!action || typeof action !== "object") return { ok: false, reason: "Invalid action." };
  if (action.type === "navigate") {
    try {
      const url = new URL(action.url);
      if (!["http:", "https:"].includes(url.protocol)) return { ok: false, reason: "Only HTTP(S) navigation is allowed." };
      return { ok: true };
    } catch {
      return { ok: false, reason: "Invalid navigation URL." };
    }
  }
  if (!["click", "type"].includes(action.type) || !SAFE_REF.test(String(action.ref || ""))) {
    return { ok: false, reason: "Action requires a valid page element reference." };
  }
  if (action.type === "type") {
    if (typeof action.text !== "string" || action.text.length > 4000) return { ok: false, reason: "Typing payload is invalid or too long." };
    if (isSensitiveField(action.field || {})) return { ok: false, reason: "Typing into sensitive fields is blocked." };
    if (action.field && !isWritableField(action.field)) return { ok: false, reason: "Typing is limited to text-entry controls." };
  }
  return { ok: true };
}

export function actionSummary(action, target = null) {
  if (action.type === "navigate") return `Open ${action.url}`;
  const identity = target?.label ? `${action.ref} (${target.label})` : action.ref;
  if (action.type === "type") return `Type into ${identity}: ${action.text.slice(0, 120)}`;
  return `Click ${identity}`;
}
