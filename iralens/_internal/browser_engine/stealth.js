/*
 * IraLens stealth layer (page side).
 *
 * Runs in the page's JavaScript realm, twice:
 *
 *   1. `__iralens_stealth_install()` -- before the engine initializes the
 *      document. It tells the engine which browser identity to report, so
 *      the values the engine derives from it (user agent, platform, brand
 *      list) are the ones this profile wants.
 *
 *   2. `__iralens_stealth_apply()` -- right after initialization and before
 *      the page's own scripts run. It pins the values that would otherwise
 *      be re-randomized on every navigation (screen size, core count,
 *      memory, GPU, canvas, battery) and fixes the surfaces that report
 *      something a real Chrome would not report.
 *
 * Why "pin" matters: the upstream engine re-seeds its fingerprint on every
 * page load, so a client that visits three pages in a row reports three
 * different screens and three different GPUs. A real browser never does
 * that, and the inconsistency is far more suspicious than any single value.
 * The profile is stable for the whole session, so every page agrees.
 *
 * What this layer does NOT do: it does not defeat a CAPTCHA, does not
 * forge a challenge token, and does not fake a WebGL context. Faking WebGL
 * would push pages down a rendering path the engine cannot serve (the
 * upstream engine returns `null` from `getContext("webgl")` for exactly
 * that reason), so a page would render blank instead of falling back.
 */

(function () {
  "use strict";

  var PROFILE = globalThis.__iralens_stealth_profile || {};

  // The engine's own host entry points live on the global object. They are
  // not enumerable, but a script that calls `Object.getOwnPropertyNames`
  // still finds them -- and names like `__iralens_load_document` give the
  // game away instantly. The engine filters its internals out of the
  // reflection APIs using a list it keeps on the global, so adding these
  // names to that list keeps them out of sight.
  var ENGINE_INTERNALS = [
    "__iralens_load_document",
    "__iralens_mcp_call",
    "__iralens_host_state",
    "__iralens_stealth_install",
    "__iralens_stealth_apply",
    "__iralens_stealth_profile",
  ];

  // ----------------------------------------------------------------- helpers

  function hideGlobal(name) {
    // The engine filters its own internals out of `Object.getOwnPropertyNames`
    // using a list it keeps on the global. Adding our names keeps a
    // fingerprinting script from finding `__iralens_stealth_apply` on the
    // window object, which would be a dead giveaway.
    try {
      var list = globalThis.__obscura_hide_list;
      if (Array.isArray(list) && list.indexOf(name) === -1) list.push(name);
    } catch (e) {}
  }

  function hideEngineInternals() {
    for (var i = 0; i < ENGINE_INTERNALS.length; i++) hideGlobal(ENGINE_INTERNALS[i]);
  }

  var nativeFns = typeof WeakSet === "function" ? new WeakSet() : null;

  function markNative(fn, name) {
    if (typeof fn !== "function") return fn;
    try {
      if (nativeFns) nativeFns.add(fn);
      var label = name || fn.name || "";
      Object.defineProperty(fn, "toString", {
        value: function toString() {
          return "function " + label + "() { [native code] }";
        },
        writable: true, configurable: true, enumerable: false,
      });
    } catch (e) {}
    return fn;
  }

  // The engine already patched `Function.prototype.toString` so its own
  // internals look native. Chain onto it instead of replacing it, so the
  // engine's masking keeps working.
  var upstreamToString = Function.prototype.toString;
  function stealthToString() {
    if (nativeFns && nativeFns.has(this)) {
      return "function " + (this.name || "") + "() { [native code] }";
    }
    return upstreamToString.call(this);
  }
  markNative(stealthToString, "toString");
  try {
    Object.defineProperty(Function.prototype, "toString", {
      value: stealthToString, writable: true, configurable: true, enumerable: false,
    });
  } catch (e) {}

  function defineHidden(target, name, value) {
    try {
      Object.defineProperty(target, name, {
        value: value, writable: true, configurable: true, enumerable: false,
      });
      return true;
    } catch (e) {
      try { target[name] = value; return true; } catch (e2) { return false; }
    }
  }

  function defineGetter(target, name, getter) {
    try {
      Object.defineProperty(target, name, {
        get: markNative(getter, "get " + name),
        set: undefined, enumerable: true, configurable: true,
      });
      return true;
    } catch (e) { return false; }
  }

  function safe(fn) {
    try { fn(); } catch (e) {}
  }

  // ---------------------------------------------------------------- install

  function install() {
    // These globals are what the engine's own `navigator` accessors read.
    // Setting them before initialization is what makes the *engine* report
    // this profile, instead of the stealth layer fighting the engine.
    safe(function () { globalThis.__obscura_stealth = true; });
    if (PROFILE.userAgent) safe(function () { globalThis.__obscura_ua = PROFILE.userAgent; });
    if (PROFILE.platform) safe(function () { globalThis.__obscura_platform = PROFILE.platform; });
    if (PROFILE.uaPlatform) safe(function () { globalThis.__obscura_ua_platform = PROFILE.uaPlatform; });
    if (PROFILE.platformVersion !== undefined) {
      safe(function () { globalThis.__obscura_ua_platform_version = PROFILE.platformVersion; });
    }
    if (PROFILE.language) safe(function () { globalThis.__obscura_language = PROFILE.language; });
  }

  // ------------------------------------------------------------------ apply

  function pinScreen() {
    var screenObj = globalThis.screen;
    if (!screenObj) return;
    // Own accessors shadow the prototype getters, so every view of the
    // screen (including the one an iframe sees) reports the same numbers.
    defineGetter(screenObj, "width", function () { return PROFILE.screenWidth; });
    defineGetter(screenObj, "height", function () { return PROFILE.screenHeight; });
    defineGetter(screenObj, "availWidth", function () { return PROFILE.availWidth; });
    defineGetter(screenObj, "availHeight", function () { return PROFILE.availHeight; });
    try {
      Object.defineProperty(screenObj, "colorDepth", {
        value: PROFILE.colorDepth, writable: false, configurable: true, enumerable: true,
      });
      Object.defineProperty(screenObj, "pixelDepth", {
        value: PROFILE.colorDepth, writable: false, configurable: true, enumerable: true,
      });
    } catch (e) {}
  }

  function pinViewport() {
    try {
      globalThis.innerWidth = PROFILE.innerWidth;
      globalThis.innerHeight = PROFILE.innerHeight;
      globalThis.outerWidth = PROFILE.outerWidth;
      globalThis.outerHeight = PROFILE.outerHeight;
      globalThis.devicePixelRatio = PROFILE.devicePixelRatio;
    } catch (e) {}
    try {
      if (globalThis.visualViewport) {
        Object.defineProperty(globalThis.visualViewport, "width", {
          value: PROFILE.innerWidth, configurable: true, enumerable: true,
        });
        Object.defineProperty(globalThis.visualViewport, "height", {
          value: PROFILE.innerHeight, configurable: true, enumerable: true,
        });
      }
    } catch (e) {}
  }

  function pinBrandList() {
    var nav = globalThis.navigator;
    if (!nav || !nav.userAgentData) return;
    var brands = PROFILE.brands || [];
    var uad = nav.userAgentData;
    try {
      Object.defineProperty(uad, "brands", {
        get: markNative(function () { return brands.map(function (b) { return { brand: b.brand, version: b.version }; }); }, "get brands"),
        set: undefined, enumerable: true, configurable: true,
      });
      Object.defineProperty(uad, "platform", {
        get: markNative(function () { return PROFILE.uaPlatform; }, "get platform"),
        set: undefined, enumerable: true, configurable: true,
      });
      Object.defineProperty(uad, "platformVersion", {
        get: markNative(function () { return PROFILE.platformVersion; }, "get platformVersion"),
        set: undefined, enumerable: true, configurable: true,
      });
      var fullVersionList = brands.map(function (b) {
        return { brand: b.brand, version: b.version + ".0.0.0" };
      });
      uad.getHighEntropyValues = markNative(function getHighEntropyValues(hints) {
        var wanted = hints || {};
        var out = {};
        var known = {
          architecture: PROFILE.architecture,
          bitness: PROFILE.bitness,
          model: PROFILE.model,
          platform: PROFILE.uaPlatform,
          platformVersion: PROFILE.platformVersion,
          uaFullVersion: (PROFILE.uaFullVersion || "145.0.0.0"),
          wow64: !!PROFILE.wow64,
          mobile: false,
          brands: brands.map(function (b) { return { brand: b.brand, version: b.version }; }),
          fullVersionList: fullVersionList,
        };
        Object.keys(known).forEach(function (key) {
          if (wanted[key]) out[key] = known[key];
        });
        if (!Object.keys(out).length) {
          Object.keys(known).forEach(function (key) { out[key] = known[key]; });
        }
        return Promise.resolve(out);
      }, "getHighEntropyValues");
    } catch (e) {}
  }

  function pinConnection() {
    var nav = globalThis.navigator;
    if (!nav || !nav.connection) return;
    var conn = nav.connection;
    defineGetter(conn, "effectiveType", function () { return PROFILE.effectiveType; });
    defineGetter(conn, "rtt", function () { return PROFILE.rtt; });
    defineGetter(conn, "downlink", function () { return PROFILE.downlink; });
    defineGetter(conn, "saveData", function () { return false; });
  }

  function pinBattery() {
    var nav = globalThis.navigator;
    if (!nav || typeof nav.getBattery !== "function") return;
    // The engine re-randomizes the battery level on every navigation. A
    // browser whose battery level changes on every page load is not a
    // browser.
    nav.getBattery = markNative(function getBattery() {
      return Promise.resolve({
        charging: true, chargingTime: 0, dischargingTime: Infinity,
        level: 0.97, addEventListener: function () {}, removeEventListener: function () {},
      });
    }, "getBattery");
  }

  function pinStorage() {
    var nav = globalThis.navigator;
    if (!nav || !nav.storage) return;
    nav.storage.estimate = markNative(function estimate() {
      return Promise.resolve({ quota: 299977904946, usage: 4123456 });
    }, "estimate");
    nav.storage.persisted = markNative(function persisted() {
      return Promise.resolve(false);
    }, "persisted");
  }

  function pinGeolocation() {
    var nav = globalThis.navigator;
    if (!nav || !nav.geolocation) return;
    // A real Chrome without location permission calls the *error* callback
    // with PERMISSION_DENIED. The engine answered with coordinates instead,
    // which is the kind of thing only a headless client does.
    function denied(callback) {
      if (typeof callback !== "function") return;
      var error = {
        code: 1, message: "User denied Geolocation", PERMISSION_DENIED: 1,
        POSITION_UNAVAILABLE: 2, TIMEOUT: 3,
      };
      try { callback(error); } catch (e) {}
    }
    nav.geolocation.getCurrentPosition = markNative(function getCurrentPosition(success, error) {
      denied(error);
    }, "getCurrentPosition");
    nav.geolocation.watchPosition = markNative(function watchPosition(success, error) {
      denied(error);
      return 0;
    }, "watchPosition");
  }

  function pinAudio() {
    var Original = globalThis.AudioContext || globalThis.webkitAudioContext;
    if (typeof Original !== "function") return;
    function StealthAudioContext() {
      var ctx = new (Function.prototype.bind.apply(Original, [null].concat(Array.prototype.slice.call(arguments))))();
      try {
        // The engine re-seeds these per page load, so a browser whose
        // sample rate changes between two pages is not a browser.
        if (PROFILE.audioBaseLatency !== undefined) ctx.baseLatency = PROFILE.audioBaseLatency;
        if (PROFILE.audioSampleRate !== undefined) ctx.sampleRate = PROFILE.audioSampleRate;
      } catch (e) {}
      return ctx;
    }
    StealthAudioContext.prototype = Original.prototype;
    markNative(StealthAudioContext, "AudioContext");
    try {
      if (globalThis.AudioContext) globalThis.AudioContext = StealthAudioContext;
      else globalThis.webkitAudioContext = StealthAudioContext;
    } catch (e) {}
    // The compressor's parameters are seeded the same way.
    try {
      if (Original.prototype && Original.prototype.createDynamicsCompressor) {
        var originalCompressor = Original.prototype.createDynamicsCompressor;
        Original.prototype.createDynamicsCompressor = markNative(function createDynamicsCompressor() {
          var node = originalCompressor.apply(this, arguments);
          try {
            node.threshold.value = -24;
            node.knee.value = 30;
            node.ratio.value = 12;
          } catch (e) {}
          return node;
        }, "createDynamicsCompressor");
      }
    } catch (e) {}
  }

  function pinMemory() {
    try {
      var perf = globalThis.performance;
      if (!perf || !perf.memory) return;
      perf.memory.usedJSHeapSize = 41234567;
      perf.memory.totalJSHeapSize = 61234567;
      perf.memory.jsHeapSizeLimit = 4294705152;
    } catch (e) {}
  }

  function pinWebGL() {
    // The engine has no WebGL backend and returns `null` from
    // `getContext("webgl")` on purpose. If a context ever exists, it must
    // report this profile's GPU rather than a random one.
    var contexts = [globalThis.WebGLRenderingContext, globalThis.WebGL2RenderingContext];
    var UNMASKED_VENDOR = 37445;
    var UNMASKED_RENDERER = 37446;
    contexts.forEach(function (Ctx) {
      if (!Ctx || !Ctx.prototype) return;
      var original = Ctx.prototype.getParameter;
      if (typeof original !== "function") return;
      Ctx.prototype.getParameter = markNative(function getParameter(parameter) {
        if (parameter === UNMASKED_VENDOR) return PROFILE.webglVendor;
        if (parameter === UNMASKED_RENDERER) return PROFILE.webglRenderer;
        try { return original.call(this, parameter); } catch (e) { return null; }
      }, "getParameter");
    });
  }

  // --------------------------------------------------------------- canvas
  //
  // The engine's 2D canvas is backed by a host operation that only exists in
  // the Rust tier, so `getContext("2d")` returned `null` here. A page that
  // probes the canvas therefore learns that it is not talking to a browser.
  //
  // The fallback below implements the part of the 2D API that fingerprinting
  // and layout code actually touches: it rasterizes rectangles and image
  // data into a real pixel buffer, and encodes that buffer as a PNG. Glyph
  // rendering is *not* implemented -- `fillText` draws nothing -- so a page
  // that renders text into a canvas gets an empty area. That limitation is
  // deliberate and documented: a wrong-but-present canvas beats a missing
  // one for the purpose this layer exists for, and faking glyphs would be a
  // bigger lie than an empty rectangle.

  function crc32(bytes) {
    var table = crc32.table;
    if (!table) {
      table = new Int32Array(256);
      for (var n = 0; n < 256; n++) {
        var c = n;
        for (var k = 0; k < 8; k++) c = (c & 1) ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1);
        table[n] = c;
      }
      crc32.table = table;
    }
    var crc = -1;
    for (var i = 0; i < bytes.length; i++) {
      crc = (crc >>> 8) ^ table[(crc ^ bytes[i]) & 0xff];
    }
    return (crc ^ -1) >>> 0;
  }

  function adler32(bytes) {
    var a = 1, b = 0;
    for (var i = 0; i < bytes.length; i++) {
      a = (a + bytes[i]) % 65521;
      b = (b + a) % 65521;
    }
    return ((b << 16) | a) >>> 0;
  }

  function pngChunk(type, data) {
    var out = [];
    var length = data.length;
    out.push((length >>> 24) & 0xff, (length >>> 16) & 0xff, (length >>> 8) & 0xff, length & 0xff);
    var typeBytes = [];
    for (var i = 0; i < 4; i++) typeBytes.push(type.charCodeAt(i));
    var payload = typeBytes.concat(Array.prototype.slice.call(data));
    out = out.concat(payload);
    var crc = crc32(new Uint8Array(payload));
    out.push((crc >>> 24) & 0xff, (crc >>> 16) & 0xff, (crc >>> 8) & 0xff, crc & 0xff);
    return out;
  }

  function encodePNG(width, height, rgba) {
    // Raw scanlines (filter byte 0) wrapped in a zlib stream of stored
    // (uncompressed) deflate blocks: no compressor is available here and a
    // canvas fingerprint does not care about the compression ratio.
    var raw = new Uint8Array((width * 4 + 1) * height);
    var pos = 0;
    for (var y = 0; y < height; y++) {
      raw[pos++] = 0;
      for (var x = 0; x < width * 4; x++) raw[pos++] = rgba[y * width * 4 + x];
    }
    var blocks = [];
    var offset = 0;
    var maxBlock = 65535;
    if (raw.length === 0) {
      blocks.push(0x01, 0x00, 0x00, 0xff, 0xff);
    } else {
      while (offset < raw.length) {
        var size = Math.min(maxBlock, raw.length - offset);
        var last = (offset + size >= raw.length) ? 1 : 0;
        blocks.push(last, size & 0xff, (size >>> 8) & 0xff, (~size) & 0xff, ((~size) >>> 8) & 0xff);
        for (var i = 0; i < size; i++) blocks.push(raw[offset + i]);
        offset += size;
      }
    }
    var adler = adler32(raw);
    var zlib = [0x78, 0x01].concat(blocks);
    zlib.push((adler >>> 24) & 0xff, (adler >>> 16) & 0xff, (adler >>> 8) & 0xff, adler & 0xff);

    var ihdr = new Uint8Array([
      (width >>> 24) & 0xff, (width >>> 16) & 0xff, (width >>> 8) & 0xff, width & 0xff,
      (height >>> 24) & 0xff, (height >>> 16) & 0xff, (height >>> 8) & 0xff, height & 0xff,
      8, 6, 0, 0, 0,
    ]);
    var bytes = pngChunk("IHDR", ihdr)
      .concat(pngChunk("IDAT", new Uint8Array(zlib)))
      .concat(pngChunk("IEND", new Uint8Array(0)));
    return "data:image/png;base64," + base64(new Uint8Array(bytes));
  }

  function base64(bytes) {
    var chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    var out = "";
    for (var i = 0; i < bytes.length; i += 3) {
      var b0 = bytes[i], b1 = bytes[i + 1], b2 = bytes[i + 2];
      out += chars[b0 >> 2];
      out += chars[((b0 & 3) << 4) | ((b1 === undefined ? 0 : b1) >> 4)];
      out += b1 === undefined ? "=" : chars[((b1 & 15) << 2) | ((b2 === undefined ? 0 : b2) >> 6)];
      out += b2 === undefined ? "=" : chars[b2 & 63];
    }
    return out;
  }

  function applyCanvasNoise(rgba, seed) {
    // Real browsers add a per-installation noise to canvas readouts, so
    // two different machines never produce the same hash. Without this,
    // every IraLens client would share one canvas fingerprint -- a fleet
    // with an identical hash is a stronger signal than any single value.
    if (!rgba || !rgba.length) return rgba;
    var state = (seed | 0) || 1;
    for (var i = 0; i < rgba.length; i += 4) {
      state = (state * 1103515245 + 12345) & 0x7fffffff;
      if (state % 337 === 0) {
        rgba[i] = (rgba[i] + 1) & 0xff;
      }
    }
    return rgba;
  }

  function Fallback2D(canvas) {
    this.canvas = canvas;
    this.fillStyle = "#000000";
    this.strokeStyle = "#000000";
    this.lineWidth = 1;
    this.font = "10px sans-serif";
    this.textAlign = "start";
    this.textBaseline = "alphabetic";
    this.globalAlpha = 1;
    this.globalCompositeOperation = "source-over";
    this.shadowBlur = 0;
    this.shadowColor = "rgba(0, 0, 0, 0)";
    this.shadowOffsetX = 0;
    this.shadowOffsetY = 0;
    this.lineCap = "butt";
    this.lineJoin = "miter";
    this.miterLimit = 10;
    this._stack = [];
    this._resize();
  }

  Fallback2D.prototype._resize = function () {
    var w = parseInt(this.canvas.getAttribute("width") || "300", 10);
    var h = parseInt(this.canvas.getAttribute("height") || "150", 10);
    if (!isFinite(w) || w <= 0) w = 300;
    if (!isFinite(h) || h <= 0) h = 150;
    w = Math.min(w, 4096);
    h = Math.min(h, 4096);
    this._w = w;
    this._h = h;
    this._buf = new Uint8ClampedArray(w * h * 4);
  };

  Fallback2D.prototype._blend = function (x, y, r, g, b, a) {
    if (x < 0 || y < 0 || x >= this._w || y >= this._h) return;
    var idx = (y * this._w + x) * 4;
    var buf = this._buf;
    var alpha = a / 255;
    if (alpha >= 1) {
      buf[idx] = r; buf[idx + 1] = g; buf[idx + 2] = b; buf[idx + 3] = a;
      return;
    }
    buf[idx] = buf[idx] * (1 - alpha) + r * alpha;
    buf[idx + 1] = buf[idx + 1] * (1 - alpha) + g * alpha;
    buf[idx + 2] = buf[idx + 2] * (1 - alpha) + b * alpha;
    buf[idx + 3] = Math.max(buf[idx + 3], a);
  };

  Fallback2D.prototype.fillRect = function (x, y, w, h) {
    var color = parseColor(this.fillStyle) || [0, 0, 0, 255];
    var x0 = Math.round(x), y0 = Math.round(y);
    var x1 = Math.round(x + w), y1 = Math.round(y + h);
    for (var py = y0; py < y1; py++) {
      for (var px = x0; px < x1; px++) {
        this._blend(px, py, color[0], color[1], color[2], color[3]);
      }
    }
  };

  Fallback2D.prototype.strokeRect = function (x, y, w, h) {
    this.fillRect(x, y, w, 1);
    this.fillRect(x, y + h - 1, w, 1);
    this.fillRect(x, y, 1, h);
    this.fillRect(x + w - 1, y, 1, h);
  };

  Fallback2D.prototype.clearRect = function (x, y, w, h) {
    var x0 = Math.round(x), y0 = Math.round(y);
    var x1 = Math.round(x + w), y1 = Math.round(y + h);
    for (var py = y0; py < y1; py++) {
      for (var px = x0; px < x1; px++) {
        if (px < 0 || py < 0 || px >= this._w || py >= this._h) continue;
        var idx = (py * this._w + px) * 4;
        this._buf[idx] = 0; this._buf[idx + 1] = 0; this._buf[idx + 2] = 0; this._buf[idx + 3] = 0;
      }
    }
  };

  Fallback2D.prototype.fillText = function () { /* glyphs are not rendered */ };
  Fallback2D.prototype.strokeText = function () {};

  Fallback2D.prototype.measureText = function (text) {
    var size = parseFloat(String(this.font || "10px sans-serif").match(/(\d+(?:\.\d+)?)px/)?.[1] || "10");
    var width = (String(text || "").length) * size * 0.5;
    return {
      width: width,
      actualBoundingBoxLeft: 0,
      actualBoundingBoxRight: width,
      actualBoundingBoxAscent: size * 0.8,
      actualBoundingBoxDescent: size * 0.2,
      fontBoundingBoxAscent: size * 0.8,
      fontBoundingBoxDescent: size * 0.2,
    };
  };

  Fallback2D.prototype.drawImage = function () {};
  Fallback2D.prototype.putImageData = function (data, x, y) {
    if (!data || !data.data) return;
    var source = data.data;
    var width = data.width || source.length / 4;
    for (var row = 0; row < data.height; row++) {
      for (var col = 0; col < width; col++) {
        var src = (row * width + col) * 4;
        var dstX = Math.round(x) + col;
        var dstY = Math.round(y) + row;
        if (dstX < 0 || dstY < 0 || dstX >= this._w || dstY >= this._h) continue;
        var dst = (dstY * this._w + dstX) * 4;
        this._buf[dst] = source[src];
        this._buf[dst + 1] = source[src + 1];
        this._buf[dst + 2] = source[src + 2];
        this._buf[dst + 3] = source[src + 3];
      }
    }
  };

  Fallback2D.prototype.getImageData = function (x, y, w, h) {
    var width = Math.max(0, Math.round(w));
    var height = Math.max(0, Math.round(h));
    var out = new Uint8ClampedArray(width * height * 4);
    for (var row = 0; row < height; row++) {
      for (var col = 0; col < width; col++) {
        var sx = Math.round(x) + col, sy = Math.round(y) + row;
        var dst = (row * width + col) * 4;
        if (sx < 0 || sy < 0 || sx >= this._w || sy >= this._h) continue;
        var src = (sy * this._w + sx) * 4;
        out[dst] = this._buf[src];
        out[dst + 1] = this._buf[src + 1];
        out[dst + 2] = this._buf[src + 2];
        out[dst + 3] = this._buf[src + 3];
      }
    }
    applyCanvasNoise(out, PROFILE.canvasSeed);
    return { data: out, width: width, height: height };
  };

  Fallback2D.prototype.createImageData = function (w, h) {
    var width = Math.max(0, Math.round(w || 0));
    var height = Math.max(0, Math.round(h || 0));
    return { data: new Uint8ClampedArray(width * height * 4), width: width, height: height };
  };

  Fallback2D.prototype.getContextAttributes = function () {
    return { alpha: true, desynchronized: false, colorSpace: "srgb", willReadFrequently: false };
  };

  Fallback2D.prototype.save = function () {
    this._stack.push({
      fillStyle: this.fillStyle, strokeStyle: this.strokeStyle, lineWidth: this.lineWidth,
      font: this.font, textAlign: this.textAlign, textBaseline: this.textBaseline,
      globalAlpha: this.globalAlpha, globalCompositeOperation: this.globalCompositeOperation,
    });
  };
  Fallback2D.prototype.restore = function () {
    var state = this._stack.pop();
    if (!state) return;
    var self = this;
    Object.keys(state).forEach(function (key) { self[key] = state[key]; });
  };
  Fallback2D.prototype.translate = function () {};
  Fallback2D.prototype.scale = function () {};
  Fallback2D.prototype.rotate = function () {};
  Fallback2D.prototype.transform = function () {};
  Fallback2D.prototype.setTransform = function () {};
  Fallback2D.prototype.resetTransform = function () {};
  Fallback2D.prototype.beginPath = function () {};
  Fallback2D.prototype.closePath = function () {};
  Fallback2D.prototype.moveTo = function () {};
  Fallback2D.prototype.lineTo = function () {};
  Fallback2D.prototype.bezierCurveTo = function () {};
  Fallback2D.prototype.quadraticCurveTo = function () {};
  Fallback2D.prototype.arc = function () {};
  Fallback2D.prototype.arcTo = function () {};
  Fallback2D.prototype.ellipse = function () {};
  Fallback2D.prototype.rect = function () {};
  Fallback2D.prototype.fill = function () {};
  Fallback2D.prototype.stroke = function () {};
  Fallback2D.prototype.clip = function () {};
  Fallback2D.prototype.isPointInPath = function () { return false; };
  Fallback2D.prototype.isPointInStroke = function () { return false; };
  Fallback2D.prototype.setLineDash = function () {};
  Fallback2D.prototype.getLineDash = function () { return []; };

  function parseColor(value) {
    if (typeof value !== "string") return null;
    var text = value.trim().toLowerCase();
    if (text === "black") return [0, 0, 0, 255];
    if (text === "white") return [255, 255, 255, 255];
    if (text === "red") return [255, 0, 0, 255];
    if (text === "green") return [0, 128, 0, 255];
    if (text === "blue") return [0, 0, 255, 255];
    if (text === "transparent") return [0, 0, 0, 0];
    var hex = text.match(/^#([0-9a-f]{3,8})$/);
    if (hex) {
      var digits = hex[1];
      if (digits.length === 3) {
        return [
          parseInt(digits[0] + digits[0], 16),
          parseInt(digits[1] + digits[1], 16),
          parseInt(digits[2] + digits[2], 16),
          255,
        ];
      }
      if (digits.length === 6) {
        return [
          parseInt(digits.slice(0, 2), 16),
          parseInt(digits.slice(2, 4), 16),
          parseInt(digits.slice(4, 6), 16),
          255,
        ];
      }
      if (digits.length === 8) {
        return [
          parseInt(digits.slice(0, 2), 16),
          parseInt(digits.slice(2, 4), 16),
          parseInt(digits.slice(4, 6), 16),
          parseInt(digits.slice(6, 8), 16),
        ];
      }
    }
    var rgb = text.match(/^rgba?\(([^)]+)\)$/);
    if (rgb) {
      var parts = rgb[1].split(",").map(function (p) { return parseFloat(p); });
      return [
        parts[0] | 0, parts[1] | 0, parts[2] | 0,
        parts.length > 3 ? Math.round(parts[3] * 255) : 255,
      ];
    }
    return null;
  }

  function installCanvasFallback() {
    if (PROFILE.canvasFallback === false) return;
    var proto = globalThis.HTMLCanvasElement && globalThis.HTMLCanvasElement.prototype;
    if (!proto || typeof proto.getContext !== "function") return;
    var originalGetContext = proto.getContext;
    var originalToDataURL = proto.toDataURL;

    proto.getContext = markNative(function getContext(type) {
      var ctx = null;
      try { ctx = originalGetContext.call(this, type); } catch (e) { ctx = null; }
      if (ctx || !type || String(type).toLowerCase() !== "2d") return ctx;
      var impl = new Fallback2D(this);
      try { this._ctx = impl; } catch (e) {}
      return impl;
    }, "getContext");

    proto.toDataURL = markNative(function toDataURL(type, quality) {
      var ctx = this._ctx;
      if (ctx instanceof Fallback2D) {
        var copy = new Uint8ClampedArray(ctx._buf);
        applyCanvasNoise(copy, PROFILE.canvasSeed);
        return encodePNG(ctx._w, ctx._h, copy);
      }
      try { return originalToDataURL.call(this, type, quality); } catch (e) { return "data:,"; }
    }, "toDataURL");
  }

  function apply() {
    // The engine re-seeds these on every initialization, so they are set
    // *after* initialization, not before it.
    safe(function () {
      if (PROFILE.hardwareConcurrency) globalThis.__obscura_hw = PROFILE.hardwareConcurrency;
      if (PROFILE.deviceMemory) globalThis.__obscura_mem = PROFILE.deviceMemory;
    });
    safe(pinScreen);
    safe(pinViewport);
    safe(pinBrandList);
    safe(pinConnection);
    safe(pinBattery);
    safe(pinStorage);
    safe(pinGeolocation);
    safe(pinAudio);
    safe(pinMemory);
    safe(pinWebGL);
    safe(installCanvasFallback);
  }

  defineHidden(globalThis, "__iralens_stealth_install", install);
  defineHidden(globalThis, "__iralens_stealth_apply", apply);
  hideEngineInternals();
})();
