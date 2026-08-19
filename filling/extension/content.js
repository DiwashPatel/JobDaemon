const SENSITIVE_TERMS = [
  "password", "social security", "ssn", "tax id", "national id", "bank account", "routing number",
  "credit card", "date of birth", "birthday", "gender", "race", "ethnicity", "disability",
  "veteran", "sexual orientation", "captcha", "criminal history", "conviction"
];

const FIELD_RULES = [
  ["full_name", ["full name", "legal name", "your name"]],
  ["first_name", ["first name", "given name", "firstname"]],
  ["last_name", ["last name", "family name", "surname", "lastname"]],
  ["preferred_name", ["preferred name", "nickname"]],
  ["email", ["email", "e mail"]],
  ["phone", ["phone", "mobile", "telephone"]],
  ["address_line_2", ["address line 2", "apartment", "suite", "unit"]],
  ["address_line_1", ["address line 1", "street address", "mailing address", "permanent address", "address"]],
  ["city", ["city", "town"]],
  ["state", ["state", "province", "region"]],
  ["postal_code", ["zip code", "zip postal code", "postal code", "zipcode", "zip"]],
  ["country", ["country"]],
  ["linkedin", ["linkedin"]],
  ["github", ["github"]],
  ["website", ["portfolio", "personal website", "website", "homepage"]],
  ["school", ["school", "university", "college", "institution"]],
  ["degree_level", ["highest degree", "degree level"]],
  ["degree", ["degree", "degree type"]],
  ["major", ["major", "field of study", "concentration", "discipline"]],
  ["minor", ["minor"]],
  ["graduation_month", ["graduation month", "expected graduation month", "anticipated graduation month"]],
  ["graduation_year", ["graduation year", "expected graduation year", "anticipated graduation year"]],
  ["graduation_date", ["graduation date", "expected graduation", "anticipated graduation"]],
  ["gpa", ["gpa", "grade point"]],
  ["skills", ["technical skills", "skills"]],
  ["interests", ["interests"]],
  ["experience_company", ["employer", "company"]],
  ["experience_title", ["job title", "position title", "role"]],
  ["experience_location", ["work location", "job location"]],
  ["experience_start_date", ["start date", "from date"]],
  ["experience_end_date", ["end date", "to date"]],
  ["experience_description", ["responsibilities", "accomplishments", "description"]],
  ["project_name", ["project name", "project title"]],
  ["project_url", ["project url", "project link", "project website"]],
  ["project_date", ["project date", "project completion date"]],
  ["project_description", ["project description", "project details"]]
];

// This is a value-normalization table, not a job-site rule. It lets a profile
// containing "CA" safely select a native option labelled "California", and vice versa.
const US_STATES = new Map([
  ["al", "alabama"], ["ak", "alaska"], ["az", "arizona"], ["ar", "arkansas"], ["ca", "california"], ["co", "colorado"], ["ct", "connecticut"], ["de", "delaware"], ["fl", "florida"], ["ga", "georgia"], ["hi", "hawaii"], ["id", "idaho"], ["il", "illinois"], ["in", "indiana"], ["ia", "iowa"], ["ks", "kansas"], ["ky", "kentucky"], ["la", "louisiana"], ["me", "maine"], ["md", "maryland"], ["ma", "massachusetts"], ["mi", "michigan"], ["mn", "minnesota"], ["ms", "mississippi"], ["mo", "missouri"], ["mt", "montana"], ["ne", "nebraska"], ["nv", "nevada"], ["nh", "new hampshire"], ["nj", "new jersey"], ["nm", "new mexico"], ["ny", "new york"], ["nc", "north carolina"], ["nd", "north dakota"], ["oh", "ohio"], ["ok", "oklahoma"], ["or", "oregon"], ["pa", "pennsylvania"], ["ri", "rhode island"], ["sc", "south carolina"], ["sd", "south dakota"], ["tn", "tennessee"], ["tx", "texas"], ["ut", "utah"], ["vt", "vermont"], ["va", "virginia"], ["wa", "washington"], ["wv", "west virginia"], ["wi", "wisconsin"], ["wy", "wyoming"], ["dc", "district of columbia"]
]);

let proposalCache = new Map();
let nextProposalId = 1;

function normalize(value) {
  return (value || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

function visibleText(element) {
  return normalize(element?.innerText || element?.textContent || "");
}

function labelFor(element) {
  const parts = [element.getAttribute("aria-label"), element.placeholder, element.name, element.id];
  if (element.labels) parts.push(...Array.from(element.labels).map((label) => label.innerText));
  const labelledBy = element.getAttribute("aria-labelledby");
  if (labelledBy) {
    labelledBy.split(/\s+/).forEach((id) => parts.push(document.getElementById(id)?.innerText));
  }
  const nearestLabel = element.closest("label");
  if (nearestLabel) parts.push(nearestLabel.innerText);
  const group = element.closest("fieldset, [role=group], .form-group, .field");
  if (group) parts.push(group.querySelector("legend, label, [class*=label]")?.innerText);
  return normalize(parts.filter(Boolean).join(" "));
}

function displayLabel(element) {
  if (element.labels?.length) return element.labels[0].innerText.trim();
  const nearestLabel = element.closest("label");
  if (nearestLabel) return nearestLabel.innerText.trim();
  return element.getAttribute("aria-label") || element.placeholder || element.name || "Unlabelled field";
}

function keyFor(label) {
  if (SENSITIVE_TERMS.some((term) => label.includes(term))) return "sensitive";
  for (const [key, terms] of FIELD_RULES) {
    if (terms.some((term) => label.includes(term))) return key;
  }
  return null;
}

function canonicalState(value) {
  const normalized = normalize(value);
  if (US_STATES.has(normalized)) return US_STATES.get(normalized);
  for (const name of US_STATES.values()) {
    if (normalized === name) return name;
  }
  return normalized;
}

function nativeDateValue(element, value) {
  const date = String(value).trim();
  if (element.type === "date") return /^\d{4}-\d{2}-\d{2}$/.test(date) ? date : "";
  if (element.type === "month") return /^\d{4}-\d{2}$/.test(date) ? date : "";
  return date;
}

function indexedValue(profile, key, index) {
  const personal = profile.personal || {};
  const education = (profile.education || [])[index] || (profile.education || [])[0] || {};
  const experience = (profile.experience || [])[index] || {};
  const project = (profile.projects || [])[index] || {};
  const graduation = education.graduation_date || "";
  const degree = education.degree || "";
  const isoMonth = graduation.match(/^\d{4}-(\d{2})/)?.[1] || "";
  const values = {
    ...personal,
    ...(profile.links || {}),
    full_name: [personal.first_name, personal.last_name].filter(Boolean).join(" "),
    degree_level: degree.toLowerCase().includes("bachelor") ? "Bachelor's" : degree,
    school: education.school,
    degree,
    major: education.major,
    minor: education.minor,
    graduation_date: graduation,
    graduation_month: isoMonth || graduation.match(/[A-Za-z]+/)?.[0] || "",
    graduation_year: graduation.match(/\b\d{4}\b/)?.[0] || "",
    gpa: education.gpa,
    skills: (profile.skills || []).join(", "),
    interests: (profile.interests || []).join(", "),
    experience_company: experience.company,
    experience_title: experience.title,
    experience_location: experience.location,
    experience_start_date: experience.start_date,
    experience_end_date: experience.end_date,
    experience_description: experience.description,
    project_name: project.name,
    project_url: project.url,
    project_date: project.date,
    project_description: project.description
  };
  return values[key] || "";
}

function explicitAnswer(profile, element, label) {
  const control = element.type === "checkbox" ? "checkbox" : element.type === "radio" ? "radio" : element instanceof HTMLSelectElement ? "select" : null;
  if (!control) return null;
  return (profile.application_answers || []).find((answer) => {
    const patterns = (answer.label_patterns || []).map(normalize).filter(Boolean);
    return answer.control === control && patterns.length && patterns.every((pattern) => label.includes(pattern));
  }) || null;
}

function matchingSelectOption(element, value, key) {
  const wanted = normalize(value);
  const exact = Array.from(element.options).find((option) => normalize(option.value) === wanted || normalize(option.text) === wanted);
  if (exact) return exact;
  if (key !== "state") return null;
  const canonical = canonicalState(value);
  return Array.from(element.options).find((option) => canonicalState(option.value) === canonical || canonicalState(option.text) === canonical) || null;
}

function isCustomCombobox(element) {
  return element.getAttribute("role") === "combobox" && !(element instanceof HTMLSelectElement);
}

function isEmpty(element) {
  if (element instanceof HTMLSelectElement) return !element.value;
  if (element.type === "checkbox" || element.type === "radio") return true;
  return !element.value;
}

function makeMatch(element, key, value, label) {
  const id = `proposal-${nextProposalId++}`;
  const match = { id, element, key, value: String(value), label: displayLabel(element) };
  proposalCache.set(id, match);
  return match;
}

function scan(profile) {
  proposalCache = new Map();
  const counters = new Map();
  const summary = { fillable: 0, existing: 0, fileInputs: 0, sensitive: 0 };
  const matches = [];
  const controls = Array.from(new Set(document.querySelectorAll("input, textarea, select, [role=combobox]")));

  for (const element of controls) {
    if (element.disabled || element.readOnly || element.type === "hidden") continue;
    if (element.type === "submit" || element.type === "button" || element.type === "reset") continue;
    if (element.type === "file") { summary.fileInputs += 1; continue; }
    if (element.type === "password") { summary.sensitive += 1; continue; }

    const label = labelFor(element);
    const key = keyFor(label);
    if (key === "sensitive") { summary.sensitive += 1; continue; }
    if (!isEmpty(element) && element.type !== "checkbox" && element.type !== "radio") { summary.existing += 1; continue; }

    const explicit = explicitAnswer(profile, element, label);
    if (explicit) {
      if (element.type === "radio") {
        const optionLabel = visibleText(document.querySelector(`label[for="${CSS.escape(element.id)}"]`) || element.closest("label"));
        if (normalize(explicit.value) !== optionLabel) continue;
      }
      matches.push(makeMatch(element, "explicit_answer", explicit.value, label));
      summary.fillable += 1;
      continue;
    }

    if (!key) continue;
    if (element.type === "checkbox" || element.type === "radio") continue;
    const index = counters.get(key) || 0;
    counters.set(key, index + 1);
    const educationKey = ["school", "degree", "major", "minor", "graduation_date", "graduation_month", "graduation_year", "gpa"].includes(key);
    const experienceKey = key.startsWith("experience_");
    const projectKey = key.startsWith("project_");
    const value = indexedValue(profile, key, educationKey || experienceKey || projectKey ? index : 0);
    if (!value) continue;
    const safeValue = nativeDateValue(element, value);
    if (!safeValue) continue;
    if (element instanceof HTMLSelectElement && !matchingSelectOption(element, safeValue, key)) continue;
    if (isCustomCombobox(element) && !safeValue) continue;
    matches.push(makeMatch(element, key, safeValue, label));
    summary.fillable += 1;
  }
  return { matches: matches.map(({ element, ...match }) => match), summary };
}

function setTextValue(element, value) {
  const prototype = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
  setter?.call(element, value);
  element.dispatchEvent(new Event("input", { bubbles: true }));
  element.dispatchEvent(new Event("change", { bubbles: true }));
  element.dispatchEvent(new Event("blur", { bubbles: true }));
}

function setSelectValue(element, value, key) {
  const option = matchingSelectOption(element, value, key);
  if (!option) return false;
  element.value = option.value;
  element.dispatchEvent(new Event("input", { bubbles: true }));
  element.dispatchEvent(new Event("change", { bubbles: true }));
  return true;
}

function setComboboxValue(element, value, key) {
  const wanted = key === "state" ? canonicalState(value) : normalize(value);
  element.click();
  element.focus();
  const options = Array.from(document.querySelectorAll('[role="option"]')).filter((option) => {
    const optionValue = key === "state" ? canonicalState(option.textContent) : normalize(option.textContent);
    return optionValue === wanted && option.offsetParent !== null;
  });
  if (options.length !== 1) return false;
  options[0].click();
  return true;
}

function applyMatch(match) {
  const element = match.element;
  if (!element?.isConnected || element.disabled || element.readOnly) return false;
  if (element instanceof HTMLSelectElement) return !element.value && setSelectValue(element, match.value, match.key);
  if (isCustomCombobox(element)) return setComboboxValue(element, match.value, match.key);
  if (element.type === "checkbox") {
    const desired = match.value === true || normalize(match.value) === "true" || normalize(match.value) === "yes";
    if (element.checked === desired) return false;
    element.checked = desired;
  } else if (element.type === "radio") {
    if (element.checked) return false;
    element.checked = true;
  } else {
    if (element.value) return false;
    setTextValue(element, match.value);
    return true;
  }
  element.dispatchEvent(new Event("input", { bubbles: true }));
  element.dispatchEvent(new Event("change", { bubbles: true }));
  return true;
}

function valuesMatch(element, match) {
  if (!element?.isConnected) return { status: "missing" };
  if (isCustomCombobox(element)) return { status: "needs_review" };
  if (element instanceof HTMLSelectElement) {
    const selected = element.options[element.selectedIndex];
    if (!selected) return { status: "failed" };
    const matches = match.key === "state"
      ? canonicalState(selected.value || selected.text) === canonicalState(match.value)
      : normalize(selected.value) === normalize(match.value) || normalize(selected.text) === normalize(match.value);
    return { status: matches ? "verified" : "failed" };
  }
  if (element.type === "checkbox") {
    const expected = normalize(match.value) === "true" || normalize(match.value) === "yes";
    return { status: element.checked === expected ? "verified" : "failed" };
  }
  if (element.type === "radio") return { status: element.checked ? "verified" : "failed" };
  return { status: String(element.value || "") === match.value ? "verified" : "failed" };
}

globalThis.JobDaemonFiller = {
  getMatch: (id) => proposalCache.get(id),
  valuesMatch
};

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type === "scanForm") sendResponse(scan(message.profile));
  if (message.type === "fillApproved") {
    let filled = 0;
    let skipped = 0;
    for (const id of message.selectedIds || []) {
      if (applyMatch(proposalCache.get(id))) filled += 1;
      else skipped += 1;
    }
    sendResponse({ filled, skipped });
  }
  return true;
});
