/**
 * popup.js — Milestone 3 (fixed)
 *
 * Profile source: GET {API}/api/v1/get-session
 * No chrome.storage complexity — backend holds the session.
 *
 * Flow:
 *   1. Fetch profile from backend session
 *   2. Scan active tab for form fields (via content script)
 *   3. Send fields + profile to backend AI for mapping
 *   4. Show operator which fields will be filled
 *   5. Click Fill → content script fills the form
 */

"use strict";

// Auto-detects which backend to use — tries your local dev server
// first (fast timeout), falls back to the deployed Railway backend if
// nothing's running locally. This means you never have to remember to
// manually flip this between local testing and normal day-to-day use —
// whichever one is actually reachable gets used automatically.
const LOCAL_API = "http://127.0.0.1:8000";
const RAILWAY_API = "https://web-production-a52e0.up.railway.app";
let API = RAILWAY_API;  // default until resolveApiBase() runs

async function resolveApiBase() {
  try {
    const r = await fetch(`${LOCAL_API}/api/v1/health`, {
      signal: AbortSignal.timeout(1200),
    });
    if (r.ok) {
      API = LOCAL_API;
      console.log("[SmartFill AI] Using local backend:", LOCAL_API);
      return;
    }
  } catch {
    // local backend not reachable — fall through to Railway
  }
  API = RAILWAY_API;
  console.log("[SmartFill AI] Using Railway backend:", RAILWAY_API);
}

const PROFILE_DISPLAY = [
  { key: "name",            label: "Name" },
  { key: "father_name",     label: "Father" },
  { key: "dob",             label: "DOB" },
  { key: "gender",          label: "Gender" },
  { key: "mobile",          label: "Mobile" },
  { key: "email",           label: "Email" },
  { key: "aadhaar_number",  label: "Aadhaar" },
  { key: "pan_number",      label: "PAN" },
  { key: "passport_number", label: "Passport" },
  { key: "dl_number",       label: "DL" },
  { key: "address",         label: "Address" },
  { key: "degree",          label: "Degree" },
  { key: "university",      label: "University" },
  { key: "account_number",  label: "Account" },
  { key: "ifsc",            label: "IFSC" },
];

const ID_FIELDS = new Set(["aadhaar_number", "pan_number", "passport_number", "account_number"]);

let state = {
  profile: null,
  scannedFields: [],
  mapping: {},
};

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", async () => {
  await resolveApiBase();
  await checkHealth();
  bindEvents();
  await loadProfile();
});

function bindEvents() {
  document.getElementById("openAppBtn").addEventListener("click", () => {
    chrome.tabs.create({ url: `${API}/app` });
  });
  document.getElementById("clearProfileBtn").addEventListener("click", clearProfile);
  document.getElementById("fillBtn").addEventListener("click", fillForm);
  document.getElementById("rescanBtn").addEventListener("click", rescan);
}

// ── Health + backend check ────────────────────────────────────────────────────
async function checkHealth() {
  try {
    const r = await fetch(`${API}/api/v1/health`, {
      signal: AbortSignal.timeout(3000),
    });
    const ok = r.ok;
    document.getElementById("statusDot").className = `status-dot ${ok ? "online" : "offline"}`;
    document.getElementById("statusLabel").textContent = ok ? "Backend online" : "Offline";
    return ok;
  } catch {
    document.getElementById("statusDot").className = "status-dot offline";
    document.getElementById("statusLabel").textContent = "Backend offline";
    return false;
  }
}

// ── Load profile from backend session ─────────────────────────────────────────
async function loadProfile() {
  setPageStatus("⏳", "Loading profile…");
  try {
    const r = await fetch(`${API}/api/v1/get-session`, {
      credentials: "include",
      signal: AbortSignal.timeout(5000),
    });
    if (!r.ok) throw new Error("Backend error");
    const data = await r.json();

    if (data.ok && data.profile && Object.keys(data.profile).length > 0) {
      state.profile = data.profile;

      // Synthesize a generic 'marks_identification' key directly onto
      // state.profile (not a local copy) so it's available everywhere
      // that reads state.profile — the mapping preview (renderMapping),
      // the actual fill payload sent to content.js, and any future
      // matching logic. Prefers the most senior certificate's value
      // (degree > inter > ssc) since government forms typically only
      // have ONE generic "Identification Marks" field, not one per
      // certificate.
      if (!state.profile.marks_identification) {
        for (const k of ["degree_marks_identification", "inter_marks_identification", "ssc_marks_identification"]) {
          if (state.profile[k]) {
            state.profile.marks_identification = state.profile[k];
            break;
          }
        }
      }

      showReadyScreen();
      await scanAndMap();
    } else {
      showEmptyScreen();
    }
  } catch (err) {
    // Backend offline or no session
    showEmptyScreen();
    if (err.name !== "TypeError") {
      // TypeError = fetch failed = backend offline (already shown in status)
      setPageStatus("❌", "Could not reach backend.");
    }
  }
}

async function clearProfile() {
  try {
    await fetch(`${API}/api/v1/clear-session`, { method: "DELETE", credentials: "include" });
  } catch {}
  state.profile = null;
  state.mapping = {};
  state.scannedFields = [];
  showEmptyScreen();
}

// ── Screens ───────────────────────────────────────────────────────────────────
function showEmptyScreen() {
  document.getElementById("screenEmpty").classList.remove("hidden");
  document.getElementById("screenReady").classList.add("hidden");
}

function showReadyScreen() {
  document.getElementById("screenEmpty").classList.add("hidden");
  document.getElementById("screenReady").classList.remove("hidden");
  const ps = document.getElementById("pageStatus");
  if (ps) ps.classList.remove("hidden");
  renderProfileCard();
}

function renderProfileCard() {
  const p = state.profile;
  if (!p) return;

  document.getElementById("profileName").textContent = p.name || "Customer";

  // Profile from get-session is a flat dict of plain strings
  // (result of getFinalProfile() from review.html)
  const populated = PROFILE_DISPLAY.filter(f => {
    const v = p[f.key];
    return v && typeof v === "string" && v.trim().length > 0;
  });
  document.getElementById("profileMeta").textContent = `${populated.length} fields ready`;

  const container = document.getElementById("profileFields");
  container.innerHTML = populated.map(({ key, label }) => {
    const val = String(p[key] || "");
    const isId = ID_FIELDS.has(key);
    const display = isId && val.length > 4 ? "•••• " + val.slice(-4) : val;
    return `<span class="field-pill ${isId ? "id" : ""}">${label}: ${esc(display)}</span>`;
  }).join("");

  renderAssetThumbnails(p);
}

function renderAssetThumbnails(p) {
  const wrap = document.getElementById("profileAssets");
  const items = [];
  if (p.applicant_photo)     items.push({ label: "Photo", src: p.applicant_photo });
  if (p.applicant_signature) items.push({ label: "Signature", src: p.applicant_signature });

  if (!items.length) {
    wrap.classList.add("hidden");
    wrap.innerHTML = "";
    return;
  }
  wrap.classList.remove("hidden");
  wrap.innerHTML = items.map(i =>
    `<div class="asset-thumb-chip"><img src="${i.src}" alt="${i.label}"/><span>${i.label}</span></div>`
  ).join("");
}

// ── Scan + AI map ─────────────────────────────────────────────────────────────
async function scanAndMap() {
  setPageStatus("🔍", "Scanning page for form fields…");
  hideMapping();
  hideFillResult();
  setFillBtn(false);
  showError("");

  // Get current active tab
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) {
    setPageStatus("❌", "No active tab found.");
    return;
  }

  // Skip browser internal pages only
  if (!tab.url ||
      tab.url.startsWith("chrome://") ||
      tab.url.startsWith("chrome-extension://") ||
      tab.url === "about:blank" ||
      tab.url === "about:newtab") {
    setPageStatus("🌐", "Open an application form website, then click Rescan.");
    return;
  }

  // Always inject content script fresh — handles:
  //   - Pages loaded before extension was installed
  //   - Pages that blocked auto-injection
  //   - UIDAI and other govt sites with strict CSP
  try {
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ["content.js"],
    });
    await sleep(300);  // let script fully initialise
  } catch (injErr) {
    // Injection may fail on some pages (PDF, chrome:// etc)
    // Try sending message anyway — script might already be there
    await sleep(100);
  }

  // Scan fields — retry once if first attempt fails
  let scanResp;
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      scanResp = await chrome.tabs.sendMessage(tab.id, { type: "DO_SCAN" });
      if (scanResp?.fields) break;
    } catch (err) {
      if (attempt === 0) {
        await sleep(500);  // wait and retry
      } else {
        setPageStatus("⚠️", "Cannot read this page. Try: right-click → Inspect → reload, then Rescan.");
        return;
      }
    }
  }

  if (!scanResp?.fields?.length) {
    setPageStatus("📋", "No fillable form fields found on this page.");
    document.getElementById("noFieldsMsg").classList.remove("hidden");
    return;
  }

  state.scannedFields = scanResp.fields;
  setPageStatus("⚡", `Found ${scanResp.fields.length} fields — mapping…`);

  // AI field mapping via backend
  try {
    const mapResp = await fetch(`${API}/api/v1/map-fields`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        profile: state.profile,
        fields: scanResp.fields.map(f => ({
          id: f.id,
          label: f.label,
          type: f.type,
          name: f.name || "",
          placeholder: f.placeholder || "",
        })),
      }),
      signal: AbortSignal.timeout(10000),
    });

    const mapData = await mapResp.json();
    state.mapping = mapData.mapping || {};

    const matchCount = Object.keys(state.mapping).length;
    setPageStatus(
      matchCount > 0 ? "✅" : "⚠️",
      matchCount > 0
        ? `${matchCount} field${matchCount !== 1 ? "s" : ""} ready to fill`
        : "No matching fields found"
    );

    renderMapping(scanResp.fields, state.mapping);
    setFillBtn(matchCount > 0);

  } catch (err) {
    setPageStatus("⚠️", "Mapping failed — using basic matching.");
    // Use keyword fallback built into backend
    state.mapping = buildKeywordMapping(scanResp.fields, state.profile);
    renderMapping(scanResp.fields, state.mapping);
    setFillBtn(Object.keys(state.mapping).length > 0);
  }
}

// ── Local keyword fallback (if backend unreachable) ───────────────────────────
function buildKeywordMapping(fields, profile) {
  // Ordered by specificity — longer/more specific keywords first
  const KEYWORDS = {
    name:             ["applicant name","full name","candidate name","student name","name of"],
    father_name:      ["father name","father's name","guardian name","father","s/o","d/o"],
    mother_name:      ["mother name","mother's name","mother"],
    gender:           ["gender","sex"],
    dob:              ["date of birth","birth date","birthdate","d.o.b","dob"],
    mobile:           ["mobile number","mobile no","phone number","phone no",
                       "contact number","contact no","whatsapp","mobile","phone","cell"],
    email:            ["email address","email id","e-mail address","email","e-mail","mail id"],
    address:          ["permanent address","residential address","address"],
    aadhaar_number:   ["aadhaar number","aadhaar no","aadhar number","aadhaar","aadhar","uid"],
    pan_number:       ["pan number","pan card no","permanent account number","pan"],
    passport_number:  ["passport number","passport no","passport"],
    doi:              ["date of issue","issue date"],
    doe:              ["date of expiry","expiry date","valid till","valid upto","expiry"],
    dl_number:        ["driving licence number","driving licence","dl number","dl no"],
    voter_id:         ["voter id","epic number","voter"],
    degree:           ["degree name","course name","degree"],
    specialization:   ["specialization","branch","stream"],
    university:       ["university name","university"],
    college:          ["college name","college"],
    year_of_passing:  ["year of passing","pass year"],
    percentage:       ["percentage","marks percentage","cgpa"],
    account_number:   ["account number","bank account number","acc no"],
    ifsc:             ["ifsc code","ifsc"],
    bank_name:        ["bank name"],
    marks_identification: [
      "visible identification marks","identification marks","identification mark",
      "distinguishing marks","distinguishing mark","visible marks",
    ],
  };

  // File-upload fields (photo/signature) — handled separately below since
  // the generic SKIP list (which blocks "upload") would otherwise hide them.
  const ASSET_KEYWORDS = {
    applicant_signature: ["applicant signature","upload signature","candidate signature","upload your signature","signature"],
    applicant_photo: ["applicant photo","upload photo","photograph","passport size photo","recent photograph","candidate photo","photo"],
  };

  // Synthesize a generic 'marks_identification' value for forms with ONE
  // combined "Identification Marks" field (the common case) rather than
  // separate per-certificate fields. Prefers the most senior certificate's
  // value (degree > inter > ssc), falling back to whichever is populated.
  if (!profile.marks_identification) {
    profile = { ...profile };
    for (const k of ["degree_marks_identification", "inter_marks_identification", "ssc_marks_identification"]) {
      if (profile[k]) { profile.marks_identification = profile[k]; break; }
    }
  }

  const SKIP = ["captcha","otp","password","confirm","retype","submit","search","upload"];

  const mapping = {};
  fields.forEach(f => {
    const combined = `${f.label} ${f.name||""} ${f.placeholder||""}`.toLowerCase();

    // File inputs (photo/signature upload) — matched before the generic
    // SKIP list so "upload photo" isn't discarded by the "upload" keyword.
    if (f.type === "file") {
      if (profile.applicant_signature && ASSET_KEYWORDS.applicant_signature.some(k => combined.includes(k))) {
        mapping[f.id] = "applicant_signature";
      } else if (profile.applicant_photo && ASSET_KEYWORDS.applicant_photo.some(k => combined.includes(k))) {
        mapping[f.id] = "applicant_photo";
      }
      return;
    }

    // Skip sensitive/non-fillable fields
    if (SKIP.some(s => combined.includes(s))) return;

    let bestKey = null, bestScore = 0;
    for (const [key, kws] of Object.entries(KEYWORDS)) {
      if (!profile[key]) continue;
      for (const kw of kws) {
        if (combined.includes(kw) && kw.length > bestScore) {
          bestScore = kw.length;
          bestKey = key;
        }
      }
    }
    if (bestKey) mapping[f.id] = bestKey;
  });
  return mapping;
}

async function rescan() {
  document.getElementById("noFieldsMsg").classList.add("hidden");
  await scanAndMap();
}

// ── Render mapping preview ────────────────────────────────────────────────────
function renderMapping(fields, mapping) {
  const section = document.getElementById("mappingSection");
  const list = document.getElementById("mappingList");
  section.classList.remove("hidden");

  const matched = fields.filter(f => mapping[f.id]);
  if (!matched.length) {
    list.innerHTML = '<div style="color:var(--text-3);font-size:11px;text-align:center;padding:8px 0">No matching fields found on this page</div>';
    return;
  }

  const ASSET_KEYS = new Set(["applicant_photo", "applicant_signature"]);
  list.innerHTML = matched.map(f => {
    const key = mapping[f.id];
    const val = state.profile[key] || "";
    const displayVal = ASSET_KEYS.has(key)
      ? "🖼 Image attached"
      : ID_FIELDS.has(key) && val.length > 4
      ? "•••• " + val.slice(-4) : val;
    return `
      <div class="mapping-row matched">
        <span class="map-from" title="${esc(f.label)}">${esc(truncate(f.label, 20))}</span>
        <span class="map-arrow">→</span>
        <span class="map-to matched" title="${esc(val)}">${esc(truncate(displayVal, 18))}</span>
      </div>`;
  }).join("");
}

// ── Fill form ─────────────────────────────────────────────────────────────────
async function fillForm() {
  if (!state.profile || !Object.keys(state.mapping).length) return;

  const btn = document.getElementById("fillBtn");
  btn.textContent = "Filling…";
  btn.disabled = true;
  hideFillResult();

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) {
    showError("No active tab.");
    btn.textContent = "Fill Form";
    btn.disabled = false;
    return;
  }

  try {
    console.log("[SmartFill DEBUG] state.profile.aadhaar_number at fill time:", state.profile.aadhaar_number);
    const resp = await chrome.tabs.sendMessage(tab.id, {
      type: "DO_FILL",
      profile: state.profile,
      options: { mapping: state.mapping },
    });

    showFillResult(resp?.filled || 0, resp?.failed || 0);
  } catch (err) {
    showError("Fill failed: " + err.message + ". Try refreshing the page.");
  }

  btn.textContent = "Fill Form";
  btn.disabled = false;
}

// ── UI helpers ────────────────────────────────────────────────────────────────
function showFillResult(filled, failed) {
  const el = document.getElementById("fillResult");
  el.classList.remove("hidden");
  if (filled > 0) {
    el.style.cssText = "background:var(--green-dim);border-color:rgba(34,197,94,0.25);color:var(--green)";
    el.textContent = `✓ Filled ${filled} field${filled !== 1 ? "s" : ""} successfully!${failed > 0 ? ` (${failed} skipped)` : ""}`;
  } else {
    el.style.cssText = "background:var(--amber-dim);border-color:rgba(245,158,11,0.25);color:var(--amber)";
    el.textContent = "No fields filled — try Rescan or refresh the page.";
  }
}

function setPageStatus(icon, text) {
  document.getElementById("pageStatusIcon").textContent = icon;
  document.getElementById("pageStatusText").textContent = text;
}

function setFillBtn(enabled) {
  document.getElementById("fillBtn").disabled = !enabled;
}

function hideMapping() {
  document.getElementById("mappingSection").classList.add("hidden");
  document.getElementById("noFieldsMsg").classList.add("hidden");
}

function hideFillResult() {
  document.getElementById("fillResult").classList.add("hidden");
}

function showError(msg) {
  const el = document.getElementById("errorBanner");
  if (!msg) { el.classList.add("hidden"); return; }
  el.textContent = `⚠ ${msg}`;
  el.classList.remove("hidden");
  setTimeout(() => el.classList.add("hidden"), 6000);
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function truncate(s, n) { return s.length > n ? s.slice(0, n-1) + "…" : s; }
function esc(s) {
  return String(s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}
