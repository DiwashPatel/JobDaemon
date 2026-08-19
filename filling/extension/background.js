// Every future provider (local model or server) implements this same read API.
const PROFILE_PROVIDER = {
  baseUrl: "http://127.0.0.1:8765/api/v1",
  profilePath: "/profile",
  expectedApiVersion: "v1"
};

async function requestService(path) {
  const response = await fetch(`${PROFILE_PROVIDER.baseUrl}${path}`);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || "The local profile service rejected the request.");
  }
  if (payload.api_version !== PROFILE_PROVIDER.expectedApiVersion) {
    throw new Error("The profile provider returned an unsupported API version.");
  }
  return payload;
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
});

chrome.action.onClicked.addListener(async (tab) => {
  if (tab.id) await chrome.sidePanel.open({ tabId: tab.id });
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type !== "getProfile") {
    return;
  }

  requestService(PROFILE_PROVIDER.profilePath)
    .then(({ profile }) => sendResponse({ ok: true, profile }))
    .catch((error) => sendResponse({ ok: false, error: error.message }));
  return true;
});
