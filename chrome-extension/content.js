// content.js — Chrome extension content script for Mitra Fill
// Scans form fields, matches them to profile data, and fills them automatically.

"use strict";

const TIMEOUT_SCAN = 3000;
const TIMEOUT_OPTIONS = 1500;

// ── Form field scanner ────────────────────────────────────────────────────────

async function scanPageFields() {
  const fields = [];
  const seen = new Set();

  // Native <select> and <input type="file">
  document.querySelectorAll("select, input[type='file']").forEach((el, idx) => {
    if (!isVisible(el)) return;
    const label = getLabel(el);
    if (!label || label.length < 2) return;
    const stableId = el.id || `[select_${idx}]`;
    if (seen.has(stableId)) return;
    seen.add(stableId);
    fields.push({
      id: stableId,
      label: label,
      type: el.type === "file" ? "file" : "select",
      name: el.getAttribute("name") || "",
      placeholder: el.getAttribute("placeholder") || "",
    });
  });

  // Custom dropdowns (e.g. SSC's <app-dropdown>)
  document.querySelectorAll(".ng-dropdown, [class*='dropdown' i]").forEach((el, idx) => {
    if (!isVisible(el)) return;
    if (el.querySelector("input[type='file']")) return; // already handled above
    
    // Find the trigger element (usually a div with class like "select-type" or similar)
    const trigger = el.querySelector("[class*='select' i], [class*='trigger' i], .dropdown-trigger, button");
    if (!trigger) return;

    const stableId = (() => {
      if (el.id) return `#${el.id}`;
      if (trigger.id) return `#${trigger.id}`;
      if (trigger.getAttribute("data-sf-dd")) return `[data-sf-dd="${trigger.getAttribute("data-sf-dd")}"]`;
      return `[ng-dropdown_${idx}]`;
    })();

    if (seen.has(stableId)) return;
    seen.add(stableId);

    const label = getCustomDropdownLabel(el);
    if (!label || label.length < 2) return;

    fields.push({
      id: stableId,
      label: label,
      type: "custom-dropdown",
      name: el.getAttribute("formcontrolname") || "",
    });
  });

  // Regular text/email/tel/date inputs
  document.querySelectorAll("input:not([type='file']):not([type='hidden']), textarea").forEach((el, idx) => {
    if (!isVisible(el)) return;
    const label = getLabel(el);
    if (!label || label.length < 2) return;
    const stableId = el.id || el.name || `[input_${idx}]`;
    if (seen.has(stableId)) return;
    seen.add(stableId);
    fields.push({
      id: stableId,
      label: label,
      type: el.type || "text",
      name: el.getAttribute("name") || "",
      placeholder: el.getAttribute("placeholder") || "",
    });
  });

  return fields;
}

// ── Label detection for custom dropdown components ───────────────────────────
// The generic getLabel() strategies all fail for components like SSC's
// <app-dropdown>: the scanner's trigger element is a deeply-nested inner
// div (e.g. .select-type showing "Select"), while the visible label text
// lives in a .label div that is a sibling of the trigger's PARENT —
// unreachable via label[for]/aria/previous-sibling/parent-child searches.
// Confirmed structure on ssc.gov.in:
//   <app-dropdown label="10. Year of Passing">
//     <div class="ng-dropdown">
//       <div class="label required">10. Year of Passing</div>
//       <div class="value-area"><div class="select-type">Select</div>…
// So: read the component's own label attribute first, then look for a
// .label element within the enclosing dropdown container, and only then
// fall back to the generic strategies.
function getCustomDropdownLabel(el) {
  // 1. The Angular component's own label attribute
  const host = el.closest("app-dropdown");
  if (host) {
    const attr = host.getAttribute("label");
    if (attr && clean(attr).length > 1) return clean(attr);
  }

  // 2. A label-ish element inside the enclosing dropdown container
  const container = el.closest('app-dropdown, .ng-dropdown, [class*="dropdown" i]');
  if (container) {
    const lblEl = container.querySelector('label, .label, [class*="label" i]');
    if (lblEl && lblEl !== el && !lblEl.contains(el)) {
      const t = clean(lblEl.textContent);
      if (t && t.length > 1) return t;
    }
  }

  // 3. Generic 8-strategy fallback
  return getLabel(el);
}

// ── Label detection — 8 strategies ───────────────────────────────────────────
function getLabel(el) {
  // 1. <label for="...">
  if (el.id) {
    const lbl = document.querySelector(`label[for="${el.id}"]`);
    if (lbl) return clean(lbl.textContent);
  }

  // 2. aria-label
  const ariaLabel = el.getAttribute("aria-label");
  if (ariaLabel) return clean(ariaLabel);

  // 3. aria-labelledby
  const ariaLabelledBy = el.getAttribute("aria-labelledby");
  if (ariaLabelledBy) {
    const labelEl = document.getElementById(ariaLabelledBy);
    if (labelEl) return clean(labelEl.textContent);
  }

  // 4. Immediate previous sibling (usually a <label>)
  let prev = el.previousElementSibling;
  while (prev && (prev.tagName === "BR" || prev.className.includes("hidden"))) {
    prev = prev.previousElementSibling;
  }
  if (prev && (prev.tagName === "LABEL" || prev.textContent.length < 100)) {
    const t = clean(prev.textContent);
    if (t) return t;
  }

  // 5. Parent's text node before the element
  const parent = el.parentElement;
  if (parent) {
    const t = clean(parent.textContent.split(el.textContent)[0]);
    if (t && t.length > 1) return t;
  }

  // 6. Closest <fieldset><legend> ancestor
  const fieldset = el.closest("fieldset");
  if (fieldset) {
    const legend = fieldset.querySelector("legend");
    if (legend) return clean(legend.textContent);
  }

  // 7. Closest <form><h*> or <form><strong> ancestor
  const form = el.closest("form");
  if (form) {
    const header = form.querySelector("h1, h2, h3, h4, h5, h6, strong, b");
    if (header) return clean(header.textContent);
  }

  // 8. Placeholder
  const ph = el.getAttribute("placeholder");
  if (ph) return clean(ph);

  return "";
}

function clean(s) {
  return (s || "").replace(/\s+/g, " ").replace(/[\n\r\t]/g, " ").trim();
}

function isVisible(el) {
  if (!el.offsetParent) return false;
  const style = window.getComputedStyle(el);
  return style.display !== "none" && style.visibility !== "hidden";
}

// ── Form filling ──────────────────────────────────────────────────────────────

async function fillForm(mapping) {
  const results = { filled: 0, failed: 0, errors: [] };

  for (const [fieldId, profileKey] of Object.entries(mapping)) {
    try {
      const el = findElement(fieldId);
      if (!el) {
        results.errors.push(`Field ${fieldId} not found`);
        results.failed++;
        continue;
      }

      const value = window.MITRA_PROFILE?.[profileKey];
      if (!value) {
        results.failed++;
        continue;
      }

      const success = await fillField(el, value, profileKey);
      if (success) {
        results.filled++;
      } else {
        results.failed++;
      }
    } catch (err) {
      console.error("[MitraFill] Fill error | field:", fieldId, "error:", err);
      results.errors.push(err.message);
      results.failed++;
    }
  }

  return results;
}

function findElement(fieldId) {
  // Try by ID first
  if (fieldId.startsWith("#")) {
    const id = fieldId.slice(1);
    return document.getElementById(id);
  }
  // Try by data attribute
  if (fieldId.startsWith("[") && fieldId.endsWith("]")) {
    const match = fieldId.match(/\[([^\]]+)="?([^\]"]+)"?\]/);
    if (match) {
      const [, attr, val] = match;
      return document.querySelector(`[${attr}="${val}"]`);
    }
  }
  // Fallback: try name or data-* attributes
  return document.querySelector(`[name="${fieldId}"], [data-field="${fieldId}"]`);
}

async function fillField(el, value, profileKey) {
  const type = el.type || el.tagName.toLowerCase();

  // File input — store for later (operator picks files)
  if (type === "file") {
    console.log("[MitraFill] File field detected (manual upload needed):", profileKey);
    return false;
  }

  // Select or custom dropdown
  if (type === "select" || el.classList.contains("ng-dropdown") || el.closest(".ng-dropdown")) {
    if (el.tagName === "SELECT") {
      return fillSelect(el, value);
    } else {
      return await fillCustomDropdown(el, value);
    }
  }

  // Text input, date, email, etc.
  if (type === "text" || type === "email" || type === "tel" || type === "date" || type === "textarea") {
    el.value = value;
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.dispatchEvent(new Event("input", { bubbles: true }));
    console.log("[MitraFill] Filled text field | profile_key:", profileKey, "| value:", value);
    return true;
  }

  // Radio or checkbox
  if (type === "radio" || type === "checkbox") {
    const match = Array.from(document.querySelectorAll(`[name="${el.name}"]`)).find(
      r => clean(r.value).toLowerCase() === clean(value).toLowerCase() ||
           (r.labels?.[0] && clean(r.labels[0].textContent).toLowerCase().includes(clean(value).toLowerCase()))
    );
    if (match) {
      match.checked = true;
      match.dispatchEvent(new Event("change", { bubbles: true }));
      console.log("[MitraFill] Filled checkbox/radio | profile_key:", profileKey, "| value:", value);
      return true;
    }
    return false;
  }

  return false;
}

function fillSelect(el, value) {
  const vl = clean(value).toLowerCase();
  const opts = Array.from(el.options).map(o => ({ value: o.value, text: clean(o.text) }));

  // Priority order: exact value → exact text → partial text
  const match =
    opts.find(o => o.value === value) ||
    opts.find(o => o.value.toLowerCase() === vl) ||
    opts.find(o => o.text.toLowerCase().trim() === vl) ||
    opts.find(o => o.text.toLowerCase().includes(vl)) ||
    opts.find(o => vl.includes(o.text.toLowerCase().trim()) && o.text.length > 2);

  if (!match) {
    console.warn("[MitraFill] No select option matched | value:", value);
    return false;
  }

  el.value = match.value;
  el.dispatchEvent(new Event("change", { bubbles: true }));
  console.log("[MitraFill] Filled select | matched:", match.text);
  return true;
}

async function fillCustomDropdown(el, value) {
  const targetText = value.toLowerCase().trim();
  const beforeText = clean(el.textContent);
  console.log("[MitraFill] fillCustomDropdown start | target:", value, "| trigger text before:", beforeText);

  // Find trigger element
  let trigger = el.querySelector("[class*='select' i], button, [tabindex]");
  if (!trigger) trigger = el;

  // Click to open
  openDropdownTrigger(trigger);
  console.log("[MitraFill] Dropdown trigger clicked, waiting for options panel…");

  const opts = await waitForDropdownOptions(trigger, TIMEOUT_OPTIONS);
  console.log("[MitraFill] Options found:", opts.map(o => clean(o.textContent)));

  if (!opts.length) {
    console.warn("[MitraFill] No options found in dropdown");
    return false;
  }

  // Match option
  const match = opts.find(li => clean(li.textContent).toLowerCase() === targetText) ||
                opts.find(li => clean(li.textContent).toLowerCase().includes(targetText)) ||
                opts.find(li => targetText.includes(clean(li.textContent).toLowerCase()) && li.textContent.trim().length > 1);

  if (!match) {
    console.warn("[MitraFill] No matching option found | value:", value);
    openDropdownTrigger(trigger); // close
    return false;
  }

  console.log("[MitraFill] Matched option:", clean(match.textContent));

  // Click the option
  clickOption(match);
  await sleep(300);

  const afterText = clean(trigger.textContent);
  console.log("[MitraFill] Trigger text after click:", afterText);

  return afterText !== beforeText;
}

function openDropdownTrigger(el) {
  el.click();
  el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
}

function clickOption(el) {
  el.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true }));
  el.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
  el.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
  el.dispatchEvent(new PointerEvent("pointerup", { bubbles: true }));
  el.click();
}

async function waitForDropdownOptions(triggerEl, timeout) {
  const startTime = Date.now();
  while (Date.now() - startTime < timeout) {
    // Look for a list of <li> elements that became visible near the trigger
    let candidates = [];
    const scopes = [
      triggerEl.closest("app-dropdown"),
      triggerEl.closest('.ng-dropdown, [class*="dropdown" i]'),
      triggerEl.parentElement,
    ].filter(Boolean);
    for (const nearby of scopes) {
      candidates = Array.from(nearby.querySelectorAll("li")).filter(isVisible);
      if (candidates.length) break;
    }
    if (candidates.length > 0) return candidates;
    await sleep(50);
  }
  return [];
}

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

// ── Message handlers ──────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "SCAN_FIELDS") {
    scanPageFields().then(fields => {
      sendResponse({ fields });
    }).catch(err => {
      sendResponse({ error: err.message });
    });
    return true; // async response
  }

  if (message.type === "FILL_FORM") {
    fillForm(message.mapping).then(results => {
      sendResponse(results);
    }).catch(err => {
      sendResponse({ error: err.message, filled: 0, failed: 0, errors: [] });
    });
    return true; // async response
  }

  if (message.type === "SET_PROFILE") {
    window.MITRA_PROFILE = message.profile;
    sendResponse({ ok: true });
  }
});

console.log("[MitraFill] Content script loaded");
