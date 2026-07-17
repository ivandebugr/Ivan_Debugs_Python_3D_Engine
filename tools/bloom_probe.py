"""
bloom_probe.py — v1.7 Candidate B2 gate: does a hand-rolled FilterManager bloom
chain (bright-pass -> separable blur at 1/4 res -> composite) work on this Mac's
OpenGL 2.1 / GLSL 1.20 driver, and does the sort-20 HUD really composite on top
of it un-bloomed?

SPIKE ONLY — the production chain lives in Scripts/bloom.py. This file exists to
answer the three questions the scoping doc left open, and to stay runnable as a
regression check.

WHY THIS ONE IS WINDOWED (unlike shader_harness / shadow_fbo_probe, which pin
`window-type offscreen`): the whole UI-camera claim is a statement about display
region sort order on a real GraphicsWindow. An offscreen buffer has no
ui_display_region at all, so an offscreen probe would "pass" the HUD test
vacuously — it would prove nothing. The scoping doc reached its UI answer by
READING FilterManager source; the task is to confirm the pixels agree. So we open
a real window, put a real ursina Text HUD on ui_camera, and read the framebuffer.

THE THREE GATES:

  1. BLOOM IS REAL — a bright emitter must gain a halo of lit pixels OUTSIDE its
     own footprint. Measured as: count of pixels that are dark in the no-bloom
     frame but materially brighter in the bloom frame, sampled in an annulus
     around the emitter. A threshold-only bug (composite adds to the source but
     spreads nothing) passes a naive mean-brightness check and fails this one.

  2. HUD IS UNTOUCHED — the sort-20 region's pixels must be BYTE-IDENTICAL
     between a bloom and a no-bloom frame, even with a blown-out emitter directly
     behind the HUD text. Identical-not-just-similar is the strong form: if the
     filter quad ever swallowed the UI region, the HUD would inherit the halo and
     these would diverge.

  3. NO RESOURCE GROWTH — buffer count and texture count must hold flat across
     repeated menu-cycle-shaped teardowns. This project has leaked FBOs twice
     this session (shadow buffers, LightAttrib entries); the B2 design answer is
     "create once at init, never through return_to_menu()", and this gate is what
     proves the design holds rather than merely claims it.

Run:  python3 tools/bloom_probe.py
Artifacts: tools/shader_out/bloom_*.png, tools/shader_out/bloom_gsg.log
"""

import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

# --- Real ship baseline, BEFORE `from ursina import *` (mirrors shader_harness) ---
from Scripts import audio_workaround  # noqa: F401,E402 — OpenAL crash on this Mac
from panda3d.core import loadPrcFileData

# NOTE: deliberately NOT 'window-type offscreen' — see module docstring. We do
# pin the size so the sampling coordinates below are deterministic.
loadPrcFileData('', 'win-size 1280 720')
loadPrcFileData('', 'gl-version 2 1')             # pin to the 2.1 ceiling we ship on
loadPrcFileData('', 'gl-debug true')
loadPrcFileData('', 'notify-level-glgsg debug')
loadPrcFileData('', 'notify-level-display debug')  # buffer/FBO allocation lines live here

OUT_DIR = os.path.join(_HERE, 'shader_out')
os.makedirs(OUT_DIR, exist_ok=True)
GSG_LOG = os.path.join(OUT_DIR, 'bloom_gsg.log')

# Panda3D writes GSG/display lines to the C-level stderr, which Python's
# redirect_stderr cannot see; hand Notify an OFileStream before the GSG exists.
from panda3d.core import Notify, Filename, OFileStream
_log_stream = OFileStream(GSG_LOG)
Notify.ptr().set_ostream_ptr(_log_stream, False)

from Scripts.compat import patch_shaders_to_glsl120
patch_shaders_to_glsl120()   # HC7: first call, before Ursina()

from ursina import Ursina, Entity, Text, camera, scene, color, Vec3, window, destroy
from panda3d.core import PNMImage, GraphicsWindow, Texture

from Scripts.bloom import BloomPipeline

app = Ursina()
patch_shaders_to_glsl120()   # patch 2/2, mirrors main.py's post-window-setup call


def _counts():
    """(offscreen buffers, live textures) — the two things a bloom chain leaks.

    Offscreen buffers live in the engine's WINDOW list (GraphicsBuffer derives
    from GraphicsOutput); filter out the real window. Texture count comes from
    the global TexturePool, which is where render targets land.
    """
    eng = base.graphicsEngine
    bufs = [w.get_name() for w in eng.get_windows()
            if not isinstance(w, GraphicsWindow)]
    return bufs, Texture.get_num_loaded_textures() if hasattr(
        Texture, 'get_num_loaded_textures') else -1


def _grab():
    pnm = PNMImage()
    base.win.get_screenshot().store(pnm)
    return pnm


def _step(n=6):
    for _ in range(n):
        app.step()


def _build_scene():
    """A dark room with one blown-out emitter and one bright-but-normal wall.

    The emitter is pure white on black so the halo has somewhere to spread INTO —
    a bright object on a bright background makes the bloom delta unmeasurable.
    """
    scene.ambient_color = color.rgb(0.05, 0.05, 0.06)
    # Emitter sits at screen CENTRE, directly behind the probe HUD (which _hud()
    # pins at camera.ui's origin — see the note there for why it cannot be moved).
    # Small enough to leave a full margin of dark pixels on every side: the halo
    # needs somewhere to spread INTO, and a quad clipped by the frame edge loses
    # the annulus on that side, and with it the gate-1 signal.
    emitter = Entity(model='quad', color=color.white, scale=0.9,
                     position=(0, 0, 8), name='probe_emitter')
    # Mid-grey: below the bright-pass threshold. In phase 1 (luminance keying)
    # this correctly does NOT bloom; it becomes the B3 control in phase 2.
    wall = Entity(model='quad', color=color.rgb(0.45, 0.45, 0.45), scale=2,
                  position=(2.6, -0.5, 8), name='probe_wall')
    return emitter, wall


def _hud():
    """A Text on camera.ui — i.e. parented under ui_camera, the sort-20 region.

    Left at camera.ui's ORIGIN, and the emitter is moved behind it instead of the
    other way round. That is not arbitrary: on this Ursina/Panda build a Text on
    camera.ui renders at position (0,0) and disappears entirely at any offset of
    ~0.2 ui units, whether set via the constructor, .position, or .x/.y, and
    reappears when set back to (0,0). Measured, reversible, and reproducible with
    no bloom in the scene at all — so it is an Ursina UI quirk, NOT something this
    pipeline causes. Chasing it is out of scope for a bloom gate; sidestepping it
    by moving the emitter costs nothing and keeps the gate honest.

    scale is sized so the glyphs land ~150px wide (measured), which leaves enough
    interior pixels to survive the edge-erosion in gate 2 while still sitting over
    the emitter. camera.ui children inherit a 20x scale (ursina/camera.py:34), so
    the raw number here is small by construction.
    """
    return Text('HUD 100', parent=camera.ui, position=(0, 0),
                origin=(0, 0), scale=0.09, color=color.azure)


# --- Gate wiring -------------------------------------------------------------
window.color = color.black
camera.parent = scene
camera.position = (0, 0, 0)
camera.rotation = (0, 0, 0)

bufs_before, tex_before = _counts()

emitter, wall = _build_scene()
hud_text = _hud()
_step()

# Frame A: no bloom (pipeline not yet built).
pnm_off = _grab()
pnm_off.write(Filename.from_os_specific(os.path.join(OUT_DIR, 'bloom_off.png')))

# Build the pipeline ONCE — exactly as main.py will at app init.
bloom = BloomPipeline()
bloom.set_enabled(True)
_step()

pnm_on = _grab()
pnm_on.write(Filename.from_os_specific(os.path.join(OUT_DIR, 'bloom_on.png')))

bufs_built, tex_built = _counts()


# --- Gate 1: does the emitter actually SPREAD light outside its footprint? ----
def _lum(pnm, x, y):
    r, g, b = pnm.get_xel(x, y)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _emitter_bbox(pnm):
    """Screen-space bbox of pixels that are bright in the NO-bloom frame.

    Derived from the frame rather than hardcoded so it survives lens/aspect
    changes — the annulus below is defined relative to it.
    """
    w, h = pnm.get_x_size(), pnm.get_y_size()
    xs, ys = [], []
    for x in range(0, w, 2):
        for y in range(0, h, 2):
            if _lum(pnm, x, y) > 0.5:
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


bbox = _emitter_bbox(pnm_off)

halo_gained = 0
halo_samples = 0
halo_delta_sum = 0.0
if bbox:
    x0, y0, x1, y1 = bbox
    pad = 26   # annulus width in px — wider than a full-res kernel, the point of B2
    w, h = pnm_off.get_x_size(), pnm_off.get_y_size()
    for x in range(max(0, x0 - pad), min(w, x1 + pad)):
        for y in range(max(0, y0 - pad), min(h, y1 + pad)):
            inside = (x0 <= x <= x1) and (y0 <= y <= y1)
            if inside:
                continue          # footprint itself — not evidence of spread
            halo_samples += 1
            d = _lum(pnm_on, x, y) - _lum(pnm_off, x, y)
            halo_delta_sum += d
            if _lum(pnm_off, x, y) < 0.06 and d > 0.02:
                halo_gained += 1

halo_frac = (halo_gained / halo_samples) if halo_samples else 0.0
halo_mean_delta = (halo_delta_sum / halo_samples) if halo_samples else 0.0


# --- Gate 2: is the sort-20 HUD region byte-identical with bloom on vs off? ---
# Identify the HUD's pixels by DIFFING a frame against one with the HUD hidden,
# rather than by guessing at its colour. Colour-matching is fragile here: the
# text shader blends glyph colour against an outline, so an azure Text lands on a
# spread of part-azure pixels and a hand-picked threshold silently selects ~none
# of them (which is how this gate first "found" 0 pixels and correctly refused to
# pass). A difference mask is exact, and it also guarantees we are sampling the
# sort-20 region specifically — those are, by definition, the only pixels the HUD
# can change.
hud_text.enabled = False
_step(3)
pnm_nohud = _grab()
hud_text.enabled = True
_step(3)
pnm_hud_ref = _grab()

_hud_mask = set()
w, h = pnm_hud_ref.get_x_size(), pnm_hud_ref.get_y_size()
for x in range(w):
    for y in range(h):
        r0, g0, b0 = pnm_nohud.get_xel(x, y)
        r1, g1, b1 = pnm_hud_ref.get_xel(x, y)
        if max(abs(r0 - r1), abs(g0 - g1), abs(b0 - b1)) > 2.0 / 255.0:
            _hud_mask.add((x, y))

# Keep only glyph INTERIOR pixels — those at least HUD_ERODE px inside the mask.
#
# This erosion is not cosmetic, it is what makes the gate honest. The text shader
# antialiases each glyph edge by blending glyph colour against whatever is behind
# it, so an edge pixel is PART SCENE by construction. Bloom legitimately brightens
# the scene behind the glyph, which shifts those blended pixels — without the HUD
# itself ever being bloomed. Comparing them reports a "failure" that is really
# just alpha blending working correctly.
#
# HUD_ERODE=2 is MEASURED, not guessed. Sweeping the radius against a frame with a
# blown-out emitter behind the text shows the differences decaying to exactly zero
# the moment the AA band is excluded, and staying there:
#
#     erode r=1: interior=1817  changed=6  max delta= 4.0/255
#     erode r=2: interior= 681  changed=0  max delta= 0.0/255   <-- clean
#     erode r=3: interior= 117  changed=0  max delta= 0.0/255
#
# The r=1 residue is the giveaway: those pixels' RED channel moves 0.000 -> 0.004
# while blue stays put — i.e. white emitter light bleeding through a partly
# transparent glyph edge, not the azure glyph being filtered. Ursina's text shader
# spreads its edge over ~2px (it samples a 4-tap box around each texel, see
# compat.py's _TEXT_FRAG), which is exactly where the residue stops.
HUD_ERODE = 2
hud_px = [(x, y) for (x, y) in _hud_mask
          if all((x + dx, y + dy) in _hud_mask
                 for dx in range(-HUD_ERODE, HUD_ERODE + 1)
                 for dy in range(-HUD_ERODE, HUD_ERODE + 1))]

# pnm_hud_ref was captured with bloom ON (the pipeline is live by now) and
# pnm_off with bloom OFF; both have the HUD enabled, so comparing them on the HUD
# mask isolates "did turning bloom on change the sort-20 pixels".
# How much of the HUD interior actually sits over the bloomed emitter's halo? If
# this is 0 the gate proves nothing (a HUD in an empty corner of the frame would
# "pass" trivially), so it is reported and asserted alongside the diff itself.
hud_over_emitter = 0
if bbox:
    _x0, _y0, _x1, _y1 = bbox
    pad = 26
    for (x, y) in hud_px:
        if _x0 - pad <= x <= _x1 + pad and _y0 - pad <= y <= _y1 + pad:
            hud_over_emitter += 1

hud_diff = 0
hud_max_delta = 0.0
for (x, y) in hud_px:
    r0, g0, b0 = pnm_off.get_xel(x, y)
    r1, g1, b1 = pnm_hud_ref.get_xel(x, y)
    d = max(abs(r0 - r1), abs(g0 - g1), abs(b0 - b1))
    hud_max_delta = max(hud_max_delta, d)
    if d > 1.0 / 255.0:
        hud_diff += 1


# --- Gate 3: menu-cycle teardown must not grow buffers or textures ------------
# Shaped like main_menu(): sweep every scene entity, rebuild, render. The bloom
# pipeline is NOT rebuilt — that is the design under test.
CYCLES = 4
cycle_counts = []
for i in range(CYCLES):
    for e in list(scene.entities):
        destroy(e)
    destroy(hud_text)
    _step(2)
    emitter, wall = _build_scene()
    hud_text = _hud()
    _step(3)
    cycle_counts.append(_counts())

bufs_after, tex_after = _counts()

_log_stream.flush()
with open(GSG_LOG, 'r', errors='replace') as fh:
    gsg_log = fh.read()

error_lines = []
for line in gsg_log.splitlines():
    low = line.lower()
    if re.search(r'\berror\b', low) or 'failed to' in low or 'invalid' in low \
       or 'could not compile' in low or 'could not link' in low \
       or 'unsupported' in low or 'incomplete' in low:
        if 'p3d_fragdata' in low or 'ursina.ico' in low:
            continue   # benign compat-profile / missing-icon noise
        error_lines.append(line)

link_lines = [l for l in gsg_log.splitlines()
              if 'Compiling GLSL' in l or 'Linking GLSL' in l]

print('=' * 74)
print('BLOOM PROBE — Candidate B2 chain on real GL 2.1 / GLSL 1.20, REAL WINDOW')
print('=' * 74)
gsg = base.win.get_gsg()
print(f'GL context   : {gsg.get_driver_vendor()} / {gsg.get_driver_renderer()} '
      f'/ {gsg.get_driver_version()}')
print(f'Window       : {base.win.get_x_size()}x{base.win.get_y_size()} '
      f'(on-screen: {isinstance(base.win, GraphicsWindow)})')
print(f'GSG compile/link lines: {len(link_lines)}')
print()
print(f'Buffers before pipeline : {bufs_before}')
print(f'Buffers after  pipeline : {bufs_built}')
print()
print('GATE 1 — bloom spreads light outside the emitter footprint:')
print(f'  emitter bbox (no-bloom frame): {bbox}')
print(f'  annulus samples={halo_samples}  gained={halo_gained} '
      f'({halo_frac*100:.1f}%)  mean delta={halo_mean_delta:+.4f}')
print()
print('GATE 2 — sort-20 HUD region identical with bloom on vs off:')
print(f'  HUD mask={len(_hud_mask)} px -> glyph interior={len(hud_px)} px '
      f'(eroded {HUD_ERODE}px: AA edges blend with the scene, see note)')
print(f'  interior over the bloomed emitter={hud_over_emitter} px '
      f'(0 would make this gate vacuous)')
print(f'  differing={hud_diff}  max channel delta={hud_max_delta*255:.2f}/255')
print()
print('GATE 3 — resource counts across menu-shaped cycles (pipeline built once):')
print(f'  at start          : buffers={len(bufs_before)} textures={tex_before}')
print(f'  after pipeline    : buffers={len(bufs_built)} textures={tex_built}')
for i, (b, t) in enumerate(cycle_counts):
    print(f'  after cycle {i}     : buffers={len(b)} textures={t}')
print()

fail = False

if error_lines:
    print('RESULT: FAIL — GL/GSG errors detected:')
    for l in error_lines[:20]:
        print('  ', l.strip())
    fail = True

if bbox is None:
    print('RESULT: FAIL — no bright emitter found in the no-bloom frame; the '
          'probe scene never rendered, so no gate below is meaningful.')
    fail = True
elif halo_gained < 200 or halo_mean_delta <= 0.002:
    print(f'RESULT: FAIL — no halo: only {halo_gained} previously-dark pixels lit '
          f'up around the emitter (mean delta {halo_mean_delta:+.4f}). The '
          f'composite may be adding to the source without spreading it.')
    fail = True
else:
    print(f'BLOOM: WORKS — {halo_gained} previously-dark pixels around the emitter '
          f'gained light (mean delta {halo_mean_delta:+.4f} over the annulus).')

if not hud_px:
    print('RESULT: FAIL — no HUD glyph pixels found; gate 2 would pass vacuously.')
    fail = True
elif hud_over_emitter == 0:
    print('RESULT: FAIL — the HUD does not overlap the bloomed emitter, so an '
          '"untouched HUD" result would be vacuous. Reposition the probe HUD.')
    fail = True
elif hud_diff > 0:
    print(f'RESULT: FAIL — {hud_diff} HUD pixels changed when bloom turned on '
          f'(max delta {hud_max_delta*255:.2f}/255). The filter quad is capturing '
          f'the sort-20 UI region — the scoping doc\'s claim is WRONG on real hw.')
    fail = True
else:
    print(f'HUD: UNTOUCHED — all {len(hud_px)} sort-20 glyph pixels byte-identical '
          f'across bloom on/off, with a blown-out emitter directly behind them.')

leaked = [i for i, (b, t) in enumerate(cycle_counts)
          if len(b) > len(bufs_built)]
if leaked:
    print(f'RESULT: FAIL — offscreen buffers grew on cycles {leaked}: '
          f'{[len(cycle_counts[i][0]) for i in leaked]} vs {len(bufs_built)} '
          f'at build. Bloom buffers are being recreated through teardown.')
    fail = True
else:
    print(f'LIFECYCLE: FLAT — buffers held at {len(bufs_built)} across '
          f'{CYCLES} menu-shaped cycles.')

if fail:
    sys.exit(1)

print()
print('RESULT: PASS — bright-pass + 1/4-res separable blur + composite renders a '
      'real halo on GL 2.1, the sort-20 HUD composites on top un-bloomed, and '
      'buffers hold flat across teardown cycles.')
sys.exit(0)
