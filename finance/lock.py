"""App lock: requests that come from the phone need a Face ID (passkey) or password unlock.

`tailscale serve` adds X-Forwarded-For to every request it proxies and a client can't remove it, so a
request without that header came from this computer, which needs no unlock. An unlock lasts while the
app is in use and lapses after IDLE_SECONDS with no requests, so reopening the app asks again.

The password (scrypt hash) and passkeys live in one JSON file; sessions and challenges live in memory,
so restarting the app locks it. Setting a new password from the terminal is the way back in: it
removes every passkey and ends every session.

Framework-free on purpose: the same file runs the Money app (finance_tracker/finance/lock.py) and the
Docker panel (linux-remote/lock.py). Keep the two copies identical; tests live with the Money app.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlparse

IDLE_SECONDS = 300
CHALLENGE_SECONDS = 120
FREE_TRIES = 5          # wrong passwords before each further one makes you wait
MAX_WAIT = 900
MIN_PASSWORD = 6
AUTH_PREFIX = "/api/auth"


class Refused(Exception):
    def __init__(self, message, status=401, retry_after=None):
        super().__init__(message)
        self.status, self.retry_after = status, retry_after


def b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def from_phone(headers) -> bool:
    return headers.get("X-Forwarded-For") is not None


def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)


class Lock:
    def __init__(self, path, cookie="app_lock", clock=time.time, idle=IDLE_SECONDS, rp_name="App"):
        self.path, self.cookie, self.clock, self.idle, self.rp_name = Path(path), cookie, clock, idle, rp_name
        self._mutex = threading.Lock()
        self._sessions = {}     # token -> [last seen, generation]
        self._challenges = {}   # challenge -> (expires, kind)
        self._fails = 0
        self._blocked_until = 0.0
        self._fails_generation = None  # a new password from the terminal starts the count again

    # ---- stored state ----------------------------------------------------------------------

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except FileNotFoundError:
            return {}

    def _save(self, doc: dict):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(doc, f, indent=1)
        os.replace(tmp, self.path)

    def set_password(self, password: str):
        """From the terminal. Ends every session and removes every passkey."""
        # Numbers only, so the phone shows its number pad. The wait after wrong guesses keeps 6 digits slow to guess.
        if not (password.isascii() and password.isdigit()) or len(password) < MIN_PASSWORD:
            raise ValueError(f"use {MIN_PASSWORD} or more numbers, nothing else")
        with self._mutex:
            doc = self._load()
            salt = os.urandom(16)
            self._save({"generation": doc.get("generation", 0) + 1,
                        "password": {"salt": b64e(salt), "hash": b64e(_hash(password, salt))},
                        "user_id": b64e(os.urandom(16)), "passkeys": []})

    # ---- sessions --------------------------------------------------------------------------

    def _new_session(self, doc) -> str:
        token = secrets.token_urlsafe(32)
        self._sessions[token] = [self.clock(), doc.get("generation", 0)]
        return token

    def _valid(self, token, doc) -> bool:
        s = self._sessions.get(token or "")
        if not s:
            return False
        if s[1] != doc.get("generation", 0) or self.clock() - s[0] > self.idle:
            self._sessions.pop(token, None)
            return False
        s[0] = self.clock()
        return True

    def _token(self, headers):
        jar = SimpleCookie()
        try:
            jar.load(headers.get("Cookie") or "")
        except Exception:
            return None
        return jar[self.cookie].value if self.cookie in jar else None

    def _cookie(self, token) -> str:
        return f"{self.cookie}={token or ''}; Path=/; HttpOnly; Secure; SameSite=Strict" + ("" if token else "; Max-Age=0")

    # ---- password --------------------------------------------------------------------------

    def unlock_password(self, password: str) -> str:
        with self._mutex:
            now = self.clock()
            doc = self._load()
            if doc.get("generation") != self._fails_generation:
                self._fails, self._blocked_until, self._fails_generation = 0, 0.0, doc.get("generation")
            if now < self._blocked_until:
                raise Refused("Too many wrong passwords. Wait and try again.", 429, int(self._blocked_until - now) + 1)
            pw = doc.get("password")
            if not pw:
                raise Refused("No password set yet.", 409)
            if not hmac.compare_digest(_hash(str(password), b64d(pw["salt"])), b64d(pw["hash"])):
                self._fails += 1
                if self._fails >= FREE_TRIES:
                    self._blocked_until = now + min(30 * 2 ** (self._fails - FREE_TRIES), MAX_WAIT)
                raise Refused("Wrong password.")
            self._fails, self._blocked_until = 0, 0.0
            return self._new_session(doc)

    # ---- HTTP glue: both apps call these two ------------------------------------------------

    def blocks(self, path: str, headers) -> bool:
        """True when this API request must be answered 401 (locked)."""
        if not path.startswith("/api/") or path == AUTH_PREFIX or path.startswith(AUTH_PREFIX + "/"):
            return False
        if not from_phone(headers):
            return False
        with self._mutex:
            return not self._valid(self._token(headers), self._load())

    def handle(self, method: str, path: str, headers, body: bytes):
        """Answers /api/auth requests: (status, json-able dict, Set-Cookie value or None)."""
        try:
            return self._handle(method, path, headers, body)
        except Refused as e:
            out = {"error": str(e)}
            if e.retry_after:
                out["retry_after"] = e.retry_after
            return e.status, out, None

    def _handle(self, method, path, headers, body):
        phone = from_phone(headers)
        if method == "GET" and path == AUTH_PREFIX:
            with self._mutex:
                doc = self._load()
                unlocked = not phone or self._valid(self._token(headers), doc)
            return 200, {"unlocked": unlocked, "phone": phone, "password_set": bool(doc.get("password")),
                         "passkeys": len(doc.get("passkeys", []))}, None
        if method != "POST":
            return 405, {"error": "method"}, None
        # A custom header forces a CORS preflight, which neither app answers, so other sites can't post here.
        if headers.get("X-Lock") != "1":
            return 403, {"error": "missing X-Lock header"}, None
        try:
            data = json.loads(body or b"{}")
        except ValueError:
            return 400, {"error": "bad JSON"}, None
        if not isinstance(data, dict):
            return 400, {"error": "bad JSON"}, None
        if path == AUTH_PREFIX + "/password":
            return 200, {"unlocked": True}, self._cookie(self.unlock_password(data.get("password", "")))
        if path == AUTH_PREFIX + "/lock":
            with self._mutex:
                self._sessions.pop(self._token(headers) or "", None)
            return 200, {"unlocked": False}, self._cookie(None)
        if path == AUTH_PREFIX + "/passkey/options":
            return 200, self.passkey_options(data.get("kind"), headers), None
        cred = data.get("credential")
        if path in (AUTH_PREFIX + "/passkey/register", AUTH_PREFIX + "/passkey/login") and not isinstance(cred, dict):
            return 400, {"error": "missing credential"}, None
        if path == AUTH_PREFIX + "/passkey/register":
            return 200, {"passkeys": self.passkey_register(cred, headers)}, None
        if path == AUTH_PREFIX + "/passkey/login":
            return 200, {"unlocked": True}, self._cookie(self.passkey_login(cred, headers))
        return 404, {"error": "not found"}, None

    # ---- Face ID (WebAuthn passkeys) ----------------------------------------------------------
    # The browser hands over the public key as SPKI DER (getPublicKey()), so no CBOR parsing is needed.
    # Attestation isn't checked: adding a passkey already takes an unlocked session.

    @staticmethod
    def _origin(headers) -> str:
        return f"{headers.get('X-Forwarded-Proto') or 'http'}://{headers.get('X-Forwarded-Host') or headers.get('Host') or ''}"

    def passkey_options(self, kind, headers) -> dict:
        origin = self._origin(headers)
        rp_id = urlparse(origin).hostname or ""
        with self._mutex:
            doc = self._load()
            mine = [{"type": "public-key", "id": p["id"]} for p in doc.get("passkeys", []) if urlparse(p["origin"]).hostname == rp_id]  # same host, any port: one passkey serves every app
            if kind == "register":
                if not self._valid(self._token(headers), doc):
                    raise Refused("Unlock with the password first.")
            elif kind == "login":
                if not mine:
                    raise Refused("Face ID isn't turned on here.", 409)
            else:
                raise Refused("kind must be register or login", 400)
            now = self.clock()
            live = sorted((v[0], c) for c, v in self._challenges.items() if v[0] > now)[-50:]
            self._challenges = {c: self._challenges[c] for _, c in live}
            challenge = b64e(os.urandom(32))
            self._challenges[challenge] = (now + CHALLENGE_SECONDS, kind)
        if kind == "login":
            return {"challenge": challenge, "rpId": rp_id, "allowCredentials": mine,
                    "userVerification": "required", "timeout": 60000}
        return {"challenge": challenge, "rp": {"id": rp_id, "name": self.rp_name},
                # Both apps share one host, so each passkey carries its app's name to tell them apart in Passwords.
                "user": {"id": doc["user_id"], "name": self.rp_name, "displayName": self.rp_name},
                "pubKeyCredParams": [{"type": "public-key", "alg": -7}, {"type": "public-key", "alg": -257}],
                "authenticatorSelection": {"authenticatorAttachment": "platform", "userVerification": "required",
                                           "residentKey": "preferred"},
                "excludeCredentials": mine, "attestation": "none", "timeout": 60000}

    def _client_data(self, cred, kind, origin) -> bytes:
        raw = b64d(cred["clientDataJSON"])
        cd = json.loads(raw)
        entry = self._challenges.pop(str(cd.get("challenge")), None)  # single use, even when the rest fails
        want = "webauthn.create" if kind == "register" else "webauthn.get"
        if (not entry or entry[1] != kind or entry[0] < self.clock() or cd.get("type") != want
                or cd.get("origin") != origin):
            raise Refused("Face ID check failed.")
        return raw

    @staticmethod
    def _auth_data(cred, rp_id) -> tuple:
        ad = b64d(cred["authenticatorData"])
        rp_hash = hashlib.sha256(rp_id.encode()).digest()
        if len(ad) < 37 or not hmac.compare_digest(ad[:32], rp_hash) or ad[32] & 0x05 != 0x05:  # present + verified
            raise Refused("Face ID check failed.")
        return ad, int.from_bytes(ad[33:37], "big")

    def passkey_register(self, cred, headers) -> int:
        from cryptography.hazmat.primitives.serialization import load_der_public_key

        origin = self._origin(headers)
        try:
            with self._mutex:
                doc = self._load()
                if not self._valid(self._token(headers), doc):
                    raise Refused("Unlock with the password first.")
                self._client_data(cred, "register", origin)
                ad, count = self._auth_data(cred, urlparse(origin).hostname or "")
                # The new key's id sits in the authenticator data (flag 0x40) after a 16-byte AAGUID and a length.
                size = int.from_bytes(ad[53:55], "big") if len(ad) >= 55 else -1
                if not ad[32] & 0x40 or size < 1 or b64e(ad[55:55 + size]) != cred["id"]:
                    raise Refused("That Face ID setup couldn't be read.", 400)
                spki = b64d(cred["publicKey"])
                _key_kind(load_der_public_key(spki))
                keys = doc.setdefault("passkeys", [])
                if not any(p["id"] == cred["id"] for p in keys):
                    # The AAGUID says which app saved the passkey (Apple Passwords, Bitwarden, ...).
                    keys.append({"id": str(cred["id"]), "key": b64e(spki), "origin": origin, "count": count,
                                 "aaguid": ad[37:53].hex(),
                                 "added": datetime.now(timezone.utc).isoformat(timespec="seconds")})
                    self._save(doc)
                return len(keys)
        except Refused:
            raise
        except Exception:
            raise Refused("That Face ID setup couldn't be read.", 400)

    def passkey_login(self, cred, headers) -> str:
        origin = self._origin(headers)
        try:
            with self._mutex:
                doc = self._load()
                p = next((p for p in doc.get("passkeys", []) if p["id"] == cred.get("id") and urlparse(p["origin"]).hostname == urlparse(origin).hostname), None)
                raw = self._client_data(cred, "login", origin)
                if not p:
                    raise Refused("Face ID check failed.")
                ad, count = self._auth_data(cred, urlparse(origin).hostname or "")
                _verify(b64d(p["key"]), b64d(cred["signature"]), ad + hashlib.sha256(raw).digest())
                if count or p["count"]:  # a counter that goes backwards means a copied key
                    if count <= p["count"]:
                        raise Refused("Face ID check failed.")
                    p["count"] = count
                    self._save(doc)
                return self._new_session(doc)
        except Refused:
            raise
        except Exception:
            raise Refused("Face ID check failed.")


def _key_kind(key) -> str:
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

    if isinstance(key, ec.EllipticCurvePublicKey):
        return "ec"
    if isinstance(key, rsa.RSAPublicKey):
        return "rsa"
    if isinstance(key, ed25519.Ed25519PublicKey):
        return "ed25519"
    raise ValueError("unsupported key type")


def _verify(spki: bytes, signature: bytes, message: bytes):
    """Raises if `signature` over `message` doesn't check out with the stored key."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, padding
    from cryptography.hazmat.primitives.serialization import load_der_public_key

    key = load_der_public_key(spki)
    kind = _key_kind(key)
    if kind == "ec":
        key.verify(signature, message, ec.ECDSA(hashes.SHA256()))
    elif kind == "rsa":
        key.verify(signature, message, padding.PKCS1v15(), hashes.SHA256())
    else:
        key.verify(signature, message)


def main(argv=None):
    """`python -m finance.lock <file>`: set the password from the terminal (the way back in if locked out)."""
    import getpass
    import sys

    argv = sys.argv[1:] if argv is None else argv
    path = Path(argv[0])
    first = getpass.getpass(f"New password ({MIN_PASSWORD} or more numbers): ")
    if getpass.getpass("Again: ") != first:
        sys.exit("The two didn't match. Nothing changed.")
    try:
        Lock(path).set_password(first)
    except ValueError as e:
        sys.exit(f"Not set: {e}.")
    print("Password set. Face ID on each device needs turning on again after the next unlock.")


if __name__ == "__main__":
    main()
