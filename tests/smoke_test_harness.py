"""
smoke_test_harness.py — Headless-ish regression harness for Ivan's 3D Engine.

This is the permanent version of the one-off repro Claude Code built to chase
the R-key teardown crash: it drives the REAL Ursina app through its actual
input dispatch (app.input(key, is_raw=True) -> __main__.input() ->
per-entity input()) and frame loop (app.step()), instead of calling internal
functions directly. That distinction mattered — calling _enter_play_mode()
or game.return_to_menu() in isolation missed bugs that only appear when
Ursina's real per-frame entity update ordering is involved.

Why this exists: every fix session so far has required Ivan to manually run
the game, reproduce a bug by hand, and paste a console log back into a chat.
This harness lets the auto-fix loop do that step itself, so the loop can
verify "did the fix actually work" without a human at the keyboard.

HOW TO EXTEND:
  Add a new scenario as a method on GameTestHarness following the existing
  pattern. Each scenario must:
    1. Launch (or reuse) the app via self.launch_main() or self.launch_editor()
    2. Drive input/frames via self.press(key) / self.step(n)
    3. Call self.assert_clean() at the point where a crash would have
       occurred, or self.capture_crash() to record one for the orchestrator
    4. Return a CrashReport (or None if the scenario passed clean)

NOTE FOR CLAUDE CODE: every state/function name and source line referenced
in the scenario bodies below was verified against the actual current source
(main.py, Scripts/game.py, Scripts/player_controller.py, Scripts/level_editor.py).
The win/gameover scenarios start a real game and reach WIN/GAME_OVER through
the real triggers (enemy kills / player HP 0) so the R-teardown actually
sweeps live player + enemy + EndScreen entities — the v1.2.5 NodePath crash
path. Re-verify the cited line numbers if those files change.
"""

from __future__ import annotations

import io
import re
import runpy
import sys
import traceback
import contextlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Patterns that mean "this run is not clean" even if no Python exception
# propagated — Panda3D assertions are C++-level and never raise.
CRASH_PATTERNS = [
    re.compile(r"Traceback \(most recent call last\)"),
    re.compile(r"Assertion failed:"),
    re.compile(r"^Exception:"),
    re.compile(r"^AssertionError"),
]


@dataclass
class CrashReport:
    """A captured failure, normalised into a comparable signature so the
    orchestrator can tell 'same bug' from 'new bug' from 'fixed'."""

    scenario: str
    raw_output: str
    signature: str = field(default="")

    def __post_init__(self):
        if not self.signature:
            self.signature = self._derive_signature()

    def _derive_signature(self) -> str:
        """Best-effort fingerprint: last exception type + last
        project-file:line mentioned, or the first Assertion line if no
        Python traceback is present. Good enough to distinguish
        'same crash persists' from 'a different crash now exists'."""
        lines = self.raw_output.splitlines()

        last_exc = None
        last_loc = None
        for line in lines:
            m = re.match(r"^(?:Exception|AssertionError|[A-Za-z]+Error): ", line)
            if m:
                last_exc = line.strip()
            m2 = re.search(r'File "([^"]+\.py)", line (\d+)', line)
            if m2 and "site-packages" not in m2.group(1):
                last_loc = f"{Path(m2.group(1)).name}:{m2.group(2)}"

        if last_exc or last_loc:
            return f"{last_loc or '?'} | {last_exc or '?'}"

        for line in lines:
            if "Assertion failed" in line:
                return line.strip()[:120]

        return "unknown-crash"


class GameTestHarness:
    """Drives main.py or Scripts/level_editor.py through Ursina's real
    dispatch path inside the current process, capturing stdout/stderr."""

    def __init__(self):
        self.app = None
        self._stdout_buf = io.StringIO()

    # ------------------------------------------------------------------
    # Launch helpers
    # ------------------------------------------------------------------

    def _run_module_without_blocking(self, module_path: Path):
        """runpy a module whose `__main__` block ends in `app.run()`, but stop
        that call from handing control to ShowBase's blocking main loop.

        Both main.py and Scripts/level_editor.py call `app.run()` at the end of
        their `if __name__ == '__main__'` block (level_editor.py builds the app
        inside _launch() since the v1.6 split, but still assigns the module-level
        `app` and ends in `app.run()`).
        `Ursina.run()` (ursina/main.py) ends in `super().run()` — ShowBase's
        blocking loop — which would never return, so runpy itself would never
        return either. We can't edit those files (and the harness is meant to
        drive the *real* `__main__` wiring), so instead we patch `Ursina.run`
        to a no-op for the duration of the import. The Ursina instance is fully
        constructed by the time `app.run()` is reached, so neutering only the
        final blocking call leaves a live app we can drive with step()/input().
        We restore the original `run` afterwards so nothing else is affected."""
        # Ursina derives application.asset_folder (and fonts_folder) from
        # sys.argv[0]'s parent at first import. Under `python -m tests.smoke_
        # test_harness`, argv[0] is THIS file, so assets resolved against
        # tests/ — which broke every launch the moment main.py started loading
        # a real font (v1.5 Inter-Bold). Point argv[0] at the target module
        # BEFORE the first ursina import below, exactly what `python main.py`
        # would have produced; restore it after launch.
        original_argv0 = sys.argv[0]
        sys.argv[0] = str(module_path)

        import ursina.main as _ursina_main

        # `ursina.main.Ursina` is wrapped by an @singleton decorator, so the
        # public name is a SingletonProxy with no `run` of its own — the real
        # class (which defines run/step/input) lives in the proxy's __new__
        # closure. Recover it so we patch the method the instance actually uses.
        real_cls = _ursina_main.Ursina
        if not hasattr(real_cls, "run"):
            for cell in (real_cls.__new__.__closure__ or ()):
                obj = cell.cell_contents
                if isinstance(obj, type) and hasattr(obj, "run"):
                    real_cls = obj
                    break
        if not hasattr(real_cls, "run"):
            raise RuntimeError(
                "could not locate the real Ursina class with a run() method "
                "to neuter; ursina's singleton wrapping may have changed"
            )

        original_run = real_cls.run

        def _noop_run(self, info=True):  # signature matches Ursina.run
            return None

        real_cls.run = _noop_run
        try:
            with contextlib.redirect_stdout(self._stdout_buf), \
                 contextlib.redirect_stderr(self._stdout_buf):
                ns = runpy.run_path(str(module_path), run_name="__main__")
        finally:
            real_cls.run = original_run
            sys.argv[0] = original_argv0

        self.app = ns.get("app")

        # Ursina dispatches global input/update to `__main__` (ursina/main.py:279
        # reads sys.modules['__main__'].input). main.py and level_editor.py define
        # those handlers inside their `if __name__ == '__main__'` block, so when
        # `python main.py` runs they live on the real __main__. But runpy executes
        # the module in a throwaway namespace and RESTORES sys.modules['__main__']
        # to this harness afterwards, orphaning those handlers — app.input('r')
        # would then find no __main__.input and the R / Esc logic never runs.
        # Re-bind the module's input/update/text_input onto the live __main__ so
        # scripted input reaches the same code path the real game uses.
        live_main = sys.modules["__main__"]
        for hook in ("input", "update", "text_input"):
            fn = ns.get(hook)
            if callable(fn):
                setattr(live_main, hook, fn)

        return self.app

    def launch_main(self):
        """Boot main.py the same way `python main.py` would (real `__main__`
        wiring, real shader patch, real main_menu()), but keep the Ursina app
        object alive for scripted input instead of handing control to a
        blocking app.run(). main.py's module-level instance is `app = Ursina(...)`
        (main.py:537); `_run_module_without_blocking` neutralises the trailing
        `app.run()` and returns that instance."""
        return self._run_module_without_blocking(PROJECT_ROOT / "main.py")

    def launch_editor(self):
        """Same pattern for Scripts/level_editor.py — since the v1.6 split its
        __main__ block assigns the module-level `app = _launch()` (which builds
        Ursina(title="Level Editor")) and ends in a blocking `app.run()`.
        The LevelEditor class itself lives in Scripts/editor_core.py."""
        return self._run_module_without_blocking(
            PROJECT_ROOT / "Scripts" / "level_editor.py"
        )

    def start_real_game(self):
        """Click the main-menu Play button to enter a real PLAYING session
        (spawns the Player + all level enemies), the same way a human does.

        WIN/GAME_OVER are reached via the real triggers (trigger_win fires from
        the global update() when count_layer(ENEMY)==0; trigger_game_over fires
        from Player.update when health<=0), and BOTH only fire from PLAYING
        (Scripts/game.py:40,47). So the R-teardown scenarios must actually be in
        a live game first — otherwise return_to_menu()/_clear_gameplay_entities()
        sweeps an empty scene and never touches the player/enemy/EndScreen
        teardown that the v1.2.5 NodePath crash lived in. start_game() is a
        closure inside main_menu() (main.py:290) so it can't be called directly;
        invoking play_button.on_click() is the faithful equivalent.

        main.py boots into IntroScreen (main.py:796) before main_menu() is ever
        built (v1.7) — IntroScreen.input() advances to main_menu() on any
        key/click (main.py:603-605). Dismiss it the same way a human would
        (press any key) before looking for the Play button, or main_menu()
        never runs and the search below finds nothing."""
        if self.app is None:
            raise RuntimeError("launch_main() before start_real_game()")
        from ursina import scene
        if any(type(e).__name__ == "IntroScreen" for e in scene.entities):
            self.press("space")
            self.step(1)
        play_button = next(
            (e for e in scene.entities if getattr(e, "text", None) == "Play"),
            None,
        )
        if play_button is None or not callable(getattr(play_button, "on_click", None)):
            raise RuntimeError(
                "main-menu Play button not found after launch — main_menu() "
                "wiring may have changed (main.py:287)"
            )
        with contextlib.redirect_stdout(self._stdout_buf), \
             contextlib.redirect_stderr(self._stdout_buf):
            play_button.on_click()

        # The Play button is a ThemedButton (v1.7): on_click plays a press
        # animation and DEFERS the real start_game() callback by
        # BUTTON_CLICK_ANIM_DURATION (0.1s) via invoke() (Scripts/ui_theme.py).
        # So start_game() — which sets game.player and flips state to PLAYING —
        # does not run synchronously inside on_click(); it fires a few frames
        # later once that 0.1s timer elapses. The old fixed `step(3)` in the
        # scenarios wasn't enough wall-clock to cross 0.1s, so game.player was
        # still None when gameover_then_r dereferenced it (win_then_r only
        # passed by accident: with no game ever started, the enemy count is 0
        # and the win condition fired immediately). Step here until the game
        # actually reaches PLAYING — waiting on the real state, not a magic
        # frame count, so this stays correct if the animation duration is tuned.
        from Scripts.game import game, Game
        with contextlib.redirect_stdout(self._stdout_buf), \
             contextlib.redirect_stderr(self._stdout_buf):
            for _ in range(120):   # generous cap (~2s of frames); real transition takes <10
                if game.state == Game.PLAYING and game.player is not None:
                    break
                self.app.step()
        if game.state != Game.PLAYING or game.player is None:
            raise RuntimeError(
                "game did not reach PLAYING after clicking Play — the deferred "
                "ThemedButton callback (start_game) never fired (ui_theme.py "
                "BUTTON_CLICK_ANIM_DURATION / main.py start_game wiring)"
            )

    # ------------------------------------------------------------------
    # Drive input / frames
    # ------------------------------------------------------------------

    def press(self, key: str):
        if self.app is None:
            raise RuntimeError("launch_main()/launch_editor() before press()")
        with contextlib.redirect_stdout(self._stdout_buf), \
             contextlib.redirect_stderr(self._stdout_buf):
            self.app.input(key, is_raw=True)

    def step(self, n: int = 1):
        if self.app is None:
            raise RuntimeError("launch_main()/launch_editor() before step()")
        with contextlib.redirect_stdout(self._stdout_buf), \
             contextlib.redirect_stderr(self._stdout_buf):
            for _ in range(n):
                self.app.step()

    # ------------------------------------------------------------------
    # Crash capture
    # ------------------------------------------------------------------

    def output_so_far(self) -> str:
        return self._stdout_buf.getvalue()

    def has_crashed(self) -> bool:
        out = self.output_so_far()
        return any(p.search(out) for p in CRASH_PATTERNS)

    def capture_crash(self, scenario: str) -> Optional[CrashReport]:
        if not self.has_crashed():
            return None
        return CrashReport(scenario=scenario, raw_output=self.output_so_far())

    def assert_clean(self, scenario: str):
        report = self.capture_crash(scenario)
        if report:
            raise AssertionError(
                f"[{scenario}] crash detected: {report.signature}\n"
                f"--- captured output (tail) ---\n"
                + "\n".join(self.output_so_far().splitlines()[-40:])
            )

    def reset(self):
        """Tear down the current app instance between scenarios. Ursina
        doesn't support clean re-init in one process reliably — the
        orchestrator runs each scenario in a FRESH subprocess instead of
        relying on this. This method exists for in-process unit testing
        of the harness itself only."""
        self.app = None
        self._stdout_buf = io.StringIO()


# ----------------------------------------------------------------------
# Scenarios — each one is run by the orchestrator as a standalone
# subprocess invocation: `python -m tests.smoke_test_harness <scenario>`
# ----------------------------------------------------------------------

def scenario_load_and_close() -> Optional[CrashReport]:
    """Baseline: app launches, loads level.json, exits clean."""
    h = GameTestHarness()
    h.launch_main()
    h.step(10)
    return h.capture_crash("load_and_close")


def scenario_win_then_r() -> Optional[CrashReport]:
    """Start a real game -> kill every enemy (the real win condition) -> WIN
    overlay appears -> press R -> must return to MAIN_MENU with zero Panda3D
    assertions and zero Python exceptions.

    Reaches WIN faithfully (confirmed against current source): enemies are
    killed via Enemy.die() (the AliveEntity path the real game uses); the global
    update() in main.py:593-594 then sees collision_manager.count_layer(
    Layers.ENEMY) == 0 while state == PLAYING and calls game.trigger_win(),
    which sets state = Game.WIN (Scripts/game.py:42) AND builds the EndScreen
    overlay via _show_end_screen (Scripts/game.py:43,52). main.py:624 then
    honours R for WIN/GAME_OVER -> return_to_menu() -> _clear_gameplay_entities()
    -> main_menu(). Starting a real game first is required: _clear_gameplay_
    entities() only exercises the crash-prone player/enemy/EndScreen teardown
    (the v1.2.5 NodePath assertion) when those entities actually exist —
    forcing game.state directly from MAIN_MENU would sweep an empty scene and
    skip exactly what this scenario guards."""
    h = GameTestHarness()
    h.launch_main()
    h.step(5)
    h.start_real_game()
    h.step(3)

    from Scripts.game import game
    for enemy in list(game.enemies):
        if getattr(enemy, "alive", False):
            enemy.die()
    h.step(3)  # let global update() detect zero enemies and fire trigger_win()
    h.press("r")
    h.step(5)
    return h.capture_crash("win_then_r")


def scenario_gameover_then_r() -> Optional[CrashReport]:
    """Start a real game -> drop player HP to 0 (the real death condition) ->
    GAME OVER overlay appears -> press R -> must return to MAIN_MENU cleanly.
    Mirrors scenario_win_then_r.

    Reaches GAME_OVER faithfully: setting game.player.health = 0 makes
    Player.update fire game.trigger_game_over() (player_controller.py:124-125),
    which sets state = Game.GAME_OVER (Scripts/game.py:49) and builds the
    EndScreen overlay. R is honoured for both WIN and GAME_OVER via the same
    branch (main.py:624), so this exercises the identical R-teardown path on a
    real player + EndScreen."""
    h = GameTestHarness()
    h.launch_main()
    h.step(5)
    h.start_real_game()
    h.step(3)

    from Scripts.game import game
    game.player.health = 0
    h.step(3)  # let Player.update see health<=0 and fire trigger_game_over()
    h.press("r")
    h.step(5)
    return h.capture_crash("gameover_then_r")


def scenario_editor_f5_roundtrip() -> Optional[CrashReport]:
    """level_editor.py: load level, press F5 to enter play-in-editor,
    press F5 again (or Esc) to exit, confirm editor UI and blocks survive
    intact with zero crashes."""
    h = GameTestHarness()
    h.launch_editor()
    h.step(10)
    h.press("f5")
    h.step(10)
    h.press("f5")
    h.step(10)
    return h.capture_crash("editor_f5_roundtrip")


SCENARIOS = {
    "load_and_close": scenario_load_and_close,
    "win_then_r": scenario_win_then_r,
    "gameover_then_r": scenario_gameover_then_r,
    "editor_f5_roundtrip": scenario_editor_f5_roundtrip,
}


def run_scenario(name: str) -> int:
    """Entry point for subprocess invocation. Prints PASS/FAIL plus the
    crash signature (if any) to stdout in a format the orchestrator can
    parse, then exits 0 (clean) or 1 (crash captured)."""
    if name not in SCENARIOS:
        print(f"SMOKE_RESULT: UNKNOWN_SCENARIO {name}")
        return 2

    try:
        report = SCENARIOS[name]()
    except Exception as e:  # the harness itself blew up, not the game
        print(f"SMOKE_RESULT: HARNESS_ERROR {name}")
        print(f"SMOKE_SIGNATURE: harness:{type(e).__name__}: {e}")
        traceback.print_exc()
        return 2

    if report is None:
        print(f"SMOKE_RESULT: PASS {name}")
        return 0

    print(f"SMOKE_RESULT: FAIL {name}")
    print(f"SMOKE_SIGNATURE: {report.signature}")
    print("SMOKE_OUTPUT_BEGIN")
    print(report.raw_output)
    print("SMOKE_OUTPUT_END")
    return 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python -m tests.smoke_test_harness <scenario_name>")
        print(f"available: {', '.join(SCENARIOS)}")
        sys.exit(2)
    sys.exit(run_scenario(sys.argv[1]))
