"""Proactive SLO-burn poller — open an investigation BEFORE a human is paged.

The PagerDuty webhook path reacts to an alert someone already filed. This closes the
loop the other way: periodically evaluate each service's error-budget burn rate and,
when one crosses the page-worthy threshold (Google SRE multi-window burn), auto-start
an investigation — reactive → proactive.

Deduped by a per-service cooldown so a sustained burn doesn't spawn a fresh
investigation every tick. The decision logic is pure/testable; the loop is a
background asyncio task started from the API lifespan when COPILOT_SLO_POLLER is
enabled (OFF by default — it spends tokens autonomously).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

log = logging.getLogger("devcopilot.slo")


def is_page_worthy(burn: dict) -> bool:
    """True when a burn-rate result is page-worthy (verdict starts with 'page').
    Matches app/mcp/servers/datadog/server.py::_burn_rate verdict wording."""
    return str(burn.get("verdict", "")).lower().startswith("page")


def select_alerts(services: list[str], burn_fn: Callable[[str], dict]) -> list[dict]:
    """Evaluate each service's burn rate and return the page-worthy ones. Pure over
    the injected `burn_fn`; a service whose burn read errors/raises is skipped."""
    alerts: list[dict] = []
    for svc in services:
        try:
            burn = burn_fn(svc)
        except Exception:  # noqa: BLE001 — one bad service must not stop the sweep
            log.warning("burn-rate read failed for %s", svc, exc_info=True)
            continue
        if isinstance(burn, dict) and not burn.get("error") and is_page_worthy(burn):
            alerts.append({"service": svc, "burn": burn})
    return alerts


class SLOPoller:
    """Periodically sweeps services for page-worthy burn and fires `trigger_fn`,
    honoring a per-service cooldown."""

    def __init__(
        self,
        services_fn: Callable[[], list[str]],
        burn_fn: Callable[[str], dict],
        trigger_fn: Callable[[str, dict], Awaitable[None]],
        interval: int = 300,
        cooldown: int = 3600,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._services_fn = services_fn
        self._burn_fn = burn_fn
        self._trigger_fn = trigger_fn
        self.interval = max(10, interval)
        self.cooldown = max(0, cooldown)
        self._clock = clock
        self._last_fired: dict[str, float] = {}

    def _due(self, service: str, now: float) -> bool:
        last = self._last_fired.get(service)
        return last is None or (now - last) >= self.cooldown

    async def tick(self, now: float | None = None) -> list[str]:
        """One sweep: fire (and record) for each page-worthy service off cooldown.
        Returns the services fired this tick. Never raises for a single failure."""
        now = self._clock() if now is None else now
        fired: list[str] = []
        for alert in select_alerts(self._services_fn(), self._burn_fn):
            svc = alert["service"]
            if not self._due(svc, now):
                continue
            self._last_fired[svc] = now
            try:
                await self._trigger_fn(svc, alert["burn"])
                fired.append(svc)
            except Exception:  # noqa: BLE001 — a trigger failure must not kill the loop
                log.exception("SLO investigation trigger failed for %s", svc)
        return fired

    async def run(self) -> None:
        """Background loop: tick, sleep, forever (until cancelled)."""
        log.info("SLO poller started (interval=%ss, cooldown=%ss)", self.interval, self.cooldown)
        while True:
            try:
                fired = await self.tick()
                if fired:
                    log.info("SLO poller opened investigations for: %s", ", ".join(fired))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("SLO poll tick failed")
            await asyncio.sleep(self.interval)
