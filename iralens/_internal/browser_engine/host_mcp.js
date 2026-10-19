"use strict";
/*
 * IraLens Internal Browser Engine — Post-Bootstrap Lifecycle & MCP Tool Layer
 *
 * Runs inside the V8 isolate after vendored Obscura `bootstrap.js`, `indexed_db.js`,
 * and `markdown.rs` (`HTML_TO_MARKDOWN_JS`).
 * Derived from `crates/obscura-browser/src/page.rs`, `accessibility.rs`, and
 * `crates/obscura-mcp/src/lib.rs` (commit bd9529418b7fa975b50eabeac415cfaaa865a588, Apache-2.0).
 */

(function () {
  const state = globalThis.__iralens_host_state;
  const hostOps = globalThis.Deno.core.ops;
  delete globalThis.Deno;

  // Retain __obscura_init so multiple navigations in the same isolate can re-run it
  state.savedObscuraInit = globalThis.__obscura_init;

  function drainTimers(maxRounds) {
    const limit = maxRounds || 32;
    let rounds = 0;
    while (rounds < limit && state.timers.size > 0) {
      const ready = [];
      for (const [id, entry] of state.timers.entries()) {
        if (entry.delay <= 100) {
          ready.push([id, entry.cb]);
        }
      }
      if (ready.length === 0) break;
      for (const [id, cb] of ready) {
        state.timers.delete(id);
        try {
          if (typeof cb === "function") cb();
        } catch (err) {
          state.consoleMessages.push(`[error] Uncaught timer error: ${err?.message || err}`);
        }
      }
      rounds++;
    }
  }

  function resolveElementByRefOrSelector(args) {
    if (args.ref !== undefined && args.ref !== null && String(args.ref) !== "") {
      const refId = Number(args.ref);
      const nid = state.refMap.get(refId);
      if (nid === undefined) {
        throw new Error(`Unknown or stale element reference [ref=${args.ref}]. Take a new snapshot first.`);
      }
      const el = findElementByNid(nid);
      if (!el) {
        throw new Error(`Stale element reference [ref=${args.ref}]: element is no longer in the document.`);
      }
      return el;
    }
    if (args.selector !== undefined && args.selector !== null && String(args.selector).trim() !== "") {
      const sel = String(args.selector);
      const el = globalThis.document.querySelector(sel);
      if (!el) {
        throw new Error(`no element matches selector '${sel}'`);
      }
      return el;
    }
    throw new Error("Missing 'ref' or 'selector' parameter");
  }

  function findElementByNid(targetNid) {
    const walk = (root) => {
      if (!root) return null;
      if (root._nid === targetNid) return root;
      const children = root.children || [];
      for (let i = 0; i < children.length; i++) {
        const found = walk(children[i]);
        if (found) return found;
      }
      return null;
    };
    return walk(globalThis.document?.documentElement);
  }

  function isElementHidden(el) {
    if (!el || el.nodeType !== 1) return true;
    const tag = (el.tagName || "").toLowerCase();
    if (tag === "script" || tag === "style" || tag === "head" || tag === "meta" || tag === "link" || tag === "noscript" || tag === "template") {
      return true;
    }
    if (el.hasAttribute("hidden")) return true;
    if ((el.getAttribute("aria-hidden") || "").toLowerCase() === "true") return true;
    const style = (el.getAttribute("style") || "").toLowerCase();
    if (/display\s*:\s*none/.test(style) || /visibility\s*:\s*hidden/.test(style)) return true;
    return false;
  }

  function computeAccessibleName(el) {
    if (!el || el.nodeType !== 1) return "";
    const ariaLabel = el.getAttribute("aria-label");
    if (ariaLabel && ariaLabel.trim()) return ariaLabel.trim();
    const labelledBy = el.getAttribute("aria-labelledby");
    if (labelledBy && globalThis.document) {
      const parts = labelledBy.trim().split(/\s+/)
        .map(id => globalThis.document.getElementById(id)?.textContent?.trim() || "")
        .filter(Boolean);
      if (parts.length) return parts.join(" ");
    }
    const tag = (el.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") {
      const id = el.getAttribute("id");
      if (id && globalThis.document) {
        const label = globalThis.document.querySelector(`label[for="${id.replace(/"/g, '\\"')}"]`);
        if (label && label.textContent) return label.textContent.replace(/\s+/g, " ").trim();
      }
      const parentLabel = el.closest ? el.closest("label") : null;
      if (parentLabel && parentLabel.textContent) {
        return parentLabel.textContent.replace(/\s+/g, " ").trim();
      }
      const placeholder = el.getAttribute("placeholder");
      if (placeholder && placeholder.trim()) return placeholder.trim();
      const title = el.getAttribute("title");
      if (title && title.trim()) return title.trim();
      const nameAttr = el.getAttribute("name");
      if (nameAttr && nameAttr.trim()) return nameAttr.trim();
      if (tag === "input") {
        const t = (el.getAttribute("type") || "text").toLowerCase();
        if (t === "submit" || t === "button" || t === "reset") {
          return (el.value || t).trim();
        }
      }
      return "";
    }
    if (tag === "img") {
      return (el.getAttribute("alt") || el.getAttribute("title") || "").trim();
    }
    return (el.textContent || "").replace(/\s+/g, " ").trim();
  }

  function mapRole(el) {
    const explicit = (el.getAttribute("role") || "").trim().toLowerCase();
    if (explicit) return explicit;
    const tag = (el.tagName || "").toLowerCase();
    if (/^h[1-6]$/.test(tag)) return "heading";
    if (tag === "a" && el.hasAttribute("href")) return "link";
    if (tag === "button" || tag === "summary") return "button";
    if (tag === "textarea") return "textbox";
    if (tag === "select") return "combobox";
    if (tag === "option") return "option";
    if (tag === "img") return "image";
    if (tag === "nav") return "navigation";
    if (tag === "main") return "main";
    if (tag === "header") return "banner";
    if (tag === "footer") return "contentinfo";
    if (tag === "aside") return "complementary";
    if (tag === "form") return "form";
    if (tag === "ul" || tag === "ol") return "list";
    if (tag === "li") return "listitem";
    if (tag === "table") return "table";
    if (tag === "tr") return "row";
    if (tag === "td" || tag === "th") return "cell";
    if (tag === "input") {
      const t = (el.getAttribute("type") || "text").toLowerCase();
      if (t === "hidden") return "";
      if (t === "submit" || t === "button" || t === "reset" || t === "image") return "button";
      if (t === "checkbox") return "checkbox";
      if (t === "radio") return "radio";
      if (t === "search") return "searchbox";
      return "textbox";
    }
    return "";
  }

  function isActionableRole(role, el) {
    if (["link", "button", "textbox", "searchbox", "checkbox", "radio", "combobox", "listbox", "option", "menuitem", "tab"].includes(role)) {
      return true;
    }
    if (el && (el.hasAttribute("onclick") || el.hasAttribute("tabindex"))) return true;
    return false;
  }

  function syncStateSnapshot() {
    const cookies = state.setCookieQueue.splice(0, state.setCookieQueue.length);
    const localEntries = Array.from(state.localStorage.entries());
    const sessionEntries = Array.from(state.sessionStorage.entries());
    const nav = state.pendingNavigation;
    state.pendingNavigation = null;
    const histIdx = state.pendingHistoryIndex;
    state.pendingHistoryIndex = null;
    return {
      url: String(globalThis.location?.href || state.url),
      title: String(globalThis.document?.title || ""),
      pendingNavigation: nav,
      pendingHistoryIndex: histIdx,
      setCookies: cookies,
      cookieString: state.cookieString,
      localStorage: localEntries,
      sessionStorage: sessionEntries,
      consoleMessages: state.consoleMessages.slice()
    };
  }

  function submitFormElement(formEl, submitterEl) {
    if (!formEl) return false;
    // Dispatch submit event first so JS handlers (preventDefault) can run
    try {
      const ev = new globalThis.Event("submit", { bubbles: true, cancelable: true });
      const proceed = formEl.dispatchEvent(ev);
      if (!proceed) return true;
    } catch (_e) {}

    const actionAttr = (submitterEl && submitterEl.getAttribute?.("formaction")) || formEl.getAttribute("action") || state.url;
    const methodAttr = ((submitterEl && submitterEl.getAttribute?.("formmethod")) || formEl.getAttribute("method") || "GET").toUpperCase();
    const targetUrl = hostOps.op_url_resolve(actionAttr, state.url) || state.url;

    const pairs = [];
    const controls = formEl.querySelectorAll("input, textarea, select, button");
    for (let i = 0; i < controls.length; i++) {
      const ctrl = controls[i];
      if (ctrl.disabled) continue;
      const name = ctrl.getAttribute("name");
      if (!name) continue;
      const tag = (ctrl.tagName || "").toLowerCase();
      const type = (ctrl.getAttribute("type") || "text").toLowerCase();
      if (tag === "button" || type === "submit" || type === "button" || type === "reset") {
        if (ctrl !== submitterEl) continue;
      }
      if (type === "checkbox" || type === "radio") {
        if (!ctrl.checked) continue;
        pairs.push(encodeURIComponent(name) + "=" + encodeURIComponent(ctrl.value || "on"));
      } else if (tag === "select") {
        const opts = ctrl.querySelectorAll("option");
        let matched = false;
        for (let j = 0; j < opts.length; j++) {
          if (opts[j].selected || opts[j].hasAttribute("selected")) {
            pairs.push(encodeURIComponent(name) + "=" + encodeURIComponent(opts[j].value ?? opts[j].textContent ?? ""));
            matched = true;
            if (!ctrl.multiple) break;
          }
        }
        if (!matched && ctrl.value) {
          pairs.push(encodeURIComponent(name) + "=" + encodeURIComponent(ctrl.value));
        }
      } else {
        pairs.push(encodeURIComponent(name) + "=" + encodeURIComponent(ctrl.value ?? ""));
      }
    }
    const encoded = pairs.join("&");
    if (methodAttr === "POST") {
      hostOps.op_navigate(targetUrl, "POST", encoded);
    } else {
      const base = targetUrl.split("?")[0];
      const full = encoded ? (base + "?" + encoded) : targetUrl;
      hostOps.op_navigate(full, "GET", "");
    }
    return true;
  }

  Object.defineProperty(globalThis, "__iralens_load_document", {
    value: function (payloadJson) {
      const payload = JSON.parse(payloadJson);
      state.url = payload.url || "about:blank";
      const parsedUrl = JSON.parse(hostOps.op_url_parse(state.url, ""));
      state.origin = parsedUrl.ok ? parsedUrl.origin : "null";
      state.referrer = payload.referrer || "";
      state.encoding = payload.encoding || "UTF-8";
      state.contentType = payload.contentType || "text/html";
      state.scriptingEnabled = payload.scriptingEnabled !== false;
      state.cookieString = payload.cookieString || "";
      state.setCookieQueue = [];
      state.pendingNavigation = null;
      state.pendingHistoryIndex = null;
      state.consoleMessages = [];
      state.timers.clear();

      state.localStorage.clear();
      for (const [k, v] of (payload.localStorage || [])) {
        state.localStorage.set(String(k), String(v));
      }
      state.sessionStorage.clear();
      for (const [k, v] of (payload.sessionStorage || [])) {
        state.sessionStorage.set(String(k), String(v));
      }
      if (payload.sessionHistory) {
        state.sessionHistory = payload.sessionHistory;
      }

      // Reset and parse HTML into document node 0
      hostOps.op_dom("document_write_reset", "", "", 0);
      hostOps.op_dom("set_inner_html", "0", "", 0);
      // Use parse_html_document path on node 0 via internal helper:
      const docFragNid = Number(hostOps.op_dom("create_document_fragment", "", "", 0));
      hostOps.op_dom("parse_html_document", String(docFragNid), String(payload.html || ""), 0);
      const children = JSON.parse(hostOps.op_dom("child_nodes", String(docFragNid), "", 0));
      for (const cid of children) {
        hostOps.op_dom("append_child", "0", String(cid), 0);
      }

      globalThis.__documentReadyState__ = "loading";
      globalThis.__obscura_init = state.savedObscuraInit;
      // Stealth identity is applied through the engine's own hooks (the same
      // ones the upstream Rust tier writes), never by patching a prototype.
      // Ordering matters in both directions:
      //   * before init, because the engine derives screen/viewport and the
      //     CPU/memory pools while initializing;
      //   * again after init, because __obscura_init re-declares its own
      //     internal globals, which resets them to undefined;
      //   * `after` last, because init reseeds the CPU/memory pools on every
      //     navigation and a real browser's hardware does not change on reload.
      const stealth = payload.stealth || null;
      const applyStealthGlobals = (values) => {
        if (!values) return;
        for (const key of Object.keys(values)) {
          if (key.indexOf("__obscura_") === 0) globalThis[key] = values[key];
        }
      };
      applyStealthGlobals(stealth && stealth.before);
      globalThis.__obscura_init();
      applyStealthGlobals(stealth && stealth.before);
      applyStealthGlobals(stealth && stealth.after);

      // Execute connected <script> elements in document order if scripting is enabled
      const externalMap = payload.externalScripts || {};
      if (state.scriptingEnabled && globalThis.document) {
        const scripts = globalThis.document.querySelectorAll("script");
        for (let i = 0; i < scripts.length; i++) {
          const s = scripts[i];
          const type = (s.getAttribute("type") || "").trim().toLowerCase();
          if (type && type !== "text/javascript" && type !== "application/javascript" && type !== "module" && !type.endsWith("+javascript")) {
            continue;
          }
          const srcAttr = s.getAttribute("src");
          let code = "";
          if (srcAttr) {
            const resolvedSrc = hostOps.op_url_resolve(srcAttr, state.url) || srcAttr;
            code = externalMap[resolvedSrc] ?? externalMap[srcAttr] ?? "";
          } else {
            code = s.textContent || "";
          }
          if (code && code.trim()) {
            try {
              (0, eval)(code);
            } catch (err) {
              state.consoleMessages.push(`[error] Script error: ${err?.message || err}`);
            }
          }
        }
      }

      globalThis.__documentReadyState__ = "interactive";
      try {
        globalThis.document.dispatchEvent(new globalThis.Event("DOMContentLoaded", { bubbles: false, cancelable: false }));
      } catch (_e) {}
      try {
        globalThis.window.dispatchEvent(new globalThis.Event("DOMContentLoaded", { bubbles: false, cancelable: false }));
      } catch (_e) {}

      globalThis.__documentReadyState__ = "complete";
      try {
        globalThis.window.dispatchEvent(new globalThis.Event("load", { bubbles: false, cancelable: false }));
      } catch (_e) {}

      drainTimers(32);
      return JSON.stringify(syncStateSnapshot());
    },
    writable: false,
    enumerable: false,
    configurable: false
  });

  Object.defineProperty(globalThis, "__iralens_mcp_call", {
    value: function (toolName, argsJson) {
      const args = argsJson ? JSON.parse(argsJson) : {};
      switch (toolName) {
        case "browser_snapshot": {
          state.refMap.clear();
          let nextRef = 1;
          const lines = [];
          const title = globalThis.document?.title || "";
          const url = globalThis.location?.href || state.url;
          lines.push(`- RootWebArea "${title}" [url=${url}]`);

          const walk = (el, depth) => {
            if (!el || isElementHidden(el)) return;
            const role = mapRole(el);
            const indent = "  ".repeat(depth);
            if (role) {
              const name = computeAccessibleName(el);
              let line = `${indent}- ${role}`;
              if (name) line += ` "${name.slice(0, 200)}"`;
              if (isActionableRole(role, el)) {
                const ref = nextRef++;
                state.refMap.set(ref, el._nid);
                line += ` [ref=${ref}]`;
              }
              const tag = (el.tagName || "").toLowerCase();
              if (/^h[1-6]$/.test(tag)) {
                line += ` [level=${tag.slice(1)}]`;
              }
              if (tag === "input" || tag === "textarea" || tag === "select") {
                const val = el.value;
                if (val) line += ` [value="${String(val).slice(0, 80)}"]`;
                if (el.checked) line += " [checked]";
                if (el.disabled) line += " [disabled]";
              }
              lines.push(line);
              if (["button", "link", "heading", "option"].includes(role)) {
                return;
              }
              const children = el.children || [];
              for (let i = 0; i < children.length; i++) {
                walk(children[i], depth + 1);
              }
            } else {
              const children = el.children || [];
              if (children.length === 0) {
                const txt = (el.textContent || "").replace(/\s+/g, " ").trim();
                if (txt) lines.push(`${indent}- text "${txt.slice(0, 240)}"`);
              } else {
                for (let i = 0; i < children.length; i++) {
                  walk(children[i], depth);
                }
              }
            }
          };

          if (globalThis.document?.body) {
            walk(globalThis.document.body, 1);
          } else if (globalThis.document?.documentElement) {
            walk(globalThis.document.documentElement, 1);
          }
          let out = lines.join("\n");
          const maxChars = Number(args.max_chars) || 0;
          if (maxChars > 0 && out.length > maxChars) {
            out = out.slice(0, maxChars) + "\n...[truncated]";
          }
          return JSON.stringify({ text: out, state: syncStateSnapshot() });
        }

        case "browser_markdown": {
          const mdFn = globalThis.__obscura_html_to_markdown;
          let md = "";
          if (typeof mdFn === "function") {
            md = String(mdFn() || "");
          } else {
            md = (globalThis.document?.body?.textContent || "").trim();
          }
          const maxChars = Number(args.max_chars) || 0;
          if (maxChars > 0 && md.length > maxChars) {
            md = md.slice(0, maxChars);
          }
          return JSON.stringify({ text: md, state: syncStateSnapshot() });
        }

        case "browser_links": {
          const limit = Number(args.limit) || 100;
          const internalOnly = Boolean(args.internal_only);
          const baseParsed = JSON.parse(hostOps.op_url_parse(state.url, ""));
          const baseHost = baseParsed.ok ? baseParsed.hostname : "";
          const anchors = globalThis.document ? globalThis.document.querySelectorAll("a[href]") : [];
          const links = [];
          for (let i = 0; i < anchors.length && links.length < limit; i++) {
            const a = anchors[i];
            const rawHref = a.getAttribute("href") || "";
            if (!rawHref || rawHref.startsWith("javascript:")) continue;
            const resolved = hostOps.op_url_resolve(rawHref, state.url) || rawHref;
            if (internalOnly && baseHost) {
              const p = JSON.parse(hostOps.op_url_parse(resolved, ""));
              if (p.ok && p.hostname !== baseHost) continue;
            }
            const text = (a.textContent || "").replace(/\s+/g, " ").trim();
            links.push({ text, href: resolved, url: resolved });
          }
          return JSON.stringify({ data: links, state: syncStateSnapshot() });
        }

        case "browser_interactive_elements": {
          const els = globalThis.document
            ? globalThis.document.querySelectorAll("a[href], button, input, select, textarea, [role='button'], [role='link'], [onclick]")
            : [];
          const items = [];
          let refCounter = 1;
          state.refMap.clear();
          for (let i = 0; i < els.length; i++) {
            const el = els[i];
            if (isElementHidden(el)) continue;
            const ref = refCounter++;
            state.refMap.set(ref, el._nid);
            const tag = (el.tagName || "").toLowerCase();
            const role = mapRole(el) || tag;
            const name = computeAccessibleName(el);
            const id = el.getAttribute("id");
            const selector = id ? `#${id}` : tag;
            items.push({
              ref,
              tag,
              role,
              name,
              type: el.getAttribute("type") || undefined,
              href: tag === "a" ? (hostOps.op_url_resolve(el.getAttribute("href") || "", state.url) || el.getAttribute("href")) : undefined,
              value: el.value !== undefined ? el.value : undefined,
              selector
            });
          }
          return JSON.stringify({ data: items, state: syncStateSnapshot() });
        }

        case "browser_click": {
          const el = resolveElementByRefOrSelector(args);
          const tag = (el.tagName || "").toLowerCase();
          const type = (el.getAttribute("type") || "").toLowerCase();
          try { if (typeof el.focus === "function") el.focus(); } catch (_e) {}
          const prevNav = state.pendingNavigation;
          if (typeof el.click === "function") {
            el.click();
          } else {
            el.dispatchEvent(new globalThis.MouseEvent("click", { bubbles: true, cancelable: true }));
          }
          drainTimers(16);
          // If clicking a submit button or link did not already queue a navigation:
          if (!state.pendingNavigation && !prevNav) {
            if (tag === "a" && el.getAttribute("href")) {
              const href = el.getAttribute("href");
              if (!href.startsWith("javascript:") && !href.startsWith("#")) {
                const targetUrl = hostOps.op_url_resolve(href, state.url) || href;
                hostOps.op_navigate(targetUrl, "GET", "");
              }
            } else if ((tag === "button" && (type === "" || type === "submit")) || (tag === "input" && type === "submit")) {
              const form = el.closest ? el.closest("form") : null;
              if (form) submitFormElement(form, el);
            }
          }
          return JSON.stringify({
            text: `Clicked <${tag}> ${computeAccessibleName(el) ? '"' + computeAccessibleName(el) + '"' : ""}`.trim(),
            state: syncStateSnapshot()
          });
        }

        case "browser_fill": {
          const el = resolveElementByRefOrSelector(args);
          const val = String(args.value ?? "");
          try { if (typeof el.focus === "function") el.focus(); } catch (_e) {}
          el.value = val;
          hostOps.op_dom("set_form_value", String(el._nid), val, 0);
          el.dispatchEvent(new globalThis.Event("input", { bubbles: true }));
          el.dispatchEvent(new globalThis.Event("change", { bubbles: true }));
          if (args.submit) {
            const form = el.closest ? el.closest("form") : null;
            if (form) submitFormElement(form, null);
          }
          drainTimers(16);
          return JSON.stringify({
            text: `Filled element with value (${val.length} chars)`,
            state: syncStateSnapshot()
          });
        }

        case "browser_type": {
          const el = resolveElementByRefOrSelector(args);
          const text = String(args.text ?? "");
          try { if (typeof el.focus === "function") el.focus(); } catch (_e) {}
          let cur = String(el.value ?? "");
          for (let i = 0; i < text.length; i++) {
            const ch = text.charAt(i);
            el.dispatchEvent(new globalThis.KeyboardEvent("keydown", { key: ch, bubbles: true, cancelable: true }));
            el.dispatchEvent(new globalThis.KeyboardEvent("keypress", { key: ch, bubbles: true, cancelable: true }));
            cur += ch;
            el.value = cur;
            hostOps.op_dom("set_form_value", String(el._nid), cur, 0);
            el.dispatchEvent(new globalThis.Event("input", { bubbles: true }));
            el.dispatchEvent(new globalThis.KeyboardEvent("keyup", { key: ch, bubbles: true, cancelable: true }));
          }
          el.dispatchEvent(new globalThis.Event("change", { bubbles: true }));
          if (args.submit) {
            const form = el.closest ? el.closest("form") : null;
            if (form) submitFormElement(form, null);
          }
          drainTimers(16);
          return JSON.stringify({
            text: `Typed ${text.length} characters`,
            state: syncStateSnapshot()
          });
        }

        case "browser_press_key": {
          const key = String(args.key || "Enter");
          const target = args.selector
            ? resolveElementByRefOrSelector({ selector: args.selector })
            : (globalThis.document?.activeElement || globalThis.document?.body || globalThis.document?.documentElement);
          if (!target) throw new Error("No target element for key press");
          const kd = new globalThis.KeyboardEvent("keydown", { key, bubbles: true, cancelable: true });
          const proceed = target.dispatchEvent(kd);
          target.dispatchEvent(new globalThis.KeyboardEvent("keyup", { key, bubbles: true, cancelable: true }));
          if (proceed && key === "Enter") {
            const form = target.closest ? target.closest("form") : null;
            if (form) submitFormElement(form, null);
          }
          drainTimers(16);
          return JSON.stringify({
            text: `Pressed key '${key}'`,
            state: syncStateSnapshot()
          });
        }

        case "browser_select_option": {
          const el = resolveElementByRefOrSelector(args);
          const opts = el.querySelectorAll("option");
          let matchedVal = null;
          for (let i = 0; i < opts.length; i++) {
            const opt = opts[i];
            const optVal = opt.getAttribute("value") ?? (opt.textContent || "").trim();
            const optLabel = (opt.textContent || "").trim();
            const isMatch = (args.value !== undefined && args.value !== null && optVal === String(args.value))
              || (args.label !== undefined && args.label !== null && optLabel === String(args.label));
            if (isMatch) {
              opt.selected = true;
              opt.setAttribute("selected", "");
              matchedVal = optVal;
            } else if (!el.multiple) {
              opt.selected = false;
              opt.removeAttribute("selected");
            }
          }
          if (matchedVal === null) {
            throw new Error("No matching <option> found in <select>");
          }
          el.value = matchedVal;
          hostOps.op_dom("set_form_value", String(el._nid), matchedVal, 0);
          el.dispatchEvent(new globalThis.Event("input", { bubbles: true }));
          el.dispatchEvent(new globalThis.Event("change", { bubbles: true }));
          drainTimers(16);
          return JSON.stringify({
            text: `Selected option '${matchedVal}'`,
            state: syncStateSnapshot()
          });
        }

        case "browser_detect_forms": {
          const forms = globalThis.document ? globalThis.document.querySelectorAll("form") : [];
          const out = [];
          for (let i = 0; i < forms.length; i++) {
            const f = forms[i];
            const fields = [];
            const ctrls = f.querySelectorAll("input, textarea, select, button");
            for (let j = 0; j < ctrls.length; j++) {
              const c = ctrls[j];
              fields.push({
                tag: (c.tagName || "").toLowerCase(),
                type: c.getAttribute("type") || "text",
                name: c.getAttribute("name") || "",
                id: c.getAttribute("id") || "",
                label: computeAccessibleName(c),
                value: c.value ?? "",
                required: c.hasAttribute("required")
              });
            }
            out.push({
              index: i,
              id: f.getAttribute("id") || "",
              action: hostOps.op_url_resolve(f.getAttribute("action") || "", state.url) || state.url,
              method: (f.getAttribute("method") || "GET").toUpperCase(),
              fields
            });
          }
          return JSON.stringify({ data: out, state: syncStateSnapshot() });
        }

        case "browser_fill_form": {
          const root = args.form_selector
            ? resolveElementByRefOrSelector({ selector: args.form_selector })
            : (globalThis.document?.querySelector("form") || globalThis.document);
          if (!root) throw new Error("No form found in document");
          const rawFields = args.fields || {};
          const entries = Array.isArray(rawFields)
            ? rawFields.map((item) => [String(item.selector || item.name || ""), item.value])
            : Object.entries(rawFields);
          let filledCount = 0;
          for (const [key, val] of entries) {
            if (!key) continue;
            let fieldEl = root.querySelector(`[name="${key.replace(/"/g, '\\"')}"]`)
              || (key.startsWith("#") || key.startsWith(".") || key.includes("[")
                  ? root.querySelector(key)
                  : root.querySelector(`#${key.replace(/"/g, '\\"')}`))
              || root.querySelector(key);
            if (!fieldEl) {
              const allInputs = root.querySelectorAll("input, textarea, select");
              for (let i = 0; i < allInputs.length; i++) {
                if (computeAccessibleName(allInputs[i]).toLowerCase() === key.toLowerCase()) {
                  fieldEl = allInputs[i];
                  break;
                }
              }
            }
            if (!fieldEl) throw new Error(`Form field '${key}' not found`);
            const tag = (fieldEl.tagName || "").toLowerCase();
            const type = (fieldEl.getAttribute("type") || "text").toLowerCase();
            if (type === "checkbox" || type === "radio") {
              const checked = val === "true" || val === "1" || val === "on" || val === true;
              fieldEl.checked = checked;
              hostOps.op_dom("set_form_checked", String(fieldEl._nid), checked ? "true" : "false", 0);
            } else if (tag === "select") {
              fieldEl.value = String(val);
              hostOps.op_dom("set_form_value", String(fieldEl._nid), String(val), 0);
            } else {
              fieldEl.value = String(val);
              hostOps.op_dom("set_form_value", String(fieldEl._nid), String(val), 0);
            }
            fieldEl.dispatchEvent(new globalThis.Event("input", { bubbles: true }));
            fieldEl.dispatchEvent(new globalThis.Event("change", { bubbles: true }));
            filledCount++;
          }
          if (args.submit) {
            const form = (root.tagName || "").toLowerCase() === "form" ? root : (root.closest ? root.closest("form") : globalThis.document?.querySelector("form"));
            if (form) submitFormElement(form, null);
          }
          drainTimers(16);
          return JSON.stringify({
            text: `Filled ${filledCount} form fields${args.submit ? " and submitted" : ""}`,
            state: syncStateSnapshot()
          });
        }

        case "browser_extract": {
          if (args.schema && typeof args.schema === "object" && !Array.isArray(args.schema)) {
            const out = {};
            for (const [fieldName, selSpec] of Object.entries(args.schema)) {
              const selStr = String(selSpec || "");
              if (!selStr) {
                out[fieldName] = null;
                continue;
              }
              const els = globalThis.document ? globalThis.document.querySelectorAll(selStr) : [];
              if (els.length === 0) {
                out[fieldName] = null;
              } else if (els.length === 1) {
                out[fieldName] = (els[0].textContent || "").trim();
              } else {
                const arr = [];
                for (let i = 0; i < els.length; i++) arr.push((els[i].textContent || "").trim());
                out[fieldName] = arr;
              }
            }
            return JSON.stringify({ data: out, state: syncStateSnapshot() });
          }
          const sel = String(args.selector || "");
          if (!sel) throw new Error("Missing 'selector' parameter");
          const attr = args.attribute ? String(args.attribute) : null;
          const readEl = (el) => {
            if (!attr || attr === "text" || attr === "textContent") return (el.textContent || "").trim();
            if (attr === "html" || attr === "innerHTML") return el.innerHTML || "";
            if (attr === "outerHTML") return el.outerHTML || "";
            if (attr === "value") return el.value ?? "";
            return el.getAttribute(attr);
          };
          if (args.multiple) {
            const els = globalThis.document ? globalThis.document.querySelectorAll(sel) : [];
            const vals = [];
            for (let i = 0; i < els.length; i++) vals.push(readEl(els[i]));
            return JSON.stringify({ data: vals, state: syncStateSnapshot() });
          }
          const els = globalThis.document ? globalThis.document.querySelectorAll(sel) : [];
          if (!els || els.length === 0) throw new Error(`no element matches selector '${sel}'`);
          if (els.length === 1 && !args.fields) {
            return JSON.stringify({ data: readEl(els[0]), state: syncStateSnapshot() });
          }
          const rows = [];
          for (let i = 0; i < els.length; i++) {
            const el = els[i];
            rows.push({
              text: (el.textContent || "").trim(),
              html: el.innerHTML || "",
              href: el.getAttribute("href") || undefined
            });
          }
          return JSON.stringify({ data: rows, state: syncStateSnapshot() });
        }

        case "browser_get_attribute": {
          const el = resolveElementByRefOrSelector(args);
          const attr = String(args.attribute || "");
          if (!attr) throw new Error("Missing 'attribute' parameter");
          const val = el.getAttribute(attr);
          return JSON.stringify({ data: val, state: syncStateSnapshot() });
        }

        case "browser_count": {
          const sel = String(args.selector || "");
          if (!sel) throw new Error("Missing 'selector' parameter");
          const els = globalThis.document ? globalThis.document.querySelectorAll(sel) : [];
          return JSON.stringify({ data: { selector: sel, count: els.length }, state: syncStateSnapshot() });
        }

        case "browser_search": {
          const q = String(args.query || "").trim();
          if (!q) throw new Error("Missing 'query' parameter");
          const ctxChars = Number(args.context_chars) || 100;
          const bodyText = (globalThis.document?.body?.textContent || "").replace(/\s+/g, " ");
          const lowerText = bodyText.toLowerCase();
          const lowerQ = q.toLowerCase();
          const matches = [];
          let idx = 0;
          while (matches.length < 50) {
            const pos = lowerText.indexOf(lowerQ, idx);
            if (pos === -1) break;
            const start = Math.max(0, pos - ctxChars);
            const end = Math.min(bodyText.length, pos + q.length + ctxChars);
            matches.push({
              index: pos,
              snippet: bodyText.slice(start, end).trim()
            });
            idx = pos + Math.max(1, q.length);
          }
          return JSON.stringify({ data: { query: q, count: matches.length, matches }, state: syncStateSnapshot() });
        }

        case "browser_scroll": {
          const dir = String(args.direction || "down").toLowerCase();
          const amount = Number(args.amount) || 500;
          if (dir === "top") state.scrollOffset = { x: 0, y: 0 };
          else if (dir === "bottom") state.scrollOffset = { x: 0, y: 10000 };
          else if (dir === "up") state.scrollOffset.y = Math.max(0, state.scrollOffset.y - amount);
          else if (dir === "down") state.scrollOffset.y += amount;
          else if (dir === "left") state.scrollOffset.x = Math.max(0, state.scrollOffset.x - amount);
          else if (dir === "right") state.scrollOffset.x += amount;
          globalThis.window.scrollX = state.scrollOffset.x;
          globalThis.window.scrollY = state.scrollOffset.y;
          globalThis.window.pageXOffset = state.scrollOffset.x;
          globalThis.window.pageYOffset = state.scrollOffset.y;
          return JSON.stringify({
            data: { scrolled: dir, x: state.scrollOffset.x, y: state.scrollOffset.y },
            state: syncStateSnapshot()
          });
        }

        case "browser_evaluate": {
          const expr = String(args.expression ?? "");
          let val = (0, eval)(expr);
          if (typeof val === "function") {
            val = val();
          }
          drainTimers(16);
          return JSON.stringify({
            data: val === undefined ? null : val,
            state: syncStateSnapshot()
          });
        }

        default:
          throw new Error(`Unsupported V8 tool: ${toolName}`);
      }
    },
    writable: false,
    enumerable: false,
    configurable: false
  });
})();
