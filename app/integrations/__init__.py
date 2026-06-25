"""External delivery / trigger integrations (Slack, PagerDuty webhooks).

These are pure helpers — signature verification, payload parsing, and message
building — with no dependency on the agent or FastAPI app, so they're easy to
unit-test. The API layer (app/api/main.py) wires them to the agent.
"""
