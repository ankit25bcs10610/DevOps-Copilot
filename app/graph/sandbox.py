"""Sandbox counterfactual: PROVE a proposed fix resolves the incident.

The verify node reasons about whether a fix *should* address the root cause; this
module actually demonstrates it — apply the proposed patch to a throwaway copy of
the target repo and run a reproducer command, checking the failing behaviour is
gone.

Contained by construction:
  - operates on a `tempfile` copy, never the real repo;
  - a wall-clock timeout on every subprocess;
  - the reproducer command is operator-configured (`COPILOT_SANDBOX_CMD`), not
    model-supplied — only the *patch* comes from the agent, applied to known source.

Because it executes the model's patch, it is OFF by default (`COPILOT_SANDBOX_VERIFY`).

Verdicts:
  resolved      — reproducer FAILED before the patch and PASSES after it (proof).
  not_resolved  — reproducer still fails after the patch (proof it doesn't work).
  no_repro      — reproducer passed even before the patch (it doesn't reproduce the bug).
  patch_failed  — the patch could not be applied.
  no_patch      — no machine-applicable patch was provided (sandbox skipped).
  error         — the sandbox itself failed (never fatal to the run).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger("devcopilot.sandbox")

_MAX_OUT = 2000  # cap captured output so a chatty test can't bloat the report/context


def _run(cmd: str | list[str], cwd: str | Path, timeout: int) -> tuple[int, str]:
    """Run a command, returning (returncode, combined-output). Never raises."""
    try:
        proc = subprocess.run(  # noqa: S603 — cmd is operator-configured, cwd is a temp copy
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=isinstance(cmd, str),
        )
        return proc.returncode, (proc.stdout + proc.stderr)[-_MAX_OUT:]
    except subprocess.TimeoutExpired:
        return 124, "(timed out)"
    except Exception as exc:  # noqa: BLE001 — a runner failure must not break the run
        return 999, f"(runner error: {exc})"


def _apply_patch(patch: str, cwd: Path, timeout: int = 20) -> tuple[bool, str]:
    """Apply a unified-diff patch to the working tree at `cwd`. Tries a few strip
    levels / tools since LLM-produced diffs vary. Returns (applied, last_error)."""
    patch_file = cwd / "_copilot_fix.patch"
    patch_file.write_text(patch if patch.endswith("\n") else patch + "\n")
    last = ""
    for cmd in (
        ["git", "apply", "--whitespace=nowarn", str(patch_file)],
        ["git", "apply", "-p0", "--whitespace=nowarn", str(patch_file)],
        ["patch", "-p1", "-i", str(patch_file)],
    ):
        rc, out = _run(cmd, cwd, timeout)
        if rc == 0:
            patch_file.unlink(missing_ok=True)
            return True, ""
        last = out
    patch_file.unlink(missing_ok=True)
    return False, last


def run_counterfactual(repo_path: str | Path, patch: str, cmd: str, timeout: int = 30) -> dict:
    """Apply `patch` to a temp copy of `repo_path` and run `cmd` before and after,
    returning a structured verdict (see module docstring). Pure of side effects on
    the real repo; safe to call on any input."""
    result: dict = {
        "ran": False,
        "applied": False,
        "baseline_failed": None,
        "patched_passed": None,
        "verdict": "error",
        "detail": "",
        "cmd": cmd,
    }
    if not patch or not str(patch).strip():
        result["verdict"] = "no_patch"
        result["detail"] = "No machine-applicable patch was provided; sandbox skipped."
        return result
    src = Path(repo_path)
    if not src.is_dir():
        result["detail"] = f"target repo not found: {repo_path}"
        return result

    tmp: str | None = None
    try:
        tmp = tempfile.mkdtemp(prefix="copilot_sandbox_")
        dst = Path(tmp) / "repo"
        shutil.copytree(src, dst)
        result["ran"] = True

        # 1) Baseline — the reproducer MUST fail on the unpatched code, else it
        #    doesn't actually reproduce the incident and can prove nothing.
        base_rc, _ = _run(cmd, dst, timeout)
        result["baseline_failed"] = base_rc != 0

        # 2) Apply the proposed patch.
        applied, apply_err = _apply_patch(patch, dst)
        result["applied"] = applied
        if not applied:
            result["verdict"] = "patch_failed"
            result["detail"] = f"could not apply the proposed patch: {apply_err[:400]}"
            return result

        # 3) Post-fix — the reproducer must now pass.
        fix_rc, fix_out = _run(cmd, dst, timeout)
        result["patched_passed"] = fix_rc == 0

        if not result["baseline_failed"]:
            result["verdict"] = "no_repro"
            result["detail"] = (
                "Reproducer passed even before the fix — it does not reproduce the incident, "
                "so a pass afterwards proves nothing."
            )
        elif result["patched_passed"]:
            result["verdict"] = "resolved"
            result["detail"] = "Reproducer failed before the fix and passes after it."
        else:
            result["verdict"] = "not_resolved"
            result["detail"] = f"Reproducer still fails after the fix: {fix_out[:400]}"
        return result
    except Exception as exc:  # noqa: BLE001 — sandbox must never break a finished run
        log.exception("sandbox counterfactual failed")
        result["detail"] = f"sandbox error: {exc}"
        return result
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
