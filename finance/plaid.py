"""Minimal Plaid REST client (stdlib only) and the token store data/items.json.

Read-only by scope: the app only ever requests the `transactions` and `investments` products.
Keys come from env vars; access tokens live only in data/items.json (mode 600) and never reach
the browser, the database, a log line or an error message.
"""
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

from . import config

READ_ONLY_PRODUCTS = ("transactions", "investments")


class PlaidError(Exception):
    """A Plaid API error. Carries only Plaid's code and message, never request data."""

    def __init__(self, code, message="", error_type=""):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.error_type = error_type


class PlaidClient:
    def __init__(self, env="production"):
        if env not in ("production", "sandbox"):
            raise ValueError(f"unknown Plaid env {env!r}")
        self.env = env

    def post(self, path, body):
        secret_var = "PLAID_SANDBOX_SECRET" if self.env == "sandbox" else "PLAID_SECRET"
        missing = [v for v in ("PLAID_CLIENT_ID", secret_var) if not os.environ.get(v)]
        if missing:
            raise PlaidError("MISSING_KEYS", f"env vars not set: {', '.join(missing)}; start the app with ./run.sh")
        payload = {"client_id": os.environ["PLAID_CLIENT_ID"], "secret": os.environ[secret_var], **body}
        req = urllib.request.Request(
            f"https://{self.env}.plaid.com{path}",
            json.dumps(payload).encode(),
            {"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            try:
                err = json.load(e)
            except ValueError:
                raise PlaidError("HTTP_ERROR", f"HTTP {e.code} from {path}") from None
            raise PlaidError(err.get("error_code") or "UNKNOWN", err.get("error_message") or "", err.get("error_type") or "") from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            reason = getattr(e, "reason", e)
            raise PlaidError("NETWORK_ERROR", f"{type(reason).__name__} calling {path}") from None


def load_items():
    path = config.items_path()
    if not path.exists():
        return []
    return json.loads(path.read_text())


def save_item(item):
    """Add or replace an Item in data/items.json (mode 600)."""
    items = [i for i in load_items() if i["item_id"] != item["item_id"]] + [item]
    fd = os.open(config.items_path(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(items, f, indent=2)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
