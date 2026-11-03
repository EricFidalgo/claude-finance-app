"""Linking banks from the app's Money screen: Plaid Link tokens, adding a bank, fixing one, removing one.

The browser gets link tokens and opaque connection ids only; access tokens and Plaid's item ids stay on this side.
Plaid Link runs in the page, so it works on this computer (http://localhost:8787) and on the phone (HTTPS through
tailscale serve); Plaid accepts plain http on localhost only.
"""
import hashlib

from . import config, store
from .plaid import READ_ONLY_PRODUCTS, PlaidError, load_items, remove_item, save_item
from .sync import forget_item, record_relink

# Plaid's answer when the Item is already gone on its side; removing it here still goes ahead.
GONE = {"ITEM_NOT_FOUND", "INVALID_ACCESS_TOKEN"}


def connection_id(item_id):
    """What the browser calls an Item. A hash, so Plaid's item id never leaves the server."""
    return hashlib.sha256(f"connection:{item_id}".encode()).hexdigest()[:16]


def _item_id(conn, cid):
    known = {i["item_id"] for i in load_items()} | {it["item_id"] for it in store.items(conn)}
    found = next((i for i in known if connection_id(i) == cid), None)
    if found is None:
        raise LookupError(cid)
    return found


def _token_item(item_id):
    item = next((i for i in load_items() if i["item_id"] == item_id), None)
    if item is None:
        raise LookupError(item_id)
    return item


def link_token(conn, make_client, kind=None, cid=None):
    """A Link token: to add a bank (`kind` is one of the read-only products), or with `cid` to fix that bank's
    login or change which accounts it shares (Plaid's update mode)."""
    req = {"client_name": "Money", "language": "en", "country_codes": ["US"], "user": {"client_user_id": "owner"}}
    if cid:
        item = _token_item(_item_id(conn, cid))
        client = make_client(item.get("env", "production"))
        req["access_token"] = item["access_token"]
        req["update"] = {"account_selection_enabled": True}
    elif kind in READ_ONLY_PRODUCTS:
        client = make_client(config.plaid_env())
        req["products"] = [kind]
    else:
        raise ValueError(f"kind must be one of {', '.join(READ_ONLY_PRODUCTS)}")
    return client.post("/link/token/create", req)["link_token"]


def add(make_client, public_token, institution):
    """Swap the public token Link handed the page for an access token, and keep it. Returns the new item id."""
    env = config.plaid_env()
    res = make_client(env).post("/item/public_token/exchange", {"public_token": public_token})
    save_item({"item_id": res["item_id"], "access_token": res["access_token"], "institution": institution, "env": env})
    return res["item_id"]


def renewed(conn, cid):
    """Link's update mode finished: the login works again and an estimated consent year restarts."""
    item_id = _item_id(conn, cid)
    record_relink(conn, item_id)
    return item_id


def remove(conn, make_client, cid):
    """Tell Plaid to drop the Item (its token stops working and billing ends), then forget its token and data.
    Returns the institution's name."""
    item_id = _item_id(conn, cid)
    item = next((i for i in load_items() if i["item_id"] == item_id), None)
    name = next((it["institution"] for it in store.items(conn) if it["item_id"] == item_id), None)
    if item:
        try:
            make_client(item.get("env", "production")).post("/item/remove", {"access_token": item["access_token"]})
        except PlaidError as e:
            if e.code not in GONE:
                raise
        remove_item(item_id)
        name = name or item.get("institution")
    forget_item(conn, item_id)
    return name
