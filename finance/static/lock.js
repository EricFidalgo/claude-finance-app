// Lock screen for the app lock (lock.py). Load it before the app's own script:
//   <script src="/lock.js" data-name="Money" data-setup="./run.sh password"></script>
// It wraps fetch: an API answer of 401 covers the page with the lock screen, and a GET is retried
// after the unlock, so the app's own code needs no changes. Kept identical in the Money app and the
// Docker panel.
(() => {
  const me = document.currentScript;
  const NAME = me?.dataset.name || "App";
  const SETUP = me?.dataset.setup || "";
  const AWAY_MS = 5 * 60 * 1000;
  const realFetch = window.fetch.bind(window);
  const isApi = (u) => { try { const p = new URL(u, location.href).pathname; return p.startsWith("/api/") && p !== "/api/auth" && !p.startsWith("/api/auth/"); } catch { return false; } };

  const b64d = (s) => { s = s.replace(/-/g, "+").replace(/_/g, "/"); s += "=".repeat((4 - (s.length % 4)) % 4); return Uint8Array.from(atob(s), (c) => c.charCodeAt(0)); };
  const b64e = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  const post = async (path, body) => {
    const r = await realFetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-Lock": "1" }, body: JSON.stringify(body || {}) });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw Object.assign(new Error(data.error || `Error ${r.status}`), { status: r.status, retryAfter: data.retry_after });
    return data;
  };
  const state = () => realFetch("/api/auth", { cache: "no-store" }).then((r) => r.json());
  const skipKey = "lock-skip-faceid";
  const skipped = () => { try { return localStorage.getItem(skipKey) === "1"; } catch { return false; } };
  const skip = () => { try { localStorage.setItem(skipKey, "1"); } catch {} };

  async function faceIdUnlock() {
    const o = await post("/api/auth/passkey/options", { kind: "login" });
    const c = await navigator.credentials.get({ publicKey: {
      challenge: b64d(o.challenge), rpId: o.rpId, userVerification: o.userVerification, timeout: o.timeout,
      allowCredentials: o.allowCredentials.map((x) => ({ type: x.type, id: b64d(x.id) })) } });
    await post("/api/auth/passkey/login", { credential: {
      id: c.id, clientDataJSON: b64e(c.response.clientDataJSON),
      authenticatorData: b64e(c.response.authenticatorData), signature: b64e(c.response.signature) } });
  }

  async function faceIdTurnOn() {
    const o = await post("/api/auth/passkey/options", { kind: "register" });
    const c = await navigator.credentials.create({ publicKey: {
      ...o, challenge: b64d(o.challenge), user: { ...o.user, id: b64d(o.user.id) },
      excludeCredentials: o.excludeCredentials.map((x) => ({ type: x.type, id: b64d(x.id) })) } });
    await post("/api/auth/passkey/register", { credential: {
      id: c.id, clientDataJSON: b64e(c.response.clientDataJSON),
      authenticatorData: b64e(c.response.getAuthenticatorData()), publicKey: b64e(c.response.getPublicKey()) } });
  }

  // ---- the screen (styles set through the DOM, which the Money app's CSP allows) ----------------
  const dark = matchMedia("(prefers-color-scheme: dark)");
  const C = () => dark.matches ? { bg: "#000", fg: "#f2f2f7", sub: "#98989f", field: "#1c1c1e", line: "#38383a", accent: "#0a84ff", bad: "#ff453a" }
                               : { bg: "#f2f2f7", fg: "#000", sub: "#6c6c70", field: "#fff", line: "#d1d1d6", accent: "#007aff", bad: "#d70015" };
  const el = (tag, style, text) => { const e = document.createElement(tag); Object.assign(e.style, style || {}); if (text != null) e.textContent = text; return e; };
  const button = (text, primary) => { const c = C(); return el("button", { width: "100%", padding: "14px", fontSize: "17px", fontWeight: "600", borderRadius: "12px",
    border: primary ? "none" : `1px solid ${c.line}`, background: primary ? c.accent : "transparent", color: primary ? "#fff" : c.accent, cursor: "pointer", fontFamily: "inherit" }, text); };

  let screen = null, shown = null, waiting = null, release = null, asking = false, attempt = 0;
  const showing = (info) => `${info.password_set}/${info.passkeys}`;

  function render(info) {
    const c = C();
    screen?.remove();
    shown = showing(info);
    screen = el("div", { position: "fixed", inset: "0", zIndex: "2147483647", background: c.bg, color: c.fg, display: "flex", alignItems: "center", justifyContent: "center",
      padding: "max(16px, env(safe-area-inset-top)) 16px max(16px, env(safe-area-inset-bottom))", boxSizing: "border-box",
      fontFamily: "-apple-system, BlinkMacSystemFont, system-ui, sans-serif" });
    screen.setAttribute("role", "dialog"); screen.setAttribute("aria-modal", "true"); screen.setAttribute("aria-label", `${NAME} is locked`);
    const box = el("div", { width: "100%", maxWidth: "340px", display: "flex", flexDirection: "column", gap: "12px" });
    box.append(el("div", { fontSize: "28px", fontWeight: "700", textAlign: "center" }, `${NAME} is locked`));
    const msg = el("div", { fontSize: "15px", color: c.sub, textAlign: "center", minHeight: "20px" });
    msg.setAttribute("aria-live", "polite");
    const say = (text, bad) => { msg.textContent = text; msg.style.color = bad ? c.bad : c.sub; };
    screen.append(box);
    document.body.append(screen);

    if (!info.password_set) {
      say(SETUP ? `Set a password first. In Termius, run: ${SETUP}` : "Set a password first, from the terminal.");
      box.append(msg);
      const again = button("I've set it", true);
      again.onclick = () => check();
      box.append(again);
      return;
    }

    const done = async () => {
      const s = await state();
      if (!s.passkeys && window.PublicKeyCredential && !skipped()) return offerFaceId();
      finish();
    };

    if (info.passkeys && window.PublicKeyCredential) {
      const fid = button("Unlock with Face ID", true);
      // A second Face ID request cancels the first on iPhone, so only the newest one reports back.
      const tryFaceId = async () => {
        const mine = ++attempt;
        asking = true;
        try { say(""); await faceIdUnlock(); finish(); }
        catch (e) { if (mine === attempt) say(e.name === "NotAllowedError" ? "Face ID was cancelled. Try again or use the password." : e.message, true); }
        finally { if (mine === attempt) asking = false; }
      };
      fid.onclick = tryFaceId;  // a tap always goes ahead
      box.append(fid);
      // Try once straight away, unless a request is already open; iOS may want a tap, and then the button is there.
      setTimeout(() => { if (!asking) tryFaceId(); }, 50);
    }
    const form = el("form", { display: "flex", flexDirection: "column", gap: "12px", margin: "0" });
    const pw = el("input", { padding: "14px", fontSize: "17px", borderRadius: "12px", border: `1px solid ${c.line}`, background: c.field, color: c.fg, fontFamily: "inherit", width: "100%", boxSizing: "border-box" });
    Object.assign(pw, { type: "password", inputMode: "numeric", autocomplete: "current-password", placeholder: "Password (numbers)", name: "password" });
    pw.setAttribute("aria-label", "Password");
    const go = button("Unlock with password", !info.passkeys);
    go.type = "submit";
    form.append(pw, go);
    form.onsubmit = async (ev) => {
      ev.preventDefault();
      go.disabled = true; say("");
      try { await post("/api/auth/password", { password: pw.value }); pw.value = ""; await done(); }
      catch (e) { say(e.retryAfter ? `Too many wrong passwords. Try again in ${e.retryAfter} seconds.` : e.message, true); go.disabled = false; pw.select(); }
    };
    box.append(form, msg);
    if (!info.passkeys) pw.focus();
  }

  function offerFaceId() {
    const c = C();
    const box = screen.firstChild;
    shown = "offer";
    box.replaceChildren(el("div", { fontSize: "28px", fontWeight: "700", textAlign: "center" }, "Use Face ID next time?"),
      el("div", { fontSize: "15px", color: c.sub, textAlign: "center" }, "Unlock with a glance instead of typing the password."));
    const msg = el("div", { fontSize: "15px", color: c.bad, textAlign: "center", minHeight: "20px" });
    const yes = button("Turn on Face ID", true), no = button("Not now", false);
    yes.onclick = async () => { try { await faceIdTurnOn(); finish(); } catch (e) { msg.textContent = e.name === "NotAllowedError" ? "Cancelled." : e.message; } };
    no.onclick = () => { skip(); finish(); };
    box.append(yes, no, msg);
  }

  function finish() {
    screen?.remove(); screen = null; shown = null;
    const r = release; waiting = null; release = null;
    r?.();
  }

  function lock(info) {
    // The app's first request and the lock's own check both land here on opening: draw the screen once,
    // and again only when what it should show has changed (a password set, Face ID removed, the offer up).
    if (waiting && screen && shown === showing(info)) return waiting;
    if (!waiting) waiting = new Promise((res) => { release = res; });
    render(info);
    return waiting;
  }

  async function check(away) {
    const s = await state().catch(() => null);
    // Back after time away and the server can't be reached: cover the page anyway.
    if (!s && away) return lock({ password_set: true, passkeys: 0 });
    if (s && !s.unlocked) return lock(s);
    if (s && screen) finish();
  }

  window.fetch = async (input, init) => {
    const spare = input instanceof Request ? input.clone() : input;  // a Request's body can only be read once
    const res = await realFetch(input, init);
    if (res.status === 401 && isApi(typeof input === "string" ? input : input.url)) {
      await (waiting || state().then(lock));
      // Only reads are retried: a tap like "turn off" made before the lock shouldn't fire minutes later.
      const method = (init?.method || (input instanceof Request ? input.method : "GET")).toUpperCase();
      return method === "GET" ? realFetch(spare, init) : res;
    }
    return res;
  };

  let hiddenAt = 0;
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) hiddenAt = Date.now();
    else if (hiddenAt && Date.now() - hiddenAt > AWAY_MS) check(true);
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => check()); else check();
})();
