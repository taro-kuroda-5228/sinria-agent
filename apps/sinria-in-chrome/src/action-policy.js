const SENSITIVE_PATTERN =
  /(pass(word)?|secret|token|api[-_ ]?key|auth|credential|credit|card|cc-|cvv|cvc|ssn|social|medical|patient|health)/i;
const CONSEQUENTIAL_PATTERN =
  /(\b(send|submit|publish|post|message|delete|remove|erase|purchase|buy|pay|checkout|order|book|reserve|confirm|sign[ -]?in|log[ -]?in|authorize|approve|upload|share|invite)\b|送信|投稿|公開|削除|購入|支払|決済|注文|予約|確定|ログイン|サインイン|承認|アップロード|共有|招待)/i;
const SAFE_REF = /^e[1-9]\d*$/;

export function isSensitiveField(field = {}) {
  const fingerprint = [
    field.type,
    field.name,
    field.id,
    field.autocomplete,
    field.placeholder,
    field.ariaLabel,
  ]
    .filter(Boolean)
    .join(" ");
  return (
    String(field.type || "").toLowerCase() === "password" ||
    SENSITIVE_PATTERN.test(fingerprint)
  );
}

export function isWritableField(field = {}) {
  const tag = String(field.tag || "").toLowerCase();
  if (tag === "textarea") return true;
  if (String(field.contenteditable || "").toLowerCase() === "true") return true;
  if (tag !== "input") return false;
  return ["", "text", "search", "email", "url", "tel", "number"].includes(
    String(field.type || "").toLowerCase(),
  );
}

export function validateAction(action) {
  if (!action || typeof action !== "object")
    return { ok: false, reason: "Invalid action." };
  if (!Number.isInteger(Number(action.tabId)) || Number(action.tabId) < 0)
    return { ok: false, reason: "Action requires an explicit tabId." };
  if (["back", "forward", "reload", "close_tab", "activate_tab"].includes(action.type))
    return { ok: true };
  if (action.type === "open_tab") {
    try {
      const url = new URL(action.url);
      if (!["http:", "https:"].includes(url.protocol))
        return { ok: false, reason: "Only HTTP(S) tab URLs are allowed." };
      return { ok: true };
    } catch {
      return { ok: false, reason: "Invalid tab URL." };
    }
  }
  if (action.type === "navigate") {
    try {
      const url = new URL(action.url);
      if (!["http:", "https:"].includes(url.protocol))
        return { ok: false, reason: "Only HTTP(S) navigation is allowed." };
      return { ok: true };
    } catch {
      return { ok: false, reason: "Invalid navigation URL." };
    }
  }
  const elementActions = new Set([
    "click",
    "type",
    "focus",
    "hover",
    "scroll_into_view",
    "select",
    "check",
    "uncheck",
    "keypress",
    "choose_file",
    "download",
  ]);
  if (
    !elementActions.has(action.type) ||
    !SAFE_REF.test(String(action.ref || ""))
  ) {
    return {
      ok: false,
      reason: "Action requires a valid page element reference.",
    };
  }
  if (action.type === "type") {
    if (typeof action.text !== "string" || action.text.length > 4000)
      return { ok: false, reason: "Typing payload is invalid or too long." };
    if (
      action.field &&
      !isWritableField(action.field) &&
      !isSensitiveField(action.field)
    )
      return { ok: false, reason: "Typing is limited to text-entry controls." };
  }
  if (
    action.type === "select" &&
    (typeof action.value !== "string" || action.value.length > 1000)
  )
    return { ok: false, reason: "Selection value is invalid." };
  if (action.type === "keypress") {
    const keys = new Set([
      "Enter",
      "Tab",
      "Escape",
      "ArrowUp",
      "ArrowDown",
      "ArrowLeft",
      "ArrowRight",
      "Home",
      "End",
      "PageUp",
      "PageDown",
      "Backspace",
      "Delete",
      " ",
    ]);
    if (!keys.has(action.key))
      return { ok: false, reason: "Key is not in the safe key set." };
  }
  return { ok: true };
}

export function requiresActionApproval(action = {}, target = null) {
  if (action.type === "choose_file") return true;
  if (
    action.type === "type" &&
    isSensitiveField(target?.field || action.field || {})
  )
    return true;
  if (action.type !== "click") return false;
  const fingerprint = [
    target?.label,
    target?.text,
    target?.ariaLabel,
    target?.field?.name,
    target?.field?.id,
    action.intent,
  ]
    .filter(Boolean)
    .join(" ");
  return CONSEQUENTIAL_PATTERN.test(fingerprint);
}

export function actionSummary(action, target = null) {
  if (!action || typeof action !== "object" || !action.type)
    return "Reject malformed browser action";
  if (["back", "forward", "reload", "close_tab", "activate_tab"].includes(action.type)) {
    const verbs = { back: "Go back in", forward: "Go forward in", reload: "Reload", close_tab: "Close", activate_tab: "Activate" };
    return `${verbs[action.type]} tab ${action.tabId}`;
  }
  if (action.type === "open_tab") return `Open ${String(action.url || "").slice(0, 240)}`;
  if (action.type === "navigate") return `Navigate tab ${action.tabId} to ${String(action.url || "").slice(0, 240)}`;
  const identity = target?.label
    ? `${action.ref} (${target.label})`
    : action.ref;
  if (action.type === "type")
    return `Type into ${identity}: ${action.text.slice(0, 120)}`;
  if (action.type === "select") return `Select ${action.value} in ${identity}`;
  if (action.type === "keypress") return `Press ${action.key} on ${identity}`;
  const verbs = {
    click: "Click",
    focus: "Focus",
    hover: "Hover",
    scroll_into_view: "Scroll to",
    check: "Check",
    uncheck: "Uncheck",
  };
  return `${verbs[action.type] || "Interact with"} ${identity}`;
}
