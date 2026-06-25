"""Slack delivery: signed-request verification, message posting, Block Kit.

Used to post an investigation's findings into a channel and to render the
human-approval gate as interactive Approve/Reject buttons. Verification follows
Slack's signing-secret scheme (https://api.slack.com/authentication/verifying-requests-from-slack).
"""

from __future__ import annotations

import hashlib
import hmac
import time


def verify_signature(signing_secret: str, timestamp: str, raw_body: bytes, signature: str) -> bool:
    """Constant-time Slack request verification with a 5-minute replay window."""
    if not (signing_secret and timestamp and signature):
        return False
    try:
        if abs(time.time() - int(timestamp)) > 300:
            return False
    except (TypeError, ValueError):
        return False
    basestring = b"v0:" + timestamp.encode() + b":" + raw_body
    digest = "v0=" + hmac.new(signing_secret.encode(), basestring, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature)


def approval_blocks(thread_id: str, title: str, detail: str) -> list[dict]:
    """Block Kit message for a pending write approval, with Approve/Reject buttons
    whose `value` carries the thread_id so the callback can resume the right run."""
    return [
        {"type": "section", "text": {"type": "mrkdwn",
         "text": f":warning: *Approval needed* — *{title}*\n{detail[:2800]}"}},
        {"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "Approve"},
             "style": "primary", "action_id": "approve", "value": thread_id},
            {"type": "button", "text": {"type": "plain_text", "text": "Reject"},
             "style": "danger", "action_id": "reject", "value": thread_id},
        ]},
    ]


def result_blocks(title: str, answer: str) -> list[dict]:
    """Block Kit message for a completed investigation."""
    return [
        {"type": "section", "text": {"type": "mrkdwn",
         "text": f":mag: *Investigation complete* — *{title}*"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": (answer or "(no answer)")[:2900]}},
    ]


async def post_message(
    token: str, channel: str, text: str, blocks: list[dict] | None = None
) -> dict:
    """Post to Slack via chat.postMessage. No-op (returns a skip marker) when Slack
    isn't configured, so the trigger path degrades gracefully without a bot token."""
    if not (token and channel):
        return {"ok": False, "skipped": "slack not configured"}
    import httpx

    payload: dict = {"channel": channel, "text": text}
    if blocks:
        payload["blocks"] = blocks
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
    return resp.json()
