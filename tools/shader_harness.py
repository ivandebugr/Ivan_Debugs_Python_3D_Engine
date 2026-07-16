"""
shader_harness.py — headless GLSL 1.20 lit-shader verification.

Same pattern as the core-profile spike, but pinned to the REAL ship baseline
(gl-version 2 1, the macOS OpenGL 2.1 / GLSL 1.20 ceiling) instead of a 3.2
core context. It:

  1. Forces an offscreen GL 2.1 context and routes Panda3D's GSG notifier to a
     log file so every shader compile/link line is captured (Panda3D writes to
     the C-level stderr, which Python's redirect_stderr cannot see — we point
     Notify at a file stream instead).
  2. Renders the same cube TWICE — once under Ursina's unlit_shader (the old
     flat path) and once under Scripts/lit_shader.lit_shader with a
     DirectionalLight + ambient in the scene — and dumps a PNG of each.
  3. Scans the captured GSG log for compile/link errors; exits non-zero on any.
  4. Reports the shading signal: the luminance STANDARD DEVIATION across the
     cube's lit pixels. A flat/unlit surface has ~0 variance (every facing the
     same colour); a real lighting model produces a face-to-face gradient, so
     the lit frame's stddev is materially higher. That non-uniformity — not
     mean brightness — is the proof the lighting model changed the pixels.

Run:  python3 tools/shader_harness.py
Artifacts: tools/shader_out/before_unlit.png, tools/shader_out/after_lit.png,
           tools/shader_out/gsg.log
"""

import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)   # so asset/texture relative paths resolve like the real app

# --- Real ship baseline, BEFORE `from ursina import *` -----------------------
from Scripts import audio_workaround  # noqa: F401,E402 — OpenAL crash on this Mac
from panda3d.core import loadPrcFileData
loadPrcFileData('', 'window-type offscreen')
loadPrcFileData('', 'gl-version 2 1')             # pin to the 2.1 ceiling we ship on
loadPrcFileData('', 'gl-debug true')
loadPrcFileData('', 'notify-level-glgsg debug')

OUT_DIR = os.path.join(_HERE, 'shader_out')
os.makedirs(OUT_DIR, exist_ok=True)
GSG_LOG = os.path.join(OUT_DIR, 'gsg.log')

# Route Panda3D's Notify (the glgsg compile/link lines live here) to a file
# BEFORE the GSG is created, so we capture compilation at Ursina() time.
# Panda3D writes these to the C-level stderr, which Python's redirect_stderr
# cannot intercept; handing Notify an OFileStream is the supported route.
from panda3d.core import Notify, Filename, OFileStream
_log_stream = OFileStream(GSG_LOG)
Notify.ptr().set_ostream_ptr(_log_stream, False)

from ursina import Ursina, Entity, camera, scene, color, Vec3, window, destroy
from ursina.shaders.unlit_shader import unlit_shader
from ursina.lights import DirectionalLight
from panda3d.core import PNMImage

from Scripts.lit_shader import lit_shader

# The gun-MTL symptom class: a dark tactical diffuse. Use a mid-dark grey so the
# multiplicative lighting term has headroom to show a gradient (a 0.05 albedo
# clamps every lit pixel below 0.05 and hides the shading in the screenshot,
# even though the math is identical).
DARK_MATERIAL = color.rgb(0.35, 0.35, 0.38)


def _cube_stats(pnm):
    """Return (mean, stddev, n) of luminance over non-background pixels."""
    w, h = pnm.get_x_size(), pnm.get_y_size()
    step = max(1, min(w, h) // 96)
    vals = []
    for x in range(0, w, step):
        for y in range(0, h, step):
            r, g, b = pnm.get_xel(x, y)
            lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
            if lum > 0.002:            # skip pure-black background
                vals.append(lum)
    if not vals:
        return 0.0, 0.0, 0
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    return mean, var ** 0.5, len(vals)


def _clear_scene():
    for e in list(scene.entities):
        destroy(e)
    for _ in range(2):
        app.step()


def _render_and_dump(label, shader, with_light):
    _clear_scene()
    cube = Entity(model='cube', shader=shader, color=DARK_MATERIAL,
                  rotation=(25, 40, 0), position=(0, 0, 6), scale=3)
    sun = None
    if with_light:
        scene.ambient_color = color.rgb(0.12, 0.12, 0.14)
        sun = DirectionalLight()
        sun.look_at(Vec3(-1, -1, 1))
    camera.position = (0, 0, 0)
    camera.rotation = (0, 0, 0)
    window.color = color.black
    for _ in range(4):
        app.step()

    pnm = PNMImage()
    base.win.get_screenshot().store(pnm)
    path = os.path.join(OUT_DIR, f'{label}.png')
    pnm.write(Filename.from_os_specific(path))

    destroy(cube)
    if sun is not None:
        destroy(sun)
    return path, _cube_stats(pnm)


app = Ursina()

before_path, (before_mean, before_std, before_n) = _render_and_dump(
    'before_unlit', unlit_shader, with_light=False)
after_path, (after_mean, after_std, after_n) = _render_and_dump(
    'after_lit', lit_shader, with_light=True)

# Flush + read the captured GSG log.
_log_stream.flush()
with open(GSG_LOG, 'r', errors='replace') as fh:
    gsg_log = fh.read()

compile_lines = [l for l in gsg_log.splitlines()
                 if 'Compiling GLSL' in l or 'Linking GLSL' in l]

error_lines = []
for line in gsg_log.splitlines():
    low = line.lower()
    if re.search(r'\berror\b', low) or 'failed to' in low or 'invalid' in low \
       or 'could not compile' in low or 'could not link' in low:
        if 'p3d_fragdata' in low or 'ursina.ico' in low:
            continue   # benign compat-profile / missing-icon noise
        error_lines.append(line)

print('=' * 68)
print('SHADER HARNESS — GLSL 1.20 lit shader on real GL 2.1 baseline')
print('=' * 68)
gsg = base.win.get_gsg()
print(f'GL context : {gsg.get_driver_vendor()} / {gsg.get_driver_renderer()} '
      f'/ {gsg.get_driver_version()}')
print('GLSL       : 1.20 (compatibility profile)')
print()
print(f'GSG compile/link lines captured: {len(compile_lines)}')
for l in compile_lines:
    print('  ', l.strip())
print()
print('Shading signal (luminance over cube pixels):')
print(f'  before (unlit) : mean={before_mean:.4f}  stddev={before_std:.4f}  n={before_n}')
print(f'  after  (lit)   : mean={after_mean:.4f}  stddev={after_std:.4f}  n={after_n}')
print(f'  stddev delta   : {after_std - before_std:+.4f}   '
      f'(higher = surface now shades instead of flat)')
print(f'  PNGs           : {before_path}')
print(f'                   {after_path}')
print()

if error_lines:
    print('RESULT: FAIL — compile/link errors detected:')
    for l in error_lines:
        print('  ', l.strip())
    sys.exit(1)

if after_std <= before_std + 1e-4:
    print('RESULT: FAIL — lit surface shows no more variation than the flat '
          'unlit surface; lighting model had no visible effect.')
    sys.exit(2)

print('RESULT: PASS — zero compile/link errors under GL 2.1; lit surface '
      'shades (stddev up), unlit surface was flat.')
sys.exit(0)
