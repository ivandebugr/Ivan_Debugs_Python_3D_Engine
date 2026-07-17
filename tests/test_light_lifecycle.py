"""Verification harness for light teardown (v1.7) — the shadow-FBO leak fix.

Framework-free, runnable as a plain script (matches the other tests here). Drives
the REAL main.py main_menu() rather than a lookalike, because the leak lived in
that function's sweep and a reimplementation would not have caught it.

Background: `destroy(light)` leaks. Ursina's DirectionalLight registers its light
NodePath on render's LightAttrib and keeps no handle to it, so destroy() detaches
the entity but leaves the light lit and its shadow FBO allocated — one more orphan
per menu rebuild. See Scripts/light_lifecycle.py for the full teardown contract.

Confirms:

  1. destroy_light() releases a light completely — on_lights returns to 0.
  2. Release is ORDER-INDEPENDENT: destroying the first of two live lights leaves
     exactly the second lit. (The "clear the last LightAttrib entry" shortcut
     passes a naive test and fails this one.)
  3. The '**/+Light' node pattern matches all four Ursina light types. '+LightNode'
     matches ambient ONLY and would silently leak the sun.
  4. END TO END: six real main_menu() cycles hold a flat on_lights == 1.
  5. CONTROL: with the fix disabled, the same six cycles climb 1..6 — proving the
     test observes the fix rather than a harness artifact.

Run: python3 tests/test_light_lifecycle.py
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

from panda3d.core import GraphicsWindow, LightAttrib  # noqa: E402

# Shader patch must precede Ursina() — Hard Constraint #7, same order as main.py's __main__.
from Scripts.compat import patch_shaders_to_glsl120  # noqa: E402

patch_shaders_to_glsl120()

from ursina import Ursina, application  # noqa: E402

# main_menu() builds Text with 'assets/fonts/Inter-Bold.ttf'; Ursina resolves fonts
# against asset_folder, which defaults to the CWD of the running script.
application.asset_folder = _ROOT
# Separate setting from asset_folder, and it must be set too: Ursina derives it from
# the running script's folder, so without this the .bam model cache is rebuilt into a
# stray tests/models_compressed/ instead of reusing the project's own.
application.models_compressed_folder = _ROOT / 'models_compressed'

app = Ursina(title='light-lifecycle-test')
patch_shaders_to_glsl120()   # main.py patches a second time after window setup

from ursina.lights import AmbientLight, DirectionalLight, PointLight, SpotLight  # noqa: E402

from Scripts.light_lifecycle import _LIGHT_NODE_PATTERN, destroy_light, is_light  # noqa: E402

failures = []


def check(label, got, want):
    ok = got == want
    print(f'  {"PASS" if ok else "FAIL"}  {label}: got {got!r}, want {want!r}')
    if not ok:
        failures.append(label)


def on_lights():
    attrib = render.get_attrib(LightAttrib)  # noqa: F821 — Panda3D builtin from ShowBase
    return attrib.get_num_on_lights() if attrib else 0


def shadow_buffers():
    """Offscreen buffers the engine holds. Shadow FBOs live in the window list
    (GraphicsBuffer derives from GraphicsOutput); filter out the real window."""
    return [w.get_name() for w in base.graphicsEngine.get_windows()  # noqa: F821
            if not isinstance(w, GraphicsWindow)]


def settle(frames=3):
    for _ in range(frames):
        app.step()


print('\n[1] destroy_light() fully releases a light')
_baseline = on_lights()
sun = DirectionalLight()
settle()
check('light is lit after create', on_lights(), _baseline + 1)
destroy_light(sun)
settle()
check('on_lights back to baseline after destroy_light', on_lights(), _baseline)
check('no shadow buffers retained', len(shadow_buffers()), 0)

print('\n[2] release is order-independent (destroy the FIRST of two)')
first, second = DirectionalLight(), DirectionalLight()
settle()
check('two lights lit', on_lights(), _baseline + 2)
destroy_light(first)
settle()
check('destroying first leaves exactly one lit', on_lights(), _baseline + 1)
destroy_light(second)
settle()
check('destroying second returns to baseline', on_lights(), _baseline)

print('\n[3] the node pattern matches every Ursina light type')
for cls in (DirectionalLight, AmbientLight, PointLight, SpotLight):
    light = cls()
    settle(1)
    # AmbientLight is a LightNode; the rest are LightLensNode. Only their shared
    # 'Light' base matches all four — this is the assertion that catches a
    # regression back to '+LightNode'.
    check(f'{cls.__name__} matches {_LIGHT_NODE_PATTERN}',
          len(light.find_all_matches(_LIGHT_NODE_PATTERN)), 1)
    check(f'{cls.__name__} is_light()', is_light(light), True)
    destroy_light(light)
    settle(1)
check('all light types released', on_lights(), _baseline)

print('\n[4] end-to-end: real main_menu() stays flat across 6 cycles')
import main as M  # noqa: E402 — after Ursina() so main_menu()'s entities can build

M.app = app

CYCLES = 6
counts, buffers = [], []
for i in range(CYCLES):
    M.main_menu()
    settle()
    counts.append(on_lights())
    buffers.append(len(shadow_buffers()))
print(f'  on_lights across cycles: {counts}')
print(f'  buffers   across cycles: {buffers}')
# Exactly one sun: the current level's, alive. Flat == released and rebuilt, not accumulated.
check('on_lights flat at 1', counts, [1] * CYCLES)
check('shadow buffers flat', len(set(buffers)), 1)

print('\n[5] control: with the fix disabled the leak reappears')
# Neutralise the routing in main_menu()'s sweep so lights take the plain destroy()
# path again — everything else identical. If this does NOT climb, the test above is
# measuring something other than the fix.
M.is_light = lambda e: False
control = []
for i in range(CYCLES):
    M.main_menu()
    settle()
    control.append(on_lights())
print(f'  on_lights across cycles (fix disabled): {control}')
# Asserted as a per-cycle DELTA, not absolute values: section [4] leaves its last
# sun lit, so the control's first cycle leaks that one plus its own and the run
# starts at 2. The leak signature is "+1 lit light that is never released, every
# cycle" — that is the invariant worth pinning.
deltas = [b - a for a, b in zip(control, control[1:])]
check('control leaks exactly one light per cycle', deltas, [1] * (CYCLES - 1))

print()
if failures:
    print(f'RESULT: FAIL — {len(failures)} check(s) failed: {failures}')
    sys.exit(1)
print('RESULT: PASS — lights release completely, order-independently, and the real '
      'main_menu() no longer leaks lights or shadow buffers across cycles.')
sys.exit(0)
