"""Paths and tunables. Everything that differs between the container, the host and tests comes from env vars."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    path = Path(os.environ.get("FINANCE_DATA_DIR") or ROOT / "data")
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "finance.db"


def items_path() -> Path:
    return data_dir() / "items.json"


def plan_path() -> Path:
    return data_dir() / "plan.json"


def allowed_hosts() -> list:
    """Host names the app answers to: this machine and the tailnet (tailscale serve). Anything else is refused,
    so a web page that points its own domain at 127.0.0.1 (DNS rebinding) cannot read the API."""
    extra = [h.strip() for h in os.environ.get("FINANCE_EXTRA_HOSTS", "").split(",") if h.strip()]
    return ["localhost", "127.0.0.1", "*.ts.net", *extra]


def scheduler_enabled() -> bool:
    return os.environ.get("FINANCE_SCHEDULER", "on") != "off"


SYNC_INTERVAL_HOURS = 6
# A sync older than this shows as stale on the attention list.
STALE_AFTER_HOURS = 13
# Retry sooner while Plaid is still preparing an Item's data.
NOT_READY_RETRY_MINUTES = 5
# Manual "Sync now" is ignored if the last attempt was more recent than this.
MANUAL_SYNC_MIN_MINUTES = 10

# Institutions that share one login and can report the same account under two Items.
# Accounts are keyed by family + mask, so such an account counts once.
INSTITUTION_FAMILY = {
    "ins_127989": "bofa",  # Bank of America
    "ins_115610": "bofa",  # Merrill (Merrill Edge, CMA-Edge)
}

# How long an institution's data-sharing consent lasts when Plaid doesn't report an expiry. Bank of America asks
# customers to re-authorize yearly; the app estimates expiry from when it first saw the Item.
CONSENT_MONTHS = {"ins_127989": 12}
