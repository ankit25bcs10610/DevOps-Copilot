"""SCIM 2.0 user provisioning — map an IdP's SCIM payloads to the tenant store.

Enterprise SSO has two halves: authentication (already covered — OIDC/JWT via
app/tenancy/supabase_auth.py verifies RS256/ES256 tokens against a JWKS) and
provisioning (SCIM). This module is the provisioning half: parse SCIM User resources
and render SCIM responses, so an IdP (Okta/Entra/WorkOS) can create/deprovision users
in an org. Parsing/serialization are pure; the HTTP endpoints (app/api/main.py) wire
them to the store under a SCIM bearer token scoped to one org.
"""

from __future__ import annotations

_SCIM_USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
_SCIM_LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"


def parse_scim_user(payload: dict) -> dict:
    """Extract the fields we store from a SCIM User resource. `userName` is the email;
    `active` defaults to True. Also pulls the first email if userName is absent."""
    payload = payload or {}
    email = (payload.get("userName") or "").strip().lower()
    if not email:
        emails = payload.get("emails") or []
        if emails and isinstance(emails, list) and isinstance(emails[0], dict):
            email = (emails[0].get("value") or "").strip().lower()
    active = payload.get("active", True)
    return {"email": email, "active": bool(active), "external_id": payload.get("externalId", "")}


def to_scim_user(user_id: str, email: str, active: bool = True, role: str = "") -> dict:
    """Render a stored user as a SCIM User resource for the IdP response."""
    resource = {
        "schemas": [_SCIM_USER_SCHEMA],
        "id": user_id,
        "userName": email,
        "active": active,
        "emails": [{"value": email, "primary": True}],
        "meta": {"resourceType": "User"},
    }
    if role:
        resource["roles"] = [{"value": role}]
    return resource


def scim_list(resources: list[dict]) -> dict:
    """Wrap resources in a SCIM ListResponse."""
    return {
        "schemas": [_SCIM_LIST_SCHEMA],
        "totalResults": len(resources),
        "Resources": resources,
        "startIndex": 1,
        "itemsPerPage": len(resources),
    }


def is_deprovision(payload: dict) -> bool:
    """True when a SCIM PATCH/PUT sets the user inactive (deprovisioning)."""
    if payload.get("active") is False:
        return True
    # PatchOp form: {"Operations":[{"op":"replace","value":{"active":false}}]}
    for op in payload.get("Operations", []) or []:
        val = op.get("value")
        if val is False:
            return True
        if isinstance(val, dict) and val.get("active") is False:
            return True
    return False
