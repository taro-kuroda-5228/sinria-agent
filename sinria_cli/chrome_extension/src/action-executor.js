// Serialized into the active tab. Validation is repeated in page context.
export function executePageAction(action, expected = null) {
  const sensitive = /(pass(word)?|secret|token|api[-_ ]?key|auth|credential|credit|card|cc-|cvv|cvc|ssn|social|medical|patient|health)/i;
  const writableTypes = new Set(["", "text", "search", "email", "url", "tel", "number"]);
  if (!action || typeof action !== "object") return { ok: false, error: "Invalid action." };
  if (action.type === "navigate") {
    try {
      const target = new URL(action.url, location.href);
      if (!["http:", "https:"].includes(target.protocol)) return { ok: false, error: "Unsafe navigation blocked." };
      location.assign(target.href);
      return { ok: true, detail: `Navigating to ${target.href}` };
    } catch { return { ok: false, error: "Invalid navigation URL." }; }
  }
  if (!/^e[1-9]\d*$/.test(String(action.ref || ""))) return { ok: false, error: "Invalid element reference." };
  if (!expected || expected.ref !== action.ref || typeof expected.guard !== "string" || !expected.guard) {
    return { ok: false, error: "Action is not bound to an approved page snapshot." };
  }
  const el = document.querySelector(`[data-sinria-ref="${action.ref}"]`);
  if (!el) return { ok: false, error: "Element is no longer available. Refresh the page snapshot." };
  if (el.getAttribute("data-sinria-guard") !== expected.guard) {
    return { ok: false, error: "Page changed after approval. Refresh the page snapshot and review again." };
  }
  if (el.tagName.toLowerCase() !== String(expected.tag || "").toLowerCase()) {
    return { ok: false, error: "Approved element identity changed. Refresh the page snapshot." };
  }
  if (action.type === "click") {
    el.scrollIntoView({ block: "center", behavior: "smooth" });
    el.click();
    return { ok: true, detail: `Clicked ${action.ref}` };
  }
  if (action.type === "type") {
    const type = (el.getAttribute("type") || "").toLowerCase();
    const field = [type, el.getAttribute("name"), el.id, el.getAttribute("autocomplete"), el.getAttribute("placeholder"), el.getAttribute("aria-label")].filter(Boolean).join(" ");
    if (type === "password" || sensitive.test(field)) return { ok: false, error: "Typing into sensitive fields is blocked." };
    const tag = el.tagName.toLowerCase();
    if (!(tag === "textarea" || el.isContentEditable || (tag === "input" && writableTypes.has(type)))) {
      return { ok: false, error: "Typing is limited to text-entry controls." };
    }
    if (typeof action.text !== "string" || action.text.length > 4000) return { ok: false, error: "Typing payload is invalid." };
    el.focus();
    if (el.isContentEditable) el.textContent = action.text;
    else el.value = action.text;
    el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: action.text }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return { ok: true, detail: `Typed into ${action.ref}` };
  }
  return { ok: false, error: "Unsupported action." };
}
