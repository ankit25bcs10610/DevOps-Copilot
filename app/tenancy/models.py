"""Tenant domain model: orgs, roles/RBAC, plans/quotas, API keys, usage.

Pure data + policy (no I/O), so the permission matrix and quota logic are
unit-testable in isolation. The store (store.py) persists these.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- Roles & RBAC ---------------------------------------------------------- #
# Ranked least → most privileged. A permission requires a minimum role.
ROLE_RANK: dict[str, int] = {"viewer": 0, "responder": 1, "admin": 2, "owner": 3}
ROLES = tuple(ROLE_RANK)

# action -> minimum role that may perform it.
_PERMISSIONS: dict[str, str] = {
    "view": "viewer",                 # read investigations / config
    "run_investigation": "responder", # start a chat/investigation
    "approve_action": "responder",    # approve/reject a write the agent proposes
    "manage_integrations": "admin",   # set Datadog/GitHub/etc. credentials
    "manage_members": "admin",        # invite/remove members, change roles
    "manage_api_keys": "admin",       # issue/revoke API keys
    "manage_billing": "owner",        # change plan / billing
    "delete_org": "owner",
}
ACTIONS = tuple(_PERMISSIONS)


def can(role: str, action: str) -> bool:
    """True if `role` is allowed to perform `action`."""
    need = _PERMISSIONS.get(action)
    if need is None:
        return False
    return ROLE_RANK.get(role, -1) >= ROLE_RANK[need]


def normalize_role(role: str) -> str:
    r = (role or "").strip().lower()
    return r if r in ROLE_RANK else "viewer"


# --- Plans & quotas -------------------------------------------------------- #
# -1 == unlimited.
PLAN_QUOTAS: dict[str, dict[str, int]] = {
    "free": {"investigations_per_month": 50, "seats": 3, "integrations": 2, "api_keys": 2},
    "team": {"investigations_per_month": 1000, "seats": 25, "integrations": 10, "api_keys": 20},
    "enterprise": {"investigations_per_month": -1, "seats": -1, "integrations": -1, "api_keys": -1},
}
PLANS = tuple(PLAN_QUOTAS)


def normalize_plan(plan: str) -> str:
    p = (plan or "").strip().lower()
    return p if p in PLAN_QUOTAS else "free"


def quota(plan: str, key: str) -> int:
    """The limit for `key` under `plan` (-1 = unlimited)."""
    return PLAN_QUOTAS.get(normalize_plan(plan), PLAN_QUOTAS["free"]).get(key, 0)


def within_quota(plan: str, key: str, current: int) -> bool:
    """True if one more unit of `key` is allowed given `current` usage/count."""
    limit = quota(plan, key)
    return limit < 0 or current < limit


# --- Records --------------------------------------------------------------- #
@dataclass
class Org:
    id: str
    name: str
    plan: str = "free"
    created_at: str = ""
    stripe_customer_id: str = ""


@dataclass
class User:
    id: str
    email: str
    created_at: str = ""


@dataclass
class Membership:
    org_id: str
    user_id: str
    role: str = "viewer"


@dataclass
class ApiKey:
    id: str
    org_id: str
    prefix: str          # public, used to look the key up
    name: str = ""
    role: str = "responder"
    created_at: str = ""
    last_used_at: str = ""
    revoked_at: str = ""

    @property
    def active(self) -> bool:
        return not self.revoked_at


@dataclass
class UsageRecord:
    org_id: str
    kind: str            # e.g. "investigation", "tokens"
    amount: int = 1
    ts: str = ""
    meta: dict = field(default_factory=dict)
