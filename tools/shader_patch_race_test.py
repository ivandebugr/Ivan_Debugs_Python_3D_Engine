"""
shader_patch_race_test.py — regression test for the compat double-patch race.

Bug (fixed in Scripts/compat.py _patch_shader_obj): the second
patch_shaders_to_glsl120() call used to `del obj._shader` on the stock shaders.
Ursina's Text.create_text_section reads `self.shader._shader` UNCONDITIONALLY
(ursina/text.py:251, `setShader(self.shader._shader)`) — no compiled-guard,
unlike entity.shader_setter which recompiles first. So any Text rebuilding its
glyphs in the same frame as the patch hit
`AttributeError: 'Shader' object has no attribute '_shader'`.

This reproduces it deterministically by driving a live Text's .text setter
across the frames immediately after the second patch. Against the buggy `del`
version this failed 20/20; against the fix (leave _shader intact, only set
compiled=False) it passes 20/20.

Run:  python3 tools/shader_patch_race_test.py   (exit 0 = pass)
"""

import os
import sys
import traceback

from panda3d.core import loadPrcFileData
loadPrcFileData('', 'audio-library-name null')
loadPrcFileData('', 'window-type offscreen')
loadPrcFileData('', 'gl-version 2 1')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Scripts.compat import patch_shaders_to_glsl120

patch_shaders_to_glsl120()                 # pre-Ursina (HC7/HC10)
from ursina import Ursina, Entity, Text, color, camera
app = Ursina()

# Live Text + geometry BEFORE the second patch, so the patch lands while both
# are alive and rendering — the exact window that used to crash.
text = Text(text='init', parent=camera.ui, y=0)
cube = Entity(model='cube', shader=Entity.default_shader, color=color.white,
              position=(0, 0, 6), scale=3)

patch_shaders_to_glsl120()                 # post-window-setup (2nd call)

try:
    for i in range(8):
        text.text = f'frame {i}'           # rebuilds glyphs -> setShader(shader._shader)
        app.step()
except AttributeError as e:
    if '_shader' in str(e):
        print('FAIL — compat double-patch race reproduced:')
        traceback.print_exc()
        sys.exit(1)
    raise

print('PASS — Text survived the second shader patch; no _shader AttributeError.')
sys.exit(0)
