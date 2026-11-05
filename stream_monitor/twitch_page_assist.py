"""Twitch in-page assist orchestrator driven by Chromium CDP.

Runs when viewer_engagement.page_assist_enabled and launch requirements hold.
Each managed Twitch window gets one background worker that:
  1. accepts content-classification / Start Watching gates
  2. optionally enables theater mode (verified; idempotent; never blind-toggle)
  3. ensures chat is visible, then optionally claims Channel Points bonus
     with fuzzy delay / bounded click offset and post-click verification
  4. optionally reloads on a fuzzy interval, broadcasts managed-page F5, and
     re-runs (1)+(2) after navigation
"""

from __future__ import annotations

import json
import logging
import math
import random
import re
import threading
import time
from typing import Any
from urllib.parse import urlsplit

from stream_monitor.cdp_client import (
    DEFAULT_CDP_ATTACH_TIMEOUT_S,
    CdpClient,
    page_urls_match,
)
from stream_monitor.page_assist_status import publish_page_assist_status
from stream_monitor.playback_health import PlaybackHealth
from stream_monitor.twitch_player_recovery import PLAYER_SNAPSHOT_JS, PlayerRecovery
from stream_monitor.viewer_engagement_model import (
    ViewerEngagementSettings,
    coerce_viewer_engagement,
    is_twitch_url,
    page_assist_runtime_allowed,
)

logger = logging.getLogger(__name__)

_GATE_TIMEOUT_S = 60.0
_GATE_POLL_S = 1.0
_READY_CONFIRM_POLLS = 2
_THEATER_VERIFY_WAIT_S = 0.6
_CLAIM_VERIFY_WAIT_S = 0.8
_CLAIM_VERIFY_POLLS = 6
_PAGE_CHECK_INTERVAL_S = 30.0
_POINTS_STALL_WINDOW_S = 30 * 60.0
_POINTS_STALL_RETRY_LIMIT = 2
_POINTS_TOOLTIP_WAIT_S = 1.5
_POINTS_TOOLTIP_POLL_S = 0.1
_RECONNECT_BACKOFF_MAX_S = 30.0
_F5_BROADCAST_BINDING = "helloStreamerCheckManagedPages"
_F5_WINDOW_STAGGER_S = 1.0
_F5_PLAYBACK_VERIFY_TIMEOUT_S = 45.0
_F5_PLAYBACK_VERIFY_INTERVAL_S = 1.0
_F5_PLAYBACK_VERIFY_MAX_GAP_S = 5.0
_F5_PAGE_RETRY_DELAYS_S = (8.0, 20.0)
_F5_REQUIRED_MEDIA_ADVANCES = 2

# Best-effort DOM helpers. Twitch UI changes; failures are non-fatal.
_FIND_GATE_JS = r"""
(() => {
  const texts = [
    "start watching",
    "開始觀看",
    "开始观看",
    "視聴を開始",
    "시청 시작",
    "regarder",
    "ver ahora",
    "assistir"
  ];
  const prefer = document.querySelector(
    '[data-a-target="content-classification-gate-overlay-start-watching-button"]'
  );
  const candidates = prefer
    ? [prefer]
    : Array.from(document.querySelectorAll('button, [role="button"]'));
  for (const el of candidates) {
    if (!(el instanceof HTMLElement)) continue;
    const label = ((el.innerText || el.textContent || "") + " " +
      (el.getAttribute("aria-label") || "")).trim().toLowerCase();
    const matched = prefer === el || texts.some((t) => label.includes(t));
    if (!matched) continue;
    const r = el.getBoundingClientRect();
    if (r.width <= 1 || r.height <= 1) continue;
    return {found: true, x: r.left + r.width / 2, y: r.top + r.height / 2};
  }
  return {found: false};
})()
"""

_PAGE_READY_JS = r"""
(() => {
  const gateTexts = [
    "start watching",
    "開始觀看",
    "开始观看",
    "視聴を開始",
    "시청 시작",
    "regarder",
    "ver ahora",
    "assistir"
  ];
  const prefer = document.querySelector(
    '[data-a-target="content-classification-gate-overlay-start-watching-button"]'
  );
  let gatePresent = false;
  if (prefer instanceof HTMLElement) {
    const r = prefer.getBoundingClientRect();
    gatePresent = r.width > 1 && r.height > 1;
  } else {
    for (const el of document.querySelectorAll('button, [role="button"]')) {
      if (!(el instanceof HTMLElement)) continue;
      const label = ((el.innerText || el.textContent || "") + " " +
        (el.getAttribute("aria-label") || "")).trim().toLowerCase();
      if (!gateTexts.some((t) => label.includes(t))) continue;
      const r = el.getBoundingClientRect();
      if (r.width > 1 && r.height > 1) { gatePresent = true; break; }
    }
  }
  let readyState = 0;
  let paused = true;
  let hasVideo = false;
  for (const v of document.querySelectorAll("video")) {
    if (!(v instanceof HTMLVideoElement)) continue;
    const r = v.getBoundingClientRect();
    if (r.width <= 1 || r.height <= 1) continue;
    hasVideo = true;
    if (v.readyState >= readyState) {
      readyState = v.readyState;
      paused = !!v.paused;
    }
  }
  return {
    gatePresent,
    hasVideo,
    readyState,
    paused,
    playbackReady: hasVideo && readyState >= 3
  };
})()
"""

# Read-only observation probe (does not drive assist decisions).
_PAGE_OBS_JS = r"""
(() => {
  const gateTexts = [
    "start watching",
    "開始觀看",
    "开始观看",
    "視聴を開始",
    "시청 시작",
    "regarder",
    "ver ahora",
    "assistir"
  ];
  const prefer = document.querySelector(
    '[data-a-target="content-classification-gate-overlay-start-watching-button"]'
  );
  let gatePresent = false;
  if (prefer instanceof HTMLElement) {
    const r = prefer.getBoundingClientRect();
    gatePresent = r.width > 1 && r.height > 1;
  } else {
    for (const el of document.querySelectorAll('button, [role="button"]')) {
      if (!(el instanceof HTMLElement)) continue;
      const label = ((el.innerText || el.textContent || "") + " " +
        (el.getAttribute("aria-label") || "")).trim().toLowerCase();
      if (!gateTexts.some((t) => label.includes(t))) continue;
      const r = el.getBoundingClientRect();
      if (r.width > 1 && r.height > 1) { gatePresent = true; break; }
    }
  }
  let readyState = null;
  let paused = null;
  let hasVideo = false;
  let currentTime = null;
  for (const v of document.querySelectorAll("video")) {
    if (!(v instanceof HTMLVideoElement)) continue;
    const r = v.getBoundingClientRect();
    if (r.width <= 1 || r.height <= 1) continue;
    hasVideo = true;
    const rs = Number(v.readyState) || 0;
    if (readyState === null || rs >= readyState) {
      readyState = rs;
      paused = !!v.paused;
      currentTime = Number.isFinite(v.currentTime) ? v.currentTime : null;
    }
  }
  const visibility = (document.visibilityState || "unknown");
  return {
    url: location.href,
    gatePresent,
    hasVideo,
    readyState,
    paused,
    currentTime,
    visibilityState: visibility
  };
})()
"""

# The second balance in Twitch's community-points summary is exposed as
# ``copo-balance-string``. Its compact display (for example, 11.5萬) is not an
# exact reading; the associated coin icon's accessible tooltip contains both
# balances in full. Hovering that icon is read-only and never clicks the page.
_FIND_POINTS_BALANCE_JS = r"""
(() => {
  const visible = (el) => {
    if (!(el instanceof HTMLElement) || !el.isConnected) return false;
    const r = el.getBoundingClientRect();
    if (r.width <= 1 || r.height <= 1 || r.right <= 0 || r.bottom <= 0 ||
        r.left >= innerWidth || r.top >= innerHeight) return false;
    for (let node = el; node instanceof HTMLElement; node = node.parentElement) {
      const s = getComputedStyle(node);
      if (node.hidden || s.display === 'none' || s.visibility === 'hidden' ||
          s.visibility === 'collapse' || Number(s.opacity) === 0) return false;
    }
    return true;
  };
  const summaries = [...document.querySelectorAll(
    '[data-test-selector="community-points-summary"], ' +
    '[data-a-target="community-points-summary"]'
  )].filter(visible);
  for (const summary of summaries) {
    const balance = summary.querySelector('[data-test-selector="copo-balance-string"]');
    if (!(balance instanceof HTMLElement) || !visible(balance)) continue;
    const icon = balance.parentElement?.querySelector('img, svg');
    const text = (balance.innerText || balance.textContent || '').trim();
    let hover = null;
    if (icon instanceof HTMLElement && visible(icon)) {
      const r = icon.getBoundingClientRect();
      // Twitch displays the full, exact value when the second balance icon is
      // hovered. Aim inside the icon, away from the adjacent compact text.
      hover = {x: r.left + Math.max(1, r.width * 0.8), y: r.top + r.height / 2};
    }
    return {found: true, displayed: text, hover};
  }
  return {found: false};
})()
"""

_READ_POINTS_TOOLTIP_JS = r"""
(() => {
  const visible = (el) => {
    if (!el.isConnected || el.getClientRects().length === 0) return false;
    for (let node = el; node instanceof HTMLElement; node = node.parentElement) {
      const s = getComputedStyle(node);
      if (node.hidden || s.display === 'none' || s.visibility === 'hidden' ||
          s.visibility === 'collapse' || Number(s.opacity) === 0) return false;
    }
    return true;
  };
  return [...document.querySelectorAll('[role="tooltip"]')]
    .filter(visible)
    .map(el => (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim())
    .filter(Boolean);
})()
"""

_CHAT_STATE_JS = r"""
(() => {
  const visible = (el) => {
    if (!(el instanceof HTMLElement)) return false;
    const r = el.getBoundingClientRect();
    if (r.width <= 1 || r.height <= 1) return false;
    const st = window.getComputedStyle(el);
    if (st.visibility === "hidden" || st.display === "none" || Number(st.opacity) === 0) {
      return false;
    }
    return true;
  };
  const labelOf = (el) => ((el.getAttribute("aria-label") || "") + " " +
    (el.innerText || el.textContent || "")).trim().toLowerCase();
  const expandHints = [
    "expand chat",
    "show chat",
    "expand",
    "展開聊天",
    "展开聊天",
    "顯示聊天",
    "显示聊天",
    "チャットを表示",
    "채팅 펼치기",
    "채팅 표시"
  ];
  const collapseHints = [
    "collapse chat",
    "hide chat",
    "collapse",
    "收合聊天",
    "折叠聊天",
    "隱藏聊天",
    "隐藏聊天",
    "チャットを非表示",
    "채팅 접기",
    "채팅 숨기기"
  ];
  const chatShells = [
    document.querySelector('[data-test-selector="chat-room-component-layout"]'),
    document.querySelector('section[data-a-target="chat-room-header-label"]') &&
      document.querySelector('[data-a-target="chat-input"]')?.closest("section"),
    document.querySelector(".chat-room"),
    document.querySelector('[data-a-target="right-column-chat-bar"]')
  ].filter(Boolean);
  let chatVisible = false;
  for (const shell of chatShells) {
    if (visible(shell)) { chatVisible = true; break; }
  }
  if (!chatVisible) {
    const input = document.querySelector(
      '[data-a-target="chat-input"], textarea[data-a-target="chat-input"], ' +
      '[data-a-target="chat-input-text"]'
    );
    chatVisible = visible(input);
  }
  const loginHints = [
    "log in",
    "sign in",
    "登入",
    "登录",
    "ログイン",
    "로그인"
  ];
  let notLoggedIn = false;
  for (const el of document.querySelectorAll("button, a, [role='button']")) {
    if (!(el instanceof HTMLElement) || !visible(el)) continue;
    const label = labelOf(el);
    if (loginHints.some((h) => label.includes(h)) &&
        (label.includes("chat") || label.includes("聊天") ||
         label.includes("チャット") || label.includes("채팅") ||
         !!el.closest('[data-a-target="right-column-chat-bar"], .chat-room'))) {
      notLoggedIn = true;
      break;
    }
  }
  const toggleSelectors = [
    '[data-a-target="right-column__toggle-collapse-btn"]',
    'button[aria-label*="Expand Chat" i]',
    'button[aria-label*="Collapse Chat" i]',
    'button[aria-label*="Show Chat" i]',
    'button[aria-label*="Hide Chat" i]',
    'button[aria-label*="展開聊天"]',
    'button[aria-label*="收合聊天"]',
    'button[aria-label*="展开聊天"]',
    'button[aria-label*="折叠聊天"]'
  ];
  const seen = new Set();
  let expandControl = null;
  let collapsed = false;
  for (const sel of toggleSelectors) {
    for (const node of document.querySelectorAll(sel)) {
      if (!(node instanceof HTMLElement) || seen.has(node) || !visible(node)) continue;
      seen.add(node);
      const label = labelOf(node);
      const isExpand = expandHints.some((h) => label.includes(h)) &&
        !collapseHints.some((h) => label.includes(h));
      const isCollapse = collapseHints.some((h) => label.includes(h));
      const pressed = node.getAttribute("aria-pressed");
      const expanded = node.getAttribute("aria-expanded");
      if (isExpand || pressed === "false" || expanded === "false") {
        collapsed = true;
        if (!expandControl) {
          const r = node.getBoundingClientRect();
          expandControl = {
            found: true,
            x: r.left + r.width / 2,
            y: r.top + r.height / 2,
            label: label.slice(0, 80)
          };
        }
      } else if (isCollapse || pressed === "true" || expanded === "true") {
        chatVisible = chatVisible || true;
      }
    }
  }
  if (chatVisible && !collapsed) {
    return {status: "visible", chatVisible: true, collapsed: false, expandControl: null, notLoggedIn};
  }
  if (collapsed && expandControl) {
    return {status: "collapsed", chatVisible: false, collapsed: true, expandControl, notLoggedIn};
  }
  if (notLoggedIn) {
    return {status: "not_logged_in", chatVisible, collapsed, expandControl, notLoggedIn: true};
  }
  return {
    status: chatVisible ? "visible" : "hidden",
    chatVisible,
    collapsed: !!collapsed,
    expandControl,
    notLoggedIn
  };
})()
"""

_FIND_CLAIM_JS = r"""
(() => {
  const visible = (el) => {
    if (!(el instanceof HTMLElement)) return false;
    if (el.hasAttribute("disabled") || el.getAttribute("aria-disabled") === "true") {
      return false;
    }
    const r = el.getBoundingClientRect();
    if (r.width <= 1 || r.height <= 1) return false;
    const st = window.getComputedStyle(el);
    if (st.visibility === "hidden" || st.display === "none" || Number(st.opacity) === 0) {
      return false;
    }
    if (st.pointerEvents === "none") return false;
    return true;
  };
  const hitTestable = (el) => {
    const r = el.getBoundingClientRect();
    const x = r.left + Math.min(Math.max(r.width / 2, 1), r.width - 1);
    const y = r.top + Math.min(Math.max(r.height / 2, 1), r.height - 1);
    const top = document.elementFromPoint(x, y);
    if (!top) return false;
    return el === top || el.contains(top) || top.contains(el);
  };
  const labelOf = (el) => ((el.getAttribute("aria-label") || "") + " " +
    (el.innerText || el.textContent || "")).trim().toLowerCase();
  const bonusHints = [
    "claim bonus",
    "claim a bonus",
    "click to claim",
    "領取獎勵",
    "领取奖励",
    "ボーナスを受け取る",
    "보너스 받기",
    "claim",
    "領取",
    "领取",
    "受け取る",
    "받기"
  ];
  const excludeHints = [
    "community points",
    "channel points",
    "points menu",
    "balance",
    "預測",
    "预测",
    "prediction",
    "drops",
    "redeem",
    "兌換",
    "兑换"
  ];
  const roots = [];
  const summary = document.querySelector(
    '[data-a-target="community-points-summary"], [data-test-selector="community-points-summary"]'
  );
  if (summary) roots.push(summary);
  const chat = document.querySelector(
    '[data-test-selector="chat-room-component-layout"], .chat-room, ' +
    '[data-a-target="right-column-chat-bar"]'
  );
  if (chat) roots.push(chat);
  if (!roots.length) roots.push(document);

  const isBonusCue = (el) => {
    const label = labelOf(el);
    if (el.closest(".claimable-bonus") || el.querySelector(".claimable-bonus__icon") ||
        el.classList.contains("claimable-bonus") ||
        el.closest('[data-test-selector="community-points-summary-claimable-bonus"]')) {
      return true;
    }
    // Chat may contain extensions, Drops or other reward buttons. Localized
    // reward text alone is only evidence inside the channel-points summary.
    if (!el.closest('[data-a-target="community-points-summary"], ' +
      '[data-test-selector="community-points-summary"], .community-points-summary')) return false;
    return (
      label.includes("bonus") ||
      label.includes("獎勵") ||
      label.includes("奖励") ||
      label.includes("ボーナス") ||
      label.includes("보너스")
    );
  };
  const candidates = [];
  const pushCandidate = (el, reason) => {
    if (!(el instanceof HTMLElement) || !visible(el) || !hitTestable(el)) return;
    // Require explicit bonus cues; never treat summary/menu openers as claim.
    if (!isBonusCue(el)) return;
    const label = labelOf(el);
    if (excludeHints.some((h) => label.includes(h))) return;
    const r = el.getBoundingClientRect();
    candidates.push({
      found: true,
      reason,
      x: r.left + r.width / 2,
      y: r.top + r.height / 2,
      left: r.left,
      top: r.top,
      width: r.width,
      height: r.height,
      label: label.slice(0, 120),
      key: [
        Math.round(r.left), Math.round(r.top),
        Math.round(r.width), Math.round(r.height),
        label.slice(0, 40)
      ].join("|")
    });
  };

  for (const root of roots) {
    for (const icon of root.querySelectorAll(".claimable-bonus__icon, .claimable-bonus")) {
      const btn = icon.closest("button, [role='button']") || icon;
      pushCandidate(btn, "claimable-bonus");
    }
    for (const el of root.querySelectorAll(
      'button[aria-label*="Claim Bonus" i], button[aria-label*="claim a bonus" i], ' +
      'button[aria-label*="領取獎勵"], button[aria-label*="领取奖励"], ' +
      'button[aria-label*="ボーナス"], button[aria-label*="보너스"], ' +
      '[data-test-selector="community-points-summary-claimable-bonus"] button, ' +
      'button.claimable-bonus, .community-points-summary .claimable-bonus button'
    )) {
      pushCandidate(el, "bonus-selector");
    }
  }

  // Localized claim labels only inside the points/chat area — never document-wide.
  for (const root of roots) {
    if (root === document) continue;
    for (const el of root.querySelectorAll("button, [role='button']")) {
      const label = labelOf(el);
      if (!bonusHints.some((h) => label.includes(h))) continue;
      if (!label.includes("bonus") && !label.includes("獎勵") &&
          !label.includes("奖励") && !label.includes("ボーナス") &&
          !label.includes("보너스") && !el.closest(".claimable-bonus") &&
          !el.querySelector(".claimable-bonus__icon")) {
        // Bare "Claim"/"領取" without bonus cue: only accept inside claimable-bonus.
        continue;
      }
      pushCandidate(el, "localized-bonus");
    }
  }

  if (!candidates.length) {
    const pointsPresent = [...document.querySelectorAll(
      '[data-a-target="community-points-summary"], ' +
      '[data-test-selector="community-points-summary"], .community-points-summary'
    )].some(visible);
    return {found: false, pointsPresent};
  }
  // Prefer smallest hit target (actual bonus chest over large wrappers).
  candidates.sort((a, b) => (a.width * a.height) - (b.width * b.height));
  return candidates[0];
})()
"""

_THEATER_STATE_JS = r"""
(() => {
  const labelOf = (el) => ((el.getAttribute("aria-label") || "") + " " +
    (el.innerText || el.textContent || "")).trim().toLowerCase();
  const enterHints = [
    "enter theatre",
    "enter theater",
    "開啟劇院",
    "开启剧院",
    "進入劇院",
    "进入剧院",
  ];
  const exitHints = [
    "exit theatre",
    "exit theater",
    "leave theatre",
    "leave theater",
    "關閉劇院",
    "关闭剧院",
    "離開劇院",
    "离开剧院",
    "シアターモードを終了",
    "극장 모드 종료"
  ];
  const genericHints = [
    "theatre",
    "theater",
    "劇院",
    "剧院",
    "劇場",
    "剧场",
    "シアター",
    "극장"
  ];
  const selectors = [
    '[data-a-target="player-theatre-mode-button"]',
    '[data-a-target="player-theater-mode-button"]',
    'button[aria-label*="theatre" i]',
    'button[aria-label*="theater" i]',
    'button[aria-label*="劇院"]',
    'button[aria-label*="剧院"]',
    'button[aria-label*="劇場"]',
    'button[aria-label*="剧场"]',
    'button[aria-label*="シアター"]',
    'button[aria-label*="극장"]'
  ];
  const visible = (el) => {
    if (!(el instanceof HTMLElement)) return false;
    const r = el.getBoundingClientRect();
    if (r.width <= 1 || r.height <= 1) return false;
    const st = window.getComputedStyle(el);
    if (st.visibility === "hidden" || st.display === "none" || Number(st.opacity) === 0) return false;
    return true;
  };
  const seen = new Set();
  const controls = [];
  for (const sel of selectors) {
    for (const node of document.querySelectorAll(sel)) {
      if (!(node instanceof HTMLElement) || seen.has(node) || !visible(node)) continue;
      seen.add(node);
      controls.push(node);
    }
  }
  for (const el of document.querySelectorAll("button, [role='button']")) {
    if (!(el instanceof HTMLElement) || seen.has(el) || !visible(el)) continue;
    const label = labelOf(el);
    if (!genericHints.some((h) => label.includes(h))) continue;
    seen.add(el);
    controls.push(el);
  }
  const inferControlState = (el) => {
    const pressed = el.getAttribute("aria-pressed");
    const label = labelOf(el);
    if (pressed === "true") return "on";
    if (pressed === "false") return "off";
    if (exitHints.some((h) => label.includes(h))) return "on";
    if (enterHints.some((h) => label.includes(h))) return "off";
    // Current Twitch uses a bare mode label when OFF, including localized
    // labels and an optional shortcut. Scope this evidence to player controls;
    // aria-expanded describes a popup and is not theater state.
    const modeLabel = label.replace(/\s*[（(]\s*alt\s*\+\s*t\s*[）)]\s*$/i, "").trim();
    const defaultLabels = ["theatre mode", "theater mode", "劇院模式", "剧院模式",
      "シアターモード", "극장 모드"];
    if (defaultLabels.includes(modeLabel) && (
      el.matches('[data-a-target="player-theatre-mode-button"], [data-a-target="player-theater-mode-button"]') ||
      el.closest('[data-a-target="player-controls"], .player-controls')
    )) return "off";
    // Other generic labels are insufficient evidence to toggle.
    return "unknown";
  };
  let control = null;
  let controlState = "unknown";
  for (const el of controls) {
    const r = el.getBoundingClientRect();
    const state = inferControlState(el);
    const candidate = {
      found: true,
      x: r.left + r.width / 2,
      y: r.top + r.height / 2,
      state,
      label: labelOf(el).slice(0, 80)
    };
    if (state !== "unknown") {
      control = candidate;
      controlState = state;
      break;
    }
    if (!control) control = candidate;
  }
  const root = document.documentElement;
  const body = document.body;
  const theaterLayout = !!(
    document.querySelector(".video-player--theatre, .video-player--theater") ||
    document.querySelector('[data-a-target="player-overlay-theater-mode"]') ||
    root.classList.contains("theatreMode") ||
    root.classList.contains("theaterMode") ||
    root.classList.contains("theatre-mode") ||
    root.classList.contains("theater-mode") ||
    (body && (
      body.classList.contains("theatre-mode") ||
      body.classList.contains("theater-mode") ||
      body.classList.contains("theatreMode") ||
      body.classList.contains("theaterMode")
    )) ||
    !!document.querySelector('.persistent-player[data-theatre="true"], ' +
      '.persistent-player[data-theater="true"]')
  );
  let state = "unknown";
  if (controlState === "on" || (theaterLayout && controlState !== "off")) {
    state = "on";
  } else if (controlState === "off" && !theaterLayout) {
    state = "off";
  } else if (controlState === "off" && theaterLayout) {
    // Conflicting signals — do not toggle.
    state = "unknown";
  } else if (theaterLayout) {
    state = "on";
  }
  // Only expose clickable control when we know it is OFF (safe to enable).
  const clickable = control && controls.some(el => {
    const r = el.getBoundingClientRect();
    if (r.left + r.width / 2 !== control.x || r.top + r.height / 2 !== control.y) return false;
    if (el.hasAttribute("disabled") || el.getAttribute("aria-disabled") === "true") return false;
    if (getComputedStyle(el).pointerEvents === "none") return false;
    const top = document.elementFromPoint(control.x, control.y);
    return !!top && (top === el || el.contains(top));
  });
  const safeControl = (clickable && controlState === "off" && state === "off")
    ? control
    : (control && controlState === "on" ? control : null);
  return {state, control: safeControl, controlState, theaterLayout: !!theaterLayout};
})()
"""

_F5_BROADCAST_SCRIPT = (
    "(() => {"
    "if (window !== window.top || window.__helloStreamerF5Installed) return;"
    "window.__helloStreamerF5Installed = true;"
    "window.addEventListener('keydown', event => {"
    "if (event.key !== 'F5' || event.altKey || event.ctrlKey || "
    "event.metaKey || event.shiftKey) return;"
    "const host = location.hostname.toLowerCase();"
    "if (host !== 'twitch.tv' && !host.endsWith('.twitch.tv')) return;"
    "const binding = window." + _F5_BROADCAST_BINDING + ";"
    "event.preventDefault();"
    "if (event.repeat || typeof binding !== 'function') return;"
    "try {"
    "const theater = " + _THEATER_STATE_JS + ";"
    "binding(JSON.stringify({url: location.href, theater: theater && theater.state}));"
    "} catch (_) {}"
    "}, true);"
    "})();"
)

_FOCUS_PLAYER_JS = r"""
(() => {
  const targets = [
    '[data-a-target="player-controls"]',
    '.player-controls',
    '.video-player__container',
    '.video-player',
    'main'
  ];
  for (const sel of targets) {
    const el = document.querySelector(sel);
    if (!(el instanceof HTMLElement)) continue;
    try { el.focus({preventScroll: true}); } catch (_) { try { el.focus(); } catch (_) {} }
    return {focused: true, selector: sel};
  }
  return {focused: false};
})()
"""

_PLAYER_HOVER_JS = r"""
(() => {
  for (const video of document.querySelectorAll("video")) {
    const r = video.getBoundingClientRect();
    if (r.width <= 1 || r.height <= 1) continue;
    const x = r.left + r.width / 2, y = r.top + r.height / 2;
    if (x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) continue;
    const top = document.elementFromPoint(x, y);
    if (!top || !(top === video || top.closest('.video-player, [data-a-target="video-player"]'))) continue;
    return {x, y};
  }
  return null;
})()
"""

_assist_lock = threading.Lock()
_assist_by_url: dict[str, "_PageAssistWorker"] = {}


def _broadcast_f5_check(source_worker: "_PageAssistWorker | None" = None) -> None:
    with _assist_lock:
        workers = list(_assist_by_url.values())
    if source_worker in workers:
        workers.remove(source_worker)
        workers.insert(0, source_worker)
    for index, worker in enumerate(workers):
        worker.request_external_check(delay_s=index * _F5_WINDOW_STAGGER_S)
    logger.info("F5 point-check broadcast queued for %d managed page(s)", len(workers))


def _url_key(url: str) -> str:
    parts = urlsplit((url or "").strip())
    path = parts.path.rstrip("/") or "/"
    return f"{parts.scheme}://{parts.netloc}{path}".lower()


def fuzzy_uniform(low: float, high: float) -> float:
    """Sample inclusive-ish range with ordered bounds."""
    a = float(low)
    b = float(high)
    if b < a:
        a, b = b, a
    if a == b:
        return a
    return random.uniform(a, b)


def _parse_exact_points_balance(value: Any) -> int | None:
    """Parse an un-abbreviated balance string such as ``115,380``."""
    if not isinstance(value, str):
        return None
    normalized = value.strip().replace(",", "").replace("\u00a0", "")
    if not re.fullmatch(r"\d+", normalized):
        return None
    try:
        return int(normalized)
    except ValueError:
        return None


def _parse_points_tooltip_balance(tooltips: Any) -> int | None:
    """Return the second exact number from an unambiguous Twitch balance tip."""
    if not isinstance(tooltips, list):
        return None
    candidates: list[int] = []
    for tooltip in tooltips:
        if not isinstance(tooltip, str):
            continue
        matches = re.findall(r"(?<!\d)\d[\d,]*(?!\d)", tooltip)
        if len(matches) != 2:
            continue
        try:
            candidates.append(int(matches[1].replace(",", "")))
        except ValueError:
            continue
    return candidates[0] if len(candidates) == 1 else None


def _points_playback_eligible(snapshot: Any, *, connected: bool) -> bool:
    if not connected or not isinstance(snapshot, dict):
        return False
    current_time = snapshot.get("currentTime")
    return bool(
        snapshot.get("playerPresent")
        and snapshot.get("hasVideo")
        and snapshot.get("readyState", 0) >= 3
        and not snapshot.get("paused")
        and not snapshot.get("ended")
        and not snapshot.get("mediaError")
        and not snapshot.get("errorCode")
        and not snapshot.get("offline")
        and not snapshot.get("gatePresent")
        and isinstance(snapshot.get("videoId"), str)
        and bool(snapshot.get("videoId"))
        and isinstance(current_time, (int, float))
        and not isinstance(current_time, bool)
        and math.isfinite(float(current_time))
    )


def is_standalone_managed_window(
    *,
    managed: bool,
    app_mode: bool,
    new_window: bool,
) -> bool:
    """True when Twitch opens in a managed separate/solo window (UI docs).

    Regular tabs (``managed`` with neither app mode nor new window) are not
    standalone; tab management disabled (``managed=False``) also fails.
    """
    return bool(managed and (app_mode or new_window))


def should_start_page_assist(
    url: str,
    settings: ViewerEngagementSettings | dict[str, Any] | None,
    *,
    managed: bool,
    isolated_profile: bool,
    chromium_family: bool,
    standalone_window: bool,
) -> bool:
    engagement = coerce_viewer_engagement(settings)
    if engagement is None or not engagement.page_assist_active():
        return False
    if not page_assist_runtime_allowed():
        return False
    if not (
        managed
        and isolated_profile
        and chromium_family
        and standalone_window
    ):
        return False
    return is_twitch_url(url)


def _bounded_jitter(
    x: float,
    y: float,
    *,
    left: float,
    top: float,
    width: float,
    height: float,
    offset: int,
) -> tuple[float, float, int, int]:
    """Apply click jitter clamped inside the hit-tested button rect."""
    if offset <= 0 or width <= 2 or height <= 2:
        return x, y, 0, 0
    max_dx = max(0, int(min(offset, (width / 2) - 1)))
    max_dy = max(0, int(min(offset, (height / 2) - 1)))
    dx = random.randint(-max_dx, max_dx) if max_dx else 0
    dy = random.randint(-max_dy, max_dy) if max_dy else 0
    nx = min(max(x + dx, left + 1.0), left + width - 1.0)
    ny = min(max(y + dy, top + 1.0), top + height - 1.0)
    return nx, ny, int(round(nx - x)), int(round(ny - y))


class _PageAssistWorker:
    def __init__(
        self,
        url: str,
        port: int,
        settings: ViewerEngagementSettings,
        user_data_dir: str = "",
    ) -> None:
        self.url = url
        self.port = port
        self.settings = settings
        self.user_data_dir = user_data_dir
        self._theater_confirmed_once = False
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._settings_changed = threading.Event()
        self._external_check_requested = threading.Event()
        self._external_check_lock = threading.Lock()
        self._external_check_due_at: float | None = None
        self._main_frame_navigated = threading.Event()
        self._connected_once = False
        self._initial_main_frame_seen = False
        self._state_lock = threading.Lock()
        self._current_page_url = url
        self._main_frame_loader_id: str | None = None
        self._last_theater_state = "unknown"
        self._last_theater_state_at = 0.0
        self._f5_verification_active = False
        self._f5_retry_count = 0
        self._f5_attempt_deadline: float | None = None
        self._f5_retry_at: float | None = None
        self._f5_last_media_sample: tuple[str, float, float] | None = None
        self._f5_media_advances = 0
        self._f5_was_paused_before_refresh = False
        self._points_last_balance: int | None = None
        self._points_active_watch_seconds = 0.0
        self._points_media_sample: tuple[str, float, float] | None = None
        self._points_stall_attempts = 0
        self._points_stall_exhausted = False
        self._points_next_check_at: float | None = None
        self._confirmed_offline = threading.Event()
        self._playback_health = PlaybackHealth()
        self._playback_state = "verifying"
        self._player_recovery = PlayerRecovery(url, self._stop, self._confirmed_offline)
        self._thread = threading.Thread(
            target=self._run,
            name=f"twitch-page-assist-{_url_key(url)[-24:]}",
            daemon=True,
        )
        self._obs_gate_attempt = "none"
        self._obs_gate_result = "none"
        self._obs_next_snapshot_at = 0.0

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def request_external_check(self, *, delay_s: float = 0.0) -> None:
        """Queue an immediate point-stall check requested by F5."""
        if self._stop.is_set():
            return
        due_at = time.monotonic() + max(0.0, delay_s)
        with self._external_check_lock:
            current_due = self._external_check_due_at
            self._external_check_due_at = (
                due_at if current_due is None else min(current_due, due_at)
            )
            self._external_check_requested.set()
        self._wake.set()

    def _external_check_time(self) -> float | None:
        with self._external_check_lock:
            if not self._external_check_requested.is_set():
                return None
            return self._external_check_due_at

    def _take_external_check_if_due(self, now: float) -> bool:
        with self._external_check_lock:
            if not self._external_check_requested.is_set():
                return False
            if self._external_check_due_at is not None and now < self._external_check_due_at:
                return False
            self._external_check_due_at = None
            self._external_check_requested.clear()
            return True

    def join(self, timeout: float = 2.0) -> None:
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def _sleep(self, seconds: float) -> bool:
        """Sleep interruptibly; return False when stop was requested."""
        return not self._stop.wait(timeout=max(0.0, seconds))

    def _sleep_until_page_action(self, seconds: float) -> bool:
        """Wait unless stop, navigation, or a broadcast F5 check arrives."""
        deadline = time.monotonic() + max(0.0, seconds)
        while not self._stop.is_set():
            now = time.monotonic()
            external_due = self._external_check_time()
            if self._main_frame_navigated.is_set() or (
                external_due is not None and now >= external_due
            ):
                return False
            remaining = deadline - now
            if external_due is not None:
                remaining = min(remaining, max(0.0, external_due - now))
            if remaining <= 0:
                return True
            self._wake.wait(remaining)
            self._wake.clear()
        return False

    def _wait_for_wake(self, seconds: float) -> bool:
        """Wait for work or timeout; kept separate for deterministic timing tests."""
        return self._wake.wait(timeout=max(0.0, seconds))

    def _new_client(self) -> CdpClient:
        client = CdpClient()
        client.set_event_handler(self._on_cdp_event)
        return client

    def _connect(self, client: CdpClient) -> None:
        with self._state_lock:
            url_hint = self._current_page_url
        client.connect(
            self.port,
            url_hint,
            timeout=DEFAULT_CDP_ATTACH_TIMEOUT_S,
        )

    def _refresh_debugging_port(self) -> None:
        """Resolve the profile's current endpoint after a failed CDP attach."""
        try:
            from stream_monitor.cdp_client import (
                acquire_cdp_debugging_port,
                profile_for_cdp_port,
            )

            if not self.user_data_dir:
                self.user_data_dir = profile_for_cdp_port(self.port) or ""
            if not self.user_data_dir:
                return

            attach = acquire_cdp_debugging_port(self.user_data_dir)
        except Exception:
            logger.debug("CDP port refresh failed for %s", self.url, exc_info=True)
            return
        if attach.ok and isinstance(attach.port, int) and attach.port > 0:
            if attach.port != self.port:
                logger.info("Twitch page assist updated CDP port for %s", self.url)
            self.port = attach.port

    def _remember_theater_state(self, state: str) -> None:
        if state not in {"on", "off", "unknown"}:
            state = "unknown"
        with self._state_lock:
            self._last_theater_state = state
            self._last_theater_state_at = time.monotonic()

    def _recent_theater_state(self) -> str:
        with self._state_lock:
            state = self._last_theater_state
            observed_at = self._last_theater_state_at
        if time.monotonic() - observed_at > _PAGE_CHECK_INTERVAL_S * 2:
            return "unknown"
        return state

    def _on_cdp_event(self, method: str, params: dict[str, Any]) -> None:
        if method == "Page.frameNavigated":
            frame = params.get("frame")
            if not isinstance(frame, dict) or frame.get("parentId"):
                return
            frame_url = str(frame.get("url") or "")
            loader_id = str(frame.get("loaderId") or "")
            with self._state_lock:
                previous_loader_id = self._main_frame_loader_id
                if loader_id:
                    self._main_frame_loader_id = loader_id
                if is_twitch_url(frame_url):
                    self._current_page_url = frame_url
            if not self._connected_once and not self._initial_main_frame_seen:
                self._initial_main_frame_seen = True
                return
            if not loader_id or loader_id == previous_loader_id:
                return
            self._main_frame_navigated.set()
            self._wake.set()
            return
        if method == "Page.navigatedWithinDocument":
            frame_url = str(params.get("url") or "")
            if is_twitch_url(frame_url):
                with self._state_lock:
                    self._current_page_url = frame_url
            return
        if (
            method != "Runtime.bindingCalled"
            or params.get("name") != _F5_BROADCAST_BINDING
        ):
            return
        raw = params.get("payload")
        if not isinstance(raw, str):
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict) or not is_twitch_url(
            str(payload.get("url") or ""),
        ):
            return
        state = str(payload.get("theater") or "unknown")
        with self._state_lock:
            self._current_page_url = str(payload["url"])
        self._remember_theater_state(state)
        _broadcast_f5_check(self)

    def _install_f5_broadcast(self, client: CdpClient) -> None:
        try:
            client.call("Runtime.addBinding", {"name": _F5_BROADCAST_BINDING})
            client.call(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": _F5_BROADCAST_SCRIPT},
            )
            client.evaluate(_F5_BROADCAST_SCRIPT)
        except Exception:
            logger.warning(
                "F5 refresh broadcast unavailable for %s",
                self.url,
                exc_info=True,
            )

    def _maybe_observe_snapshot(self, client: CdpClient, *, cdp: str = "connected") -> None:
        """Emit a page snapshot when observation is enabled; never affects assist."""
        try:
            from stream_monitor.watch_observation import (
                SNAPSHOT_INTERVAL_S,
                is_watch_observation_enabled,
                observe_page_snapshot,
                twitch_channel_key_from_url,
            )
        except Exception:
            return
        if not is_watch_observation_enabled():
            return
        now = time.monotonic()
        if now < self._obs_next_snapshot_at:
            return
        self._obs_next_snapshot_at = now + SNAPSHOT_INTERVAL_S
        channel = twitch_channel_key_from_url(self.url)
        if not channel:
            return
        if cdp != "connected":
            observe_page_snapshot(
                channel_key_value=channel,
                cdp=cdp,
                content_gate_attempt=self._obs_gate_attempt,
                content_gate_result=self._obs_gate_result,
            )
            return
        try:
            hit = client.evaluate(_PAGE_OBS_JS)
        except Exception:
            observe_page_snapshot(
                channel_key_value=channel,
                cdp="unknown",
                content_gate_attempt=self._obs_gate_attempt,
                content_gate_result=self._obs_gate_result,
            )
            return
        if not isinstance(hit, dict):
            observe_page_snapshot(
                channel_key_value=channel,
                cdp="unknown",
                content_gate_attempt=self._obs_gate_attempt,
                content_gate_result=self._obs_gate_result,
            )
            return
        if "url" in hit and not page_urls_match(self.url, str(hit.get("url", ""))):
            observe_page_snapshot(channel_key_value=channel, cdp="unknown")
            return
        visibility = str(hit.get("visibilityState") or "unknown")
        if visibility not in {"visible", "hidden", "prerender", "unloaded"}:
            visibility = "unknown"
        ready_state = hit.get("readyState")
        ready_int = int(ready_state) if isinstance(ready_state, (int, float)) else None
        current_time = hit.get("currentTime")
        media_time = (
            float(current_time)
            if isinstance(current_time, (int, float))
            else None
        )
        paused = hit.get("paused")
        observe_page_snapshot(
            channel_key_value=channel,
            cdp="connected",
            has_video=bool(hit.get("hasVideo")) if "hasVideo" in hit else None,
            ready_state=ready_int,
            paused=bool(paused) if isinstance(paused, bool) else None,
            media_current_time=media_time,
            content_gate_present=(
                bool(hit.get("gatePresent")) if "gatePresent" in hit else None
            ),
            content_gate_attempt=self._obs_gate_attempt,
            content_gate_result=self._obs_gate_result,
            document_visibility=visibility,
        )

    def _run(self) -> None:
        client = self._new_client()
        try:
            attach_attempt = 0
            while not self._stop.is_set():
                try:
                    self._connect(client)
                    break
                except Exception:
                    client.close()
                    self._maybe_observe_snapshot(client, cdp="unavailable")
                    if self._stop.is_set():
                        return
                    self._refresh_debugging_port()
                    attach_attempt += 1
                    publish_page_assist_status(self.url, "reconnecting")
                    retry_in = min(
                        _RECONNECT_BACKOFF_MAX_S,
                        float(2 ** min(attach_attempt - 1, 5)),
                    )
                    logger.warning(
                        "Twitch page assist attach failed for %s; retrying in %.1fs "
                        "(attempt=%d)",
                        self.url,
                        retry_in,
                        attach_attempt,
                        exc_info=True,
                    )
                    if not self._sleep(retry_in):
                        return
                    client = self._new_client()
            if self._stop.is_set():
                return
            self._install_f5_broadcast(client)
            self._connected_once = True
            publish_page_assist_status(self.url, "")
            logger.info("Twitch page assist attached via CDP for %s", self.url)
            self._obs_next_snapshot_at = 0.0
            self._maybe_observe_snapshot(client, cdp="connected")
            next_refresh_at = self._schedule_refresh()
            self._points_next_check_at = time.monotonic() + _PAGE_CHECK_INTERVAL_S
            claim_deadline = time.monotonic()
            reconnect_attempt = 0
            while not self._stop.is_set():
                self._run_gate_and_theater(client)
                next_page_check_at = self._next_page_check_at()
                while not self._stop.is_set():
                    now = time.monotonic()
                    if self._settings_changed.is_set():
                        self._settings_changed.clear()
                        claim_deadline = now
                        next_refresh_at = self._schedule_refresh()
                    if not client.connected:
                        self._reset_points_media_sample()
                        self._playback_health.reset_samples()
                        self._maybe_observe_snapshot(client, cdp="unavailable")
                        self._player_recovery.disconnected()
                        client.close()
                        client = self._new_client()
                        try:
                            self._connect(client)
                        except Exception:
                            client.close()
                            self._refresh_debugging_port()
                            publish_page_assist_status(self.url, "reconnecting")
                            if self._stop.is_set():
                                return
                            reconnect_attempt += 1
                            retry_in = min(
                                _RECONNECT_BACKOFF_MAX_S,
                                float(2 ** min(reconnect_attempt - 1, 5)),
                            )
                            logger.warning(
                                "Twitch page assist reconnect failed for %s; retrying in %.1fs "
                                "(attempt=%d)",
                                self.url,
                                retry_in,
                                reconnect_attempt,
                                exc_info=True,
                            )
                            if not self._sleep(retry_in):
                                return
                            continue
                        if self._stop.is_set():
                            return
                        self._install_f5_broadcast(client)
                        self._connected_once = True
                        reconnect_notice = self._player_recovery.notice
                        if self._f5_verification_active:
                            reconnect_notice = (
                                "f5_retrying"
                                if self._f5_retry_at is not None
                                else "f5_verifying"
                            )
                        publish_page_assist_status(self.url, reconnect_notice)
                        if self._points_stall_exhausted and not self._f5_verification_active:
                            publish_page_assist_status(
                                self.url, "points_stall_exhausted",
                            )
                        logger.info("Twitch page assist reconnected for %s", self.url)
                        self._run_gate_and_theater(client)
                        next_page_check_at = self._next_page_check_at()
                        reconnect_attempt = 0
                        now = time.monotonic()
                        self._points_next_check_at = now + _PAGE_CHECK_INTERVAL_S

                    if self._take_external_check_if_due(now):
                        if self._check_points_stall(client, user_requested=True):
                            next_refresh_at = self._schedule_refresh()
                            break  # restart gate → theater after reload
                        self._points_next_check_at = (
                            time.monotonic() + _PAGE_CHECK_INTERVAL_S
                        )
                        if not client.connected:
                            continue

                    if self._main_frame_navigated.is_set():
                        self._main_frame_navigated.clear()
                        self._restore_theater_after_navigation()
                        self._run_gate_and_theater(client)
                        next_page_check_at = self._next_page_check_at()
                        now = time.monotonic()

                    self._maybe_observe_snapshot(client, cdp="connected")
                    points_due_at = self._points_next_check_at
                    if (
                        points_due_at is not None
                        and now >= points_due_at
                        and not self._confirmed_offline.is_set()
                    ):
                        if self._check_points_stall(client, user_requested=False):
                            next_refresh_at = self._schedule_refresh()
                            break  # restart gate → theater after reload
                        self._points_next_check_at = (
                            time.monotonic() + _PAGE_CHECK_INTERVAL_S
                        )
                        now = time.monotonic()

                    if (
                        self.settings.auto_refresh
                        and not self._player_recovery.active
                        and not self._f5_verification_active
                        and self._external_check_time() is None
                        and not self._confirmed_offline.is_set()
                        and next_refresh_at is not None
                        and now >= next_refresh_at
                    ):
                        if not self._reload_page(client, reason="scheduled refresh"):
                            if not client.connected:
                                continue
                            if self._stop.is_set():
                                return
                        else:
                            next_refresh_at = self._schedule_refresh()
                            self._start_f5_verification(reset_budget=True)
                            break  # restart gate → theater after reload
                        next_refresh_at = self._schedule_refresh()
                        if self._confirmed_offline.is_set():
                            return

                    if now >= next_page_check_at:
                        if self._check_page(client):
                            next_refresh_at = self._schedule_refresh()
                            break  # restart gate → theater after recovery reload
                        next_page_check_at = self._next_page_check_at()

                    if (self.settings.claim_channel_points and now >= claim_deadline
                            and not self._player_recovery.active):
                        self._try_claim(client)
                        # Continue observing at the configured interval after
                        # success too; a new visible bonus must not be held by
                        # an unrelated fixed 12–18 minute cooldown.
                        claim_deadline = time.monotonic() + fuzzy_uniform(
                            self.settings.claim_poll_seconds_min,
                            self.settings.claim_poll_seconds_max,
                        )
                    wait_s = fuzzy_uniform(
                        self.settings.claim_poll_seconds_min,
                        self.settings.claim_poll_seconds_max,
                    )
                    wait_s = min(wait_s, max(0.1, next_page_check_at - time.monotonic()))
                    if self.settings.claim_channel_points and not self._player_recovery.active:
                        wait_s = min(wait_s, max(0.1, claim_deadline - time.monotonic()))
                    if next_refresh_at is not None and not self._player_recovery.active:
                        wait_s = min(wait_s, max(1.0, next_refresh_at - time.monotonic()))
                    external_check_at = self._external_check_time()
                    if external_check_at is not None:
                        wait_s = min(
                            wait_s,
                            max(0.1, external_check_at - time.monotonic()),
                        )
                    if self._points_next_check_at is not None:
                        wait_s = min(
                            wait_s,
                            max(0.1, self._points_next_check_at - time.monotonic()),
                        )
                    # Opt-in observation only: wake for 30s snapshots without
                    # changing claim/refresh deadlines or disabled-path waits.
                    try:
                        from stream_monitor.watch_observation import (
                            is_watch_observation_enabled,
                        )

                        if is_watch_observation_enabled():
                            wait_s = min(
                                wait_s,
                                max(1.0, self._obs_next_snapshot_at - time.monotonic()),
                            )
                    except Exception:
                        pass
                    self._wait_for_wake(wait_s)
                    self._wake.clear()
                    if self._stop.is_set():
                        return
        except Exception:
            if not self._stop.is_set():
                publish_page_assist_status(self.url, "unavailable")
            logger.exception("Twitch page assist stopped for %s", self.url)
            try:
                from stream_monitor.watch_observation import (
                    is_watch_observation_enabled,
                    observe_page_snapshot,
                    twitch_channel_key_from_url,
                )

                if is_watch_observation_enabled():
                    channel = twitch_channel_key_from_url(self.url)
                    if channel:
                        observe_page_snapshot(
                            channel_key_value=channel,
                            cdp="unavailable",
                            content_gate_attempt=self._obs_gate_attempt,
                            content_gate_result=self._obs_gate_result,
                        )
            except Exception:
                logger.debug("Watch observation CDP-failure note skipped", exc_info=True)
        finally:
            client.close()
            with _assist_lock:
                current = _assist_by_url.get(_url_key(self.url))
                if current is self:
                    _assist_by_url.pop(_url_key(self.url), None)
                if (current is None or current is self) and self._f5_verification_active:
                    self._f5_verification_active = False
                    publish_page_assist_status(self.url, "")

    def _read_points_balance(self, client: CdpClient) -> int | None:
        """Read the exact second balance; unknown UI states stay unknown."""
        if self._stop.is_set() or not client.connected:
            return None
        try:
            target = client.evaluate(_FIND_POINTS_BALANCE_JS)
        except Exception:
            logger.debug("Twitch points balance target probe failed for %s", self.url,
                         exc_info=True)
            return None
        if not isinstance(target, dict) or not target.get("found"):
            logger.debug("Twitch second points balance unavailable for %s", self.url)
            return None

        exact = _parse_exact_points_balance(target.get("displayed"))
        if exact is not None:
            return exact

        hover = target.get("hover")
        if not isinstance(hover, dict):
            logger.debug("Twitch second points tooltip target unavailable for %s", self.url)
            return None
        try:
            x = float(hover.get("x"))
            y = float(hover.get("y"))
            if not math.isfinite(x) or not math.isfinite(y):
                return None
            # Only hover the exact second-balance icon. No click or redemption
            # action is sent; compact text is never guessed or scaled.
            # Twitch can dismiss a tooltip while the pointer stays on its icon.
            # Leave first so every sample produces a fresh pointer-enter event.
            client.call("Input.dispatchMouseEvent", {
                "type": "mouseMoved", "x": 0.0, "y": 0.0,
            })
            client.call("Input.dispatchMouseEvent", {
                "type": "mouseMoved", "x": x, "y": y,
            })
        except Exception:
            logger.debug("Twitch second points hover failed for %s", self.url,
                         exc_info=True)
            return None

        deadline = time.monotonic() + _POINTS_TOOLTIP_WAIT_S
        while not self._stop.is_set() and time.monotonic() < deadline:
            if not client.connected:
                return None
            try:
                tooltips = client.evaluate(_READ_POINTS_TOOLTIP_JS)
            except Exception:
                logger.debug("Twitch second points tooltip probe failed for %s", self.url,
                             exc_info=True)
                return None
            exact = _parse_points_tooltip_balance(tooltips)
            if exact is not None:
                return exact
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not self._sleep(
                min(_POINTS_TOOLTIP_POLL_S, remaining),
            ):
                break
        logger.debug("Twitch second points tooltip has no exact value for %s", self.url)
        return None

    def _reset_points_media_sample(self) -> None:
        self._points_media_sample = None

    def _record_points_observation(
        self,
        balance: int | None,
        player_snapshot: Any,
        now: float,
        *,
        connected: bool,
    ) -> tuple[bool, bool]:
        """Update per-page balance and eligible-watch history.

        Returns ``(stall_window_elapsed, eligible_sample)``. Wall time only
        accrues when two adjacent connected samples show the same video moving
        at a plausible rate; unknown balance or playback states break the pair.
        """
        if balance is None or not connected:
            self._reset_points_media_sample()
            return False, False

        if self._points_last_balance is None:
            self._points_last_balance = balance
        elif balance > self._points_last_balance:
            previous_balance = self._points_last_balance
            was_exhausted = self._points_stall_exhausted
            self._points_last_balance = balance
            self._points_active_watch_seconds = 0.0
            self._points_stall_attempts = 0
            self._points_stall_exhausted = False
            self._reset_points_media_sample()
            logger.info(
                "Twitch second points balance increased for %s: %d -> %d; "
                "stall timer and retry allowance reset",
                self.url, previous_balance, balance,
            )
            if was_exhausted and not self._f5_verification_active:
                publish_page_assist_status(self.url, "")
        else:
            # Spending does not count as new watch credit, but the latest exact
            # amount becomes the comparison baseline for a later increase.
            self._points_last_balance = balance

        eligible = _points_playback_eligible(
            player_snapshot, connected=connected,
        )
        if not eligible:
            self._reset_points_media_sample()
            return False, False

        video_id = player_snapshot["videoId"]
        current_time = float(player_snapshot["currentTime"])
        previous = self._points_media_sample
        if previous is not None:
            previous_id, previous_time, previous_at = previous
            elapsed = now - previous_at
            media_elapsed = current_time - previous_time
            if (
                previous_id == video_id
                and 0.0 < elapsed <= _PAGE_CHECK_INTERVAL_S * 2
                and media_elapsed >= max(0.1, elapsed * 0.5)
                and media_elapsed <= elapsed * 2.0
            ):
                self._points_active_watch_seconds += elapsed
        self._points_media_sample = (video_id, current_time, now)
        return self._points_active_watch_seconds >= _POINTS_STALL_WINDOW_S, True

    def _reset_points_window_after_reload(self) -> None:
        self._points_active_watch_seconds = 0.0
        self._reset_points_media_sample()
        self._points_next_check_at = time.monotonic() + _PAGE_CHECK_INTERVAL_S

    def _check_points_stall(
        self,
        client: CdpClient,
        *,
        user_requested: bool,
    ) -> bool:
        """Observe the balance and conditionally reload this page once."""
        if self._stop.is_set() or not client.connected:
            self._reset_points_media_sample()
            return False
        try:
            balance = self._read_points_balance(client)
        except Exception:
            logger.debug("Twitch points balance read failed for %s", self.url,
                         exc_info=True)
            balance = None
        try:
            player_snapshot = client.evaluate(PLAYER_SNAPSHOT_JS)
        except Exception:
            logger.debug("Twitch points playback probe failed for %s", self.url,
                         exc_info=True)
            player_snapshot = None
        if not isinstance(player_snapshot, dict) or not page_urls_match(
            self.url, str(player_snapshot.get("url", "")),
        ):
            # A raid keeps the original window owner, but the destination's
            # points must never be credited to the original broadcast.
            balance = None
            player_snapshot = None
        stalled, eligible = self._record_points_observation(
            balance,
            player_snapshot,
            time.monotonic(),
            connected=client.connected,
        )
        self._observe_watch_progress(balance, eligible=eligible, stalled=stalled)

        if user_requested and eligible and self._points_stall_exhausted:
            self._points_stall_attempts = 0
            self._points_stall_exhausted = False
            publish_page_assist_status(self.url, "")
            logger.info("F5 re-armed points-stall cycle for %s", self.url)

        if not stalled:
            return False
        if self._points_stall_attempts >= _POINTS_STALL_RETRY_LIMIT:
            if not self._points_stall_exhausted:
                self._points_stall_exhausted = True
                publish_page_assist_status(self.url, "points_stall_exhausted")
            return False
        if not user_requested and not self.settings.points_stall_refresh_enabled:
            return False
        if (
            not eligible
            or self._confirmed_offline.is_set()
            or self._player_recovery.active
            or self._f5_verification_active
            or not client.connected
        ):
            return False

        reason = "F5 points-stall check" if user_requested else "points-stall auto refresh"
        if not self._reload_page(client, reason=reason):
            return False
        self._points_stall_attempts += 1
        self._start_f5_verification(reset_budget=True)
        logger.info(
            "Twitch points-stall reload completed for %s attempt=%d/%d balance=%d",
            self.url, self._points_stall_attempts, _POINTS_STALL_RETRY_LIMIT,
            balance,
        )
        if self._points_stall_attempts >= _POINTS_STALL_RETRY_LIMIT:
            self._points_stall_exhausted = True
            publish_page_assist_status(self.url, "points_stall_exhausted")
        return True

    def _observe_watch_progress(
        self, balance: int | None, *, eligible: bool, stalled: bool,
    ) -> None:
        from stream_monitor.watch_observation import (
            get_watch_observation,
            twitch_channel_key_from_url,
        )

        service = get_watch_observation()
        if service is None:
            return
        service.emit("watch.progress", {
            "channel_key": twitch_channel_key_from_url(self.url),
            "playback_state": self._playback_state,
            "points_balance": balance,
            "eligible_sample": eligible,
            "eligible_watch_seconds": self._points_active_watch_seconds,
            "points_stalled": stalled,
            "points_reload_attempts": self._points_stall_attempts,
            "managed_pages": len(active_page_assist_urls()),
            "server_watch_credit": "unknown",
        })

    def _check_playback_health(self, client: CdpClient) -> bool:
        if self._stop.is_set() or self._confirmed_offline.is_set():
            self._playback_health.reset_samples()
            return False
        if self._player_recovery.active or self._f5_verification_active:
            self._playback_health.reset_samples()
            self._playback_state = "verifying"
            return False
        try:
            snapshot = client.evaluate(PLAYER_SNAPSHOT_JS)
        except Exception:
            snapshot = None
        if isinstance(snapshot, dict) and not page_urls_match(
            self.url, str(snapshot.get("url", "")),
        ):
            snapshot = None
        self._playback_state = self._playback_health.observe(
            snapshot, time.monotonic(), connected=client.connected,
        )
        if self._playback_state != "stalled":
            return False
        if not self._playback_health.can_reload:
            publish_page_assist_status(self.url, "f5_unverified")
            return False
        if self._reload_page(client, reason="media stalled for 90 seconds"):
            self._playback_health.reloaded()
            self._start_f5_verification(reset_budget=True)
            return True
        return False

    def _check_page(self, client: CdpClient) -> bool:
        """Reconcile the page and verify any pending F5 recovery cycle."""
        if self._stop.is_set() or not client.connected:
            return False
        if self._confirmed_offline.is_set():
            if self._f5_verification_active:
                self._finish_f5_verification(
                    "f5_unverified", outcome="monitor_confirmed_offline",
                )
            return False
        player_recovery_active = False
        if self.settings.page_assist_active():
            self._player_recovery.step(client)
            if self._stop.is_set():
                return False
            player_recovery_active = self._player_recovery.active
        if self._check_playback_health(client):
            return True
        ready = self._probe_page_ready(client)
        if ready.get("gatePresent") and self.settings.accept_content_gate:
            self._try_accept_gate(client)
            if not self._sleep(_THEATER_VERIFY_WAIT_S):
                return False
            ready = self._probe_page_ready(client)
        if ready.get("gatePresent"):
            if self._f5_verification_active:
                self._finish_f5_verification(
                    "f5_unverified", outcome="content_gate_unresolved",
                )
            logger.info("Twitch page assist periodic page check for %s: content gate present",
                        self.url)
            return False
        if player_recovery_active:
            if self._f5_verification_active:
                self._check_f5_verification(client)
            return False
        if self._f5_verification_active and self._check_f5_verification(client):
            return True
        if self.settings.theater_mode and ready.get("playbackReady"):
            self._ensure_theater(client)
        if self._stop.is_set():
            return False
        chat = (self._ensure_chat_visible(client) if self.settings.claim_channel_points
                else self._chat_snapshot(client).get("status", "hidden"))
        theater = self._theater_snapshot(client).get("state", "unknown")
        playback = self._playback_state
        if ready.get("hasVideo") and ready.get("paused"):
            playback = "paused"
        logger.info("Twitch page assist periodic page check for %s: "
                    "playback=%s theater=%s chat=%s", self.url, playback, theater, chat)
        return False

    def _start_f5_verification(self, *, reset_budget: bool) -> None:
        if reset_budget:
            self._f5_retry_count = 0
        self._f5_verification_active = True
        self._f5_attempt_deadline = (
            time.monotonic() + _F5_PLAYBACK_VERIFY_TIMEOUT_S
        )
        self._f5_retry_at = None
        self._f5_last_media_sample = None
        self._f5_media_advances = 0
        publish_page_assist_status(self.url, "f5_verifying")
        logger.info(
            "F5 local playback verification started for %s retry=%d/%d",
            self.url,
            self._f5_retry_count,
            len(_F5_PAGE_RETRY_DELAYS_S),
        )

    def _finish_f5_verification(self, reason: str, *, outcome: str) -> None:
        self._f5_verification_active = False
        self._f5_attempt_deadline = None
        self._f5_retry_at = None
        self._f5_last_media_sample = None
        self._f5_media_advances = 0
        publish_page_assist_status(
            self.url,
            "points_stall_exhausted" if self._points_stall_exhausted else reason,
        )
        logger.info(
            "F5 local playback verification finished for %s outcome=%s retries=%d/%d",
            self.url,
            outcome,
            self._f5_retry_count,
            len(_F5_PAGE_RETRY_DELAYS_S),
        )

    def _check_f5_verification(self, client: CdpClient) -> bool:
        """Verify local playback after F5; return True when a retry reload ran."""
        if not self._f5_verification_active or self._stop.is_set():
            return False
        now = time.monotonic()
        if self._confirmed_offline.is_set():
            self._finish_f5_verification(
                "f5_unverified", outcome="monitor_confirmed_offline",
            )
            return False

        try:
            snapshot = client.evaluate(PLAYER_SNAPSHOT_JS)
        except Exception:
            logger.debug("F5 playback probe failed for %s", self.url, exc_info=True)
            snapshot = None

        if isinstance(snapshot, dict):
            if snapshot.get("offline"):
                self._finish_f5_verification(
                    "f5_unverified", outcome="player_offline",
                )
                return False
            if snapshot.get("gatePresent"):
                self._finish_f5_verification(
                    "f5_unverified", outcome="content_gate_unresolved",
                )
                return False
            if (
                snapshot.get("hasVideo")
                and snapshot.get("readyState", 0) >= 3
                and snapshot.get("paused")
                and not snapshot.get("mediaError")
            ):
                self._finish_f5_verification(
                    "f5_paused", outcome="user_paused_or_autoplay_blocked",
                )
                return False
            if snapshot.get("errorCode") == "2000":
                self._f5_last_media_sample = None
                self._f5_media_advances = 0
                self._f5_retry_at = None
                if (
                    self._player_recovery.notice == "player_2000_exhausted"
                    or not self._player_recovery.active
                ):
                    self._finish_f5_verification(
                        "f5_unverified", outcome="player_2000_unresolved",
                    )
                else:
                    # The bounded player-button recovery owns #2000. Do not
                    # bypass its budget with full-page retries.
                    self._f5_attempt_deadline = (
                        now + _F5_PLAYBACK_VERIFY_TIMEOUT_S
                    )
                return False

            current_time = snapshot.get("currentTime")
            video_id = snapshot.get("videoId")
            valid_sample = (
                snapshot.get("hasVideo")
                and snapshot.get("readyState", 0) >= 3
                and not snapshot.get("paused")
                and not snapshot.get("ended")
                and not snapshot.get("mediaError")
                and isinstance(video_id, str)
                and bool(video_id)
                and isinstance(current_time, (int, float))
                and not isinstance(current_time, bool)
                and math.isfinite(float(current_time))
            )
            if valid_sample:
                previous = self._f5_last_media_sample
                if previous is None:
                    self._f5_last_media_sample = (
                        video_id, float(current_time), now,
                    )
                else:
                    sample_gap = now - previous[2]
                    if sample_gap >= _F5_PLAYBACK_VERIFY_INTERVAL_S:
                        if (
                            sample_gap <= _F5_PLAYBACK_VERIFY_MAX_GAP_S
                            and previous[0] == video_id
                            and float(current_time) - previous[1] >= 0.1
                        ):
                            self._f5_media_advances += 1
                        else:
                            self._f5_media_advances = 0
                        self._f5_last_media_sample = (
                            video_id, float(current_time), now,
                        )
                        if self._f5_media_advances >= _F5_REQUIRED_MEDIA_ADVANCES:
                            self._finish_f5_verification(
                                "", outcome="local_media_time_advanced",
                            )
                            return False
            else:
                self._f5_last_media_sample = None
                self._f5_media_advances = 0

        if self._f5_retry_at is not None:
            if now < self._f5_retry_at:
                return False
            if self._player_recovery.active:
                self._finish_f5_verification(
                    "f5_unverified", outcome="player_recovery_unresolved",
                )
                return False
            retry_number = self._f5_retry_count
            self._f5_retry_at = None
            if self._reload_page(
                client, reason=f"F5 recovery retry {retry_number}",
            ):
                self._start_f5_verification(reset_budget=False)
                return True
            self._finish_f5_verification(
                "f5_unverified", outcome="recovery_reload_failed",
            )
            return False

        deadline = self._f5_attempt_deadline
        if deadline is None or now < deadline:
            return False
        if self._player_recovery.active:
            self._finish_f5_verification(
                "f5_unverified", outcome="player_recovery_unresolved",
            )
            return False
        if self._f5_was_paused_before_refresh:
            self._finish_f5_verification(
                "f5_paused", outcome="paused_before_f5_refresh",
            )
            return False
        if self._f5_retry_count < len(_F5_PAGE_RETRY_DELAYS_S):
            retry_delay = _F5_PAGE_RETRY_DELAYS_S[self._f5_retry_count]
            self._f5_retry_count += 1
            self._f5_retry_at = now + retry_delay
            publish_page_assist_status(self.url, "f5_retrying")
            logger.warning(
                "F5 playback not confirmed for %s; page retry %d/%d in %.1fs",
                self.url,
                self._f5_retry_count,
                len(_F5_PAGE_RETRY_DELAYS_S),
                retry_delay,
            )
            return False
        self._finish_f5_verification(
            "f5_unverified", outcome="retry_budget_exhausted",
        )
        return False

    def _next_page_check_at(self) -> float:
        now = time.monotonic()
        next_check_at = now + _PAGE_CHECK_INTERVAL_S
        if self._player_recovery.active:
            next_check_at = min(
                next_check_at,
                max(now + 0.1, self._player_recovery.next_check_at),
            )
        if self._f5_verification_active:
            next_check_at = min(
                next_check_at,
                now + _F5_PLAYBACK_VERIFY_INTERVAL_S,
            )
        return next_check_at

    def _schedule_refresh(self) -> float | None:
        if not self.settings.auto_refresh:
            return None
        minutes = fuzzy_uniform(
            self.settings.refresh_minutes_min,
            self.settings.refresh_minutes_max,
        )
        return time.monotonic() + minutes * 60.0

    def _run_gate_and_theater(self, client: CdpClient) -> None:
        if (
            self._stop.is_set()
            or self._confirmed_offline.is_set()
            or not client.connected
        ):
            return
        if self.settings.page_assist_active():
            self._player_recovery.step(client)
            if self._player_recovery.active or self._stop.is_set():
                return
        if self.settings.accept_content_gate:
            if not self._wait_for_gate_or_ready(client):
                return
        if self.settings.theater_mode and not self._wait_for_player_ready(client):
            return
        if self.settings.theater_mode and not self._stop.is_set():
            if not self._sleep_until_page_action(float(self.settings.theater_delay_seconds)):
                return
            self._ensure_theater(client)
        if self.settings.claim_channel_points and not self._stop.is_set():
            self._ensure_chat_visible(client)

    def _wait_for_gate_or_ready(self, client: CdpClient) -> bool:
        """Accept a gate when present; advance promptly once playback is ready.

        Gate overlays may appear slightly after the player is ready, so a short
        confirmation window is kept after the first ready reading. Stop remains
        cooperative throughout.
        """
        deadline = time.monotonic() + _GATE_TIMEOUT_S
        ready_streak = 0
        while (
            not self._stop.is_set()
            and not self._confirmed_offline.is_set()
            and client.connected
            and time.monotonic() < deadline
        ):
            status = self._probe_page_ready(client)
            if status.get("gatePresent"):
                if self._try_accept_gate(client):
                    return True
            elif status.get("playbackReady"):
                ready_streak += 1
                if ready_streak >= _READY_CONFIRM_POLLS:
                    logger.info(
                        "Twitch page assist playback ready without content gate for %s",
                        self.url,
                    )
                    return True
            else:
                ready_streak = 0
            if not self._sleep_until_page_action(_GATE_POLL_S):
                return False
        if self._stop.is_set() or self._confirmed_offline.is_set() or not client.connected:
            return False
        logger.info(
            "Twitch page assist gate wait finished without gate for %s",
            self.url,
        )
        return True

    def _wait_for_player_ready(self, client: CdpClient) -> bool:
        """Wait for a usable player before trying theater controls after load."""
        deadline = time.monotonic() + _GATE_TIMEOUT_S
        ready_streak = 0
        while (
            not self._stop.is_set()
            and not self._confirmed_offline.is_set()
            and client.connected
            and time.monotonic() < deadline
        ):
            status = self._probe_page_ready(client)
            if status.get("gatePresent"):
                return False
            if status.get("playbackReady"):
                ready_streak += 1
                if ready_streak >= _READY_CONFIRM_POLLS:
                    return True
            else:
                ready_streak = 0
            if not self._sleep_until_page_action(_GATE_POLL_S):
                return False
        return False

    def _probe_page_ready(self, client: CdpClient) -> dict[str, Any]:
        try:
            hit = client.evaluate(_PAGE_READY_JS)
        except Exception:
            logger.debug("Page ready probe failed for %s", self.url, exc_info=True)
            return {}
        return hit if isinstance(hit, dict) else {}

    def _theater_snapshot(self, client: CdpClient) -> dict[str, Any]:
        try:
            hit = client.evaluate(_THEATER_STATE_JS)
        except Exception:
            logger.debug("Theater probe failed for %s", self.url, exc_info=True)
            self._remember_theater_state("unknown")
            return {"state": "unknown", "control": None}
        if not isinstance(hit, dict):
            self._remember_theater_state("unknown")
            return {"state": "unknown", "control": None}
        state = str(hit.get("state") or "unknown")
        if state not in {"on", "off", "unknown"}:
            state = "unknown"
        self._remember_theater_state(state)
        control = hit.get("control")
        return {
            "state": state,
            "control": control if isinstance(control, dict) else None,
            "controlState": hit.get("controlState"),
            "theaterLayout": bool(hit.get("theaterLayout")),
        }

    def _restore_theater_after_navigation(self) -> bool:
        """Reinitialize theater only when it was recently observed active."""
        if (
            self.settings.theater_mode
            and self._recent_theater_state() == "on"
        ):
            self._theater_confirmed_once = False
            return True
        return False

    def _reload_page(self, client: CdpClient, *, reason: str) -> bool:
        user_requested = reason == "F5 points-stall check"
        is_recovery_retry = reason.startswith("F5 recovery retry")
        if not is_recovery_retry:
            self._f5_was_paused_before_refresh = False
            try:
                before = client.evaluate(PLAYER_SNAPSHOT_JS)
                self._f5_was_paused_before_refresh = bool(
                    isinstance(before, dict)
                    and before.get("hasVideo")
                    and before.get("readyState", 0) >= 3
                    and before.get("paused")
                    and not before.get("mediaError")
                )
            except Exception:
                logger.debug(
                    "Pre-F5 playback state unavailable for %s",
                    self.url,
                    exc_info=True,
                )
        if self._stop.is_set() or (
            self._confirmed_offline.is_set() and not user_requested
        ):
            return False
        if self._f5_was_paused_before_refresh and not user_requested:
            return False
        theater_was_on = (
            self.settings.theater_mode
            and self._theater_snapshot(client).get("state") == "on"
        )
        if self._stop.is_set() or (
            self._confirmed_offline.is_set() and not user_requested
        ):
            return False
        logger.info("Twitch page assist reloading %s reason=%s", self.url, reason)
        try:
            client.reload()
        except Exception:
            publish_page_assist_status(self.url, "unavailable")
            logger.exception("Page.reload failed for %s", self.url)
            return False
        self._player_recovery.page_reloaded(action=reason.replace(" ", "_"))
        self._playback_health.reset_samples()
        self._reset_points_window_after_reload()
        if theater_was_on:
            self._theater_confirmed_once = False
        if not self._sleep(3.0):
            return False
        self._main_frame_navigated.clear()
        return True

    def _focus_player(self, client: CdpClient) -> None:
        try:
            client.evaluate(_FOCUS_PLAYER_JS)
        except Exception:
            logger.debug("Player focus failed for %s", self.url, exc_info=True)

    def _ensure_theater(self, client: CdpClient) -> None:
        """Initialize theater once per worker; preserve subsequent user choices."""
        if self._stop.is_set() or self._theater_confirmed_once:
            return
        # Twitch hides its controls until the pointer enters the player.
        try:
            hover = client.evaluate(_PLAYER_HOVER_JS)
            if isinstance(hover, dict):
                client.call("Input.dispatchMouseEvent", {"type": "mouseMoved", **hover})
                if not self._sleep(_THEATER_VERIFY_WAIT_S):
                    return
        except Exception:
            logger.debug("Player hover failed for %s", self.url, exc_info=True)
        snap = self._theater_snapshot(client)
        state = str(snap["state"])
        if state == "on":
            self._theater_confirmed_once = True
            logger.info(
                "Twitch page assist theater already active for %s", self.url
            )
            return
        if state != "off":
            logger.warning(
                "Twitch page assist theater skipped for %s "
                "(state=%s; unknown must not toggle)",
                self.url,
                state,
            )
            return

        self._focus_player(client)
        control = snap.get("control") if isinstance(snap.get("control"), dict) else None

        # Prefer exact DOM control when we have explicit OFF evidence.
        if (
            isinstance(control, dict)
            and control.get("found")
            and not self._stop.is_set()
        ):
            try:
                client.click_at(float(control.get("x", 0)), float(control.get("y", 0)))
            except Exception:
                logger.exception("Theater control click failed for %s", self.url)
                return
            if not self._sleep(_THEATER_VERIFY_WAIT_S):
                return
            snap = self._theater_snapshot(client)
            state = str(snap["state"])
            if state == "on":
                self._theater_confirmed_once = True
                logger.info(
                    "Twitch page assist theater enabled via control for %s",
                    self.url,
                )
                return
            if state != "off":
                logger.warning(
                    "Twitch page assist theater unverified after control for %s "
                    "(state=%s; no further toggles)",
                    self.url,
                    state,
                )
                return

        # Optional single Alt+T only while still known off after DOM attempt.
        if state == "off" and not self._stop.is_set():
            try:
                client.send_alt_t()
            except Exception:
                logger.exception("Failed to send theater Alt+T for %s", self.url)
                return
            if not self._sleep(_THEATER_VERIFY_WAIT_S):
                return
            snap = self._theater_snapshot(client)
            state = str(snap["state"])
            if state == "on":
                self._theater_confirmed_once = True
                logger.info(
                    "Twitch page assist theater enabled via keyboard for %s",
                    self.url,
                )
                return
            if state != "off":
                logger.warning(
                    "Twitch page assist theater unverified after Alt+T for %s "
                    "(state=%s; no repeated toggle)",
                    self.url,
                    state,
                )
                return

        if state == "on":
            self._theater_confirmed_once = True
            logger.info("Twitch page assist theater verified for %s", self.url)
        else:
            logger.warning(
                "Twitch page assist theater failed for %s (state=%s)",
                self.url,
                state,
            )

    def _chat_snapshot(self, client: CdpClient) -> dict[str, Any]:
        try:
            hit = client.evaluate(_CHAT_STATE_JS)
        except Exception:
            logger.debug("Chat probe failed for %s", self.url, exc_info=True)
            return {"status": "hidden", "chatVisible": False}
        if not isinstance(hit, dict):
            return {"status": "hidden", "chatVisible": False}
        status = str(hit.get("status") or "hidden")
        if status not in {"visible", "collapsed", "hidden", "not_logged_in"}:
            status = "hidden"
        return {
            "status": status,
            "chatVisible": bool(hit.get("chatVisible")),
            "collapsed": bool(hit.get("collapsed")),
            "expandControl": (
                hit.get("expandControl")
                if isinstance(hit.get("expandControl"), dict)
                else None
            ),
            "notLoggedIn": bool(hit.get("notLoggedIn")),
        }

    def _ensure_chat_visible(self, client: CdpClient) -> str:
        """Make chat visible before points; return status for logging/claim gate.

        Returns one of: visible, hidden, collapsed, not_logged_in, unavailable.
        """
        snap = self._chat_snapshot(client)
        status = str(snap.get("status") or "hidden")
        if status == "not_logged_in" or snap.get("notLoggedIn"):
            logger.info(
                "Twitch page assist chat/points unavailable (not logged in) for %s",
                self.url,
            )
            return "not_logged_in"
        if status == "visible":
            return "visible"
        if status == "collapsed":
            control = snap.get("expandControl")
            if not isinstance(control, dict) or not control.get("found"):
                logger.warning(
                    "Twitch page assist chat collapsed but no expand control for %s",
                    self.url,
                )
                return "hidden"
            try:
                client.click_at(float(control.get("x", 0)), float(control.get("y", 0)))
            except Exception:
                logger.exception("Chat expand click failed for %s", self.url)
                return "hidden"
            if not self._sleep(_THEATER_VERIFY_WAIT_S):
                return "hidden"
            snap = self._chat_snapshot(client)
            if snap.get("notLoggedIn") or snap.get("status") == "not_logged_in":
                return "not_logged_in"
            if snap.get("status") == "visible" or snap.get("chatVisible"):
                logger.info("Twitch page assist chat expanded for %s", self.url)
                return "visible"
            logger.warning(
                "Twitch page assist chat still hidden after expand for %s",
                self.url,
            )
            return "hidden"
        logger.warning(
            "Twitch page assist chat unavailable/hidden for %s (status=%s)",
            self.url,
            status,
        )
        return "hidden"

    def _try_accept_gate(self, client: CdpClient) -> bool:
        try:
            hit = client.evaluate(_FIND_GATE_JS)
        except Exception:
            logger.debug("Gate probe failed for %s", self.url, exc_info=True)
            return False
        if not isinstance(hit, dict) or not hit.get("found"):
            return False
        x = float(hit.get("x", 0))
        y = float(hit.get("y", 0))
        self._obs_gate_attempt = "attempted"
        try:
            client.click_at(x, y)
            logger.info("Twitch page assist accepted content gate for %s", self.url)
            self._obs_gate_result = "accepted"
            return True
        except Exception:
            logger.exception("Gate click failed for %s", self.url)
            self._obs_gate_result = "failed"
            return False

    def _probe_claim(self, client: CdpClient) -> dict[str, Any]:
        try:
            hit = client.evaluate(_FIND_CLAIM_JS)
        except Exception:
            logger.debug("Claim probe failed for %s", self.url, exc_info=True)
            return {"found": False, "probeError": True}
        return hit if isinstance(hit, dict) else {"found": False, "probeError": True}

    def _claim_still_present(self, client: CdpClient, key: str) -> dict[str, Any]:
        # Re-run the finder; absence of any eligible bonus = verified disappearance.
        hit = self._probe_claim(client)
        if hit.get("probeError"):
            return {"present": True, "verified": False}
        if not hit.get("found"):
            if not hit.get("pointsPresent"):
                # Loading/navigation or hidden points UI is not claim evidence.
                return {"present": True, "verified": False}
            return {"present": False, "found": False, "same": False}
        return {
            "present": True,
            "found": True,
            "same": (not key) or hit.get("key") == key,
            "key": hit.get("key"),
        }

    def _try_claim(self, client: CdpClient) -> bool:
        """Attempt bonus claim. True only when bonus disappearance is verified."""
        chat_status = self._ensure_chat_visible(client)
        if chat_status == "not_logged_in":
            return False
        if chat_status != "visible":
            logger.info(
                "Twitch page assist skip claim; chat not visible for %s",
                self.url,
            )
            return False

        hit = self._probe_claim(client)
        if not hit.get("found"):
            if hit.get("pointsPresent"):
                logger.debug(
                    "Twitch page assist no claimable bonus for %s", self.url
                )
            else:
                logger.debug(
                    "Twitch page assist no channel-points bonus UI for %s", self.url
                )
            return False

        delay = fuzzy_uniform(
            self.settings.claim_delay_seconds_min,
            self.settings.claim_delay_seconds_max,
        )
        if not self._sleep(delay):
            return False
        if self._settings_changed.is_set() and not self.settings.claim_channel_points:
            return False

        # DOM may change during fuzzy delay — re-probe exact eligible button.
        hit = self._probe_claim(client)
        if not hit.get("found"):
            logger.info(
                "Twitch page assist claim candidate stale/missing after delay for %s",
                self.url,
            )
            return False

        offset = max(0, int(self.settings.claim_click_offset_px))
        left = float(hit.get("left", hit.get("x", 0)))
        top = float(hit.get("top", hit.get("y", 0)))
        width = float(hit.get("width", 0))
        height = float(hit.get("height", 0))
        cx = float(hit.get("x", left + width / 2))
        cy = float(hit.get("y", top + height / 2))
        if width <= 0 or height <= 0:
            width = 2.0
            height = 2.0
            left = cx - 1.0
            top = cy - 1.0
        x, y, dx, dy = _bounded_jitter(
            cx, cy, left=left, top=top, width=width, height=height, offset=offset
        )
        key = str(hit.get("key") or "")
        try:
            client.click_at(x, y)
        except Exception:
            logger.exception("Claim click failed for %s", self.url)
            return False

        # Twitch acknowledges the click asynchronously. Real playback showed
        # the bonus still present at 0.8 s, then gone with +50 points by 2 s.
        # Retry only the observation, never the click, for at most 4.8 s.
        after = {"present": True}
        for _ in range(_CLAIM_VERIFY_POLLS):
            if not self._sleep(_CLAIM_VERIFY_WAIT_S):
                return False
            after = self._claim_still_present(client, key)
            if not after.get("present"):
                break
        if after.get("present"):
            logger.warning(
                "Twitch page assist claim click unverified for %s "
                "(bonus still present; offset=%s,%s)",
                self.url,
                dx,
                dy,
            )
            return False

        logger.info(
            "Twitch page assist channel-points bonus disappeared after click "
            "for %s (verified; offset=%s,%s; not asserting server balance)",
            self.url,
            dx,
            dy,
        )
        return True


def start_page_assist(
    url: str,
    port: int,
    settings: ViewerEngagementSettings | dict[str, Any],
) -> bool:
    """Start (or replace) a page-assist worker for *url*. Returns True if started."""
    engagement = coerce_viewer_engagement(settings)
    if engagement is None or not engagement.page_assist_active():
        return False
    if not is_twitch_url(url) or port <= 0:
        return False
    engagement = engagement.for_channel(url)
    key = _url_key(url)
    from stream_monitor.cdp_client import profile_for_cdp_port

    worker = _PageAssistWorker(
        url,
        port,
        engagement,
        user_data_dir=profile_for_cdp_port(port) or "",
    )
    with _assist_lock:
        previous = _assist_by_url.pop(key, None)
        _assist_by_url[key] = worker
    if previous is not None:
        previous.stop()
        previous.join(timeout=1.0)
    worker.start()
    return True


def update_page_assist_settings(settings: ViewerEngagementSettings) -> None:
    """Apply saved switches to existing players without reloading their video."""
    with _assist_lock:
        for worker in _assist_by_url.values():
            worker.settings = settings.for_channel(worker.url)
            worker._settings_changed.set()
            worker._wake.set()


def stop_page_assist(url: str) -> None:
    key = _url_key(url)
    with _assist_lock:
        worker = _assist_by_url.pop(key, None)
    if worker is not None:
        worker.stop()
        worker.join(timeout=2.0)


def set_page_assist_live(url: str, *, is_live: bool) -> None:
    """Apply confirmed monitor edges even when close-on-offline is disabled."""
    with _assist_lock:
        worker = _assist_by_url.get(_url_key(url))
        if worker is not None:
            if is_live:
                worker._confirmed_offline.clear()
            else:
                worker._confirmed_offline.set()


def stop_all_page_assist() -> None:
    with _assist_lock:
        workers = list(_assist_by_url.values())
        _assist_by_url.clear()
    for worker in workers:
        worker.stop()
    for worker in workers:
        worker.join(timeout=2.0)


def active_page_assist_urls() -> set[str]:
    with _assist_lock:
        return set(_assist_by_url.keys())
