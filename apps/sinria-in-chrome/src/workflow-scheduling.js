export function deferAutoApproval(callback, schedule = setTimeout) {
  return schedule(callback, 0);
}

export function postActionSettleMs(actionType) {
  if (["navigate", "reload", "open_tab"].includes(actionType)) return 2500;
  if (["click", "keypress", "back", "forward"].includes(actionType)) return 1200;
  return 100;
}
