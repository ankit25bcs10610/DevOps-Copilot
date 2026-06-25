"""Incident-memory similarity search (BM25 over the bundled corpus)."""

from app.mcp.servers.memory import server as mem


def test_bm25_ranks_matching_incident_first():
    records = [
        {"title": "checkout 5xx after discount deploy", "summary": "TypeError applyDiscount",
         "root_cause": "null deref", "tags": ["checkout", "discount", "5xx"]},
        {"title": "inventory latency", "summary": "slow query", "root_cause": "missing index",
         "tags": ["latency", "database"]},
    ]
    ranked = mem.bm25_rank("checkout 5xx discount applyDiscount", records)
    assert ranked[0]["title"].startswith("checkout")
    assert ranked[0]["score"] > ranked[1]["score"]


def test_search_incidents_finds_the_discount_precedent():
    hits = mem.search_incidents("checkout-svc 5xx TypeError applyDiscount discount", limit=3)
    assert hits, "expected a prior-incident match for the discount regression"
    top = hits[0]
    assert "discount" in top["title"].lower()
    assert top["runbook"]  # the reusable fix steps come back
    assert "root_cause" in top


def test_search_incidents_returns_nothing_on_no_overlap():
    # No lexical overlap -> empty, not an arbitrary "closest" record.
    assert mem.search_incidents("xylophone unicorn zzzznomatch") == []


def test_get_incident_record_roundtrip_and_miss():
    rec = mem.get_incident_record("INC-2026-0412")
    assert rec["service"] == "checkout-svc"
    assert "error" in mem.get_incident_record("does-not-exist")
