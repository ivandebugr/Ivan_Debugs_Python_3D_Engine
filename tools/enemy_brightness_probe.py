"""
enemy_brightness_probe.py — isolate WHY the enemy drone reads too bright/white.

Renders the ACTUAL enemy-flying visual mesh through the REAL Scripts/lit_shader
under the REAL level.json lighting (sun intensity 1.5 -> clamped white, ambient
(0.35,0.35,0.40), sun dir from level.json), then measures the per-term
contribution to the mesh's mean pixel luminance by toggling shader inputs one at
a time. The point is to attribute brightness to a term, not to guess.

Two camera angles so a single lucky orientation can't hide an orientation-
dependent term (spec is view-dependent).

Run: python3 tools/enemy_brightness_probe.py
Artifacts: tools/shader_out/enemy_*.png
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

from Scripts import audio_workaround  # noqa: F401,E402 — OpenAL crash on this Mac
from panda3d.core import loadPrcFileData
loadPrcFileData('', 'window-type offscreen')
loadPrcFileData('', 'gl-version 2 1')
loadPrcFileData('', 'gl-debug true')

OUT_DIR = os.path.join(_HERE, 'shader_out')
os.makedirs(OUT_DIR, exist_ok=True)

from Scripts.compat import patch_shaders_to_glsl120
patch_shaders_to_glsl120()

from ursina import Ursina, Entity, camera, scene, color, Vec3
from ursina.lights import DirectionalLight
from panda3d.core import PNMImage, Filename, TransparencyAttrib

from Scripts.lit_shader import lit_shader
from Scripts.asset_resolve import resolve_model as _resolve_model
from Scripts import level_io

app = Ursina()
patch_shaders_to_glsl120()

# --- Real level lighting (main.py main_menu + _apply_level_lighting) ----------
scene.ambient_color = color.rgb(0.35, 0.35, 0.40)

import json
entry = None
for e in json.load(open('level.json')):
    if e.get('type') == 'light':
        entry = e
        break
if entry is None:
    entry = level_io.default_light_entry()
intensity = max(0.0, float(entry['intensity']))
r, g, b = (list(entry['colour']) + [1, 1, 1])[:3]
sun = DirectionalLight()
sun.color = color.rgb(min(1.0, r * intensity), min(1.0, g * intensity), min(1.0, b * intensity))
sun.look_at(Vec3(*entry['direction']))
print(f'sun.color = {tuple(round(c,3) for c in sun.color)}  ambient = (0.35,0.35,0.40)')

# --- The actual enemy visual mesh, built the same way Enemy.__init__ does ------
drone = Entity(
    model=_resolve_model('enemy-flying'),
    rotation_y=180,
    scale=0.9,
    position=(0, 0, 0),
    shader=lit_shader,
)
drone.setTransparency(TransparencyAttrib.M_none)
for _c in drone.getChildren():
    _c.setTransparency(TransparencyAttrib.M_none)

camera.parent = scene
window_bg = color.rgb(0.10, 0.10, 0.12)
from ursina import window
window.color = window_bg


def _grab():
    pnm = PNMImage()
    base.win.get_screenshot().store(pnm)
    return pnm


def _step(n=5):
    for _ in range(n):
        app.step()


def _mesh_lum(pnm, bg):
    """Mean/max luminance over pixels that differ from the flat background
    (i.e. the drone's footprint). Returns (mean, max, npix)."""
    w, h = pnm.get_x_size(), pnm.get_y_size()
    br, bgc, bb = bg[0], bg[1], bg[2]
    tot = 0.0
    mx = 0.0
    n = 0
    for x in range(0, w, 2):
        for y in range(0, h, 2):
            px = pnm.get_xel(x, y)
            if max(abs(px[0] - br), abs(px[1] - bgc), abs(px[2] - bb)) < 0.02:
                continue  # background
            lum = 0.2126 * px[0] + 0.7152 * px[1] + 0.0722 * px[2]
            tot += lum
            mx = max(mx, lum)
            n += 1
    return (tot / n if n else 0.0, mx, n)


# Term toggles: each dict is a set of shader inputs applied on top of the enemy's
# real config. We isolate by ZEROING one term at a time from the full config.
ENEMY_GLOW = 0.9  # the value Enemy.__init__ currently sets

CONFIGS = [
    ('albedo_only',  {'ambient_boost': (0,0,0,1), 'warm_tint':(1,1,1,1), 'cool_tint':(1,1,1,1),
                      'spec_strength':0.0, 'rim_strength':0.0, 'glow_strength':0.0}),
    ('+ambient',     {'warm_tint':(1,1,1,1), 'cool_tint':(1,1,1,1),
                      'spec_strength':0.0, 'rim_strength':0.0, 'glow_strength':0.0}),
    ('+diffuse',     {'spec_strength':0.0, 'rim_strength':0.0, 'glow_strength':0.0}),
    ('+spec',        {'rim_strength':0.0, 'glow_strength':0.0}),
    ('+rim',         {'glow_strength':0.0}),   # == the SHIPPING enemy: all lit terms, glow removed
    ('+glow(0.9)_POC', {'glow_strength':ENEMY_GLOW}),   # the removed POC value — kept as the clipped control
]

# Default_input values from lit_shader for the terms we DON'T override in a config
DEFAULTS = {
    'ambient_boost': (0.06,0.06,0.06,1.0),
    'warm_tint': (1.40,1.34,1.24,1.0),
    'cool_tint': (1.26,1.30,1.44,1.0),
    'spec_strength': 0.05,
    'rim_strength': 0.05,
    'glow_strength': 0.0,
}

ANGLES = {
    'front': ((0, 2, -7), (0, 0, 0)),
    'above': ((5, 6, -5), (0, 0, 0)),
}

for aname, (cpos, target) in ANGLES.items():
    camera.position = cpos
    camera.look_at(Vec3(*target))
    print(f'\n=== angle: {aname}  cam={cpos} ===')
    print(f'  {"config":20s} {"mean_lum":>9s} {"max_lum":>8s}  {"delta_mean":>10s}')
    prev_mean = None
    for cname, overrides in CONFIGS:
        cfg = dict(DEFAULTS)
        cfg.update(overrides)
        for k, v in cfg.items():
            drone.set_shader_input(k, v)
        _step()
        pnm = _grab()
        mean, mx, n = _mesh_lum(pnm, window_bg)
        delta = '' if prev_mean is None else f'{mean-prev_mean:+.4f}'
        clip = ' <== CLIPPED' if mx >= 0.999 else ''
        print(f'  {cname:20s} {mean:9.4f} {mx:8.4f}  {delta:>10s}{clip}')
        prev_mean = mean
        pnm.write(Filename.from_os_specific(os.path.join(OUT_DIR, f'enemy_{aname}_{cname.replace("+","").replace("(","").replace(")","").replace(".","")}.png')))

print('\nDone. PNGs in tools/shader_out/enemy_*.png')
app.userExit()
