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
    const view = el.ownerDocument?.defaultView || window;
    const style = view.getComputedStyle(el);
    const box = el.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && box.width > 0 && box.height > 0;
  };
  const roots = [];
  const crossOriginFrames = [];
  const queue = [{ root: document, framePath: [], rootPath: [] }];
  const seen = new Set();
  while (queue.length) {
    const current = queue.shift();
    if (!current?.root || seen.has(current.root)) continue;
    seen.add(current.root);
    roots.push(current);
    for (const host of current.root.querySelectorAll?.("*") || []) {
      if (host.shadowRoot) {
        const index = Array.from(host.parentElement?.children || []).indexOf(host);
        queue.push({ root: host.shadowRoot, framePath: current.framePath, rootPath: [...current.rootPath, `${host.tagName.toLowerCase()}:${Math.max(index, 0)}`] });
      }
      if (host.tagName === "IFRAME") {
        const index = Array.from(host.ownerDocument.querySelectorAll("iframe")).indexOf(host);
        try {
          const child = host.contentDocument;
          if (child) queue.push({ root: child, framePath: [...current.framePath, Math.max(index, 0)], rootPath: current.rootPath });
          else crossOriginFrames.push({ src: cleanUrl(host.src || ""), title: cleanText(host.title || "", 120), framePath: [...current.framePath, Math.max(index, 0)] });
        } catch {
          crossOriginFrames.push({ src: cleanUrl(host.src || ""), title: cleanText(host.title || "", 120), framePath: [...current.framePath, Math.max(index, 0)] });
        }
      }
    }
  }
  for (const { root } of roots) {
    root.querySelectorAll?.("[data-sinria-ref],[data-sinria-guard]").forEach((el) => {
      el.removeAttribute("data-sinria-ref");
      el.removeAttribute("data-sinria-guard");
    });
  }
  const selector = "a[href],button,input,textarea,select,[role='button'],[contenteditable='true']";
  const elements = [];
  for (const { root, framePath, rootPath } of roots) for (const el of root.querySelectorAll?.(selector) || []) {
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
      framePath,
      rootPath,
      field
    });
  }
  const root = document.querySelector("main,[role='main']") || document.body;
  const canvases = Array.from(document.querySelectorAll("canvas")).slice(0, 20).map((canvas, index) => ({
    index,
    width: Number(canvas.width) || 0,
    height: Number(canvas.height) || 0,
    label: cleanText(canvas.getAttribute("aria-label") || canvas.getAttribute("title") || "", 200),
  }));
  const charts = Array.from(document.querySelectorAll("svg[role='img'],svg[aria-label],figure")).slice(0, 20).map((node, index) => ({
    index,
    label: cleanText(node.getAttribute("aria-label") || node.getAttribute("title") || node.querySelector?.("figcaption")?.textContent || "", 300),
    text: cleanText(node.textContent || "", 1200),
  }));
  const cardRoots = [];
  const cardByKey = new Map();
  const cardTriggers = document.querySelectorAll(
    "a[href*='/sales/lead/'],a[href*='/sales/people/'],input[type='checkbox'][aria-label*='reachable'],[role='checkbox'][aria-label*='reachable']",
  );
  const resultCardRoot = (trigger) => {
    let node = trigger;
    let best = null;
    for (let depth = 0; node && node !== document.body && depth < 10; depth += 1, node = node.parentElement) {
      const reachable = node.querySelectorAll?.(
        "input[type='checkbox'][aria-label*='reachable'],[role='checkbox'][aria-label*='reachable']",
      ) || [];
      const leadLinks = node.querySelectorAll?.("a[href*='/sales/lead/'],a[href*='/sales/people/']") || [];
      const uniqueLeadLinks = new Set(Array.from(leadLinks).map((link) => link.href || link.getAttribute("href") || ""));
      const text = cleanText(node.innerText || node.textContent || "", 4000);
      if (reachable.length > 1 || uniqueLeadLinks.size > 2 || text.length > 3200) break;
      const labels = Array.from(node.querySelectorAll?.("[aria-label]") || [])
        .map((item) => cleanText(item.getAttribute("aria-label") || "", 300))
        .filter(Boolean)
        .slice(0, 20);
      if (text || labels.length) {
        const score = text.length + labels.join(" ").length;
        if (!best || score > best.score) best = { node, text, labels, score };
      }
    }
    return best;
  };
  for (const trigger of cardTriggers) {
    const candidate = resultCardRoot(trigger);
    if (!candidate) continue;
    const reachableLabel = candidate.labels.find((label) => /reachable/i.test(label)) || "";
    const profileHref = trigger.matches?.("a[href*='/sales/']")
      ? cleanUrl(trigger.href || trigger.getAttribute("href") || "")
      : "";
    const key = reachableLabel || profileHref || candidate.node;
    const existing = cardByKey.get(key);
    if (!existing || candidate.score > existing.score) cardByKey.set(key, candidate);
  }
  for (const candidate of cardByKey.values()) {
    cardRoots.push({ text: cleanText(candidate.text, 1600), labels: candidate.labels });
    if (cardRoots.length >= 40) break;
  }
  return {
    title: cleanText(document.title, 300),
    url: cleanUrl(location.href),
    text: cleanText(root?.innerText || ""),
    cards: cardRoots,
    elements,
    crossOriginFrames,
    canvases,
    charts,
    capturedAt: new Date().toISOString()
  };
}
