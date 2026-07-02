"""Proactive SLO-burn poller — page-worthiness, alert selection, cooldown."""

import asyncio

from app.slo_poller import SLOPoller, is_page_worthy, select_alerts

_PAGE = {"verdict": "page (fast burn — budget exhausts in hours)", "burn_rate_short": 20}
_TICKET = {"verdict": "ticket (slow burn — over budget)"}
_OK = {"verdict": "ok (within error budget)"}


def test_is_page_worthy():
    assert is_page_worthy(_PAGE) is True
    assert is_page_worthy(_TICKET) is False
    assert is_page_worthy(_OK) is False
    assert is_page_worthy({}) is False


def test_select_alerts_filters_and_is_resilient():
    def burn_fn(svc):
        return {
            "checkout-svc": _PAGE,
            "inventory-svc": _OK,
            "broken-svc": {"error": "no data"},
        }[svc]

    alerts = select_alerts(["checkout-svc", "inventory-svc", "broken-svc"], burn_fn)
    assert [a["service"] for a in alerts] == ["checkout-svc"]


def test_select_alerts_skips_raising_service():
    def burn_fn(svc):
        if svc == "bad":
            raise RuntimeError("boom")
        return _PAGE

    alerts = select_alerts(["bad", "good"], burn_fn)
    assert [a["service"] for a in alerts] == ["good"]


def _poller(now_box, fired, burn_map, cooldown=3600):
    async def trigger(service, burn):
        fired.append(service)

    return SLOPoller(
        services_fn=lambda: list(burn_map),
        burn_fn=lambda s: burn_map[s],
        trigger_fn=trigger,
        cooldown=cooldown,
        clock=lambda: now_box[0],
    )


def test_tick_fires_page_worthy_and_respects_cooldown():
    fired: list[str] = []
    now = [1000.0]
    poller = _poller(now, fired, {"checkout-svc": _PAGE, "inventory-svc": _OK}, cooldown=3600)

    assert asyncio.run(poller.tick()) == ["checkout-svc"]
    # Second sweep within cooldown: still burning, but do not re-open.
    assert asyncio.run(poller.tick()) == []
    # After the cooldown elapses, it re-opens.
    now[0] += 3601
    assert asyncio.run(poller.tick()) == ["checkout-svc"]
    assert fired == ["checkout-svc", "checkout-svc"]


def test_tick_swallows_trigger_errors():
    now = [0.0]

    async def boom(service, burn):
        raise RuntimeError("trigger failed")

    poller = SLOPoller(
        services_fn=lambda: ["checkout-svc"],
        burn_fn=lambda s: _PAGE,
        trigger_fn=boom,
        clock=lambda: now[0],
    )
    # A failing trigger is logged, not raised, and doesn't count as fired.
    assert asyncio.run(poller.tick()) == []
