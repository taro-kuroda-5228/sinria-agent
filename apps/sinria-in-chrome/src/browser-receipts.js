const ACTION_TYPES = new Set([
  "click",
  "type",
  "select",
  "check",
  "focus",
  "scroll_into_view",
  "keypress",
  "navigate",
  "back",
  "forward",
  "reload",
  "open_tab",
  "close_tab",
  "activate_tab",
  "choose_file",
  "readback",
]);

export function browserReceiptEvidence({
  receiptId,
  actionType,
  verified,
  readbackTitle,
} = {}) {
  const id = String(receiptId || "");
  const type = String(actionType || "");
  if (!verified || !/^[A-Za-z0-9:_-]{8,160}$/.test(id) || !ACTION_TYPES.has(type))
    return null;
  return {
    receipt_id: id,
    action_type: type,
    verified: true,
    readback_label: String(readbackTitle || "Browser action readback").slice(0, 240),
  };
}
