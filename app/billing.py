"""Stripe metered-billing adapter (port/adapter over the local usage ledger).

The usage ledger (`app/metering.py` + the tenant store) is the system of record;
this thin worker syncs not-yet-billed usage rows to Stripe **meter events**, passing
the ledger's deterministic `event_key` as the meter-event idempotency key so Stripe
de-dupes retries. The Stripe client is an injectable port: a FakeStripeClient makes
the whole sync unit-testable offline; the live client is lazily imported and only
used when STRIPE_API_KEY is set.

Live setup (creating meters/prices, attaching to subscriptions, webhooks →
store.set_plan) needs a Stripe account and is out of scope here — this is the
sync mechanism + the idempotency contract those build on.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from app.config import get_settings

log = logging.getLogger("devcopilot.billing")


class StripeClient(Protocol):
    def create_meter_event(
        self, customer_id: str, event_name: str, value: int, idempotency_key: str
    ) -> None: ...


class FakeStripeClient:
    """In-memory client for tests / offline — records the meter events it would send."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def create_meter_event(
        self, customer_id: str, event_name: str, value: int, idempotency_key: str
    ) -> None:
        self.events.append({
            "customer_id": customer_id, "event_name": event_name,
            "value": value, "idempotency_key": idempotency_key,
        })


class _LiveStripeClient:
    """Lazily-imported real client (needs `stripe` + STRIPE_API_KEY)."""

    def __init__(self, api_key: str):
        import stripe

        stripe.api_key = api_key
        self._stripe = stripe

    def create_meter_event(
        self, customer_id: str, event_name: str, value: int, idempotency_key: str
    ) -> None:
        self._stripe.billing.MeterEvent.create(
            event_name=event_name,
            payload={"stripe_customer_id": customer_id, "value": str(value)},
            identifier=idempotency_key,  # Stripe de-dupes on this
        )


def get_client() -> StripeClient | None:
    """The live client when STRIPE_API_KEY is set, else None (local-ledger-only)."""
    key = get_settings().stripe_api_key.strip()
    return _LiveStripeClient(key) if key else None


async def sync_usage_to_stripe(store: Any, client: StripeClient, limit: int = 500) -> int:
    """Push unsynced billable usage to Stripe meter events; mark them synced.
    Idempotent: the ledger's event_key is the meter-event identifier, and rows are
    only marked synced after a successful send. Returns the count synced."""
    rows = await store.unsynced_usage(limit=limit)
    event_name = get_settings().stripe_meter_event
    synced: list[str] = []
    for r in rows:
        org = await store.get_org(r["org_id"])
        customer = org.stripe_customer_id if org else ""
        if not customer:
            continue  # no Stripe customer mapped yet — leave unsynced for later
        try:
            client.create_meter_event(
                customer, event_name, int(r["amount"]), r["event_key"] or r["id"]
            )
            synced.append(r["id"])
        except Exception:  # noqa: BLE001 — a send failure leaves the row for retry
            log.exception("stripe meter event failed (org=%s)", r["org_id"])
    await store.mark_synced(synced)
    return len(synced)


# --------------------------------------------------------------------------- #
# Inbound webhooks: Stripe subscription events → tenant plan (store.set_plan).
# --------------------------------------------------------------------------- #
_PLANS = {"free", "team", "enterprise"}


def verify_stripe_signature(payload: bytes, sig_header: str, secret: str,
                            now: int | None = None, tolerance: int = 300) -> bool:
    """Verify a Stripe-Signature header (scheme: `t=<ts>,v1=<hmac>`) over
    `"{t}.{payload}"` with HMAC-SHA256. Pure; `now` injectable for tests. When no
    secret is configured, verification fails closed."""
    import hashlib
    import hmac

    if not secret or not sig_header:
        return False
    parts = dict(p.split("=", 1) for p in sig_header.split(",") if "=" in p)
    ts, v1 = parts.get("t", ""), parts.get("v1", "")
    if not ts or not v1:
        return False
    if tolerance and now is not None:
        try:
            if abs(now - int(ts)) > tolerance:
                return False  # replayed/stale
        except ValueError:
            return False
    signed = f"{ts}.".encode() + payload
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, v1)


def plan_from_stripe_event(event: dict) -> tuple[str, str] | None:
    """Map a Stripe subscription event to (org_id, plan). The org id comes from the
    subscription's metadata.org_id; the plan from metadata.plan (or 'free' on cancel).
    Returns None for events we don't act on. Pure."""
    etype = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}
    meta = obj.get("metadata") or {}
    org_id = (meta.get("org_id") or "").strip()
    if not org_id:
        return None
    if etype == "customer.subscription.deleted":
        return org_id, "free"
    if etype in ("customer.subscription.created", "customer.subscription.updated"):
        plan = (meta.get("plan") or "").strip().lower()
        return (org_id, plan) if plan in _PLANS else None
    return None
