const connectionStatus = document.getElementById("connection-status");
const scanButton = document.getElementById("scan");
const fillButton = document.getElementById("fill");
const scanStatus = document.getElementById("scan-status");
const proposals = document.getElementById("proposals");
const proposalList = document.getElementById("proposal-list");

let profile = null;
let currentMatches = [];

function setConnection(message, state = "") {
  connectionStatus.textContent = message;
  connectionStatus.className = `status ${state}`;
}

async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id || !tab.url?.startsWith("http")) throw new Error("Open a normal job application tab first.");
  return tab;
}

async function pageMessage(message) {
  const tab = await getActiveTab();
  try {
    return await chrome.tabs.sendMessage(tab.id, message);
  } catch {
    throw new Error("The page helper is not available yet. Refresh this application page once, then scan again.");
  }
}

async function loadProfile() {
  const response = await chrome.runtime.sendMessage({ type: "getProfile" });
  if (!response?.ok) throw new Error(response?.error || "Could not reach the local profile service.");
  profile = response.profile;
  setConnection("Profile connected. Open any application page and scan it.", "ready");
}

function renderMatches(matches) {
  proposalList.replaceChildren();
  for (const match of matches) {
    const row = document.createElement("div");
    row.className = "proposal";
    const label = document.createElement("label");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = true;
    checkbox.dataset.matchId = match.id;
    const details = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = match.label || match.key.replaceAll("_", " ");
    const value = document.createElement("span");
    value.textContent = match.value;
    details.append(title, value);
    label.append(checkbox, details);
    row.append(label);
    proposalList.append(row);
  }
}

scanButton.addEventListener("click", async () => {
  try {
    if (!profile) await loadProfile();
    const result = await pageMessage({ type: "scanForm", profile });
    currentMatches = result.matches;
    renderMatches(currentMatches);
    proposals.hidden = !currentMatches.length;
    scanStatus.textContent = `${result.summary.fillable} safe proposal(s), ${result.summary.existing} existing value(s) preserved, ${result.summary.fileInputs} resume upload field(s), ${result.summary.sensitive} sensitive field(s) skipped.`;
    if (!currentMatches.length) scanStatus.textContent += " Nothing safe was recognized on this page.";
  } catch (error) {
    scanStatus.textContent = error.message;
  }
});

fillButton.addEventListener("click", async () => {
  const selectedIds = Array.from(proposalList.querySelectorAll("input:checked")).map((input) => input.dataset.matchId);
  if (!selectedIds.length) {
    scanStatus.textContent = "Select at least one reviewed proposal first.";
    return;
  }
  try {
    const result = await pageMessage({ type: "fillApproved", profile, selectedIds });
    const verification = await pageMessage({ type: "verifyFilled", selectedIds });
    scanStatus.textContent = `Filled ${result.filled} reviewed field(s). Verified ${verification.verified}; ${verification.failed} did not match; ${verification.needs_review} custom control(s) need visual review; ${verification.missing} field(s) disappeared. This tool never submits.`;
    proposals.hidden = true;
  } catch (error) {
    scanStatus.textContent = error.message;
  }
});

(async () => {
  try {
    await loadProfile();
  } catch (error) {
    profile = null;
    setConnection(`Start the local service with: python filling/profile_service.py (${error.message})`, "error");
  }
})();
