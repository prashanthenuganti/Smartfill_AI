/**
 * background.js — Milestone 3 (simplified)
 *
 * Profile is now stored in the backend session (not chrome.storage).
 * This worker only relays messages between popup and content scripts.
 */

"use strict";

chrome.runtime.onInstalled.addListener(() => {
  console.log("[SmartFill AI] v2.0 installed");
  chrome.action.setBadgeBackgroundColor({ color: "#6366f1" });
});

// Relay messages to content script in active tab
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "SET_BADGE") {
    chrome.action.setBadgeText({ text: message.text || "" });
    sendResponse({ ok: true });
    return false;
  }
  return false;
});
