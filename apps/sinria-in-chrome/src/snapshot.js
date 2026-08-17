export const MAX_PAGE_TEXT = 24000;
const SENSITIVE_PATTERN = /(pass(word)?|secret|token|api[-_ ]?key|auth|credential|credit|card|cc-|cvv|cvc|ssn|social|medical|patient|health)/i;

export function truncateText(value, max = MAX_PAGE_TEXT) {
  return String(value || "").replace(/\s+/g, " ").trim().slice(0, max);
}

export function redactText(value) {
  return truncateText(value)
    .replace(/\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi, "[REDACTED_EMAIL]")
    .replace(/\b(?:bearer|token|api[-_ ]?key)\s*[:=]?\s*[A-Za-z0-9._~+\/-]{12,}\b/gi, "[REDACTED_TOKEN]")
    .replace(/\b(?:patient|mrn|medical record|date of birth|dob)\s*[:#]\s*[^,;|]{2,80}/gi, "[REDACTED_SENSITIVE_TEXT]");
}

export function sanitizeUrl(value) {
  try {
    const url = new URL(String(value || ""));
    if (!["http:", "https:"].includes(url.protocol)) return "";
    url.username = "";
    url.password = "";
    url.search = "";
    url.hash = "";
    return url.href;
  } catch {
    return "";
  }
}

export function sanitizeField(field = {}) {
  const fingerprint = [field.type, field.name, field.id, field.autocomplete, field.placeholder, field.ariaLabel]
    .filter(Boolean).join(" ");
  const sensitive = String(field.type || "").toLowerCase() === "password" || SENSITIVE_PATTERN.test(fingerprint);
  return { ...field, value: sensitive ? "[REDACTED]" : redactText(field.value).slice(0, 500), sensitive };
}

// This function is serialized by chrome.scripting.executeScript. Keep it self-contained.
export function capturePageSnapshot() {
  const maxText = 24000;
  const maxElements = 160;
  const sensitive = /(pass(word)?|secret|token|api[-_ ]?key|auth|credential|credit|card|cc-|cvv|cvc|ssn|social|medical|patient|health)/i;
  const cleanText = (value, max = maxText) => String(value || "").replace(/\s+/g, " ").trim().slice(0, max)
    .replace(/\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi, "[REDACTED_EMAIL]")
    .replace(/\b(?:bearer|token|api[-_ ]?key)\s*[:=]?\s*[A-Za-z0-9._~+\/-]{12,}\b/gi, "[REDACTED_TOKEN]")
    .replace(/\b(?:patient|mrn|medical record|date of birth|dob)\s*[:#]\s*[^,;|]{2,80}/gi, "[REDACTED_SENSITIVE_TEXT]");
  const cleanUrl = (value) => {
    try {
      const url = new URL(String(value || ""));
      if (!["http:", "https:"].includes(url.protocol)) return "";
      url.username = ""; url.password = ""; url.search = ""; url.hash = "";
      return url.href;
    } catch { return ""; }
  };
  const visible = (el) => {
    const style = getComputedStyle(el);
    const box = el.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && box.width > 0 && box.height > 0;
  };
  document.querySelectorAll("[data-sinria-ref],[data-sinria-guard]").forEach((el) => {
    el.removeAttribute("data-sinria-ref");
    el.removeAttribute("data-sinria-guard");
  });
  const selector = "a[href],button,input,textarea,select,[role='button'],[contenteditable='true']";
  const elements = [];
  for (const el of document.querySelectorAll(selector)) {
    if (elements.length >= maxElements || !visible(el)) continue;
    const ref = `e${elements.length + 1}`;
    const guard = crypto.randomUUID();
    el.setAttribute("data-sinria-ref", ref);
    el.setAttribute("data-sinria-guard", guard);
    const field = {
      type: (el.getAttribute("type") || el.tagName).toLowerCase(),
      name: el.getAttribute("name") || "",
      id: el.id || "",
      autocomplete: el.getAttribute("autocomplete") || "",
      placeholder: el.getAttribute("placeholder") || "",
      ariaLabel: el.getAttribute("aria-label") || ""
    };
    const fingerprint = Object.values(field).join(" ");
    const isSensitive = field.type === "password" || sensitive.test(fingerprint);
    const rawValue = "value" in el ? String(el.value || "") : "";
    elements.push({
      ref,
      guard,
      tag: el.tagName.toLowerCase(),
      role: el.getAttribute("role") || "",
      contenteditable: el.getAttribute("contenteditable") || "",
      label: cleanText(el.innerText || el.textContent || field.ariaLabel || field.placeholder, 240),
      href: el instanceof HTMLAnchorElement ? cleanUrl(el.href) : "",
      value: isSensitive ? "[REDACTED]" : cleanText(rawValue, 500),
      sensitive: isSensitive,
      field
    });
  }
  const root = document.querySelector("main,[role='main']") || document.body;
  return {
    title: cleanText(document.title, 300),
    url: cleanUrl(location.href),
    text: cleanText(root?.innerText || ""),
    elements,
    capturedAt: new Date().toISOString()
  };
}
