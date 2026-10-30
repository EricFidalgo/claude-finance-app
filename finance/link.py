"""Plaid Link page: link a bank, fix a login, or change which accounts an Item shares.

Run with `./run.sh link` (inside the container: `python -m finance.link [--sandbox]`), then open
http://localhost:8765 on the computer. Plaid Link in Production accepts plain http only on localhost.
Linked Items are saved to data/items.json (mode 600). The page only ever receives link tokens, never access tokens.
"""
import contextlib
import json
import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

from . import db
from .plaid import READ_ONLY_PRODUCTS, PlaidClient, PlaidError, load_items, save_item
from .sync import record_relink

PORT = 8765
APP_SYNC = "http://127.0.0.1:8787/api/sync"

PAGE = """<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Link an account</title>
<style>
:root{color-scheme:light dark}
body{font:16px/1.5 system-ui,sans-serif;max-width:34rem;margin:2.5rem auto;padding:0 1rem}
button{font:inherit;padding:.6rem 1rem;margin:.25rem .5rem .25rem 0;cursor:pointer;border-radius:.6rem}
.row{display:flex;align-items:center;justify-content:space-between;gap:.5rem;padding:.4rem 0}
#out{min-height:1.5em}
</style>
<h1>Link an account</h1>
<p>Plaid environment: <b>__ENV__</b>. Read-only: transactions and investments only.</p>
<h2>Linked</h2>
<div id="items">Loading…</div>
<h2>Add</h2>
<button data-kind="transactions">Bank or credit card</button>
<button data-kind="investments">Brokerage or retirement</button>
<p id="out" role="status" aria-live="polite"></p>
<script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
<script>
const out = document.getElementById('out');
async function api(path, body) {
  const r = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body || {})});
  return r.json();
}
async function showItems() {
  const items = await (await fetch('/api/items')).json();
  const box = document.getElementById('items');
  box.textContent = items.length ? '' : 'Nothing linked yet.';
  items.forEach(i => {
    const row = document.createElement('div');
    row.className = 'row';
    const name = document.createElement('span');
    name.textContent = i.institution || 'Unnamed bank';
    const btn = document.createElement('button');
    btn.textContent = 'Fix login or change accounts';
    btn.onclick = () => openLink({item_id: i.item_id});
    row.append(name, btn);
    box.appendChild(row);
  });
}
async function openLink(req) {
  out.textContent = 'Opening Plaid…';
  const t = await api('/api/link_token', req);
  if (!t.link_token) { out.textContent = 'Could not start Plaid Link: ' + (t.error || 'unknown error'); return; }
  Plaid.create({
    token: t.link_token,
    onSuccess: async (public_token, meta) => {
      const r = req.item_id ? await api('/api/updated', {item_id: req.item_id})
        : await api('/api/exchange', {public_token, institution: meta.institution && meta.institution.name});
      out.textContent = r.ok ? (r.message || 'Done.') : 'Failed: ' + (r.error || 'unknown error');
      showItems();
    },
    onExit: (err) => { out.textContent = err ? 'Plaid Link closed: ' + (err.display_message || err.error_code) : ''; },
  }).open();
}
document.querySelectorAll('button[data-kind]').forEach(b => b.onclick = () => openLink({kind: b.dataset.kind}));
showItems();
</script>
"""


def _sync_app():
    """Ask the running app to sync now so the new data shows up. Best effort."""
    req = urllib.request.Request(APP_SYNC, b'{"force": true}', {"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=120).close()
        return "Synced."
    except OSError:
        return "Sync will pick it up within 6 hours, or run ./run.sh sync."


def make_handler(client):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _foreign_host(self):
            # Refuse other host names so a web page that points its domain at 127.0.0.1 cannot use this page.
            if (self.headers.get("Host") or "").rsplit(":", 1)[0] in ("localhost", "127.0.0.1"):
                return False
            self._send(400, {"error": "use http://localhost:%d" % PORT})
            return True

        def do_GET(self):
            if self._foreign_host():
                return
            if self.path == "/api/items":
                return self._send(200, [{"item_id": i["item_id"], "institution": i.get("institution")}
                                        for i in load_items()])
            if self.path != "/":
                return self._send(404, {"error": "not found"})
            self._send(200, PAGE.replace("__ENV__", client.env).encode(), "text/html; charset=utf-8")

        def do_POST(self):
            if self._foreign_host():
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            try:
                if self.path == "/api/link_token":
                    return self._send(200, {"link_token": self._link_token(body)})
                if self.path == "/api/exchange":
                    res = client.post("/item/public_token/exchange", {"public_token": body.get("public_token")})
                    save_item({"item_id": res["item_id"], "access_token": res["access_token"],
                               "institution": body.get("institution"), "env": client.env})
                    return self._send(200, {"ok": True, "message": f"Linked {body.get('institution') or 'account'}. "
                                                                   f"{_sync_app()}"})
                if self.path == "/api/updated":
                    with contextlib.closing(db.connect()) as conn:
                        record_relink(conn, body.get("item_id"))
                    return self._send(200, {"ok": True, "message": f"Updated. {_sync_app()}"})
            except PlaidError as e:  # code and Plaid's message only; never the request
                return self._send(400, {"error": f"{e.code}: {e.message}"})
            except LookupError:
                return self._send(404, {"error": "unknown item"})
            self._send(404, {"error": "not found"})

        def _link_token(self, body):
            req = {"client_name": "Finance Tracker", "language": "en", "country_codes": ["US"],
                   "user": {"client_user_id": "owner"}}
            if body.get("item_id"):
                # Update mode: repairs a login (ITEM_LOGIN_REQUIRED, consent renewal) and lets the owner
                # change which accounts the Item shares.
                item = next((i for i in load_items() if i["item_id"] == body["item_id"]), None)
                if item is None:
                    raise LookupError(body["item_id"])
                req["access_token"] = item["access_token"]
                req["update"] = {"account_selection_enabled": True}
            else:
                kind = body.get("kind")
                req["products"] = [kind if kind in READ_ONLY_PRODUCTS else "transactions"]
            return client.post("/link/token/create", req)["link_token"]

        def log_message(self, fmt, *args):
            pass

    return Handler


if __name__ == "__main__":
    client = PlaidClient("sandbox" if "--sandbox" in sys.argv else "production")
    print(f"Open http://localhost:{PORT} on this computer (Plaid {client.env}). Ctrl+C when done.", flush=True)
    # 0.0.0.0 inside the container only; compose publishes the port on the host's 127.0.0.1.
    HTTPServer(("0.0.0.0", PORT), make_handler(client)).serve_forever()
