"""Sandbox counterfactual runner. Uses tiny shell reproducers (grep/test) instead
of the bundled node reproducer so the unit tests are fast and dependency-light —
the runner itself is command-agnostic."""

from pathlib import Path

from app.graph.sandbox import run_counterfactual

_PATCH_FIX = "--- a/app.txt\n+++ b/app.txt\n@@ -1 +1 @@\n-buggy\n+fixed\n"
_PATCH_WRONG = "--- a/app.txt\n+++ b/app.txt\n@@ -1 +1 @@\n-buggy\n+other\n"


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.txt").write_text("buggy\n")
    return repo


def test_resolved_when_repro_fails_then_passes(tmp_path):
    repo = _make_repo(tmp_path)
    # `grep -q fixed` fails on "buggy" (reproduces), passes once patched to "fixed".
    out = run_counterfactual(repo, _PATCH_FIX, "grep -q fixed app.txt")
    assert out["verdict"] == "resolved"
    assert out["baseline_failed"] is True
    assert out["patched_passed"] is True
    assert out["applied"] is True


def test_not_resolved_when_repro_still_fails_after_patch(tmp_path):
    repo = _make_repo(tmp_path)
    out = run_counterfactual(repo, _PATCH_WRONG, "grep -q fixed app.txt")
    assert out["verdict"] == "not_resolved"
    assert out["baseline_failed"] is True
    assert out["patched_passed"] is False


def test_no_repro_when_baseline_already_passes(tmp_path):
    repo = _make_repo(tmp_path)
    # This command passes even on the buggy code, so it doesn't reproduce anything.
    out = run_counterfactual(repo, _PATCH_FIX, "test -f app.txt")
    assert out["verdict"] == "no_repro"
    assert out["baseline_failed"] is False


def test_patch_failed_on_malformed_diff(tmp_path):
    repo = _make_repo(tmp_path)
    out = run_counterfactual(repo, "this is not a valid diff", "grep -q fixed app.txt")
    assert out["verdict"] == "patch_failed"
    assert out["applied"] is False


def test_no_patch_skips_sandbox(tmp_path):
    repo = _make_repo(tmp_path)
    out = run_counterfactual(repo, "", "grep -q fixed app.txt")
    assert out["verdict"] == "no_patch"
    assert out["ran"] is False


def test_missing_repo_is_reported_not_raised(tmp_path):
    out = run_counterfactual(tmp_path / "does-not-exist", _PATCH_FIX, "true")
    assert out["verdict"] == "error"
    assert "not found" in out["detail"]


def test_timeout_is_contained(tmp_path):
    repo = _make_repo(tmp_path)
    # A baseline that hangs is killed by the timeout (rc != 0 -> counts as failing),
    # proving the wall-clock guard works without hanging the suite.
    out = run_counterfactual(repo, _PATCH_FIX, "sleep 5", timeout=1)
    assert out["ran"] is True
    assert out["baseline_failed"] is True  # timed-out baseline is treated as a failure
