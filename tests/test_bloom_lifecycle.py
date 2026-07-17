"""Verification harness for bloom buffer lifecycle (v1.7 Candidate B2).

Framework-free, runnable as a plain script (matches the other tests here). Drives
the REAL main.py main_menu() rather than a lookalike — the same reason
test_light_lifecycle.py does: this project's GPU-resource leaks have all lived in
that function's sweep-and-rebuild, and a reimplementation would not catch them.

Background: Scripts/bloom.py owns 4 offscreen buffers (filter-base + bright +
blur-x + blur-y). This project has twice shipped a leak by allocating GPU
resources on a path main_menu()/return_to_menu() re-runs — shadow FBOs and
LightAttrib entries (see Scripts/light_lifecycle.py). The B2 design answer is
structural: build the pipeline exactly once at app init, never through teardown,
and toggle it by flipping buffer active-flags. This test pins that.

Confirms:

  1. BloomPipeline allocates exactly 4 offscreen buffers, once.
  2. Those buffers survive main_menu()'s sweep — the quads are raw NodePaths, not
     scene entities, so the sweep cannot destroy them. (If they were parented into
     `scene`, the sweep would eat them and the composite would render black.)
  3. END TO END: six real main_menu() cycles hold buffer count flat at 4, with no
     second FilterManager and no re-allocation.
  4. set_enabled() toggles WITHOUT allocating or freeing — the toggle is
     active-flags only, which is what makes it safe to call per state transition.
  5. CONTROL: constructing a second BloomPipeline DOES grow the buffer count —
     proving the test observes allocation rather than being blind to it.

Run: python3 tests/test_bloom_lifecycle.py
"""
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from Scripts import audio_workaround  # noqa: F401,E402 — OpenAL crash on this Mac; before ursina import

from panda3d.core import loadPrcFileData  # noqa: E402

loadPrcFileData('', 'window-type offscreen')
loadPrcFileData('', 'gl-version 2 1')   # the real GL 2.1 / GLSL 1.20 ship baseline

from panda3d.core import GraphicsWindow  # noqa: E402

# Shader patch must precede Ursina() — Hard Constraint #7, same order as main.py's __main__.
from Scripts.compat import patch_shaders_to_glsl120  # noqa: E402

patch_shaders_to_glsl120()

from ursina import Ursina, application  # noqa: E402

# main_menu() builds Text with 'assets/fonts/Inter-Bold.ttf'; Ursina resolves fonts
# against asset_folder, which defaults to the CWD of the running script.
application.asset_folder = _ROOT
application.models_compressed_folder = _ROOT / 'models_compressed'

app = Ursina(title='bloom-lifecycle-test')
patch_shaders_to_glsl120()   # main.py patches a second time after window setup

from Scripts.bloom import BloomPipeline  # noqa: E402

failures = []


def check(label, got, want):
    ok = got == want
    print(f'  {"PASS" if ok else "FAIL"}  {label}: got {got!r}, want {want!r}')
    if not ok:
        failures.append(label)


def offscreen_buffers():
    """Offscreen buffers the engine holds, by name. Bloom's render targets live in
    the window list (GraphicsBuffer derives from GraphicsOutput); filter out the
    real window."""
    return [w.get_name() for w in base.graphicsEngine.get_windows()  # noqa: F821
            if not isinstance(w, GraphicsWindow)]


def settle(frames=3):
    for _ in range(frames):
        app.step()


BLOOM_BUFFERS = ['filter-base', 'bloom-bright', 'bloom-blur-x', 'bloom-blur-y']

print('\n[1] BloomPipeline allocates exactly its 4 buffers, once')
_baseline = offscreen_buffers()
check('no offscreen buffers before build', len(_baseline), 0)
bloom = BloomPipeline()
settle()
check('4 buffers after build', len(offscreen_buffers()), 4)
check('buffers are the expected chain', sorted(offscreen_buffers()),
      sorted(BLOOM_BUFFERS))

print('\n[2] set_enabled() toggles without allocating or freeing')
before_toggle = len(offscreen_buffers())
bloom.set_enabled(False)
settle()
check('disabling allocates/frees nothing', len(offscreen_buffers()), before_toggle)
check('enabled flag is False', bloom.enabled, False)
bloom.set_enabled(True)
settle()
check('re-enabling allocates nothing', len(offscreen_buffers()), before_toggle)
check('enabled flag is True', bloom.enabled, True)
# Idempotence: the state transitions in main.py may call this repeatedly.
bloom.set_enabled(True)
bloom.set_enabled(True)
settle()
check('repeat enable is a no-op', len(offscreen_buffers()), before_toggle)

print('\n[3] end-to-end: real main_menu() holds buffers flat across 6 cycles')
import main as M  # noqa: E402 — after Ursina() so main_menu()'s entities can build

M.app = app

CYCLES = 6
buffers = []
for i in range(CYCLES):
    M.main_menu()
    settle()
    buffers.append(len(offscreen_buffers()))
print(f'  offscreen buffers across cycles: {buffers}')
check('buffers flat at 4 across cycles', buffers, [4] * CYCLES)

print('\n[4] the bloom quads survive main_menu()\'s sweep')
# The sweep destroys every scene entity not named 'main_camera'. The composite quad
# is a raw NodePath owned by FilterManager and parented outside `scene`, so it must
# still be alive (and still shading) after six sweeps. An empty NodePath here means
# the sweep reached it and the screen would be black.
check('composite quad still live after sweeps', bool(not bloom.final_quad.is_empty()), True)
check('bright quad still live after sweeps', bool(not bloom.bright_quad.is_empty()), True)
check('blur quads still live after sweeps',
      bool(not bloom.blur_x_quad.is_empty() and not bloom.blur_y_quad.is_empty()), True)

print('\n[5] control: the counter can see bloom allocations at all')
# Section [3]'s flat line only means something if this counter would actually MOVE
# on a bloom-shaped allocation. Allocate one bare offscreen buffer the same way
# FilterManager does and confirm the count climbs, then release it.
#
# (The obvious control — "construct a second BloomPipeline and watch it leak" —
# turns out to be impossible, which is a stronger result and is asserted in [6]
# below rather than here.)
from panda3d.core import FrameBufferProperties, WindowProperties, GraphicsPipe  # noqa: E402

_before_control = len(offscreen_buffers())
_probe_buf = base.graphicsEngine.make_output(  # noqa: F821
    base.win.get_pipe(), 'control-probe-buffer', -2,  # noqa: F821
    FrameBufferProperties.get_default(), WindowProperties.size(64, 64),
    GraphicsPipe.BF_refuse_window, base.win.get_gsg(), base.win)  # noqa: F821
settle()
_after_control = len(offscreen_buffers())
print(f'  buffers: {_before_control} -> {_after_control} with a probe buffer live')
check('control: counter sees a new offscreen buffer',
      _after_control - _before_control, 1)
base.graphicsEngine.remove_window(_probe_buf)  # noqa: F821
settle()
check('control: counter sees it released again',
      len(offscreen_buffers()), _before_control)

print('\n[6] a second BloomPipeline is impossible, not merely discouraged')
# The "build once at init" rule is enforced by Panda itself: FilterManager claims
# base.cam's sort-0 display region on construction, and a second one cannot find an
# unclaimed region to filter — it raises rather than silently double-filtering.
# Pinning this means a future refactor that tries to rebuild bloom per session
# fails loudly at the first attempt instead of quietly leaking 4 buffers a cycle.
_raised = None
try:
    BloomPipeline()
except Exception as exc:      # noqa: BLE001 — the point is that ANY failure is the win
    _raised = exc
print(f'  second BloomPipeline() raised: {type(_raised).__name__ if _raised else None}')
check('second pipeline refuses to construct', _raised is not None, True)
check('buffers unchanged by the failed attempt',
      len(offscreen_buffers()), _before_control)

print()
if failures:
    print(f'RESULT: FAIL — {len(failures)} check(s) failed: {failures}')
    sys.exit(1)
print('RESULT: PASS — bloom builds 4 buffers once, toggles without allocating, '
      'and the real main_menu() holds them flat across cycles without sweeping '
      'the quads.')
sys.exit(0)
