"use strict";
/*
 * IraLens Internal Browser Engine — V8 Host Operations Layer
 *
 * Implements the `Deno.core` bridge (`op_dom` and companion host ops) required
 * by vendored Obscura `crates/obscura-js/js/bootstrap.js` and `indexed_db.js`,
 * derived from:
 *   - `crates/obscura-dom/src/tree.rs`, `selector.rs`, `serialize.rs`
 *   - `crates/obscura-js/src/ops.rs`, `runtime.rs`, `markdown.rs`
 *   - `crates/obscura-browser/src/accessibility.rs`
 *   - `crates/obscura-mcp/src/lib.rs`
 * Upstream revision: bd9529418b7fa975b50eabeac415cfaaa865a588 (Apache-2.0)
 */

(function () {
  const HTML_NS = "http://www.w3.org/1999/xhtml";
  const SVG_NS = "http://www.w3.org/2000/svg";
  const MATHML_NS = "http://www.w3.org/1998/Math/MathML";

  const VOID_ELEMENTS = new Set([
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr"
  ]);

  const RAW_TEXT_ELEMENTS = new Set(["script", "style", "textarea", "title", "xmp", "iframe", "noembed", "noframes"]);

  const NAMED_ENTITIES = {
    amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: "\u00a0",
    copy: "\u00a9", reg: "\u00ae", trade: "\u2122", mdash: "\u2014",
    ndash: "\u2013", hellip: "\u2026", laquo: "\u00ab", raquo: "\u00bb",
    bull: "\u2022", middot: "\u00b7", sect: "\u00a7", para: "\u00b6",
    deg: "\u00b0", plusmn: "\u00b1", times: "\u00d7", divide: "\u00f7",
    euro: "\u20ac", pound: "\u00a3", yen: "\u00a5", cent: "\u00a2",
    lsquo: "\u2018", rsquo: "\u2019", ldquo: "\u201c", rdquo: "\u201d"
  };

  function decodeEntities(str) {
    if (!str || str.indexOf("&") === -1) return str || "";
    return str.replace(/&(#x[0-9a-fA-F]+|#[0-9]+|[a-zA-Z0-9]+);?/g, (match, body) => {
      if (body.charAt(0) === "#") {
        const code = (body.charAt(1) === "x" || body.charAt(1) === "X")
          ? parseInt(body.slice(2), 16)
          : parseInt(body.slice(1), 10);
        if (!Number.isNaN(code) && code > 0 && code <= 0x10FFFF) {
          try { return String.fromCodePoint(code); } catch (_e) { return match; }
        }
        return match;
      }
      const lower = body.toLowerCase();
      if (Object.prototype.hasOwnProperty.call(NAMED_ENTITIES, body)) return NAMED_ENTITIES[body];
      if (Object.prototype.hasOwnProperty.call(NAMED_ENTITIES, lower)) return NAMED_ENTITIES[lower];
      return match;
    });
  }

  function escapeAttr(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;");
  }

  function escapeText(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  // Host state shared across ops within this V8 isolate
  const state = {
    nodes: [],
    idIndex: new Map(),
    url: "about:blank",
    origin: "null",
    referrer: "",
    encoding: "UTF-8",
    contentType: "text/html",
    quirks: false,
    scriptingEnabled: true,
    sandboxOpaqueOrigin: false,
    timeOrigin: Date.now(),
    timing: {
      navigationStart: Date.now(),
      fetchStart: Date.now(),
      responseEnd: Date.now(),
      domLoading: Date.now(),
      domInteractive: Date.now(),
      domContentLoadedEventStart: Date.now(),
      domContentLoadedEventEnd: Date.now(),
      domComplete: Date.now(),
      loadEventStart: Date.now(),
      loadEventEnd: Date.now()
    },
    startedScripts: new Set(),
    externalStylesheets: new Map(),
    cssomStylesheets: new Map(),
    stylesheetGeneration: 1,
    cookieString: "",
    setCookieQueue: [],
    localStorage: new Map(),
    sessionStorage: new Map(),
    sessionHistory: { start: 0, current: 0, urls: ["about:blank"] },
    pendingNavigation: null,
    pendingHistoryIndex: null,
    consoleMessages: [],
    timers: new Map(),
    nextTimerId: 1,
    refMap: new Map(),
    savedObscuraInit: null,
    scrollOffset: { x: 0, y: 0 },
    elementScrollOffsets: new Map()
  };

  Object.defineProperty(globalThis, "__iralens_host_state", {
    value: state,
    writable: false,
    enumerable: false,
    configurable: false
  });

  function createNode(nodeType, opts) {
    const nid = state.nodes.length;
    const node = {
      nid,
      nodeType, // 9=Document, 1=Element, 3=Text, 8=Comment, 10=Doctype, 7=PI
      parent: -1,
      firstChild: -1,
      lastChild: -1,
      prevSibling: -1,
      nextSibling: -1,
      localName: opts?.localName ?? "",
      prefix: opts?.prefix ?? null,
      ns: opts?.ns ?? HTML_NS,
      attrs: opts?.attrs ? opts.attrs.slice() : [],
      text: opts?.text ?? "",
      doctypeName: opts?.doctypeName ?? "html",
      publicId: opts?.publicId ?? "",
      systemId: opts?.systemId ?? "",
      piTarget: opts?.piTarget ?? "",
      templateContents: -1,
      shadowRoot: null,
      shadowHost: -1,
      formValue: opts?.formValue !== undefined ? opts.formValue : null,
      formChecked: opts?.formChecked !== undefined ? opts.formChecked : null,
      formIndeterminate: false
    };
    state.nodes.push(node);
    return nid;
  }

  function resetTree() {
    state.nodes = [];
    state.idIndex.clear();
    state.startedScripts.clear();
    state.externalStylesheets.clear();
    state.cssomStylesheets.clear();
    state.refMap.clear();
    createNode(9, {}); // nid 0 = Document
  }
  resetTree();

  function getNode(nid) {
    return (nid >= 0 && nid < state.nodes.length) ? state.nodes[nid] : null;
  }

  function getAttr(node, name) {
    if (!node || node.nodeType !== 1) return null;
    const target = String(name).toLowerCase();
    for (let i = 0; i < node.attrs.length; i++) {
      const a = node.attrs[i];
      const qn = a.prefix ? (a.prefix + ":" + a.localName) : a.localName;
      if (qn.toLowerCase() === target || a.localName.toLowerCase() === target) {
        return a.value;
      }
    }
    return null;
  }

  function setAttr(node, name, value) {
    if (!node || node.nodeType !== 1) return;
    const lname = String(name).toLowerCase();
    const valStr = String(value ?? "");
    for (let i = 0; i < node.attrs.length; i++) {
      const a = node.attrs[i];
      const qn = (a.prefix ? a.prefix + ":" + a.localName : a.localName).toLowerCase();
      if (qn === lname) {
        a.value = valStr;
        if (lname === "id") state.idIndex.set(valStr, node.nid);
        return;
      }
    }
    node.attrs.push({ localName: lname, prefix: null, ns: "", value: valStr });
    if (lname === "id" && valStr) {
      state.idIndex.set(valStr, node.nid);
    }
  }

  function removeAttr(node, name) {
    if (!node || node.nodeType !== 1) return;
    const lname = String(name).toLowerCase();
    for (let i = node.attrs.length - 1; i >= 0; i--) {
      const a = node.attrs[i];
      const qn = (a.prefix ? a.prefix + ":" + a.localName : a.localName).toLowerCase();
      if (qn === lname) {
        if (lname === "id" && state.idIndex.get(a.value) === node.nid) {
          state.idIndex.delete(a.value);
        }
        node.attrs.splice(i, 1);
      }
    }
  }

  function isInclusiveAncestor(ancestorNid, nodeNid) {
    let cur = nodeNid;
    while (cur >= 0) {
      if (cur === ancestorNid) return true;
      const n = getNode(cur);
      cur = n ? n.parent : -1;
    }
    return false;
  }

  function isHostIncludingInclusiveAncestor(ancestorNid, nodeNid) {
    let cur = nodeNid;
    while (cur >= 0) {
      if (cur === ancestorNid) return true;
      const n = getNode(cur);
      if (!n) break;
      if (n.parent >= 0) {
        cur = n.parent;
      } else if (n.shadowHost >= 0) {
        cur = n.shadowHost;
      } else {
        break;
      }
    }
    return false;
  }

  function isConnected(nid) {
    let cur = nid;
    while (cur >= 0) {
      if (cur === 0) return true;
      const n = getNode(cur);
      if (!n) return false;
      if (n.parent >= 0) {
        cur = n.parent;
      } else if (n.shadowHost >= 0) {
        cur = n.shadowHost;
      } else {
        return false;
      }
    }
    return false;
  }

  function detachNode(nid) {
    const node = getNode(nid);
    if (!node || node.parent < 0) return false;
    const parent = getNode(node.parent);
    if (parent) {
      if (parent.firstChild === nid) parent.firstChild = node.nextSibling;
      if (parent.lastChild === nid) parent.lastChild = node.prevSibling;
    }
    if (node.prevSibling >= 0) {
      const prev = getNode(node.prevSibling);
      if (prev) prev.nextSibling = node.nextSibling;
    }
    if (node.nextSibling >= 0) {
      const next = getNode(node.nextSibling);
      if (next) next.prevSibling = node.prevSibling;
    }
    node.parent = -1;
    node.prevSibling = -1;
    node.nextSibling = -1;
    return true;
  }

  function appendChildNode(parentNid, childNid) {
    const parent = getNode(parentNid);
    const child = getNode(childNid);
    if (!parent || !child || parentNid === childNid) return false;
    if (isInclusiveAncestor(childNid, parentNid)) return false;

    // DocumentFragment (nodeType 9 with nid > 0) moves its children
    if (child.nodeType === 9 && childNid !== 0) {
      let c = child.firstChild;
      while (c >= 0) {
        const next = getNode(c).nextSibling;
        appendChildNode(parentNid, c);
        c = next;
      }
      return true;
    }

    detachNode(childNid);
    child.parent = parentNid;
    if (parent.lastChild >= 0) {
      const last = getNode(parent.lastChild);
      if (last) last.nextSibling = childNid;
      child.prevSibling = parent.lastChild;
      parent.lastChild = childNid;
    } else {
      parent.firstChild = childNid;
      parent.lastChild = childNid;
    }
    return true;
  }

  function insertBeforeNode(newNid, refNid) {
    const ref = getNode(refNid);
    const newNode = getNode(newNid);
    if (!ref || !newNode || ref.parent < 0) return false;
    const parentNid = ref.parent;
    if (newNid === refNid) return true;
    if (isInclusiveAncestor(newNid, parentNid)) return false;

    if (newNode.nodeType === 9 && newNid !== 0) {
      let c = newNode.firstChild;
      while (c >= 0) {
        const next = getNode(c).nextSibling;
        insertBeforeNode(c, refNid);
        c = next;
      }
      return true;
    }

    detachNode(newNid);
    const parent = getNode(parentNid);
    newNode.parent = parentNid;
    newNode.prevSibling = ref.prevSibling;
    newNode.nextSibling = refNid;
    if (ref.prevSibling >= 0) {
      const prev = getNode(ref.prevSibling);
      if (prev) prev.nextSibling = newNid;
    } else if (parent) {
      parent.firstChild = newNid;
    }
    ref.prevSibling = newNid;
    return true;
  }

  function getChildNids(nid) {
    const node = getNode(nid);
    if (!node) return [];
    const out = [];
    let cur = node.firstChild;
    while (cur >= 0) {
      out.push(cur);
      const n = getNode(cur);
      cur = n ? n.nextSibling : -1;
    }
    return out;
  }

  function getDescendants(rootNid, elementsOnly) {
    const out = [];
    const root = getNode(rootNid);
    if (!root) return out;
    const stack = [];
    let c = root.lastChild;
    while (c >= 0) {
      stack.push(c);
      c = getNode(c).prevSibling;
    }
    while (stack.length > 0) {
      const nid = stack.pop();
      const node = getNode(nid);
      if (!node) continue;
      if (!elementsOnly || node.nodeType === 1) {
        out.push(nid);
      }
      let child = node.lastChild;
      while (child >= 0) {
        stack.push(child);
        child = getNode(child).prevSibling;
      }
    }
    return out;
  }

  function getTextContent(nid) {
    const node = getNode(nid);
    if (!node) return "";
    if (node.nodeType === 3 || node.nodeType === 8 || node.nodeType === 7) {
      return node.text;
    }
    let out = "";
    const desc = getDescendants(nid, false);
    for (let i = 0; i < desc.length; i++) {
      const d = getNode(desc[i]);
      if (d && d.nodeType === 3) out += d.text;
    }
    return out;
  }

  function serializeNode(nid) {
    const node = getNode(nid);
    if (!node) return "";
    if (node.nodeType === 3) {
      const parent = getNode(node.parent);
      if (parent && parent.nodeType === 1 && (parent.localName === "script" || parent.localName === "style")) {
        return node.text;
      }
      return escapeText(node.text);
    }
    if (node.nodeType === 8) return "<!--" + node.text + "-->";
    if (node.nodeType === 10) return "<!DOCTYPE " + (node.doctypeName || "html") + ">";
    if (node.nodeType === 7) return "<?" + node.piTarget + " " + node.text + ">";
    if (node.nodeType === 9) {
      return getChildNids(nid).map(serializeNode).join("");
    }
    if (node.nodeType === 1) {
      const tag = node.prefix ? (node.prefix + ":" + node.localName) : node.localName;
      let s = "<" + tag;
      for (let i = 0; i < node.attrs.length; i++) {
        const a = node.attrs[i];
        const qn = a.prefix ? (a.prefix + ":" + a.localName) : a.localName;
        s += " " + qn + '="' + escapeAttr(a.value) + '"';
      }
      s += ">";
      if (VOID_ELEMENTS.has(node.localName) && node.ns === HTML_NS) {
        return s;
      }
      if (node.localName === "template" && node.templateContents >= 0) {
        s += serializeInner(node.templateContents);
      } else {
        s += serializeInner(nid);
      }
      s += "</" + tag + ">";
      return s;
    }
    return "";
  }

  function serializeInner(nid) {
    const node = getNode(nid);
    if (!node) return "";
    if (node.nodeType === 1 && node.localName === "template" && node.templateContents >= 0) {
      return getChildNids(node.templateContents).map(serializeNode).join("");
    }
    return getChildNids(nid).map(serializeNode).join("");
  }

  function parseTagAttributes(attrStr) {
    const attrs = [];
    const re = /([^\s=<>"'\/]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;
    let m;
    while ((m = re.exec(attrStr)) !== null) {
      const rawName = m[1];
      if (!rawName || rawName === "/") continue;
      const rawVal = m[2] !== undefined ? m[2] : (m[3] !== undefined ? m[3] : (m[4] !== undefined ? m[4] : ""));
      const val = decodeEntities(rawVal);
      const colonIdx = rawName.indexOf(":");
      let prefix = null;
      let localName = rawName.toLowerCase();
      if (colonIdx > 0) {
        prefix = rawName.slice(0, colonIdx).toLowerCase();
        localName = rawName.slice(colonIdx + 1).toLowerCase();
      }
      // Avoid duplicate attribute names (HTML5 keeps first)
      if (!attrs.some(a => a.localName === localName && a.prefix === prefix)) {
        attrs.push({ localName, prefix, ns: "", value: val });
      }
    }
    return attrs;
  }

  const AUTO_CLOSE_MAP = {
    li: new Set(["li"]),
    dt: new Set(["dt", "dd"]),
    dd: new Set(["dt", "dd"]),
    p: new Set([
      "address", "article", "aside", "blockquote", "details", "div", "dl",
      "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3",
      "h4", "h5", "h6", "header", "hgroup", "hr", "main", "menu", "nav",
      "ol", "p", "pre", "section", "table", "ul"
    ]),
    option: new Set(["option"]),
    optgroup: new Set(["optgroup"]),
    tr: new Set(["tr"]),
    td: new Set(["td", "th"]),
    th: new Set(["td", "th"]),
    thead: new Set(["tbody", "tfoot"]),
    tbody: new Set(["tbody", "tfoot"]),
    tfoot: new Set(["tbody"])
  };

  function parseHTMLInto(html, rootNid, isFullDocument) {
    const src = String(html ?? "");
    const stack = [rootNid];
    let i = 0;
    const len = src.length;

    const currentParent = () => {
      const top = stack[stack.length - 1];
      const topNode = getNode(top);
      if (topNode && topNode.nodeType === 1 && topNode.localName === "template") {
        if (topNode.templateContents < 0) {
          topNode.templateContents = createNode(9, {});
        }
        return topNode.templateContents;
      }
      return top;
    };

    while (i < len) {
      const lt = src.indexOf("<", i);
      if (lt === -1) {
        const txt = decodeEntities(src.slice(i));
        if (txt) {
          const tNid = createNode(3, { text: txt });
          appendChildNode(currentParent(), tNid);
        }
        break;
      }
      if (lt > i) {
        const txt = decodeEntities(src.slice(i, lt));
        if (txt) {
          const tNid = createNode(3, { text: txt });
          appendChildNode(currentParent(), tNid);
        }
      }

      // Comment
      if (src.startsWith("<!--", lt)) {
        const endComment = src.indexOf("-->", lt + 4);
        if (endComment === -1) {
          const cNid = createNode(8, { text: src.slice(lt + 4) });
          appendChildNode(currentParent(), cNid);
          break;
        }
        const cNid = createNode(8, { text: src.slice(lt + 4, endComment) });
        appendChildNode(currentParent(), cNid);
        i = endComment + 3;
        continue;
      }

      // Doctype
      if (/^<!DOCTYPE/i.test(src.slice(lt, lt + 9))) {
        const gt = src.indexOf(">", lt + 9);
        if (gt === -1) break;
        const dtNid = createNode(10, { doctypeName: "html" });
        appendChildNode(rootNid, dtNid);
        i = gt + 1;
        continue;
      }

      // CDATA
      if (src.startsWith("<![CDATA[", lt)) {
        const endCdata = src.indexOf("]]>", lt + 9);
        const content = endCdata === -1 ? src.slice(lt + 9) : src.slice(lt + 9, endCdata);
        const tNid = createNode(3, { text: content });
        appendChildNode(currentParent(), tNid);
        if (endCdata === -1) break;
        i = endCdata + 3;
        continue;
      }

      // Closing tag
      if (src.charAt(lt + 1) === "/") {
        const gt = src.indexOf(">", lt + 2);
        if (gt === -1) break;
        const rawClose = src.slice(lt + 2, gt).trim().toLowerCase();
        const closeName = rawClose.split(/\s+/)[0];
        for (let s = stack.length - 1; s >= 1; s--) {
          const sn = getNode(stack[s]);
          if (sn && sn.localName === closeName) {
            stack.length = s;
            break;
          }
        }
        i = gt + 1;
        continue;
      }

      // Opening tag
      const tagMatch = /^<([a-zA-Z][a-zA-Z0-9:-]*)/.exec(src.slice(lt));
      if (!tagMatch) {
        const tNid = createNode(3, { text: "<" });
        appendChildNode(currentParent(), tNid);
        i = lt + 1;
        continue;
      }
      const rawTagName = tagMatch[1];
      const lowerTag = rawTagName.toLowerCase();
      // Find closing '>' respecting quotes
      let gt = lt + 1 + rawTagName.length;
      let quote = null;
      while (gt < len) {
        const ch = src.charAt(gt);
        if (quote) {
          if (ch === quote) quote = null;
        } else {
          if (ch === '"' || ch === "'") quote = ch;
          else if (ch === ">") break;
        }
        gt++;
      }
      if (gt >= len) break;

      const attrChunk = src.slice(lt + 1 + rawTagName.length, gt);
      const selfClosing = /\/\s*$/.test(attrChunk) || VOID_ELEMENTS.has(lowerTag);
      const attrs = parseTagAttributes(attrChunk.replace(/\/\s*$/, ""));

      // Handle implicit auto-close on open tag
      if ( stack.length > 1 ) {
        const topNode = getNode(stack[stack.length - 1]);
        if (topNode && topNode.nodeType === 1) {
          for (const [openTag, closers] of Object.entries(AUTO_CLOSE_MAP)) {
            if (topNode.localName === openTag && closers.has(lowerTag)) {
              stack.pop();
              break;
            }
          }
        }
      }

      let ns = HTML_NS;
      for (let s = stack.length - 1; s >= 1; s--) {
        const anc = getNode(stack[s]);
        if (anc && anc.nodeType === 1) {
          if (anc.localName === "svg" || anc.ns === SVG_NS) ns = SVG_NS;
          else if (anc.localName === "math" || anc.ns === MATHML_NS) ns = MATHML_NS;
          break;
        }
      }
      if (lowerTag === "svg") ns = SVG_NS;
      if (lowerTag === "math") ns = MATHML_NS;

      const elNid = createNode(1, {
        localName: lowerTag,
        prefix: null,
        ns,
        attrs
      });
      const elNode = getNode(elNid);
      for (let a = 0; a < attrs.length; a++) {
        if (attrs[a].localName === "id" && attrs[a].value) {
          if (!state.idIndex.has(attrs[a].value)) {
            state.idIndex.set(attrs[a].value, elNid);
          }
        }
      }
      if (lowerTag === "input") {
        const valAttr = getAttr(elNode, "value");
        if (valAttr !== null) elNode.formValue = valAttr;
        if (getAttr(elNode, "checked") !== null) elNode.formChecked = true;
      } else if (lowerTag === "template") {
        elNode.templateContents = createNode(9, {});
      }

      appendChildNode(currentParent(), elNid);
      i = gt + 1;

      if (RAW_TEXT_ELEMENTS.has(lowerTag) && !selfClosing) {
        const closeRe = new RegExp("</" + lowerTag + "\\s*>", "i");
        const rest = src.slice(i);
        const closeMatch = closeRe.exec(rest);
        const rawContent = closeMatch ? rest.slice(0, closeMatch.index) : rest;
        const textContent = (lowerTag === "textarea" || lowerTag === "title")
          ? decodeEntities(rawContent)
          : rawContent;
        if (textContent) {
          const tNid = createNode(3, { text: textContent });
          appendChildNode(elNid, tNid);
        }
        if (lowerTag === "textarea") {
          elNode.formValue = textContent;
        }
        i = closeMatch ? (i + closeMatch.index + closeMatch[0].length) : len;
        continue;
      }

      if (!selfClosing) {
        stack.push(elNid);
      }
    }

    if (isFullDocument) {
      ensureDocumentStructure(rootNid);
    }
  }

  function ensureDocumentStructure(docNid) {
    const children = getChildNids(docNid);
    let htmlNid = -1;
    for (const cid of children) {
      const n = getNode(cid);
      if (n && n.nodeType === 1 && n.localName === "html") {
        htmlNid = cid;
        break;
      }
    }
    if (htmlNid < 0) {
      htmlNid = createNode(1, { localName: "html", ns: HTML_NS });
      for (const cid of children) {
        const n = getNode(cid);
        if (n && n.nodeType !== 10) {
          appendChildNode(htmlNid, cid);
        }
      }
      appendChildNode(docNid, htmlNid);
    }

    const htmlChildren = getChildNids(htmlNid);
    let headNid = -1;
    let bodyNid = -1;
    for (const cid of htmlChildren) {
      const n = getNode(cid);
      if (n && n.nodeType === 1) {
        if (n.localName === "head" && headNid < 0) headNid = cid;
        else if (n.localName === "body" && bodyNid < 0) bodyNid = cid;
      }
    }
    if (headNid < 0) {
      headNid = createNode(1, { localName: "head", ns: HTML_NS });
      if (htmlChildren.length > 0) {
        insertBeforeNode(headNid, htmlChildren[0]);
      } else {
        appendChildNode(htmlNid, headNid);
      }
    }
    if (bodyNid < 0) {
      bodyNid = createNode(1, { localName: "body", ns: HTML_NS });
      const headTags = new Set(["title", "meta", "link", "style", "base"]);
      for (const cid of getChildNids(htmlNid)) {
        if (cid === headNid || cid === bodyNid) continue;
        const n = getNode(cid);
        if (n && n.nodeType === 1 && headTags.has(n.localName)) {
          appendChildNode(headNid, cid);
        } else {
          appendChildNode(bodyNid, cid);
        }
      }
      appendChildNode(htmlNid, bodyNid);
    }
  }

  function cloneSubtree(nid, deep) {
    const src = getNode(nid);
    if (!src) return -1;
    const copyNid = createNode(src.nodeType, {
      localName: src.localName,
      prefix: src.prefix,
      ns: src.ns,
      attrs: src.attrs.map(a => ({ ...a })),
      text: src.text,
      doctypeName: src.doctypeName,
      publicId: src.publicId,
      systemId: src.systemId,
      piTarget: src.piTarget,
      formValue: src.formValue,
      formChecked: src.formChecked
    });
    if (src.templateContents >= 0) {
      getNode(copyNid).templateContents = cloneSubtree(src.templateContents, deep);
    }
    if (deep) {
      let c = src.firstChild;
      while (c >= 0) {
        const childCopy = cloneSubtree(c, true);
        if (childCopy >= 0) appendChildNode(copyNid, childCopy);
        c = getNode(c).nextSibling;
      }
    }
    return copyNid;
  }

  // =========================================================================
  // CSS Selector Engine (matching `obscura-dom/src/selector.rs`)
  // =========================================================================

  function splitSelectorGroups(selector) {
    const groups = [];
    let depth = 0;
    let quote = null;
    let start = 0;
    for (let i = 0; i < selector.length; i++) {
      const ch = selector.charAt(i);
      if (quote) {
        if (ch === "\\" && i + 1 < selector.length) { i++; continue; }
        if (ch === quote) quote = null;
      } else {
        if (ch === '"' || ch === "'") quote = ch;
        else if (ch === "(" || ch === "[") depth++;
        else if (ch === ")" || ch === "]") depth = Math.max(0, depth - 1);
        else if (ch === "," && depth === 0) {
          groups.push(selector.slice(start, i).trim());
          start = i + 1;
        }
      }
    }
    const last = selector.slice(start).trim();
    if (last) groups.push(last);
    return groups;
  }

  function tokenizeComplexSelector(sel) {
    // Splits a single complex selector into [{compound, combinator}] from left to right
    const parts = [];
    let depth = 0;
    let quote = null;
    let cur = "";
    let pendingComb = null;

    const flush = (comb) => {
      const trimmed = cur.trim();
      if (trimmed) {
        parts.push({ compound: trimmed, combinator: pendingComb });
        cur = "";
        pendingComb = comb;
      } else if (comb && comb !== " ") {
        pendingComb = comb;
      }
    };

    for (let i = 0; i < sel.length; i++) {
      const ch = sel.charAt(i);
      if (quote) {
        cur += ch;
        if (ch === "\\" && i + 1 < sel.length) { cur += sel.charAt(++i); continue; }
        if (ch === quote) quote = null;
        continue;
      }
      if (ch === '"' || ch === "'") { quote = ch; cur += ch; continue; }
      if (ch === "(" || ch === "[") { depth++; cur += ch; continue; }
      if (ch === ")" || ch === "]") { depth = Math.max(0, depth - 1); cur += ch; continue; }
      if (depth === 0 && (ch === ">" || ch === "+" || ch === "~")) {
        flush(ch);
        continue;
      }
      if (depth === 0 && /\s/.test(ch)) {
        while (i + 1 < sel.length && /\s/.test(sel.charAt(i + 1))) i++;
        if (cur.trim()) flush(" ");
        continue;
      }
      cur += ch;
    }
    flush(null);
    return parts;
  }

  function elementSiblings(node) {
    if (!node || node.parent < 0) return [];
    return getChildNids(node.parent)
      .map(getNode)
      .filter(n => n && n.nodeType === 1);
  }

  function matchNth(expr, index1Based) {
    const s = expr.replace(/\s+/g, "").toLowerCase();
    if (s === "odd") return index1Based % 2 === 1;
    if (s === "even") return index1Based % 2 === 0;
    if (/^[+-]?\d+$/.test(s)) return index1Based === parseInt(s, 10);
    const m = /^([+-]?\d*)n([+-]\d+)?$/.exec(s);
    if (!m) return false;
    const a = (m[1] === "" || m[1] === "+") ? 1 : (m[1] === "-" ? -1 : parseInt(m[1], 10));
    const b = m[2] ? parseInt(m[2], 10) : 0;
    if (a === 0) return index1Based === b;
    const diff = index1Based - b;
    if (diff * a < 0) return false;
    return diff % a === 0;
  }

  function isElementDisabled(node) {
    if (!node || node.nodeType !== 1) return false;
    if (getAttr(node, "disabled") !== null) return true;
    return false;
  }

  function isElementChecked(node) {
    if (!node || node.nodeType !== 1) return false;
    if (node.localName === "input") {
      const t = (getAttr(node, "type") || "text").toLowerCase();
      if (t === "checkbox" || t === "radio") {
        if (node.formChecked !== null) return !!node.formChecked;
        return getAttr(node, "checked") !== null;
      }
    }
    if (node.localName === "option") {
      return getAttr(node, "selected") !== null;
    }
    return false;
  }

  function matchesCompound(node, compound, scopeNid) {
    if (!node || node.nodeType !== 1) return false;
    let i = 0;
    const len = compound.length;

    while (i < len) {
      const ch = compound.charAt(i);

      if (ch === "*") {
        i++;
        continue;
      }

      // ID selector #foo
      if (ch === "#") {
        i++;
        let id = "";
        while (i < len) {
          const c = compound.charAt(i);
          if (c === "\\" && i + 1 < len) { id += compound.charAt(++i); i++; continue; }
          if (/[.#\[:\s>+~]/.test(c)) break;
          id += c;
          i++;
        }
        if ((getAttr(node, "id") || "") !== id) return false;
        continue;
      }

      // Class selector .foo
      if (ch === ".") {
        i++;
        let cls = "";
        while (i < len) {
          const c = compound.charAt(i);
          if (c === "\\" && i + 1 < len) { cls += compound.charAt(++i); i++; continue; }
          if (/[.#\[:\s>+~]/.test(c)) break;
          cls += c;
          i++;
        }
        const classAttr = getAttr(node, "class") || "";
        const classes = classAttr.trim().split(/\s+/);
        if (!classes.includes(cls)) return false;
        continue;
      }

      // Attribute selector [...]
      if (ch === "[") {
        let end = i + 1;
        let quote = null;
        while (end < len) {
          const c = compound.charAt(end);
          if (quote) {
            if (c === "\\" && end + 1 < len) { end += 2; continue; }
            if (c === quote) quote = null;
          } else {
            if (c === '"' || c === "'") quote = c;
            else if (c === "]") break;
          }
          end++;
        }
        const inner = compound.slice(i + 1, end).trim();
        i = end + 1;
        const attrMatch = /^([^\s=~|^$*]+)(?:\s*([~|^$*]?=)\s*(?:"([^"]*)"|'([^']*)'|([^\s"'\]]+))(?:\s+([iIsS]))?)?$/.exec(inner);
        if (!attrMatch) return false;
        const attrName = attrMatch[1].replace(/\\(.)/g, "$1").toLowerCase();
        const op = attrMatch[2];
        const rawVal = (attrMatch[3] ?? attrMatch[4] ?? attrMatch[5] ?? "").replace(/\\(.)/g, "$1");
        const caseInsensitive = (attrMatch[6] || "").toLowerCase() === "i";
        const actual = getAttr(node, attrName);
        if (actual === null) return false;
        if (!op) continue;
        const aVal = caseInsensitive ? actual.toLowerCase() : actual;
        const eVal = caseInsensitive ? rawVal.toLowerCase() : rawVal;
        if (op === "=" && aVal !== eVal) return false;
        if (op === "~=") {
          if (!eVal || !aVal.trim().split(/\s+/).includes(eVal)) return false;
        }
        if (op === "|=") {
          if (aVal !== eVal && !aVal.startsWith(eVal + "-")) return false;
        }
        if (op === "^=") {
          if (!eVal || !aVal.startsWith(eVal)) return false;
        }
        if (op === "$=") {
          if (!eVal || !aVal.endsWith(eVal)) return false;
        }
        if (op === "*=") {
          if (!eVal || !aVal.includes(eVal)) return false;
        }
        continue;
      }

      // Pseudo-class or pseudo-element
      if (ch === ":") {
        if (compound.charAt(i + 1) === ":") {
          // Pseudo-elements never match in querySelector
          return false;
        }
        i++;
        let name = "";
        while (i < len && /[a-zA-Z0-9_-]/.test(compound.charAt(i))) {
          name += compound.charAt(i++);
        }
        name = name.toLowerCase();
        let arg = null;
        if (compound.charAt(i) === "(") {
          let depth = 1;
          let startArg = ++i;
          while (i < len && depth > 0) {
            if (compound.charAt(i) === "(") depth++;
            else if (compound.charAt(i) === ")") depth--;
            i++;
          }
          arg = compound.slice(startArg, i - 1).trim();
        }
        if (name === "root") {
          if (node.localName !== "html") return false;
        } else if (name === "scope") {
          if (scopeNid !== undefined && scopeNid > 0 && node.nid !== scopeNid) return false;
        } else if (name === "first-child") {
          const sibs = elementSiblings(node);
          if (!sibs.length || sibs[0].nid !== node.nid) return false;
        } else if (name === "last-child") {
          const sibs = elementSiblings(node);
          if (!sibs.length || sibs[sibs.length - 1].nid !== node.nid) return false;
        } else if (name === "only-child") {
          const sibs = elementSiblings(node);
          if (sibs.length !== 1 || sibs[0].nid !== node.nid) return false;
        } else if (name === "first-of-type") {
          const sibs = elementSiblings(node).filter(s => s.localName === node.localName);
          if (!sibs.length || sibs[0].nid !== node.nid) return false;
        } else if (name === "last-of-type") {
          const sibs = elementSiblings(node).filter(s => s.localName === node.localName);
          if (!sibs.length || sibs[sibs.length - 1].nid !== node.nid) return false;
        } else if (name === "only-of-type") {
          const sibs = elementSiblings(node).filter(s => s.localName === node.localName);
          if (sibs.length !== 1 || sibs[0].nid !== node.nid) return false;
        } else if (name === "nth-child" && arg !== null) {
          const sibs = elementSiblings(node);
          const idx = sibs.findIndex(s => s.nid === node.nid) + 1;
          if (idx <= 0 || !matchNth(arg, idx)) return false;
        } else if (name === "nth-last-child" && arg !== null) {
          const sibs = elementSiblings(node);
          const pos = sibs.findIndex(s => s.nid === node.nid);
          if (pos < 0 || !matchNth(arg, sibs.length - pos)) return false;
        } else if (name === "nth-of-type" && arg !== null) {
          const sibs = elementSiblings(node).filter(s => s.localName === node.localName);
          const idx = sibs.findIndex(s => s.nid === node.nid) + 1;
          if (idx <= 0 || !matchNth(arg, idx)) return false;
        } else if (name === "empty") {
          const children = getChildNids(node.nid).map(getNode);
          if (children.some(c => c && (c.nodeType === 1 || (c.nodeType === 3 && c.text.length > 0)))) return false;
        } else if (name === "disabled") {
          if (!isElementDisabled(node)) return false;
        } else if (name === "enabled") {
          if (isElementDisabled(node)) return false;
        } else if (name === "checked") {
          if (!isElementChecked(node)) return false;
        } else if (name === "required") {
          if (getAttr(node, "required") === null) return false;
        } else if (name === "optional") {
          if (getAttr(node, "required") !== null) return false;
        } else if (name === "not" && arg !== null) {
          if (matchesSelectorList(node.nid, arg, scopeNid)) return false;
        } else if ((name === "is" || name === "where" || name === "matches") && arg !== null) {
          if (!matchesSelectorList(node.nid, arg, scopeNid)) return false;
        } else if (name === "has" && arg !== null) {
          if (querySelectorScoped(node.nid, arg, true).length === 0) return false;
        } else if (name === "any-link" || name === "link") {
          if ((node.localName !== "a" && node.localName !== "area") || getAttr(node, "href") === null) return false;
        }
        continue;
      }

      // Tag name selector
      let tag = "";
      while (i < len) {
        const c = compound.charAt(i);
        if (c === "\\" && i + 1 < len) { tag += compound.charAt(++i); i++; continue; }
        if (/[.#\[:\s>+~]/.test(c)) break;
        tag += c;
        i++;
      }
      if (!tag) return false;
      if (node.localName !== tag.toLowerCase()) return false;
    }
    return true;
  }

  function matchesComplexParts(node, parts, partIdx, scopeNid) {
    const current = parts[partIdx];
    if (!matchesCompound(node, current.compound, scopeNid)) return false;
    if (partIdx === 0) return true;

    const comb = current.combinator;
    const prevIdx = partIdx - 1;

    if (comb === ">") {
      const parent = getNode(node.parent);
      return !!parent && parent.nodeType === 1 && matchesComplexParts(parent, parts, prevIdx, scopeNid);
    }
    if (comb === "+") {
      let p = node.prevSibling;
      while (p >= 0) {
        const pn = getNode(p);
        if (pn && pn.nodeType === 1) {
          return matchesComplexParts(pn, parts, prevIdx, scopeNid);
        }
        p = pn ? pn.prevSibling : -1;
      }
      return false;
    }
    if (comb === "~") {
      let p = node.prevSibling;
      while (p >= 0) {
        const pn = getNode(p);
        if (pn && pn.nodeType === 1 && matchesComplexParts(pn, parts, prevIdx, scopeNid)) {
          return true;
        }
        p = pn ? pn.prevSibling : -1;
      }
      return false;
    }
    // Descendant combinator " "
    let anc = getNode(node.parent);
    while (anc && anc.nodeType === 1) {
      if (matchesComplexParts(anc, parts, prevIdx, scopeNid)) return true;
      anc = getNode(anc.parent);
    }
    return false;
  }

  function matchesSelectorList(nid, selectorStr, scopeNid) {
    const node = getNode(nid);
    if (!node || node.nodeType !== 1 || !selectorStr) return false;
    const groups = splitSelectorGroups(String(selectorStr));
    if (groups.length === 0) return false;
    for (let g = 0; g < groups.length; g++) {
      let group = groups[g];
      if (group.startsWith(">") || group.startsWith("+") || group.startsWith("~")) {
        group = ":scope " + group;
      }
      const parts = tokenizeComplexSelector(group);
      if (parts.length > 0 && matchesComplexParts(node, parts, parts.length - 1, scopeNid)) {
        return true;
      }
    }
    return false;
  }

  function querySelectorScoped(rootNid, selectorStr, firstOnly) {
    const descendants = getDescendants(rootNid, true);
    const out = [];
    for (let i = 0; i < descendants.length; i++) {
      const nid = descendants[i];
      if (matchesSelectorList(nid, selectorStr, rootNid)) {
        out.push(nid);
        if (firstOnly) return out;
      }
    }
    return out;
  }

  // Tree traversal helpers used by TreeWalker / NodeIterator / Range
  function nextInSubtree(rootNid, curNid) {
    const cur = getNode(curNid);
    if (!cur) return -1;
    if (cur.firstChild >= 0) return cur.firstChild;
    let n = curNid;
    while (n >= 0 && n !== rootNid) {
      const node = getNode(n);
      if (!node) return -1;
      if (node.nextSibling >= 0) return node.nextSibling;
      n = node.parent;
    }
    return -1;
  }

  function nextAfterSubtree(rootNid, curNid) {
    let n = curNid;
    while (n >= 0 && n !== rootNid) {
      const node = getNode(n);
      if (!node) return -1;
      if (node.nextSibling >= 0) return node.nextSibling;
      n = node.parent;
    }
    return -1;
  }

  function prevInSubtree(rootNid, curNid) {
    if (curNid === rootNid) return -1;
    const cur = getNode(curNid);
    if (!cur) return -1;
    if (cur.prevSibling >= 0) {
      let p = cur.prevSibling;
      while (p >= 0) {
        const pn = getNode(p);
        if (!pn || pn.lastChild < 0) return p;
        p = pn.lastChild;
      }
    }
    return cur.parent >= 0 ? cur.parent : -1;
  }

  function nodeChildIndex(nid) {
    let idx = 0;
    let cur = getNode(nid)?.prevSibling ?? -1;
    while (cur >= 0) {
      idx++;
      cur = getNode(cur)?.prevSibling ?? -1;
    }
    return idx;
  }

  function compareNodeOrder(aNid, bNid) {
    if (aNid === bNid) return 0;
    const chain = (n) => {
      const arr = [n];
      let c = getNode(n)?.parent ?? -1;
      while (c >= 0) {
        arr.push(c);
        c = getNode(c)?.parent ?? -1;
      }
      return arr.reverse();
    };
    const aa = chain(aNid);
    const bb = chain(bNid);
    if (aa[0] !== bb[0]) return aNid < bNid ? -1 : 1;
    let i = 0;
    while (i < aa.length && i < bb.length && aa[i] === bb[i]) i++;
    if (i >= aa.length) return -1;
    if (i >= bb.length) return 1;
    return nodeChildIndex(aa[i]) < nodeChildIndex(bb[i]) ? -1 : 1;
  }

  // =========================================================================
  // `op_dom` implementation matching `obscura-js/src/ops.rs`
  // =========================================================================

  function op_dom(cmd, a1, a2, _frameId) {
    const arg1 = String(a1 ?? "");
    const arg2 = String(a2 ?? "");
    switch (cmd) {
      case "prefers_reduced_motion":
      case "prefers_dark":
      case "sandbox_opaque_origin":
        return "false";
      case "scripting_enabled":
        return state.scriptingEnabled ? "true" : "false";
      case "performance_time_origin":
        return String(state.timeOrigin);
      case "performance_timing":
        if (!arg1) return JSON.stringify(state.timing);
        return state.timing[arg1] !== undefined ? String(state.timing[arg1]) : "null";
      case "performance_lifecycle":
        if (arg1 && state.timing[arg1] !== undefined) state.timing[arg1] = Date.now();
        return "null";
      case "document_lifecycle":
      case "retire_frame":
        return "true";
      case "get_form_state": {
        const n = getNode(parseInt(arg1, 10));
        if (!n) return "null";
        return JSON.stringify({
          value: n.formValue,
          checked: n.formChecked,
          indeterminate: !!n.formIndeterminate
        });
      }
      case "set_form_value": {
        const n = getNode(parseInt(arg1, 10));
        if (n) n.formValue = arg2;
        return "null";
      }
      case "set_form_checked": {
        const n = getNode(parseInt(arg1, 10));
        if (n) n.formChecked = (arg2 === "true");
        return "null";
      }
      case "set_form_indeterminate": {
        const n = getNode(parseInt(arg1, 10));
        if (n) n.formIndeterminate = (arg2 === "true");
        return "null";
      }
      case "document_node_id":
        return "0";
      case "document_compat_mode":
        return state.quirks ? "BackCompat" : "CSS1Compat";
      case "document_title": {
        const titleIds = querySelectorScoped(0, "title", true);
        if (titleIds.length === 0) return JSON.stringify("");
        const raw = getTextContent(titleIds[0]).replace(/[\t\n\f\r ]+/g, " ").trim();
        return JSON.stringify(raw);
      }
      case "document_url":
        return JSON.stringify(state.url);
      case "document_origin":
        return JSON.stringify(state.origin);
      case "document_base_url": {
        const baseIds = querySelectorScoped(0, "base[href]", true);
        if (baseIds.length > 0) {
          const href = getAttr(getNode(baseIds[0]), "href");
          if (href) {
            const resolved = op_url_resolve(href, state.url);
            if (resolved) return JSON.stringify(resolved);
          }
        }
        return JSON.stringify(state.url);
      }
      case "document_base_href": {
        const baseIds = querySelectorScoped(0, "base[href]", true);
        if (baseIds.length > 0) {
          return JSON.stringify(getAttr(getNode(baseIds[0]), "href") || "");
        }
        return JSON.stringify("");
      }
      case "document_referrer":
        return JSON.stringify(state.referrer);
      case "document_encoding":
        return JSON.stringify(state.encoding);
      case "document_content_type":
        return JSON.stringify(state.contentType);
      case "document_element": {
        for (const cid of getChildNids(0)) {
          const n = getNode(cid);
          if (n && n.nodeType === 1 && n.localName === "html") return String(cid);
        }
        return "-1";
      }
      case "document_doctype": {
        for (const cid of getChildNids(0)) {
          const n = getNode(cid);
          if (n && n.nodeType === 10) {
            return JSON.stringify({
              name: n.doctypeName,
              publicId: n.publicId,
              systemId: n.systemId,
              nodeId: cid
            });
          }
        }
        return "null";
      }
      case "get_element_by_id": {
        if (!arg1) return "-1";
        const fast = state.idIndex.get(arg1);
        if (fast !== undefined && isConnected(fast)) {
          const fn = getNode(fast);
          if (fn && getAttr(fn, "id") === arg1) return String(fast);
        }
        const desc = getDescendants(0, true);
        for (let i = 0; i < desc.length; i++) {
          const n = getNode(desc[i]);
          if (n && getAttr(n, "id") === arg1) {
            state.idIndex.set(arg1, desc[i]);
            return String(desc[i]);
          }
        }
        return "-1";
      }
      case "query_selector": {
        const hits = querySelectorScoped(0, arg1, true);
        return hits.length > 0 ? String(hits[0]) : "-1";
      }
      case "query_selector_all": {
        return JSON.stringify(querySelectorScoped(0, arg1, false));
      }
      case "query_selector_scoped": {
        const root = parseInt(arg1, 10) || 0;
        const hits = querySelectorScoped(root, arg2, true);
        return hits.length > 0 ? String(hits[0]) : "-1";
      }
      case "query_selector_all_scoped": {
        const root = parseInt(arg1, 10) || 0;
        return JSON.stringify(querySelectorScoped(root, arg2, false));
      }
      case "matches_selector": {
        const nid = parseInt(arg1, 10) || 0;
        return matchesSelectorList(nid, arg2, 0) ? "true" : "false";
      }
      case "node_type": {
        const n = getNode(parseInt(arg1, 10));
        return n ? String(n.nodeType) : "0";
      }
      case "node_name": {
        const n = getNode(parseInt(arg1, 10));
        if (!n) return JSON.stringify("");
        if (n.nodeType === 9) return JSON.stringify("#document");
        if (n.nodeType === 3) return JSON.stringify("#text");
        if (n.nodeType === 8) return JSON.stringify("#comment");
        if (n.nodeType === 10) return JSON.stringify(n.doctypeName);
        if (n.nodeType === 7) return JSON.stringify(n.piTarget);
        const qn = n.prefix ? (n.prefix + ":" + n.localName) : n.localName;
        return JSON.stringify(n.ns === HTML_NS ? qn.toUpperCase() : qn);
      }
      case "tag_name": {
        const n = getNode(parseInt(arg1, 10));
        if (!n || n.nodeType !== 1) return JSON.stringify("");
        const qn = n.prefix ? (n.prefix + ":" + n.localName) : n.localName;
        return JSON.stringify(n.ns === HTML_NS ? qn.toUpperCase() : qn);
      }
      case "local_name": {
        const n = getNode(parseInt(arg1, 10));
        return JSON.stringify((n && n.nodeType === 1) ? n.localName : "");
      }
      case "namespace_uri": {
        const n = getNode(parseInt(arg1, 10));
        return JSON.stringify((n && n.nodeType === 1) ? n.ns : "");
      }
      case "text_content": {
        return JSON.stringify(getTextContent(parseInt(arg1, 10) || 0));
      }
      case "set_text_content": {
        const n = getNode(parseInt(arg1, 10));
        if (n && (n.nodeType === 3 || n.nodeType === 8 || n.nodeType === 7)) {
          n.text = arg2;
        }
        return "true";
      }
      case "parent_node": {
        const n = getNode(parseInt(arg1, 10));
        return n && n.parent >= 0 ? String(n.parent) : "-1";
      }
      case "first_child": {
        const n = getNode(parseInt(arg1, 10));
        return n && n.firstChild >= 0 ? String(n.firstChild) : "-1";
      }
      case "last_child": {
        const n = getNode(parseInt(arg1, 10));
        return n && n.lastChild >= 0 ? String(n.lastChild) : "-1";
      }
      case "next_sibling": {
        const n = getNode(parseInt(arg1, 10));
        return n && n.nextSibling >= 0 ? String(n.nextSibling) : "-1";
      }
      case "prev_sibling": {
        const n = getNode(parseInt(arg1, 10));
        return n && n.prevSibling >= 0 ? String(n.prevSibling) : "-1";
      }
      case "next_in_subtree":
        return String(nextInSubtree(parseInt(arg1, 10) || 0, parseInt(arg2, 10) || 0));
      case "prev_in_subtree":
        return String(prevInSubtree(parseInt(arg1, 10) || 0, parseInt(arg2, 10) || 0));
      case "next_after_subtree":
        return String(nextAfterSubtree(parseInt(arg1, 10) || 0, parseInt(arg2, 10) || 0));
      case "child_nodes":
        return JSON.stringify(getChildNids(parseInt(arg1, 10) || 0));
      case "element_children":
        return JSON.stringify(
          getChildNids(parseInt(arg1, 10) || 0).filter(cid => getNode(cid)?.nodeType === 1)
        );
      case "has_child_nodes": {
        const n = getNode(parseInt(arg1, 10));
        return (n && n.firstChild >= 0) ? "true" : "false";
      }
      case "assigned_nodes":
        return "null";
      case "get_attribute": {
        const n = getNode(parseInt(arg1, 10));
        const val = getAttr(n, arg2);
        return val === null ? "null" : JSON.stringify(val);
      }
      case "set_attribute": {
        const n = getNode(parseInt(arg1, 10));
        const nul = arg2.indexOf("\0");
        if (n && nul >= 0) {
          setAttr(n, arg2.slice(0, nul), arg2.slice(nul + 1));
        }
        return "true";
      }
      case "remove_attribute": {
        const n = getNode(parseInt(arg1, 10));
        if (n) removeAttr(n, arg2);
        return "true";
      }
      case "attribute_names": {
        const n = getNode(parseInt(arg1, 10));
        if (!n || n.nodeType !== 1) return "[]";
        return JSON.stringify(n.attrs.map(a => a.prefix ? (a.prefix + ":" + a.localName) : a.localName));
      }
      case "attribute_metadata": {
        const n = getNode(parseInt(arg1, 10));
        if (!n || n.nodeType !== 1) return "null";
        const target = arg2.toLowerCase();
        const found = n.attrs.find(a => (a.prefix ? a.prefix + ":" + a.localName : a.localName).toLowerCase() === target);
        if (!found) return "null";
        return JSON.stringify({
          name: found.prefix ? (found.prefix + ":" + found.localName) : found.localName,
          value: found.value,
          namespaceURI: found.ns || null,
          prefix: found.prefix || null,
          localName: found.localName
        });
      }
      case "get_attribute_ns": {
        const n = getNode(parseInt(arg1, 10));
        const parts = arg2.split("\0");
        const local = parts[1] || parts[0] || "";
        const val = getAttr(n, local);
        return val === null ? "null" : JSON.stringify(val);
      }
      case "set_attribute_ns": {
        const n = getNode(parseInt(arg1, 10));
        const parts = arg2.split("\0");
        if (n && parts.length >= 3) {
          setAttr(n, parts[1], parts[2]);
        }
        return "true";
      }
      case "remove_attribute_ns": {
        const n = getNode(parseInt(arg1, 10));
        const parts = arg2.split("\0");
        if (n) removeAttr(n, parts[1] || parts[0] || "");
        return "true";
      }
      case "inner_html":
        return JSON.stringify(serializeInner(parseInt(arg1, 10) || 0));
      case "outer_html":
        return JSON.stringify(serializeNode(parseInt(arg1, 10) || 0));
      case "set_inner_html":
      case "set_inner_html_context":
      case "set_fragment_html_executable": {
        const nid = parseInt(arg1, 10);
        if (!(nid > 0)) return "false";
        const target = getNode(nid);
        if (!target) return "false";
        for (const cid of getChildNids(nid)) {
          detachNode(cid);
        }
        let html = arg2;
        if (cmd !== "set_inner_html") {
          const nul = arg2.indexOf("\0");
          if (nul >= 0) html = arg2.slice(nul + 1);
        }
        if (html) {
          parseHTMLInto(html, nid, false);
          if (cmd !== "set_fragment_html_executable") {
            for (const d of getDescendants(nid, true)) {
              if (getNode(d)?.localName === "script") state.startedScripts.add(d);
            }
          }
        }
        return "true";
      }
      case "parse_html_document": {
        const root = parseInt(arg1, 10);
        const rn = getNode(root);
        if (!rn || root === 0 || rn.nodeType !== 9) return "null";
        parseHTMLInto(arg2, root, true);
        return "CSS1Compat";
      }
      case "append_child": {
        const p = parseInt(arg1, 10);
        const c = parseInt(arg2, 10);
        if (Number.isNaN(p) || Number.isNaN(c)) return "false";
        return appendChildNode(p, c) ? "true" : "false";
      }
      case "remove_child": {
        const c = parseInt(arg1, 10);
        if (Number.isNaN(c)) return "false";
        return detachNode(c) ? "true" : "false";
      }
      case "insert_before": {
        const n = parseInt(arg1, 10);
        const r = parseInt(arg2, 10);
        if (Number.isNaN(n) || Number.isNaN(r)) return "false";
        return insertBeforeNode(n, r) ? "true" : "false";
      }
      case "template_contents": {
        const n = getNode(parseInt(arg1, 10));
        if (!n) return "-1";
        if (n.templateContents < 0) n.templateContents = createNode(9, {});
        return String(n.templateContents);
      }
      case "create_document_fragment":
        return String(createNode(9, {}));
      case "clone_node": {
        const nid = parseInt(arg1, 10);
        if (Number.isNaN(nid)) return "-1";
        return String(cloneSubtree(nid, arg2 === "true"));
      }
      case "create_element":
        return String(createNode(1, { localName: arg1.toLowerCase(), ns: HTML_NS }));
      case "create_element_ns": {
        const nul = arg1.indexOf("\0");
        const ns = nul >= 0 ? arg1.slice(0, nul) : HTML_NS;
        const qn = nul >= 0 ? arg1.slice(nul + 1) : arg1;
        const colon = qn.indexOf(":");
        const prefix = colon > 0 ? qn.slice(0, colon) : null;
        const localName = colon > 0 ? qn.slice(colon + 1) : qn;
        return String(createNode(1, { localName, prefix, ns: ns || HTML_NS }));
      }
      case "create_text_node":
        return String(createNode(3, { text: arg1 }));
      case "create_comment_node":
        return String(createNode(8, { text: arg1 }));
      case "create_processing_instruction":
        return String(createNode(7, { piTarget: arg1, text: arg2 }));
      case "create_doctype":
        return String(createNode(10, { doctypeName: arg1, publicId: arg2 }));
      case "pi_target":
        return JSON.stringify(getNode(parseInt(arg1, 10))?.piTarget || "");
      case "doctype_name":
        return JSON.stringify(getNode(parseInt(arg1, 10))?.doctypeName || "");
      case "doctype_public_id":
        return JSON.stringify(getNode(parseInt(arg1, 10))?.publicId || "");
      case "contains": {
        const a = parseInt(arg1, 10) || 0;
        const b = parseInt(arg2, 10) || 0;
        return getDescendants(a, false).includes(b) ? "true" : "false";
      }
      case "is_inclusive_ancestor":
        return isInclusiveAncestor(parseInt(arg1, 10), parseInt(arg2, 10)) ? "true" : "false";
      case "is_host_including_inclusive_ancestor":
        return isHostIncludingInclusiveAncestor(parseInt(arg1, 10), parseInt(arg2, 10)) ? "true" : "false";
      case "sibling_nids": {
        const n = getNode(parseInt(arg1, 10));
        if (!n) return "-1,-1";
        return String(n.prevSibling) + "," + String(n.nextSibling);
      }
      case "is_connected":
        return isConnected(parseInt(arg1, 10)) ? "true" : "false";
      case "node_index":
        return String(nodeChildIndex(parseInt(arg1, 10) || 0));
      case "compare_order":
        return String(compareNodeOrder(parseInt(arg1, 10) || 0, parseInt(arg2, 10) || 0));
      case "node_root": {
        let cur = parseInt(arg1, 10) || 0;
        while (true) {
          const p = getNode(cur)?.parent ?? -1;
          if (p < 0) break;
          cur = p;
        }
        return String(cur);
      }
      case "document_write_reset":
        resetTree();
        return "true";
      case "document_write": {
        const bodyIds = querySelectorScoped(0, "body", true);
        const target = bodyIds.length > 0 ? bodyIds[0] : 0;
        const frag = createNode(9, {});
        parseHTMLInto(arg2, frag, false);
        const placements = [];
        for (const cid of getChildNids(frag)) {
          detachNode(cid);
          placements.push([target, cid, -1]);
        }
        return JSON.stringify(placements);
      }
      case "document_write_close":
        return "[]";
      default:
        return "null";
    }
  }

  // =========================================================================
  // WHATWG URL Parser & Resolver (`op_url_parse`, `op_url_set`, `op_url_resolve`)
  // =========================================================================

  const SPECIAL_DEFAULT_PORTS = { "http:": "80", "https:": "443", "ws:": "80", "wss:": "443", "ftp:": "21" };

  function normalizePath(path) {
    if (!path) return "/";
    const leadingSlash = path.startsWith("/");
    const trailingSlash = path.length > 1 && path.endsWith("/");
    const parts = path.split("/");
    const stack = [];
    for (let i = 0; i < parts.length; i++) {
      const seg = parts[i];
      if (!seg || seg === ".") continue;
      if (seg === "..") { stack.pop(); continue; }
      stack.push(seg);
    }
    let out = (leadingSlash ? "/" : "") + stack.join("/");
    if (trailingSlash && !out.endsWith("/")) out += "/";
    return out || "/";
  }

  function parseSingleUrl(raw) {
    const s = String(raw ?? "").trim();
    const m = /^([a-zA-Z][a-zA-Z0-9+.-]*:)(?:\/\/([^/?#]*))?([^?#]*)(?:\?([^#]*))?(?:#(.*))?$/.exec(s);
    if (!m) return null;
    const protocol = m[1].toLowerCase();
    const authority = m[2];
    let pathname = m[3] || "";
    const query = m[4];
    const hashPart = m[5];

    const isSpecial = Object.prototype.hasOwnProperty.call(SPECIAL_DEFAULT_PORTS, protocol);
    if (isSpecial && authority === undefined) return null;

    let username = "";
    let password = "";
    let hostname = "";
    let port = "";

    if (authority !== undefined) {
      let hostPort = authority;
      const atIdx = authority.lastIndexOf("@");
      if (atIdx !== -1) {
        const userInfo = authority.slice(0, atIdx);
        hostPort = authority.slice(atIdx + 1);
        const colonIdx = userInfo.indexOf(":");
        if (colonIdx !== -1) {
          username = userInfo.slice(0, colonIdx);
          password = userInfo.slice(colonIdx + 1);
        } else {
          username = userInfo;
        }
      }
      if (hostPort.startsWith("[")) {
        const closeIdx = hostPort.indexOf("]");
        if (closeIdx === -1) return null;
        hostname = hostPort.slice(0, closeIdx + 1).toLowerCase();
        const rest = hostPort.slice(closeIdx + 1);
        if (rest.startsWith(":")) port = rest.slice(1);
      } else {
        const colonIdx = hostPort.lastIndexOf(":");
        if (colonIdx !== -1) {
          hostname = hostPort.slice(0, colonIdx).toLowerCase();
          port = hostPort.slice(colonIdx + 1);
        } else {
          hostname = hostPort.toLowerCase();
        }
      }
      if (port && !/^\d+$/.test(port)) return null;
      if (SPECIAL_DEFAULT_PORTS[protocol] === port) port = "";
      if (isSpecial && !hostname) return null;
      pathname = normalizePath(pathname || "/");
    }

    const host = port ? (hostname + ":" + port) : hostname;
    const search = (query !== undefined && query !== "") ? ("?" + query) : "";
    const hash = (hashPart !== undefined && hashPart !== "") ? ("#" + hashPart) : "";
    const authPrefix = username ? (username + (password ? ":" + password : "") + "@") : "";
    const href = protocol + (authority !== undefined ? "//" + authPrefix + host : "") + pathname + search + hash;
    const origin = isSpecial && host ? (protocol + "//" + host) : "null";

    return {
      ok: true,
      href,
      protocol,
      username,
      password,
      host,
      hostname,
      port,
      pathname,
      search,
      hash,
      origin
    };
  }

  function resolveUrlComponents(href, base) {
    const raw = String(href ?? "").trim();
    if (!base) return parseSingleUrl(raw);
    const direct = parseSingleUrl(raw);
    if (direct) return direct;
    const b = parseSingleUrl(base);
    if (!b) return null;
    if (!raw) return b;
    if (raw.startsWith("//")) {
      return parseSingleUrl(b.protocol + raw);
    }
    if (raw.startsWith("?")) {
      const noSearchHash = b.href.split(/[?#]/)[0];
      return parseSingleUrl(noSearchHash + raw);
    }
    if (raw.startsWith("#")) {
      const noHash = b.href.split("#")[0];
      return parseSingleUrl(noHash + raw);
    }
    const baseOrigin = b.protocol + "//" + (b.username ? (b.username + (b.password ? ":" + b.password : "") + "@") : "") + b.host;
    if (raw.startsWith("/")) {
      return parseSingleUrl(baseOrigin + raw);
    }
    const lastSlash = b.pathname.lastIndexOf("/");
    const baseDir = lastSlash >= 0 ? b.pathname.slice(0, lastSlash + 1) : "/";
    return parseSingleUrl(baseOrigin + baseDir + raw);
  }

  function op_url_parse(href, base) {
    const res = resolveUrlComponents(href, base);
    return res ? JSON.stringify(res) : JSON.stringify({ ok: false });
  }

  function op_url_resolve(href, base) {
    const res = resolveUrlComponents(href, base);
    return res ? res.href : "";
  }

  function op_url_set(href, part, value) {
    const cur = parseSingleUrl(href);
    if (!cur) return JSON.stringify({ ok: false });
    const val = String(value ?? "");
    if (part === "href") {
      const next = parseSingleUrl(val);
      return JSON.stringify(next || cur);
    }
    if (part === "protocol") cur.protocol = val.endsWith(":") ? val.toLowerCase() : (val.toLowerCase() + ":");
    else if (part === "username") cur.username = val;
    else if (part === "password") cur.password = val;
    else if (part === "host") {
      const idx = val.lastIndexOf(":");
      if (idx > 0) { cur.hostname = val.slice(0, idx).toLowerCase(); cur.port = val.slice(idx + 1); }
      else { cur.hostname = val.toLowerCase(); cur.port = ""; }
    } else if (part === "hostname") cur.hostname = val.toLowerCase();
    else if (part === "port") cur.port = val.replace(/\D.*$/, "");
    else if (part === "pathname") cur.pathname = val.startsWith("/") ? normalizePath(val) : normalizePath("/" + val);
    else if (part === "search") cur.search = val ? (val.startsWith("?") ? val : "?" + val) : "";
    else if (part === "hash") cur.hash = val ? (val.startsWith("#") ? val : "#" + val) : "";

    cur.host = cur.port ? (cur.hostname + ":" + cur.port) : cur.hostname;
    const rebuilt = cur.protocol + "//" + (cur.username ? (cur.username + (cur.password ? ":" + cur.password : "") + "@") : "") + cur.host + cur.pathname + cur.search + cur.hash;
    return JSON.stringify(parseSingleUrl(rebuilt) || cur);
  }

  function storageOp(store, command, key, value) {
    if (command === "get") {
      const v = store.get(key);
      return JSON.stringify(v !== undefined ? v : null);
    }
    if (command === "set") {
      store.set(String(key), String(value));
      return "true";
    }
    if (command === "remove") {
      store.delete(String(key));
      return "null";
    }
    if (command === "clear") {
      store.clear();
      return "null";
    }
    if (command === "keys") {
      return JSON.stringify(Array.from(store.keys()));
    }
    return "null";
  }

  // Install `globalThis.Deno.core` expected by Obscura's `bootstrap.js` & `indexed_db.js`
  const ops = new Proxy({
    op_dom,
    op_url_parse,
    op_url_resolve,
    op_url_set,
    op_url_encode_query: (q) => String(q ?? ""),
    op_document_domain_candidate: (cur, input) => {
      const c = String(cur ?? "").toLowerCase();
      const inp = String(input ?? "").toLowerCase();
      if (c === inp || c.endsWith("." + inp)) return inp;
      return "";
    },
    op_get_cookies: () => state.cookieString,
    op_set_cookie: (cookieStr) => {
      const s = String(cookieStr ?? "").trim();
      if (!s) return;
      state.setCookieQueue.push(s);
      const semi = s.indexOf(";");
      const nv = semi >= 0 ? s.slice(0, semi).trim() : s;
      const eq = nv.indexOf("=");
      if (eq > 0) {
        const name = nv.slice(0, eq).trim();
        const val = nv.slice(eq + 1).trim();
        const map = new Map();
        if (state.cookieString) {
          for (const part of state.cookieString.split(";")) {
            const p = part.trim();
            const peq = p.indexOf("=");
            if (peq > 0) map.set(p.slice(0, peq).trim(), p.slice(peq + 1).trim());
          }
        }
        if (/max-age\s*=\s*0/i.test(s)) map.delete(name);
        else map.set(name, val);
        state.cookieString = Array.from(map.entries()).map(([k, v]) => k + "=" + v).join("; ");
      }
    },
    op_local_storage: (cmd, key, val) => storageOp(state.localStorage, cmd, key, val),
    op_session_storage: (cmd, key, val) => storageOp(state.sessionStorage, cmd, key, val),
    op_storage_access: () => state.origin !== "null",
    op_session_history: () => `${state.sessionHistory.start},${state.sessionHistory.current},${state.sessionHistory.urls.length}`,
    op_history_traverse: (index) => {
      const idx = Number(index);
      if (idx >= 0 && idx < state.sessionHistory.urls.length) {
        state.pendingHistoryIndex = idx;
        state.pendingNavigation = { url: state.sessionHistory.urls[idx], method: "GET", body: "" };
        return true;
      }
      return false;
    },
    op_navigate: (url, method, body) => {
      const resolved = op_url_resolve(url, state.url) || String(url);
      state.pendingNavigation = { url: resolved, method: String(method || "GET"), body: String(body || "") };
    },
    op_console_msg: (level, msg) => {
      if (state.consoleMessages.length < 1024) {
        state.consoleMessages.push(`[${level}] ${msg}`);
      }
    },
    op_runtime_events_enabled: () => false,
    op_async_runtime_available: () => false,
    op_posted_task_generation: () => 1,
    op_posted_task: () => 1,
    op_script_mark_started: (nid) => {
      state.startedScripts.add(Number(nid));
      return true;
    },
    op_script_try_start: (nid) => {
      const id = Number(nid);
      if (state.startedScripts.has(id)) return false;
      state.startedScripts.add(id);
      return true;
    },
    op_add_import_map: () => "",
    op_shadow_attach: (hostNid, mode) => {
      const host = getNode(Number(hostNid));
      if (!host || host.shadowRoot) return -1;
      const rootNid = createNode(9, {});
      getNode(rootNid).shadowHost = Number(hostNid);
      host.shadowRoot = { mode: mode === "closed" ? "closed" : "open", rootNid };
      return rootNid;
    },
    op_shadow_root_info: (hostNid) => {
      const host = getNode(Number(hostNid));
      if (!host || !host.shadowRoot) return "";
      return `${host.shadowRoot.rootNid}:${host.shadowRoot.mode}`;
    },
    op_external_stylesheet_set: (ownerNid, css, responseUrl, clean) => {
      state.externalStylesheets.set(Number(ownerNid), { css: String(css || ""), responseUrl: String(responseUrl || ""), originClean: !!clean });
      state.stylesheetGeneration++;
      return true;
    },
    op_external_stylesheet_get: (ownerNid) => {
      const sheet = state.externalStylesheets.get(Number(ownerNid));
      return sheet ? JSON.stringify(sheet) : "null";
    },
    op_external_stylesheet_remove: (ownerNid) => {
      state.externalStylesheets.delete(Number(ownerNid));
      state.stylesheetGeneration++;
      return true;
    },
    op_stylesheet_generation: () => state.stylesheetGeneration,
    op_cssom_stylesheet_has: (ownerNid) => state.cssomStylesheets.has(Number(ownerNid)),
    op_cssom_stylesheet_update: (ownerNid, _idx, _del, rules) => {
      state.cssomStylesheets.set(Number(ownerNid), rules || []);
      state.stylesheetGeneration++;
      return true;
    },
    op_cssom_stylesheet_clear: (ownerNid) => {
      state.cssomStylesheets.delete(Number(ownerNid));
      state.stylesheetGeneration++;
    },
    op_random_bytes: (len) => {
      const n = Math.min(Math.max(0, Number(len) || 0), 1024 * 1024);
      const out = new Uint8Array(n);
      for (let i = 0; i < n; i++) out[i] = Math.floor(Math.random() * 256);
      return out;
    },
    op_encoding_for_label: (label) => {
      const l = String(label ?? "").trim().toLowerCase();
      if (l === "utf-8" || l === "utf8" || l === "unicode-1-1-utf-8") return "utf-8";
      if (l === "ascii" || l === "us-ascii" || l === "iso-8859-1" || l === "latin1" || l === "windows-1252") return "windows-1252";
      return "utf-8";
    },
    op_text_decode: (_label, bytes) => {
      let s = "";
      const arr = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes || []);
      for (let i = 0; i < arr.length; i++) s += String.fromCharCode(arr[i]);
      return JSON.stringify({ ok: true, v: s });
    },
    op_register_document_realm: () => {},
    op_document_realm_member: () => undefined,
    op_binding_called: () => {},
    op_report_browser_exception: () => {},
    op_abort_signal_state: (sig, init) => {
      if (sig && typeof sig === "object") {
        if (init !== undefined) sig.__obscuraAbortState = init;
        return sig.__obscuraAbortState;
      }
      return undefined;
    },
    op_indexed_db: () => JSON.stringify({ ok: true }),
    op_indexed_db_task: () => 1,
    op_scroll_offset: () => JSON.stringify(state.scrollOffset),
    op_scroll_to: (x, y) => {
      state.scrollOffset = { x: Number(x) || 0, y: Number(y) || 0 };
      return JSON.stringify(state.scrollOffset);
    }
  }, {
    get(target, prop) {
      return prop in target ? target[prop] : () => null;
    }
  });

  globalThis.Deno = {
    core: {
      ops,
      createTimer(cb, delay) {
        const id = state.nextTimerId++;
        state.timers.set(id, { cb, delay: Number(delay) || 0 });
        return id;
      },
      cancelTimer(id) {
        state.timers.delete(Number(id));
      },
      setReportExceptionCallback() {},
      setUnhandledPromiseRejectionHandler() {},
      setHandledPromiseRejectionHandler() {},
      isProxy: () => false,
      isAnyArrayBuffer: (v) => v instanceof ArrayBuffer || (typeof SharedArrayBuffer !== "undefined" && v instanceof SharedArrayBuffer),
      isArrayBufferView: (v) => ArrayBuffer.isView(v),
      isDate: (v) => v instanceof Date,
      isRegExp: (v) => v instanceof RegExp,
      isBoxedPrimitive: (v) => v instanceof Number || v instanceof String || v instanceof Boolean,
      isPromise: (v) => v instanceof Promise,
      isWeakMap: (v) => v instanceof WeakMap,
      isWeakSet: (v) => v instanceof WeakSet,
      isModuleNamespaceObject: () => false,
      isMap: (v) => v instanceof Map,
      isSet: (v) => v instanceof Set,
      isNativeError: (v) => v instanceof Error,
      serialize: (v) => JSON.stringify(v),
      deserialize: (v) => typeof v === "string" ? JSON.parse(v) : v
    }
  };
})();
