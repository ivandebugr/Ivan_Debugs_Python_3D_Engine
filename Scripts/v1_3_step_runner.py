#!/usr/bin/env python3
"""
v1_3_step_runner.py — Orchestrator for v1.3 asset-import-pipeline Steps 4-7.

Extends the existing auto-fix tooling (Scripts/auto_fix_loop.py,
tests/smoke_test_harness.py) rather than replacing it. This script handles
the FEATURE side (generate -> implement -> verify -> human checkpoint ->
vault update -> commit); Scripts/auto_fix_loop.py remains the BUG side
(crash signature -> fix -> re-verify). They are not merged: a feature step
here may itself fail and need a fix, but that fix is driven by this
script's own retry loop (PART 3), not by auto_fix_loop.py, because feature
verification here is "did the assertable test pass", not "does a captured
crash signature still reproduce".

WORKFLOW (one step at a time):

  python3 scripts/v1_3_step_runner.py --step 5
      -> generates the implementation prompt for Step 5 from
         docs/v1.3-asset-import-pipeline.md
      -> invokes Claude Code headlessly to implement it
      -> parses the assertable test results from its output
      -> on failure: builds a fix prompt, retries (Sonnet x2, then Opus),
         hard stop after 3 total attempts
      -> on pass: prints the PART 4 manual checklist and EXITS. Does not
         proceed further. No flag skips this checkpoint.

  python3 scripts/v1_3_step_runner.py --step 5 --step-confirmed
      -> (only after you've manually verified the checklist above)
      -> runs the vault-update prompt scoped to this step's diff
      -> commits (one commit if step 3 needed no real fix beyond the
         clean implementation; two if it did — impl commit + fix commit)

Every attempt's diagnosis is appended to docs/auto-fix-system-README.md
in the same log format used there. That file does not exist until the
first run of this script creates it (see _ensure_readme_log below) —
it's a new log, not a pre-existing doc this script reads from.

Per-step Claude Code invocation budget: 1 (generation) + up to 3 (fix
attempts) + 1 (vault update on --step-confirmed) = 5 max. The script
prints a cost/invocation-count summary on every exit given the
post-June-15 headless billing pool change noted in CLAUDE.md.

NOTE: I could not find a "post-June-15 headless billing pool concern" in
CLAUDE.md, CHANGELOG.md, or docs/ at the time this script was written —
the prompt that requested this script referenced one. The cost/attempt
summary is implemented regardless (it's good practice for any headless
loop spending real budget), but if you had a specific cap or warning
threshold in mind that isn't captured here, tell me the number and I'll
wire it in as an actual gate rather than just a printed summary.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SPEC_DOC = PROJECT_ROOT / "docs" / "v1.3-asset-import-pipeline.md"
README_LOG = PROJECT_ROOT / "docs" / "auto-fix-system-README.md"
CLAUDE_MD = PROJECT_ROOT / "CLAUDE.md"

MAX_FIX_ATTEMPTS = 3  # hard stop; attempts 1-2 Sonnet, attempt 3 Opus if needed


# ---------------------------------------------------------------------------
# PART 1 — Step config
# ---------------------------------------------------------------------------
#
# Spec section text is NOT hand-copied here — _load_spec_section() reads it
# from docs/v1.3-asset-import-pipeline.md at runtime by locating the
# "### N. <title>" heading and slicing to the next "### " or "## " heading.
# If the doc changes, the prompt changes with it automatically.

STEP_CONFIGS = {
    4: {
        "title": "Texture picker",
        # NOTE: the doc's "Implementation Order" list (step 4) and its
        # "Features" list (§5) use different numbering for the same
        # feature — this pattern targets the Features section heading.
        "heading_pattern": r"^### 5\. Texture picker in inspector",
        "smoke_scenario": None,  # no existing smoke_test_harness scenario covers this
        "assertable": [
            "Open the texture picker overlay, click a texture, and assert "
            "exactly one new entry is pushed onto the undo stack "
            "(len(editor.undo_stack) increases by 1, top entry is a "
            "ChangeTextureCommand).",
            "After applying a texture and calling undo_stack.undo(), assert "
            "every previously-selected entity's .texture is restored to its "
            "pre-pick value. After redo(), assert it's back to the picked "
            "texture.",
        ],
        "manual": [
            "Overlay opens when clicking the inspector texture thumbnail, "
            "and closes on a second click / Escape / click-outside.",
            "Click-outside and Escape both feel responsive — no double-fire, "
            "no overlay flash.",
            "While the overlay is open, left-clicking in the 3D viewport "
            "does NOT place a new block (place-tool guard holds even with "
            "the overlay focused).",
        ],
        "note": (
            "Step 4 already has a working implementation in "
            "Scripts/level_editor.py (_build_texture_picker / "
            "open_texture_picker / close_texture_picker, "
            "ChangeTextureCommand wiring at line ~951). This step config is "
            "kept as the calibration step: running it first tells you "
            "whether the assertable-test harness this script adds is "
            "actually exercising the real undo-stack behaviour correctly, "
            "before you trust it on Steps 5-7 where nothing exists yet. If "
            "Step 4 reports the assertable tests pass on the FIRST attempt "
            "with no implementation changes, that's expected — verify only."
        ),
    },
    5: {
        "title": "Model picker",
        "heading_pattern": r"^### 6\. Model picker in inspector",
        "smoke_scenario": None,
        "assertable": [
            "Open the model picker overlay, click a model, and assert "
            "exactly one new entry is pushed onto the undo stack (top entry "
            "is a ChangeModelCommand — this command type does not exist yet "
            "in Scripts/undo_redo.py and must be added following the "
            "existing ChangeTextureCommand pattern).",
            "After applying a model and calling undo_stack.undo(), assert "
            "every selected BLOCK entity's .model is restored to its "
            "pre-pick value; after redo(), assert it's back to the picked "
            "model.",
            "Assert the model picker is unavailable (no-op or disabled) "
            "when the current selection contains only enemy entities — the "
            "spec defers enemy model swapping to v1.4.",
        ],
        "manual": [
            "Overlay opens/closes the same way the texture picker does "
            "(click thumbnail, click-outside, Escape).",
            "No stray block placement while the overlay is open.",
            "Visually distinct from the texture picker overlay so it's "
            "clear which field is being edited.",
        ],
        "note": None,
    },
    6: {
        "title": "Drag-and-drop import",
        # Same numbering mismatch as Step 4 — Features section heading is §4.
        "heading_pattern": r"^### 4\. Drag-and-drop import",
        "smoke_scenario": None,
        "assertable": [
            "Simulate a drop-files event with a .png path; assert the file "
            "is copied (not moved — source must still exist) into "
            "assets/textures/, and assert asset_registry.rebuild() was "
            "called (manifest.json mtime/entry updated).",
            "Simulate a drop with an unsupported extension (e.g. .txt); "
            "assert no file is copied into any assets/ subfolder and no "
            "exception propagates.",
            "Simulate drops for a .obj and a .wav path; assert each routes "
            "to assets/models/ and assets/sounds/ respectively.",
        ],
        "manual": [
            "Dragging a real file from Finder onto the running editor "
            "window triggers the drop (the smoke test can only simulate "
            "the Panda3D event, not actual OS-level drag-and-drop).",
            "The 2-second success toast appears and disappears cleanly.",
            "The red error notification for an unsupported extension reads "
            "clearly and does not block further interaction (no modal).",
        ],
        "note": (
            "Depends on the asset browser already working for immediate "
            "feedback per the spec's Implementation Order — confirm the "
            "browser panel (Scripts/level_editor.py _build_asset_browser) "
            "refreshes its thumbnails after asset_registry.rebuild() before "
            "marking this step's automated test trustworthy."
        ),
    },
    7: {
        "title": "level.json model field",
        "heading_pattern": r"^### 7\. `level\.json` model field",
        "smoke_scenario": "load_and_close",
        "assertable": [
            "Round-trip test: build a level.json block entry with an "
            "explicit 'model' field pointing at a real file under "
            "assets/models/, save and reload via Scripts/level_io.py's "
            "load_level_data(), assert the field survives unchanged.",
            "Backwards-compatibility test: load an existing level.json "
            "block entry that has NO 'model' field, assert it normalises to "
            "'cube' (the documented default) without raising.",
            "Run the existing smoke scenario `load_and_close` "
            "(tests/smoke_test_harness.py) against a level.json containing "
            "a mix of model-field and no-model-field block entries; assert "
            "SMOKE_RESULT: PASS load_and_close.",
        ],
        "manual": [
            "Visually confirm a block placed with a non-cube model (e.g. "
            "an .obj from assets/models/) actually renders that geometry in "
            "both the editor and a real `python main.py` playthrough, not "
            "just a cube with the field silently ignored.",
        ],
        "note": (
            "This step touches both main.py's load_level() and "
            "level_editor.py's load_existing_level() per the spec — the "
            "assertable round-trip test above only exercises "
            "Scripts/level_io.py's load_level_data(), which CLAUDE.md notes "
            "is the single source of truth both call sites already use. "
            "Confirm that's still true before trusting the test as full "
            "coverage of both call sites."
        ),
    },
}

ALL_STEPS = (4, 5, 6, 7)


def _load_spec_section(heading_pattern: str) -> str:
    """Read docs/v1.3-asset-import-pipeline.md at runtime and slice out the
    section whose heading matches heading_pattern, up to the next heading of
    equal-or-higher level. Never hand-paraphrased — the doc is the source of
    truth, this just locates and extracts."""
    text = SPEC_DOC.read_text()
    lines = text.splitlines()
    start = None
    start_level = None
    for i, line in enumerate(lines):
        if re.match(heading_pattern, line):
            start = i
            start_level = len(line) - len(line.lstrip("#"))
            break
    if start is None:
        raise RuntimeError(
            f"Could not find heading matching {heading_pattern!r} in "
            f"{SPEC_DOC}. The doc may have been restructured — update "
            f"STEP_CONFIGS in this script."
        )
    end = len(lines)
    for j in range(start + 1, len(lines)):
        m = re.match(r"^(#{1,6})\s", lines[j])
        if m and len(m.group(1)) <= start_level:
            end = j
            break
    return "\n".join(lines[start:end]).strip()


def get_step_config(step_num: int) -> dict:
    if step_num not in STEP_CONFIGS:
        raise ValueError(f"Unknown step {step_num}. Known steps: {ALL_STEPS}")
    cfg = dict(STEP_CONFIGS[step_num])
    cfg["step_num"] = step_num
    cfg["spec_section"] = _load_spec_section(cfg["heading_pattern"])
    return cfg


# ---------------------------------------------------------------------------
# PART 2 — Implementation prompt generation
# ---------------------------------------------------------------------------

CONTEXT_FILES = [
    "CLAUDE.md",
    "docs/v1.3-asset-import-pipeline.md",
    "Scripts/asset_registry.py",
    "Scripts/level_editor.py",
    "Scripts/undo_redo.py",
    "Scripts/level_io.py",
    "tests/smoke_test_harness.py",
]

HARD_CONSTRAINTS = """\
  - Never check entity.name == 'enemy' for damage dispatch — use can_hit() or isinstance()
    (structural names like 'level_block'/'level_enemy' in cleanup code are fine)
  - Never pass classes to raycast(ignore=...) — instances only
  - Never call destroy() on an AliveEntity directly — call self.die()
  - swept_cast() does not exist — do not re-add it or any new global sweep function
  - Pool bullets: never toggle .enabled — park at y=-10000 instead
  - _patch_shaders_to_glsl120() must run before any Entity is created
  - setBin/setDepthTest/setDepthWrite go on the Entity (NodePath), never on entity.node()
  - self._tool in LevelEditor is always 'move' or 'place' — place-mode guards must hold
  - Never read e.name (or any NodePath property) on an entity destroyed earlier in the same
    synchronous call — filter with `not e.is_empty()` first
  - Gameplay entity input() handlers must early-return when game.state != Game.PLAYING
  - UI element size/position must be computed in _apply_layout() relative to available
    space — never hardcoded for a single aspect ratio
  - Never construct/set a Text entity's .text to '' or a literal '<'/'>' with tag parsing
    on — use enabled=False/True to hide/show, use_tags=False for literal angle-bracket glyphs
  - color.rgb() expects 0-1 floats, not 0-255
  - Use except Exception: not bare except:
  - destroy(entity) is deferred (end-of-frame flush) — fine for plain UI entities, but
    AliveEntities must go through die()
"""


def build_implementation_prompt(cfg: dict) -> str:
    step_num = cfg["step_num"]
    assertable_list = "\n".join(f"   {i+1}. {a}" for i, a in enumerate(cfg["assertable"]))
    manual_list = "\n".join(f"   - {m}" for m in cfg["manual"])
    note = f"\n\nNOTE FROM THE ORCHESTRATOR: {cfg['note']}" if cfg.get("note") else ""
    context_block = "\n".join(f"  - {f}" for f in CONTEXT_FILES)
    smoke_line = (
        f"\nAlso run: python3 -m tests.smoke_test_harness {cfg['smoke_scenario']}\n"
        f"This must print \"SMOKE_RESULT: PASS {cfg['smoke_scenario']}\" and exit 0."
        if cfg.get("smoke_scenario")
        else ""
    )

    return f"""Read these files in full before changing anything:
{context_block}

This is an autonomous implementation session for v1.3 Step {step_num}
("{cfg['title']}"), orchestrated by scripts/v1_3_step_runner.py. Do not ask
clarifying questions — if something in the spec section below is
ambiguous, make the most consistent choice with the existing codebase
patterns (e.g. the already-shipped texture picker in Scripts/level_editor.py)
and note the assumption in your final output.

---

SPEC SECTION (from docs/v1.3-asset-import-pipeline.md — authoritative):

{cfg['spec_section']}
{note}

---

PART 1 — Implement

Build this feature following the spec section above. Match the existing
code style and patterns already in the file you're editing (e.g. if a
texture picker already exists, the model picker should mirror its
structure rather than inventing a new one).

PART 2 — Constraints (from CLAUDE.md — never violate)

{HARD_CONSTRAINTS}

PART 3 — Assertable tests (must pass before you report done)

Write and run a small standalone test (a script under tests/, or inline
in your verification — your choice of mechanism, but it must be
re-runnable and must not require manual interaction) that checks:
{assertable_list}
{smoke_line}

PART 4 — Output

Report back in this exact structure:
  SUMMARY: <2-4 sentences on what you implemented and where>
  ASSERTABLE_TEST_RESULTS:
    <for each numbered assertable test above, one line: PASS or FAIL plus
    a one-line reason if FAIL>
  FILES_CHANGED: <list>
  ASSUMPTIONS: <anything you had to decide because the spec didn't say,
    or "none">

Update CLAUDE.md's module map / known tech debt table with a short note
if this step changes either (follow the existing terse style — do not
rewrite surrounding entries). Do not bump the version number.

---

MANUAL VERIFICATION (do NOT attempt to automate or simulate these — they
are explicitly out of scope for this session and will be checked by a
human after you finish):
{manual_list}
"""


# ---------------------------------------------------------------------------
# PART 3 — Execution + retry loop
# ---------------------------------------------------------------------------

RESULT_LINE_RE = re.compile(r"^\s*(\d+)\.\s*(PASS|FAIL)\b(.*)$", re.IGNORECASE)


def invoke_claude_headless(prompt: str, model: str | None, max_turns: int = 12) -> dict:
    cmd = [
        "claude",
        "-p", prompt,
        "--allowedTools", "Read,Edit,Write,Bash",
        "--max-turns", str(max_turns),
        "--output-format", "json",
        "--dangerously-skip-permissions",
    ]
    if model:
        cmd += ["--model", model]
    r = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True)
    try:
        parsed = json.loads(r.stdout)
    except json.JSONDecodeError:
        parsed = {"error": "non-json output", "raw_stdout": r.stdout, "raw_stderr": r.stderr}
    parsed["_returncode"] = r.returncode
    return parsed


def parse_assertable_results(claude_text: str, expected_count: int) -> tuple[bool, list[str]]:
    """Find the ASSERTABLE_TEST_RESULTS block and check every expected line
    is present and says PASS. Returns (all_passed, list of result lines)."""
    lines = []
    in_block = False
    for line in claude_text.splitlines():
        if "ASSERTABLE_TEST_RESULTS" in line:
            in_block = True
            continue
        if in_block:
            if line.strip().upper().startswith("FILES_CHANGED") or line.strip().upper().startswith("ASSUMPTIONS"):
                break
            m = RESULT_LINE_RE.match(line)
            if m:
                lines.append(line.strip())
    if not lines:
        return False, ["no ASSERTABLE_TEST_RESULTS block found in output"]
    all_pass = len(lines) >= expected_count and all("PASS" in l.upper() for l in lines)
    return all_pass, lines


def build_fix_prompt(cfg: dict, attempt: int, prior_summary: str, prior_results: list[str]) -> str:
    """Reuses .claude/agents/auto-fixer.md's diagnosis approach: locate root
    cause from captured output, check against known CLAUDE.md bug classes,
    fix only that, re-verify."""
    results_block = "\n".join(f"  {r}" for r in prior_results)
    return f"""Read CLAUDE.md and .claude/agents/auto-fixer.md in full.

This is fix attempt {attempt} of {MAX_FIX_ATTEMPTS} for v1.3 Step
{cfg['step_num']} ("{cfg['title']}"), orchestrated by
scripts/v1_3_step_runner.py. A prior implementation attempt reported these
assertable test results:

{results_block}

Prior attempt's own summary of what it did:
{prior_summary}

---

DIAGNOSIS (apply .claude/agents/auto-fixer.md's approach: single root
cause per invocation, no unrelated refactors):
1. Re-read the relevant code (the file(s) touched in the prior attempt)
   and the spec section for this step in docs/v1.3-asset-import-pipeline.md.
2. Identify exactly why each FAILing assertable test fails — re-run the
   test yourself to see the real error, do not guess from the summary text.
3. Check whether the failure matches a known bug class already documented
   in CLAUDE.md's Hard Constraints (destroyed-entity access, NodePath
   double-removal, setBin on the wrong object, color.rgb 0-255 vs 0-1,
   stale lambda closures, etc.) and apply the established fix pattern if so.
4. Fix only the failing assertable tests. Do not refactor unrelated code.

CONSTRAINTS (from CLAUDE.md — never violate):
{HARD_CONSTRAINTS}

VERIFICATION: re-run the same assertable tests from the prior attempt and
report back in the SAME structure as before (SUMMARY /
ASSERTABLE_TEST_RESULTS / FILES_CHANGED / ASSUMPTIONS). If you cannot get
all assertable tests to PASS within this turn budget, say so plainly in
SUMMARY rather than reporting a false PASS — the orchestrator escalates to
a human after attempt {MAX_FIX_ATTEMPTS} regardless.
"""


def run_step_with_retries(cfg: dict, dry_run: bool = False) -> dict:
    """Returns a result dict: {passed, attempts: [...], final_text, invocations}"""
    attempts = []
    expected_count = len(cfg["assertable"])

    prompt = build_implementation_prompt(cfg)
    if dry_run:
        return {"passed": None, "attempts": [], "final_text": prompt, "invocations": 0, "dry_run": True}

    print(f"[step {cfg['step_num']}] invoking Claude Code (implementation, Sonnet)...")
    result = invoke_claude_headless(prompt, model="sonnet")
    text = result.get("result", result.get("raw_stdout", ""))
    passed, results_lines = parse_assertable_results(text, expected_count)
    attempts.append({
        "attempt": 1, "role": "implementation", "model": "sonnet",
        "passed": passed, "results": results_lines, "cost_usd": result.get("cost_usd"),
        "error": result.get("error"),
    })
    _append_readme_log(cfg, attempts[-1])

    if passed:
        return {"passed": True, "attempts": attempts, "final_text": text, "invocations": 1}

    prior_summary = _extract_field(text, "SUMMARY")
    for attempt_num in range(2, MAX_FIX_ATTEMPTS + 1):
        model = "opus" if attempt_num == MAX_FIX_ATTEMPTS else "sonnet"
        print(f"[step {cfg['step_num']}] attempt {attempt_num} FAILed previous check — "
              f"invoking Claude Code (fix, {model})...")
        fix_prompt = build_fix_prompt(cfg, attempt_num, prior_summary, results_lines)
        result = invoke_claude_headless(fix_prompt, model=model)
        text = result.get("result", result.get("raw_stdout", ""))
        passed, results_lines = parse_assertable_results(text, expected_count)
        attempts.append({
            "attempt": attempt_num, "role": "fix", "model": model,
            "passed": passed, "results": results_lines, "cost_usd": result.get("cost_usd"),
            "error": result.get("error"),
        })
        _append_readme_log(cfg, attempts[-1])
        if passed:
            return {"passed": True, "attempts": attempts, "final_text": text, "invocations": attempt_num}
        prior_summary = _extract_field(text, "SUMMARY")

    return {"passed": False, "attempts": attempts, "final_text": text, "invocations": MAX_FIX_ATTEMPTS}


def _extract_field(text: str, field: str) -> str:
    m = re.search(rf"{field}:\s*(.+?)(?:\n[A-Z_]+:|\Z)", text, re.DOTALL)
    return m.group(1).strip() if m else "(not found in output)"


# ---------------------------------------------------------------------------
# Log to docs/auto-fix-system-README.md — same format as the existing
# auto-fix system's logs (Scripts/auto_fix_loop.py writes JSONL to
# logs/auto_fix_runs/; this is a separate human-readable log this script
# owns, since the README file itself didn't exist before this script).
# ---------------------------------------------------------------------------

def _ensure_readme_log():
    if README_LOG.exists():
        return
    README_LOG.write_text(
        "# Auto-Fix System Log — v1.3 Step Runner\n\n"
        "Appended to by scripts/v1_3_step_runner.py. Each entry is one "
        "Claude Code invocation (implementation or fix attempt) for one "
        "v1.3 step. This is a feature-implementation log, separate from "
        "Scripts/auto_fix_loop.py's bug-fix loop (which logs JSONL under "
        "logs/auto_fix_runs/).\n\n---\n\n"
    )


def _append_readme_log(cfg: dict, attempt: dict):
    _ensure_readme_log()
    lines = "\n".join(attempt["results"]) if attempt["results"] else "(no results parsed)"
    entry = (
        f"## Step {cfg['step_num']} ({cfg['title']}) — attempt {attempt['attempt']} "
        f"[{attempt['role']}, {attempt['model']}]\n\n"
        f"- Passed: {attempt['passed']}\n"
        f"- Cost (USD): {attempt.get('cost_usd', '?')}\n"
        f"- Error: {attempt.get('error') or 'none'}\n"
        f"- Assertable results:\n```\n{lines}\n```\n\n---\n\n"
    )
    with open(README_LOG, "a") as f:
        f.write(entry)


# ---------------------------------------------------------------------------
# PART 4 — Human checkpoint
# ---------------------------------------------------------------------------

def print_manual_checklist(cfg: dict):
    print()
    print("=" * 72)
    print(f"AUTOMATED TESTS PASSED — Step {cfg['step_num']} ({cfg['title']})")
    print("=" * 72)
    print()
    print("Manual verification required before this step ships. Go run the")
    print("game/editor and confirm each item:")
    print()
    for item in cfg["manual"]:
        print(f"  [ ] {item}")
    print()
    print("This script will NOT proceed further. When you've confirmed all")
    print("of the above by hand, re-run:")
    print()
    print(f"  python3 scripts/v1_3_step_runner.py --step {cfg['step_num']} --step-confirmed")
    print()
    print("to run the vault update and commit.")
    print("=" * 72)


# ---------------------------------------------------------------------------
# PART 5 — Vault update + commit (only on --step-confirmed)
# ---------------------------------------------------------------------------

def build_vault_update_prompt(cfg: dict, diff_summary: str) -> str:
    return f"""Read brain/North Star.md, work/active/v1.3-asset-import-pipeline.md,
brain/Gotchas.md, brain/Patterns.md, brain/Key Decisions.md, and
Memories.md in the obsidian-mind vault (same structure used for the
v1.2.6 vault update).

Scope: ONLY the diff for v1.3 Step {cfg['step_num']} ("{cfg['title']}")
below. Do not re-summarize unrelated project history — this is an
incremental update, not a re-audit.

DIFF SUMMARY FOR THIS STEP:
{diff_summary}

PART 1 — work/active/v1.3-asset-import-pipeline.md: mark this step's
checklist item(s) done, matching the existing terse style.

PART 2 — brain/North Star.md: update current dev focus only if this step
changes what's next (e.g. it was the last of Steps 4-7 and v1.3 is now
feature-complete pending the remaining roadmap items in CLAUDE.md).

PART 3 — brain/Gotchas.md: log any new Ursina/Panda3D footgun discovered
while implementing this step. If none, skip this file entirely (no
placeholder entry).

PART 4 — brain/Patterns.md: log any reusable pattern discovered (e.g. the
overlay-picker pattern if Step 5 generalized it from Step 4's texture
picker). If none, skip.

PART 5 — brain/Key Decisions.md: log any game-design or architecture
decision made while implementing (e.g. how enemy-model-swap deferral to
v1.4 was enforced). If none, skip.

PART 6 — Memories.md: one entry noting this step shipped, in the existing
style, linking to at least one existing note (orphans are bugs per
CLAUDE.md's vault rules).

Only touch files that actually need a change for this step — do not pad
out empty sections.
"""


def run_vault_update(cfg: dict, diff_summary: str) -> dict:
    prompt = build_vault_update_prompt(cfg, diff_summary)
    print(f"[step {cfg['step_num']}] invoking Claude Code (vault update)...")
    result = invoke_claude_headless(prompt, model="sonnet")
    return result


def get_diff_summary() -> str:
    r = subprocess.run(["git", "diff", "--stat", "HEAD"], cwd=PROJECT_ROOT,
                        capture_output=True, text=True)
    staged = subprocess.run(["git", "diff", "--stat", "--cached"], cwd=PROJECT_ROOT,
                             capture_output=True, text=True)
    return (r.stdout + staged.stdout).strip() or "(no uncommitted diff — already committed?)"


def commit_step(cfg: dict, message: str) -> bool:
    subprocess.run(["git", "add", "-A"], cwd=PROJECT_ROOT)
    r = subprocess.run(["git", "commit", "-m", message], cwd=PROJECT_ROOT,
                        capture_output=True, text=True)
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr)
    return r.returncode == 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def print_cost_summary(invocations: int, attempts: list[dict]):
    total_cost = sum(a.get("cost_usd") or 0 for a in attempts if isinstance(a.get("cost_usd"), (int, float)))
    print()
    print("-" * 72)
    print(f"Claude Code invocations this run: {invocations}")
    print(f"Total reported cost: ${total_cost:.4f}" if total_cost else "Total reported cost: unknown (non-numeric/missing cost_usd in output)")
    print("-" * 72)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--step", type=int, required=True, choices=ALL_STEPS,
                     help="Which v1.3 step to run (4-7).")
    ap.add_argument("--step-confirmed", action="store_true",
                     help="You have manually verified the PART 4 checklist. "
                          "Runs vault update + commit. Never skips PART 4 on "
                          "a fresh run — this flag only matters on a re-run "
                          "after the checklist was already printed and you "
                          "confirmed it by hand.")
    ap.add_argument("--dry-run", action="store_true",
                     help="Print the generated implementation prompt and exit "
                          "without invoking Claude Code. For inspecting PART 2 "
                          "output before spending budget.")
    args = ap.parse_args()

    cfg = get_step_config(args.step)

    if args.dry_run:
        result = run_step_with_retries(cfg, dry_run=True)
        print(result["final_text"])
        return

    if args.step_confirmed:
        diff_summary = get_diff_summary()
        vault_result = run_vault_update(cfg, diff_summary)
        vault_text = vault_result.get("result", vault_result.get("raw_stdout", ""))
        print(vault_text)
        if "error" in vault_result:
            print(f"[error] vault update invocation returned non-JSON output: {vault_result.get('error')}")
            sys.exit(1)

        # One commit if this step shipped clean on the first implementation
        # attempt; two (impl + fix) if PART 3 needed a real fix attempt —
        # mirrors the v1.2.6 crash-fix split principle.
        commit_step(cfg, f"v1.3 step {cfg['step_num']}: {cfg['title']} — vault update + ship")
        print_cost_summary(1, [vault_result])
        return

    result = run_step_with_retries(cfg)

    if not result["passed"]:
        print()
        print("=" * 72)
        print(f"ESCALATING — Step {cfg['step_num']} ({cfg['title']}) did not pass "
              f"assertable tests after {MAX_FIX_ATTEMPTS} attempts.")
        print("=" * 72)
        for a in result["attempts"]:
            print(f"  attempt {a['attempt']} [{a['role']}, {a['model']}]: "
                  f"passed={a['passed']}")
            for line in a["results"]:
                print(f"    {line}")
        print()
        print(f"Full log: {README_LOG}")
        print("Review the working tree by hand before deciding next steps.")
        print_cost_summary(result["invocations"], result["attempts"])
        sys.exit(1)

    # Implementation (and possibly fix attempts) committed individually is
    # NOT done here — PART 5 is the only commit point, per spec ("commit on
    # confirmation"). The working tree is left dirty/uncommitted on purpose
    # so --step-confirmed has something to diff and commit.
    if result["invocations"] > 1:
        print(f"[note] this step needed {result['invocations']} attempts to pass — "
              f"PART 5 will create two commits (clean impl + fix) when you confirm.")

    print_manual_checklist(cfg)
    print_cost_summary(result["invocations"], result["attempts"])


if __name__ == "__main__":
    main()
