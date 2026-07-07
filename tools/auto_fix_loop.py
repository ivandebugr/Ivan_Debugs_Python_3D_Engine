#!/usr/bin/env python3
"""
auto_fix_loop.py — Autonomous bug-fix loop for Ivan's 3D Engine.

WHAT THIS DOES
  1. Refuses to run on main/master — creates (or reuses) a disposable
     branch named auto-fix/<timestamp>.
  2. Runs each smoke-test scenario (tests/smoke_test_harness.py) in its
     own subprocess.
  3. If a scenario fails, builds a structured Claude Code prompt from the
     captured crash signature + output, embedding CLAUDE.md's hard rules
     and the relevant skills, and invokes `claude -p` in headless mode to
     fix it.
  4. Re-runs the SAME scenario. Compares the crash signature:
       - clean now              -> commit, move to next scenario
       - different signature    -> treat as a new bug, loop again (counts
                                    against the iteration cap)
       - identical signature    -> the fix attempt didn't work; stop and
                                    escalate to you rather than loop forever
  5. Commits a checkpoint before every Claude Code invocation so each step
     is independently revertible with `git revert` / `git reset`.
  6. NEVER merges to main. NEVER force-pushes. Stops and prints a summary
     + the branch name for you to review by hand.

SAFETY MODEL
  - All Claude Code invocations run with --dangerously-skip-permissions
    because this is unattended — that flag is only safe because step 1
    guarantees we are never on a protected branch, and step 5 guarantees
    every change is a separate, revertible commit.
  - --allowedTools is scoped to Read/Edit/Write/Bash — Bash is required
    because fixes sometimes need to touch logs/session files, but you
    should tighten this further once you've watched it run a few times.
  - --max-turns and --max-budget-usd cap a single Claude Code invocation;
    MAX_ITERATIONS caps the whole loop. Both must be exceeded before this
    script gives up and escalates — it will not loop forever.

USAGE
  python3 tools/auto_fix_loop.py
  python3 tools/auto_fix_loop.py --scenario win_then_r
  python3 tools/auto_fix_loop.py --max-iterations 8 --max-budget-usd 2.00
  python3 tools/auto_fix_loop.py --base-branch develop   # if you don't
                                                             # use 'main'
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs" / "auto_fix_runs"

PROTECTED_BRANCHES = {"main", "master"}

DEFAULT_SCENARIOS = [
    "load_and_close",
    "win_then_r",
    "gameover_then_r",
    "editor_f5_roundtrip",
]


def sh(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, **kwargs)


def current_branch() -> str:
    r = sh(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    return r.stdout.strip()


def ensure_safe_branch(base_branch: str) -> str:
    """Refuse to run on a protected branch. Create/checkout a disposable
    auto-fix branch off the current HEAD."""
    branch = current_branch()
    if branch in PROTECTED_BRANCHES or branch == base_branch:
        new_branch = f"auto-fix/{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        print(f"[safety] currently on '{branch}' — creating '{new_branch}' instead")
        r = sh(["git", "checkout", "-b", new_branch])
        if r.returncode != 0:
            print(r.stderr)
            sys.exit(1)
        return new_branch
    print(f"[safety] already on a non-protected branch: '{branch}' — reusing it")
    return branch


def ensure_clean_tree(force: bool):
    r = sh(["git", "status", "--porcelain"])
    if r.stdout.strip() and not force:
        print(
            "[safety] working tree has uncommitted changes. Commit/stash them "
            "first, or re-run with --force to let the loop commit them as the "
            "starting checkpoint."
        )
        sys.exit(1)
    if r.stdout.strip() and force:
        sh(["git", "add", "-A"])
        sh(["git", "commit", "-m", "auto-fix: checkpoint before loop start"])


def run_scenario(scenario: str) -> tuple[bool, str, str]:
    """Returns (passed, signature, raw_output)."""
    r = sh([sys.executable, "-m", "tests.smoke_test_harness", scenario])
    out = r.stdout
    passed = r.returncode == 0
    signature = ""
    for line in out.splitlines():
        if line.startswith("SMOKE_SIGNATURE:"):
            signature = line[len("SMOKE_SIGNATURE:"):].strip()
    return passed, signature, out


def build_fix_prompt(scenario: str, signature: str, raw_output: str) -> str:
    """Same structure as the manually-written fix prompts in this
    project's history: read CLAUDE.md, load focused skills, give the
    crash, give a diagnosis path, require the smoke scenario to pass
    before considering the fix done."""
    tail = "\n".join(raw_output.splitlines()[-60:])
    return f"""Read CLAUDE.md in full. Then load:
  engineering/focused-fix/SKILL.md
  engineering-team/code-reviewer/SKILL.md
  engineering-team/senior-architect/SKILL.md

This is an autonomous fix session triggered by the smoke-test harness
(tests/smoke_test_harness.py), scenario "{scenario}". Do not ask
clarifying questions — diagnose from the captured output below, make a
reasonable assumption if anything is ambiguous, and note the assumption
in a code comment.

---

CRASH SIGNATURE: {signature}

CAPTURED OUTPUT (tail):
{tail}

---

DIAGNOSIS:
1. Locate the root cause using the file:line references in the captured
   output above. Read the surrounding function fully before changing
   anything — do not patch symptoms.
2. Check whether this matches a known class of bug already documented in
   CLAUDE.md (e.g. destroyed-entity access, NodePath double-removal,
   setBin called on the wrong object type, stale lambda closures). If so,
   apply the same established fix pattern rather than inventing a new one.
3. Fix only the root cause. Do not refactor unrelated code in this pass.

CONSTRAINTS (from CLAUDE.md — never violate):
  - Never check entity.name == 'enemy' — use can_hit() or isinstance()
  - Never pass classes to raycast(ignore=) — instances only
  - Never call destroy() on a managed entity — call self.die()
  - Never toggle entity.enabled on pooled bullets — park at y=-10000
  - Never add a 4th collision authority
  - Use except Exception as e: — never bare except:
  - setBin/setDepthTest/setDepthWrite on NodePath, never on PandaNode .node()

---

VERIFICATION (must pass before you consider this done):
  python3 -m tests.smoke_test_harness {scenario}
  This must print "SMOKE_RESULT: PASS {scenario}" and exit 0.
  If it still fails, keep iterating on the SAME root cause — do not move
  on to unrelated code.

Update CLAUDE.md with a one-line note on what was fixed and why, in the
same style as existing entries. Do not bump the version number — that is
handled by the human reviewing this branch.
"""


def invoke_claude_headless(prompt: str, max_turns: int, max_budget_usd: float) -> dict:
    cmd = [
        "claude",
        "-p", prompt,
        "--agent", "auto-fixer",
        "--allowedTools", "Read,Edit,Write,Bash",
        "--max-turns", str(max_turns),
        "--max-budget-usd", str(max_budget_usd),
        "--output-format", "json",
        "--dangerously-skip-permissions",
    ]
    r = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"error": "non-json output", "raw_stdout": r.stdout, "raw_stderr": r.stderr}


def commit_checkpoint(message: str):
    sh(["git", "add", "-A"])
    r = sh(["git", "commit", "-m", message])
    return r.returncode == 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scenario", action="append", dest="scenarios",
                     help="Scenario name to run (repeatable). Default: all.")
    ap.add_argument("--max-iterations", type=int, default=5,
                     help="Max fix attempts PER SCENARIO before escalating.")
    ap.add_argument("--max-turns", type=int, default=8,
                     help="Claude Code --max-turns per invocation.")
    ap.add_argument("--max-budget-usd", type=float, default=1.50,
                     help="Claude Code --max-budget-usd per invocation.")
    ap.add_argument("--base-branch", default="main",
                     help="Protected branch name to refuse running on.")
    ap.add_argument("--force", action="store_true",
                     help="Allow starting with a dirty working tree "
                          "(commits it as the starting checkpoint).")
    args = ap.parse_args()

    PROTECTED_BRANCHES.add(args.base_branch)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    run_log_path = LOG_DIR / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"

    def log(event: dict):
        event["ts"] = time.time()
        with open(run_log_path, "a") as f:
            f.write(json.dumps(event) + "\n")
        print(f"[log] {event.get('type', 'event')}: { {k:v for k,v in event.items() if k not in ('raw_output','prompt')} }")

    ensure_clean_tree(force=args.force)
    branch = ensure_safe_branch(args.base_branch)
    log({"type": "start", "branch": branch})

    scenarios = args.scenarios or DEFAULT_SCENARIOS
    overall_ok = True

    for scenario in scenarios:
        print(f"\n=== Scenario: {scenario} ===")
        last_signature = None

        for iteration in range(1, args.max_iterations + 1):
            passed, signature, raw_output = run_scenario(scenario)
            log({"type": "scenario_run", "scenario": scenario,
                 "iteration": iteration, "passed": passed,
                 "signature": signature})

            if passed:
                print(f"  iteration {iteration}: PASS")
                if iteration > 1:
                    commit_checkpoint(
                        f"auto-fix: {scenario} fixed (iteration {iteration})"
                    )
                break

            print(f"  iteration {iteration}: FAIL — {signature}")

            if signature == last_signature:
                print(
                    f"  [escalate] same crash signature persisted after a fix "
                    f"attempt. Stopping this scenario — needs a human look.\n"
                    f"  Branch: {branch}\n"
                    f"  Run log: {run_log_path}"
                )
                log({"type": "escalate", "scenario": scenario,
                     "reason": "same_signature_after_fix", "signature": signature})
                overall_ok = False
                break

            last_signature = signature

            prompt = build_fix_prompt(scenario, signature, raw_output)
            log({"type": "invoking_claude", "scenario": scenario,
                 "iteration": iteration, "prompt": prompt})

            result = invoke_claude_headless(prompt, args.max_turns, args.max_budget_usd)
            log({"type": "claude_result", "scenario": scenario,
                 "iteration": iteration, "result": result})

            if "error" in result:
                print(f"  [error] claude invocation returned non-JSON output: "
                      f"{result.get('error')}")
                overall_ok = False
                break

            cost = result.get("cost_usd", "?")
            print(f"  claude invocation done (cost: ${cost})")
            commit_checkpoint(
                f"auto-fix: attempt {iteration} for {scenario} ({signature})"
            )

        else:
            print(f"  [escalate] hit max-iterations ({args.max_iterations}) "
                  f"without a clean pass for '{scenario}'.")
            log({"type": "escalate", "scenario": scenario,
                 "reason": "max_iterations_exceeded"})
            overall_ok = False

    print(f"\n=== Loop complete. Branch: {branch} ===")
    print(f"Run log: {run_log_path}")
    if overall_ok:
        print("All scenarios passed. Review the diff and merge by hand:")
        print(f"  git diff {args.base_branch}..{branch}")
    else:
        print("One or more scenarios needed escalation. Nothing was merged.")
        print("Review the branch and run log before deciding next steps.")
    sys.exit(0 if overall_ok else 1)


if __name__ == "__main__":
    main()
