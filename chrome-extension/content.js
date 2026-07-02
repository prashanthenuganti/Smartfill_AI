/**
 * content.js — SmartFill AI v3.1
 *
 * Portal-aware form scanner and filler.
 * Handles: Angular (UIDAI), React (NSDL, SSC), plain HTML, iframe forms.
 *
 * Government portals tested:
 *   myaadhaar.uidai.gov.in  — Angular, input validation on keypress
 *   onlineservices.nsdl.com — jQuery/plain HTML
 *   passport.gov.in         — Java Struts / plain HTML
 *   coforge / eDistrict     — React or Angular
 *   ssc.gov.in              — React (controlled inputs, needs valueTracker reset)
 */

"use strict";

// ── Guard against double-injection ──────────────────────────────────────────
// popup.js re-injects this file on every Rescan/Fill click (by design, to
// handle pages loaded before the extension or with strict CSP). Without this
// guard, the second injection throws "Identifier 'PORTAL' has already been
// declared" because const/let can't be redeclared in the same page context.
if (window.__smartfillInjected) {
  console.log("[SmartFill AI] Already injected — skipping re-execution");
} else {
  window.__smartfillInjected = true;

// ── Detect portal framework ───────────────────────────────────────────────────
const PORTAL = (function() {
  const host = location.hostname;
  if (host.includes("uidai") || host.includes("myaadhaar")) return "angular";
  if (host.includes("nsdl"))     return "jquery";
  if (host.includes("passport")) return "struts";
  if (host.includes("digilocker")) return "react";
  if (host.includes("ssc.gov.in")) return "angular"; // confirmed via DOM: _ngcontent-* attrs
  return "auto"; // detect dynamically
})();

// ── Message listener ──────────────────────────────────────────────────────────
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "PING") {
    sendResponse({ ok: true });
    return false;
  } else if (message.type === "DO_SCAN") {
    sendResponse({ ok: true, fields: scanFields() });
    return false;
  } else if (message.type === "DO_FILL") {
    // fillForm is async (custom dropdowns need to wait for the options
    // panel to render before clicking) — must return true here to tell
    // Chrome the response will arrive asynchronously, otherwise the
    // message channel closes before sendResponse fires.
    fillForm(message.profile, message.options || {})
      .then(result => sendResponse({ ok: true, ...result }))
      .catch(err => sendResponse({ ok: false, error: err.message, filled: 0, failed: 0 }));
    return true;
  } else if (message.type === "DO_HIGHLIGHT") {
    (message.fieldIds || []).forEach(sel => {
      try { flashField(document.querySelector(sel), "highlight"); } catch {}
    });
    sendResponse({ ok: true });
    return false;
  }
  return false;
});


// ── Field scanner ─────────────────────────────────────────────────────────────
function scanFields() {
  const fields = [];
  const seen = new Set();
  const selector = [
    'input:not([type="hidden"]):not([type="submit"])',
    ':not([type="button"]):not([type="reset"])',
    ':not([type="checkbox"]):not([type="radio"])',
    ':not([type="image"])',
    ", textarea, select",
  ].join("");

  document.querySelectorAll(selector).forEach((el, idx) => {
    if (!isVisible(el)) return;

    // Skip fields that look like captcha / OTP / password
    const combined = [
      el.id, el.name, el.placeholder,
      el.getAttribute("aria-label") || "",
    ].join(" ").toLowerCase();

    if (/captcha|recaptcha|otp|password|pass_word/.test(combined)) return;

    // CRITICAL FIX: some government portals (confirmed on ssc.gov.in) have
    // DUPLICATE ids on different fields due to copy-pasted component code
    // (e.g. two completely different fields both using id="txtid").
    // document.querySelector('#txtid') always returns the FIRST match in
    // the DOM, so if we blindly trust `el.id` as a unique selector, the
    // SECOND field with that same id gets a selector that actually
    // resolves to the WRONG element at fill time — the field appears
    // correctly in the scan/mapping preview (since we're looking at the
    // right `el` right now), but silently fails to fill because
    // querySelector('#txtid') during fillForm() returns a different,
    // often disabled/readonly, element instead.
    //
    // Fix: only trust el.id as a selector if it's verified unique on the
    // page. Otherwise fall back to the data-sf attribute, which we set
    // directly on THIS element and is therefore guaranteed to resolve
    // back to the exact same node later.
    const idIsUnique = el.id && document.querySelectorAll(`#${CSS.escape(el.id)}`).length === 1;

    const stableId = idIsUnique
      ? `#${CSS.escape(el.id)}`
      : el.name
      ? `[name="${CSS.escape(el.name)}"]`
      : (() => {
          // data-sf fallback — also used when id exists but is duplicated.
          // Without writing this attribute, fillForm()'s later
          // querySelector('[data-sf="N"]') would return null — fields
          // without a unique native id/name (or with a duplicated id)
          // would silently fail to fill even though they show up
          // correctly in the scan + mapping preview.
          el.setAttribute("data-sf", String(idx));
          return `[data-sf="${idx}"]`;
        })();

    if (seen.has(stableId)) return;
    seen.add(stableId);

    const label = getLabel(el);
    if (!label || label.length < 2) return;

    fields.push({
      id:          stableId,
      label:       label,
      type:        el.tagName === "SELECT" ? "select" : (el.type || "text"),
      name:        el.name  || "",
      placeholder: el.placeholder || "",
      ariaLabel:   el.getAttribute("aria-label") || "",
      currentValue: el.value || "",
    });
  });

  // ── Custom dropdown detection ────────────────────────────────────────────
  // Some Angular portals (confirmed on ssc.gov.in) use a custom component
  // (e.g. <app-dropdown>) instead of a native <select> or Angular Material
  // <mat-select>. These render as a clickable trigger div showing "Select",
  // and clicking it reveals a <ul>/<div> of plain <li> options with no
  // special role/aria attributes. They're invisible to the native-field
  // selector above, so we detect them separately here by their visible
  // "Select" placeholder text plus a dropdown-arrow icon pattern.
  const customDropdownCandidates = Array.from(
    document.querySelectorAll('app-dropdown, [class*="dropdown" i], [class*="select" i]')
  );

  customDropdownCandidates.forEach((el, idx) => {
    if (!isVisible(el)) return;
    if (el.tagName === "SELECT" || el.tagName === "INPUT" || el.tagName === "TEXTAREA") return;

    // Heuristic: must look like a closed dropdown trigger — short visible
    // text (placeholder like "Select"), and must contain a chevron/arrow
    // icon or have an associated options list elsewhere in the DOM.
    const text = clean(el.textContent);
    const looksLikeTrigger = /^select\b/i.test(text) && text.length < 30;
    if (!looksLikeTrigger) return;

    // Same duplicate-id protection as the native field scanner above —
    // only trust el.id if it's actually unique on the page.
    const ddIdIsUnique = el.id && document.querySelectorAll(`#${CSS.escape(el.id)}`).length === 1;

    const stableId = ddIdIsUnique
      ? `#${CSS.escape(el.id)}`
      : (() => {
          el.setAttribute("data-sf-dd", String(idx));
          return `[data-sf-dd="${idx}"]`;
        })();

    if (seen.has(stableId)) return;
    seen.add(stableId);

    const label = getLabel(el);
    if (!label || label.length < 2) return;

    fields.push({
      id:          stableId,
      label:       label,
      type:        "custom-dropdown",
      name:        el.getAttribute("formcontrolname") || "",
      placeholder: text,
      ariaLabel:   el.getAttribute("aria-label") || "",
      currentValue: "",
    });
  });

  return fields;
}

// ── Label detection — 8 strategies ───────────────────────────────────────────
function getLabel(el) {
  // 1. <label for="id">
  if (el.id) {
    const lbl = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
    if (lbl) return clean(lbl.textContent);
  }
  // 2. aria-label
  const al = el.getAttribute("aria-label");
  if (al) return clean(al);

  // 3. aria-labelledby
  const alby = el.getAttribute("aria-labelledby");
  if (alby) {
    const lblEl = document.getElementById(alby);
    if (lblEl) return clean(lblEl.textContent);
  }
  // 4. title attribute
  if (el.title) return clean(el.title);

  // 5. Wrapping <label>
  const wl = el.closest("label");
  if (wl) return clean(wl.textContent);

  // 6. Nearest preceding sibling/element with text
  let prev = el.previousElementSibling;
  for (let i = 0; i < 3 && prev; i++) {
    const t = clean(prev.textContent);
    if (t && t.length > 1 && t.length < 80) return t;
    prev = prev.previousElementSibling;
  }
  // 7. Parent's label-like child
  const parent = el.parentElement;
  if (parent) {
    const lbl = parent.querySelector("label, .label, .field-label, .form-label, legend");
    if (lbl && lbl !== el) return clean(lbl.textContent);
  }
  // 8. Placeholder / name
  if (el.placeholder) return clean(el.placeholder);
  if (el.name) return el.name.replace(/[_-]/g, " ").trim();

  return "";
}

function clean(s) {
  return (s || "").replace(/\s+/g, " ").replace(/[*:]/g, "").trim().slice(0, 80);
}

function isVisible(el) {
  if (!el.offsetParent && el.tagName !== "INPUT") return false;
  const s = getComputedStyle(el);
  return s.display !== "none" && s.visibility !== "hidden" && s.opacity !== "0";
}

// ── Form filler ───────────────────────────────────────────────────────────────
async function fillForm(profile, options) {
  const mapping = options.mapping || {};
  let filled = 0, failed = 0;
  const details = [];

  for (const [selector, profileKey] of Object.entries(mapping)) {
    const rawValue = profile[profileKey];
    if (!rawValue) continue;

    try {
      const el = document.querySelector(selector);

      if (!el || !isVisible(el)) {
        failed++;
        continue;
      }

      let ok, value;
      if (el.type === "file") {
        // Applicant photo/signature — rawValue is a data: URL, not text.
        ok = await fillFileInput(el, rawValue, profileKey);
        value = "[image]";
      } else {
        value = formatForFill(profileKey, rawValue);
        if (el.tagName === "SELECT") {
          ok = fillSelect(el, value);
        } else if (el.hasAttribute("data-sf-dd")) {
          // Custom click-based dropdown (e.g. SSC.gov.in's app-dropdown)
          ok = await fillCustomDropdown(el, value);
        } else {
          ok = fillInput(el, value);
        }
      }

      if (ok) {
        filled++;
        details.push({ selector, profileKey, value });
        flashField(el, "success");
      } else {
        failed++;
      }
    } catch (err) {
      failed++;
      console.warn("[SmartFill] Fill error:", selector, err.message);
    }
  }

  return { filled, failed, details };
}

// ── Value formatting for form fill ───────────────────────────────────────────
// Government portals often need specific formats (Aadhaar without spaces, etc.)
function formatForFill(key, value) {
  switch (key) {
    // Aadhaar: portals want 12 digits, no spaces
    case "aadhaar_number":
      return value.replace(/\s/g, "");

    // DOB: some portals want DD/MM/YYYY, others YYYY-MM-DD
    // We store as YYYY-MM-DD. Portal-specific code below handles conversion.
    case "dob":
    case "doi":
    case "doe":
      return value; // handled per-portal in fillInput

    // PAN: always uppercase
    case "pan_number":
      return value.toUpperCase().replace(/\s/g, "");

    // Phone: 10 digits
    case "mobile":
      return value.replace(/\D/g, "").replace(/^91/, "").slice(-10);

    default:
      return value;
  }
}

// ── React-safe native value setter ────────────────────────────────────────────
// React (and some Angular/Vue setups) attach a property descriptor on the
// input's value via a getter/setter pair on the *instance*, which shadows
// the native prototype setter. Plain `el.value = x` goes through React's
// shadowed setter and gets silently dropped because React's internal
// _valueTracker thinks nothing changed (it compares against its own
// last-known value, not the DOM's actual value).
//
// Fix: call the *prototype's* native setter directly (bypassing the
// instance-level shadow), AND explicitly reset _valueTracker so React's
// synthetic event system detects the change as real on the next dispatched
// 'input' event. This is the standard workaround used by Cypress, Playwright,
// and most browser automation tools for React-controlled inputs.
function setNativeValue(el, value) {
  const proto = el.tagName === "TEXTAREA"
    ? window.HTMLTextAreaElement.prototype
    : window.HTMLInputElement.prototype;

  const descriptor = Object.getOwnPropertyDescriptor(proto, "value");
  const nativeSetter = descriptor && descriptor.set;

  if (nativeSetter) {
    nativeSetter.call(el, value);
  } else {
    el.value = value;
  }

  // Reset React's internal value tracker so it doesn't ignore the next
  // dispatched 'input' event as a no-op.
  if (el._valueTracker) {
    el._valueTracker.setValue("");
  }
}

// ── Input filler — handles all frameworks ────────────────────────────────────
function fillInput(el, value) {
  el.focus();

  // Clear first using the React-safe setter (important for Angular
  // validation AND so React doesn't ignore the subsequent real value as
  // "unchanged" if the field already had stale text in it)
  setNativeValue(el, "");
  el.dispatchEvent(new Event("input", { bubbles: true }));

  // Set the real value via the React-safe native setter
  setNativeValue(el, value);

  // Simulate character-by-character for Angular strict validators
  if (PORTAL === "angular" || detectAngular()) {
    simulateTyping(el, value);
    return el.value.length > 0;
  }

  // Standard event sequence — bubbles:true so React's root listener
  // (attached at the document/root level, not the element) picks it up
  el.dispatchEvent(new KeyboardEvent("keydown",  { bubbles: true, key: "a" }));
  el.dispatchEvent(new KeyboardEvent("keypress", { bubbles: true, key: "a" }));
  el.dispatchEvent(new Event("input",   { bubbles: true, cancelable: true }));
  el.dispatchEvent(new Event("change",  { bubbles: true, cancelable: true }));
  el.dispatchEvent(new KeyboardEvent("keyup", { bubbles: true, key: "a" }));
  el.dispatchEvent(new Event("blur",    { bubbles: true }));

  // Also trigger ngModelChange for Angular
  el.dispatchEvent(new Event("ngModelChange", { bubbles: true }));

  return el.value === value || el.value.length > 0;
}

function detectAngular() {
  return !!(
    window.getAllAngularRootElements ||
    document.querySelector("[ng-version]") ||
    document.querySelector("app-root")
  );
}

// For Angular strict validators — type character by character
function simulateTyping(el, value) {
  setNativeValue(el, "");
  let built = "";
  for (const char of value) {
    built += char;
    setNativeValue(el, built);
    el.dispatchEvent(new KeyboardEvent("keydown",  { bubbles: true, key: char }));
    el.dispatchEvent(new KeyboardEvent("keypress", { bubbles: true, key: char }));
    el.dispatchEvent(new InputEvent("input", { bubbles: true, data: char, inputType: "insertText" }));
    el.dispatchEvent(new KeyboardEvent("keyup",    { bubbles: true, key: char }));
  }
  el.dispatchEvent(new Event("change", { bubbles: true }));
  el.dispatchEvent(new Event("blur",   { bubbles: true }));
}

// ── File input filler (applicant photo / signature) ──────────────────────────
// Converts the stored data: URL back into a File and assigns it via
// DataTransfer — the standard way to set input.files programmatically,
// since input.value can't be set directly on file inputs for security reasons.
async function fillFileInput(el, dataUrl, profileKey) {
  try {
    const res = await fetch(dataUrl);
    const blob = await res.blob();
    const name = profileKey === "applicant_signature" ? "signature.jpg" : "photo.jpg";
    const file = new File([blob], name, { type: blob.type || "image/jpeg" });

    const dt = new DataTransfer();
    dt.items.add(file);
    el.files = dt.files;

    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return el.files.length > 0;
  } catch (err) {
    console.warn("[SmartFill] File fill error:", err.message);
    return false;
  }
}

// ── Select filler ─────────────────────────────────────────────────────────────
function fillSelect(el, value) {
  const opts = Array.from(el.options);
  const vl = value.toLowerCase().trim();

  // Priority order: exact value → exact text → partial text
  const match =
    opts.find(o => o.value === value) ||
    opts.find(o => o.value.toLowerCase() === vl) ||
    opts.find(o => o.text.toLowerCase().trim() === vl) ||
    opts.find(o => o.text.toLowerCase().includes(vl)) ||
    opts.find(o => vl.includes(o.text.toLowerCase().trim()) && o.text.length > 2);

  if (!match) return false;

  el.value = match.value;
  el.dispatchEvent(new Event("change", { bubbles: true }));
  el.dispatchEvent(new Event("blur",   { bubbles: true }));
  return true;
}

// ── Custom click-based dropdown filler ────────────────────────────────────────
// For Angular components that don't use a native <select> or Material's
// mat-select — confirmed on ssc.gov.in's Gender field, structure is:
//   <app-dropdown> (trigger, shows "Select" until clicked)
//     click reveals → <div class="drop-list active ...">
//       <li class="ng-star-inserted">Female</li>
//       <li class="ng-star-inserted">Male</li>
//       <li class="ng-star-inserted">Transgender</li>
//
// CONFIRMED FINDING: manual (real) clicks select correctly every time, but
// every synthetic event sequence (mousedown/up, pointerdown/up, .click())
// fails silently — the trigger never updates. This is the signature of an
// Angular (click) handler checking event.isTrusted, which is true only for
// genuine OS-generated input and is unconditionally false for any event
// created via `new MouseEvent()`/`new PointerEvent()` — no event sequence
// can work around this from regular page-context JS.
//
// STRATEGY: bypass the click path entirely.
//   1. Try Angular's debug API (window.ng) to grab the component instance
//      bound to the trigger/option and call its selection method or the
//      underlying FormControl.setValue() directly — this updates Angular's
//      internal state the same way a real click would, without going
//      through any DOM click handler at all.
//   2. If Angular debug info isn't available (some builds strip it), fall
//      back to the synthetic click sequence — it may still work on forms
//      that don't check isTrusted, and is a no-op if it doesn't.
async function fillCustomDropdown(triggerEl, value) {
  const targetText = value.toLowerCase().trim();
  const beforeText = clean(triggerEl.textContent);
  console.log("[SmartFill] fillCustomDropdown start | target:", value, "| trigger text before:", beforeText);

  // Step 1: open the dropdown (this part has worked reliably — only the
  // option *selection* click has failed)
  openDropdownTrigger(triggerEl);
  console.log("[SmartFill] Dropdown trigger clicked, waiting for options panel…");

  const initialOptions = await waitForDropdownOptions(triggerEl, 1500);
  console.log("[SmartFill] Options found:", initialOptions.map(o => clean(o.textContent)));

  if (!initialOptions.length) {
    console.warn("[SmartFill] Dropdown opened but no options rendered for", triggerEl);
    return false;
  }

  const findMatch = (opts) =>
    opts.find(li => clean(li.textContent).toLowerCase() === targetText) ||
    opts.find(li => clean(li.textContent).toLowerCase().includes(targetText)) ||
    opts.find(li => targetText.includes(clean(li.textContent).toLowerCase()) && li.textContent.trim().length > 1);

  await sleep(50);
  const freshOptions = Array.from(document.querySelectorAll("li")).filter(isVisible);
  const match = findMatch(freshOptions);

  if (!match) {
    console.warn("[SmartFill] No matching option found for value:", value, "| available:",
      initialOptions.map(o => clean(o.textContent)));
    openDropdownTrigger(triggerEl); // close it again
    return false;
  }

  console.log("[SmartFill] Matched option:", clean(match.textContent));

  // Step 2: try the Angular-internals route first
  const viaAngular = trySelectViaAngular(triggerEl, match, value);
  if (viaAngular) {
    console.log("[SmartFill] Selected via Angular internals (bypassed click)");
  } else {
    console.log("[SmartFill] Angular internals route unavailable, falling back to click simulation");
    clickOption(match);
  }

  await sleep(250);
  const afterText = clean(triggerEl.textContent);
  console.log("[SmartFill] Trigger text after selection attempt:", afterText);

  const success = afterText !== beforeText && afterText.toLowerCase().includes(targetText);
  if (!success) {
    console.warn("[SmartFill] Trigger text did not update. before:", beforeText, "| after:", afterText);
  }
  return success;
}

// Attempts to select the option by reaching into Angular's component tree
// directly, bypassing the DOM click path (and therefore the isTrusted
// check) entirely. Tries several known Angular debug API shapes since the
// exact method name/signature varies by Angular version and how the
// component itself is written.
function trySelectViaAngular(triggerEl, optionEl, value) {
  // window.ng is Angular's global debug API — only present if the app
  // wasn't built with full production optimizations that strip it
  if (typeof window.ng === "undefined") {
    console.log("[SmartFill] window.ng not available — Angular debug API not exposed");
    return false;
  }

  try {
    // Try getting the component instance for the option <li> itself —
    // many Angular list-item components have a (click) binding that calls
    // a method like selectOption(item) or onSelect(item)
    const optionComponent = window.ng.getComponent
      ? window.ng.getComponent(optionEl)
      : null;

    // Try getting the component instance for the dropdown trigger/container
    const triggerComponent = window.ng.getComponent
      ? window.ng.getComponent(triggerEl) || window.ng.getComponent(triggerEl.closest("app-dropdown"))
      : null;

    console.log("[SmartFill] ng.getComponent results | option:", optionComponent, "| trigger:", triggerComponent);

    // Try common method names on the trigger component for setting a value
    if (triggerComponent) {
      const candidateMethods = ["selectOption", "onSelect", "select", "writeValue", "setValue", "onChange"];
      for (const methodName of candidateMethods) {
        if (typeof triggerComponent[methodName] === "function") {
          console.log("[SmartFill] Calling triggerComponent." + methodName + "(value)");
          triggerComponent[methodName](value);
          return true;
        }
      }

      // Try common property names that might be bound to *ngModel / FormControl
      const candidateProps = ["selectedValue", "value", "selected"];
      for (const propName of candidateProps) {
        if (propName in triggerComponent) {
          console.log("[SmartFill] Setting triggerComponent." + propName + " = value");
          triggerComponent[propName] = value;
          // Trigger Angular change detection manually since we bypassed
          // its normal event-driven update cycle
          if (window.ng.applyChanges) {
            try { window.ng.applyChanges(triggerComponent); } catch {}
          }
          return true;
        }
      }
    }

    console.log("[SmartFill] No usable method/property found on Angular component");
    return false;
  } catch (err) {
    console.warn("[SmartFill] trySelectViaAngular failed:", err.message);
    return false;
  }
}

// Fires the full event sequence a real click produces, in order, so
// Angular's (click) bindings — which can sometimes listen for specific
// pointer/mouse events rather than just the synthetic .click() call —
// reliably fire.
function openDropdownTrigger(el) {
  el.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true }));
  el.dispatchEvent(new MouseEvent("mousedown",     { bubbles: true }));
  el.dispatchEvent(new MouseEvent("mouseup",       { bubbles: true }));
  el.dispatchEvent(new PointerEvent("pointerup",   { bubbles: true }));
  el.click();
}

function clickOption(el) {
  el.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true }));
  el.dispatchEvent(new MouseEvent("mousedown",     { bubbles: true }));
  el.dispatchEvent(new MouseEvent("mouseup",       { bubbles: true }));
  el.dispatchEvent(new PointerEvent("pointerup",   { bubbles: true }));
  el.click();
}

// Polls for the options list to appear after a dropdown trigger is clicked.
// Searches near the trigger element first (sibling/child), then falls back
// to a page-wide search for a freshly-visible <li> list, since some Angular
// component libraries render the panel into an overlay appended to <body>
// rather than inside the trigger's own DOM subtree.
function waitForDropdownOptions(triggerEl, timeoutMs) {
  return new Promise(resolve => {
    const start = Date.now();

    const tryFind = () => {
      // Look for a list of <li> elements that became visible near the trigger
      let candidates = [];

      const nearby = triggerEl.closest("app-dropdown, div, section") || triggerEl.parentElement;
      if (nearby) {
        candidates = Array.from(nearby.querySelectorAll("li")).filter(isVisible);
      }

      if (!candidates.length) {
        // Fallback: any visible <li> list that appeared after a "drop-list"
        // style class, common naming pattern for these custom components
        const panel = document.querySelector(
          '.drop-list, [class*="dropdown-panel" i], [class*="options-list" i]'
        );
        if (panel && isVisible(panel)) {
          candidates = Array.from(panel.querySelectorAll("li")).filter(isVisible);
        }
      }

      if (candidates.length) {
        resolve(candidates);
        return true;
      }
      return false;
    };

    if (tryFind()) return;

    const interval = setInterval(() => {
      if (tryFind() || Date.now() - start > timeoutMs) {
        clearInterval(interval);
        if (Date.now() - start > timeoutMs) resolve([]);
      }
    }, 100);
  });
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// ── Visual feedback ───────────────────────────────────────────────────────────
function flashField(el, type) {
  if (!el) return;
  const COLORS = { success: "#16a34a", error: "#dc2626", highlight: "#2563eb" };
  const BG     = { success: "rgba(22,163,74,0.08)", error: "rgba(220,38,38,0.08)", highlight: "rgba(37,99,235,0.08)" };
  const origOutline = el.style.outline;
  const origBg = el.style.backgroundColor;

  el.style.outline = `2px solid ${COLORS[type] || COLORS.highlight}`;
  el.style.backgroundColor = BG[type] || BG.highlight;

  setTimeout(() => {
    el.style.outline = origOutline;
    el.style.backgroundColor = origBg;
  }, 2500);
}

console.log("[SmartFill AI] v3.1 loaded | portal:", PORTAL);

} // end injection guard
