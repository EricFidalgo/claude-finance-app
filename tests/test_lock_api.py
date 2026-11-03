"""The lock in front of the API: requests from the phone (through `tailscale serve`) need an unlock;
requests from this computer don't."""
import pytest
from starlette.testclient import TestClient

# tailscale serve stamps every proxied request with this header, and a client can't remove it.
PHONE = {"X-Forwarded-For": "100.64.0.7", "X-Forwarded-Host": "ubuntu.example.ts.net", "X-Forwarded-Proto": "https"}
LOCK = {"X-Lock": "1"}


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def app(data_dir, clock):
    from finance.web import create_app

    return create_app(lock_clock=clock)


@pytest.fixture
def phone(app):
    with TestClient(app, base_url="https://testserver") as c:
        c.headers.update(PHONE)
        yield c


@pytest.fixture
def pc(app):
    with TestClient(app) as c:
        yield c


def set_password(data_dir, password="correct horse"):
    from finance import lock

    lock.Lock(data_dir / "lock.json").set_password(password)


def unlock(phone, password="correct horse"):
    return phone.post("/api/auth/password", json={"password": password}, headers=LOCK)


def test_phone_is_locked_out_until_it_unlocks(phone, data_dir):
    set_password(data_dir)
    assert phone.get("/api/today").status_code == 401
    assert phone.get("/api/snapshot").status_code == 401


def test_this_computer_needs_no_unlock(pc, data_dir):
    set_password(data_dir)
    assert pc.get("/api/today").status_code == 200


def test_the_page_itself_loads_locked_so_the_lock_screen_can_show(phone, data_dir):
    set_password(data_dir)
    assert phone.get("/").status_code == 200
    assert phone.get("/lock.js").status_code == 200


def test_the_right_password_unlocks_the_phone(phone, data_dir):
    set_password(data_dir)
    r = unlock(phone)
    assert r.status_code == 200
    assert "HttpOnly" in r.headers["set-cookie"] and "Secure" in r.headers["set-cookie"]
    assert phone.get("/api/today").status_code == 200
    assert phone.get("/api/auth").json()["unlocked"] is True


def test_a_wrong_password_does_not_unlock(phone, data_dir):
    set_password(data_dir)
    assert unlock(phone, "nope nope").status_code == 401
    assert phone.get("/api/today").status_code == 401


def test_after_five_wrong_passwords_even_the_right_one_has_to_wait(phone, data_dir, clock):
    set_password(data_dir)
    for _ in range(5):
        assert unlock(phone, "guess guess").status_code == 401
    r = unlock(phone)
    assert r.status_code == 429 and r.json()["retry_after"] > 0
    clock.t += r.json()["retry_after"]
    assert unlock(phone).status_code == 200


def test_a_new_password_from_the_terminal_also_lifts_the_wait(phone, data_dir):
    set_password(data_dir)
    for _ in range(6):
        unlock(phone, "guess guess")
    assert unlock(phone).status_code == 429
    set_password(data_dir, "a whole new one")
    assert unlock(phone, "a whole new one").status_code == 200


def test_the_unlock_lapses_after_five_minutes_away(phone, data_dir, clock):
    set_password(data_dir)
    unlock(phone)
    clock.t += 240
    assert phone.get("/api/today").status_code == 200   # still in use: each request keeps it open
    clock.t += 240
    assert phone.get("/api/today").status_code == 200
    clock.t += 301
    assert phone.get("/api/today").status_code == 401


def test_locking_by_hand_ends_the_unlock(phone, data_dir):
    set_password(data_dir)
    unlock(phone)
    phone.post("/api/auth/lock", headers=LOCK)
    assert phone.get("/api/today").status_code == 401


def test_a_new_password_from_the_terminal_ends_every_unlock(phone, data_dir):
    set_password(data_dir)
    unlock(phone)
    set_password(data_dir, "a whole new one")
    assert phone.get("/api/today").status_code == 401
    assert unlock(phone, "a whole new one").status_code == 200


def test_with_no_password_set_the_phone_stays_locked(phone):
    assert phone.get("/api/today").status_code == 401
    assert phone.get("/api/auth").json()["password_set"] is False
    assert unlock(phone, "anything").status_code == 409


def test_unlock_requests_need_the_custom_header_so_other_sites_cant_send_them(phone, data_dir):
    set_password(data_dir)
    assert phone.post("/api/auth/password", json={"password": "correct horse"}).status_code == 403


# ---- Face ID (passkeys) ----------------------------------------------------------------------

import hashlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402

from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402

ORIGIN = "https://ubuntu.example.ts.net"
RP_ID = "ubuntu.example.ts.net"


def b64(raw):
    import base64
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class FakeFaceID:
    """Stands in for the iPhone: makes a key, answers challenges the way WebAuthn does."""

    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.id = os.urandom(16)

    def _auth_data(self, rp_id, verified, attested_id=None):
        flags = 0x01 | (0x04 if verified else 0) | (0x40 if attested_id else 0)  # present, verified, new key
        ad = hashlib.sha256(rp_id.encode()).digest() + bytes([flags]) + (0).to_bytes(4, "big")
        if attested_id:  # AAGUID (zeros, as iPhones send), then the new credential's id; the COSE key would follow
            ad += bytes(16) + len(attested_id).to_bytes(2, "big") + attested_id
        return ad

    def create(self, options, origin=ORIGIN, reported_id=None):
        cd = json.dumps({"type": "webauthn.create", "challenge": options["challenge"], "origin": origin}).encode()
        spki = self.key.public_key().public_bytes(serialization.Encoding.DER,
                                                  serialization.PublicFormat.SubjectPublicKeyInfo)
        return {"id": b64(self.id), "clientDataJSON": b64(cd),
                "authenticatorData": b64(self._auth_data(options["rp"]["id"], True, reported_id or self.id)),
                "publicKey": b64(spki)}

    def get(self, options, origin=ORIGIN, rp_id=RP_ID, verified=True, signer=None):
        cd = json.dumps({"type": "webauthn.get", "challenge": options["challenge"], "origin": origin}).encode()
        ad = self._auth_data(rp_id, verified)
        sig = (signer or self.key).sign(ad + hashlib.sha256(cd).digest(), ec.ECDSA(hashes.SHA256()))
        return {"id": b64(self.id), "clientDataJSON": b64(cd), "authenticatorData": b64(ad), "signature": b64(sig)}


def options(phone, kind):
    r = phone.post("/api/auth/passkey/options", json={"kind": kind}, headers=LOCK)
    assert r.status_code == 200, r.text
    return r.json()


def turn_on_face_id(phone, data_dir, face):
    set_password(data_dir)
    unlock(phone)
    r = phone.post("/api/auth/passkey/register", json={"credential": face.create(options(phone, "register"))},
                   headers=LOCK)
    assert r.status_code == 200, r.text
    phone.post("/api/auth/lock", headers=LOCK)
    phone.cookies.clear()


def face_id_unlock(phone, credential):
    return phone.post("/api/auth/passkey/login", json={"credential": credential}, headers=LOCK)


def test_face_id_can_only_be_turned_on_after_an_unlock(phone, data_dir):
    set_password(data_dir)
    r = phone.post("/api/auth/passkey/options", json={"kind": "register"}, headers=LOCK)
    assert r.status_code == 401


def test_face_id_unlocks_the_phone_once_turned_on(phone, data_dir):
    face = FakeFaceID()
    turn_on_face_id(phone, data_dir, face)
    assert phone.get("/api/today").status_code == 401
    assert phone.get("/api/auth").json()["passkeys"] == 1
    r = face_id_unlock(phone, face.get(options(phone, "login")))
    assert r.status_code == 200, r.text
    assert phone.get("/api/today").status_code == 200


def test_a_replayed_face_id_answer_is_refused(phone, data_dir):
    face = FakeFaceID()
    turn_on_face_id(phone, data_dir, face)
    answer = face.get(options(phone, "login"))
    assert face_id_unlock(phone, answer).status_code == 200
    phone.post("/api/auth/lock", headers=LOCK)
    phone.cookies.clear()
    assert face_id_unlock(phone, answer).status_code == 401
    assert phone.get("/api/today").status_code == 401


def test_an_answer_signed_by_some_other_key_is_refused(phone, data_dir):
    face = FakeFaceID()
    turn_on_face_id(phone, data_dir, face)
    forged = face.get(options(phone, "login"), signer=ec.generate_private_key(ec.SECP256R1()))
    assert face_id_unlock(phone, forged).status_code == 401
    assert phone.get("/api/today").status_code == 401


def test_an_answer_made_for_another_site_is_refused(phone, data_dir):
    face = FakeFaceID()
    turn_on_face_id(phone, data_dir, face)
    assert face_id_unlock(phone, face.get(options(phone, "login"), origin="https://evil.example")).status_code == 401
    assert face_id_unlock(phone, face.get(options(phone, "login"), rp_id="evil.example")).status_code == 401


def test_a_tap_without_face_id_actually_checking_the_face_is_refused(phone, data_dir):
    face = FakeFaceID()
    turn_on_face_id(phone, data_dir, face)
    assert face_id_unlock(phone, face.get(options(phone, "login"), verified=False)).status_code == 401


def test_a_new_password_from_the_terminal_removes_face_id(phone, data_dir):
    face = FakeFaceID()
    turn_on_face_id(phone, data_dir, face)
    set_password(data_dir, "a whole new one")
    assert phone.get("/api/auth").json()["passkeys"] == 0
    r = phone.post("/api/auth/passkey/options", json={"kind": "login"}, headers=LOCK)
    assert r.status_code == 409


def test_a_face_id_setup_whose_key_id_does_not_match_the_authenticator_is_refused(phone, data_dir):
    face = FakeFaceID()
    set_password(data_dir)
    unlock(phone)
    bad = face.create(options(phone, "register"), reported_id=os.urandom(16))
    assert phone.post("/api/auth/passkey/register", json={"credential": bad}, headers=LOCK).status_code == 400
    assert phone.get("/api/auth").json()["passkeys"] == 0
