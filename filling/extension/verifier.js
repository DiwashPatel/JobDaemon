function verifyFilledFields(selectedIds) {
  const results = { verified: 0, failed: 0, needs_review: 0, missing: 0 };
  for (const id of selectedIds) {
    const match = globalThis.JobDaemonFiller?.getMatch(id);
    const result = match ? globalThis.JobDaemonFiller.valuesMatch(match.element, match) : { status: "missing" };
    results[result.status] += 1;
  }
  return results;
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type === "verifyFilled") sendResponse(verifyFilledFields(message.selectedIds || []));
  return true;
});
