"""
ground_shadow_harness.py — headless before/after frame dump for GroundShadow opacity.

Renders the GroundShadow blob quad (Scripts/ground_shadow.py) over a lit floor,
at both player scale (1.0, 1.0) and enemy scale (1.5, 1.5), once with the OLD
alpha=1 tint and once with the current module's alpha. Reports the mean
darkening the shadow applies to the floor pixels it covers (lower = lighter).

Run:  python3 tools/ground_shadow_harness.py
Artifacts: tools/shader_out/shadow_before_*.png, tools/shader_out/shadow_after_*.png
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

OUT_DIR = os.path.join(_HERE, 'shader_out')
os.makedirs(OUT_DIR, exist_ok=True)

from ursina import Ursina, Entity, camera, scene, color, Vec3, window, destroy
from panda3d.core import PNMImage, Filename
from Scripts.asset_resolve import resolve_texture as _resolve_texture

CURRENT_ALPHA = 0.4   # Scripts/ground_shadow.py current value
OLD_ALPHA = 1.0        # previous value


def _clear_scene():
    for e in list(scene.entities):
        destroy(e)
    for _ in range(2):
        app.step()


def _mean_luminance(pnm, region):
    x0, y0, x1, y1 = region
    vals = []
    for x in range(x0, x1, 2):
        for y in range(y0, y1, 2):
            r, g, b = pnm.get_xel(x, y)
            vals.append(0.2126 * r + 0.7152 * g + 0.0722 * b)
    return sum(vals) / len(vals) if vals else 0.0


def _render(label, alpha, quad_scale):
    _clear_scene()
    floor = Entity(model='plane', scale=20, color=color.rgb(0.7, 0.7, 0.7),
                    position=(0, 0, 0))
    shadow_quad = Entity(
        model='quad',
        texture=_resolve_texture('blob_shadow'),
        color=(0, 0, 0, alpha),
        rotation_x=90,
        scale=quad_scale,
        position=(0, 0.02, 0),
    )
    camera.position = (0, 8, -0.01)
    camera.rotation = (90, 0, 0)
    window.color = color.white
    for _ in range(4):
        app.step()

    pnm = PNMImage()
    base.win.get_screenshot().store(pnm)
    path = os.path.join(OUT_DIR, f'{label}.png')
    pnm.write(Filename.from_os_specific(path))

    w, h = pnm.get_x_size(), pnm.get_y_size()
    cx, cy = w // 2, h // 2
    r = min(w, h) // 6
    shadow_region = (cx - r, cy - r, cx + r, cy + r)
    lum = _mean_luminance(pnm, shadow_region)

    destroy(floor)
    destroy(shadow_quad)
    return path, lum


app = Ursina()

before_player_path, before_player_lum = _render('shadow_before_player', OLD_ALPHA, (1.0, 1.0))
after_player_path, after_player_lum = _render('shadow_after_player', CURRENT_ALPHA, (1.0, 1.0))
before_enemy_path, before_enemy_lum = _render('shadow_before_enemy', OLD_ALPHA, (1.5, 1.5))
after_enemy_path, after_enemy_lum = _render('shadow_after_enemy', CURRENT_ALPHA, (1.5, 1.5))

print('=' * 68)
print('GROUND SHADOW HARNESS — opacity before/after')
print('=' * 68)
print(f'old alpha = {OLD_ALPHA}   new alpha = {CURRENT_ALPHA}')
print()
print('Mean luminance under the shadow blob (higher = lighter/less dark):')
print(f'  player  before: {before_player_lum:.4f}   after: {after_player_lum:.4f}'
      f'   delta: {after_player_lum - before_player_lum:+.4f}')
print(f'  enemy   before: {before_enemy_lum:.4f}   after: {after_enemy_lum:.4f}'
      f'   delta: {after_enemy_lum - before_enemy_lum:+.4f}')
print()
print('PNGs:')
for p in (before_player_path, after_player_path, before_enemy_path, after_enemy_path):
    print('  ', p)
print()

if after_player_lum <= before_player_lum or after_enemy_lum <= before_enemy_lum:
    print('RESULT: FAIL — new alpha did not lighten the shadow.')
    sys.exit(1)

print('RESULT: PASS — shadow blob is lighter under the new alpha for both player and enemy scale.')
sys.exit(0)
