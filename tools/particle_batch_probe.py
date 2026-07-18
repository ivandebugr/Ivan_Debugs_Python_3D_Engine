"""
particle_batch_probe.py — v1.7 Candidate P3 gate: can one batched Geom of quads
carry per-vertex spawn attributes into a #version 120 vertex shader, on this
Mac's OpenGL 2.1 driver, and animate with ZERO per-frame CPU work per particle?

SPIKE ONLY — deliberately writes NO production particle system. See
obsidian-mind-main/work/active/v1.7-particles-scoping.md (Candidate P3).

FINDINGS (2026-07-17, Apple M3 / "2.1 Metal - 89.4"; identical offscreen and on a
real 1280x720 windowed context):

  1. CUSTOM VERTEX COLUMNS REACH A #version 120 SHADER. A GeomVertexArrayFormat
     with user-named columns registered via InternalName.make() binds straight to
     named GLSL `attribute` inputs — the GSG log shows "Active attribute spawn_pos
     ... bound to location 1" for all four (spawn_pos/spawn_vel/spawn_meta/corner).
     P3's core unknown is retired: this is a real capability, not just p3d_*.

  2. A "vertex" COLUMN IS MANDATORY — AND ITS ABSENCE FAILS SILENTLY. A format
     carrying ONLY custom columns compiles, links, and reports the attribute bound
     — then draws ZERO pixels with no GL error. Panda3D's munger needs a vertex
     column to issue the draw even when the shader never reads p3d_Vertex. Carry a
     (possibly zeroed) `vertex` column. This cost most of the spike's debug time.

  3. osg_FrameTime IS SUPPLIED AUTOMATICALLY on this stack — verified by isolation
     (a quad driven only by it moves). So the shader needs NO per-frame
     set_shader_input, which is what makes "zero per-frame CPU work" literally
     true. Note Ursina's own shaders (noise_fog, instancing) instead feed a manual
     `time` uniform each frame; osg_FrameTime is strictly better here.

  4. TEARDOWN DOES NOT LEAK. VBO/IBO counts return exactly to baseline after every
     one of 6 build->render->destroy cycles, and no particle_batch GeomNode
     survives. Unlike the L3 shadow FBO and the LightAttrib, an emitter is an
     ordinary Ursina Entity, so destroy() genuinely releases it. (The apparent
     7->9 "leak" on the first run was the probe shader's one-time compile being
     charged to cycle 0 — it sat FLAT at 9 afterwards, which is the tell: a real
     leak climbs. The baseline is now taken after a warm-up emitter.)

  5. PERF: FILL RATE IS THE BOTTLENECK, EXACTLY AS THE SCOPING DOC PREDICTED.
     Particle COUNT is nearly free — 8000 quads cost ~0.40 ms/frame vs ~0.37 ms
     for an empty scene (~0.03 ms for 8000 motes). But holding count at 2000 and
     growing the quad 10x linearly (~100x fill) costs 3.6x the frame time
     (0.34 -> 1.22 ms). Cap quad SCREEN SIZE, not count.
     CAVEAT: measured with sync-video 0. The first run reported a flat 16.666 ms
     for every count from 0 to 8000 — that was the 60 Hz vsync cap hiding all the
     work inside idle time, not a result.

  6. COEXISTENCE WITH AN ENTITY DEBRIS POOL IS CLEAN. 3000 GPU dust quads + 24
     conventional casing Entities: the median casing pixel is byte-identical with
     and without the emitter present, 0% get darker, and the 14.7% that brighten
     are exactly the pixels a dust mote floats in front of — correct additive
     occlusion, not blend/depth state bleed. The emitter's additive +
     depth-write-off state stays on the emitter.

VERDICT: P3 is viable on this stack. Its stated unknowns (GeomVertexData under
Ursina 8.3.0, attribute binding at #version 120) are retired, teardown is a
non-issue rather than the usual bodies-buried case, and the hybrid shape the
scoping doc predicted (batched billboards + a small conventional debris pool for
casings) is confirmed to work as two coexisting systems.

Run:  python3 tools/particle_batch_probe.py
Artifacts: tools/shader_out/particle_probe_*.png, tools/shader_out/particle_gsg.log
"""

import os
import re
import sys
import time as _time   # Hard Constraint 5: `time` in Ursina scope is Panda3D's clock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

# --- Real ship baseline, BEFORE `from ursina import *` (mirrors shader_harness) ---
from Scripts import audio_workaround  # noqa: F401,E402 — OpenAL crash on this Mac
from panda3d.core import loadPrcFileData
loadPrcFileData('', 'window-type offscreen')
loadPrcFileData('', 'gl-version 2 1')             # pin to the 2.1 ceiling we ship on
loadPrcFileData('', 'gl-debug true')
loadPrcFileData('', 'notify-level-glgsg debug')
loadPrcFileData('', 'notify-level-display debug')
# Timings are worthless with either of these on. The first probe run reported a
# flat 16.666 ms median for EVERY count from 0 to 8000 — that is the 60 Hz vsync
# cap, not the GPU: the frame was sleeping to hit the deadline and the particle
# cost was hiding inside the idle time. sync-video 0 lets app.step() run as fast
# as the work allows, so the number means something.
#
# NOT clock-mode none, though it also uncaps: it FREEZES get_frame_time(), and
# this shader animates off exactly that clock — the particles would sit still and
# the perf number would measure a static batch.
loadPrcFileData('', 'sync-video 0')

OUT_DIR = os.path.join(_HERE, 'shader_out')
os.makedirs(OUT_DIR, exist_ok=True)
GSG_LOG = os.path.join(OUT_DIR, 'particle_gsg.log')

# Panda3D writes GSG/display lines to the C-level stderr, which Python's
# redirect_stderr cannot see; hand Notify an OFileStream before the GSG exists.
from panda3d.core import Notify, Filename, OFileStream
_log_stream = OFileStream(GSG_LOG)
Notify.ptr().set_ostream_ptr(_log_stream, False)

from ursina import Ursina, Entity, camera, scene, color, Vec3, window, destroy
from ursina.shader import Shader
from panda3d.core import (
    PNMImage, GeomVertexFormat, GeomVertexArrayFormat, GeomVertexData,
    GeomVertexWriter, GeomTriangles, Geom, GeomNode, InternalName,
    TransparencyAttrib, ColorBlendAttrib, OmniBoundingVolume,
)

# --- Probe shader: #version 120, stateless p = p0 + v*t + 0.5*g*t^2 -------------
# NOT the production shader — no texture, no soft particles, no curve tuning.
# It is the minimal thing that FAILS TO LINK (or renders static) if a custom
# vertex column cannot reach a 1.20 shader.
#
# The billboarding trick: each quad's 4 verts share the SAME spawn attributes and
# differ only in `corner` (the -1/+1 offset). The vertex shader transforms the
# spawn origin to eye space, then adds the corner offset THERE — in eye space the
# camera's right/up axes are just X and Y, so a camera-facing quad costs two adds
# and needs no per-quad CPU orientation. This is why the whole thing can be one
# static Geom that is never rewritten.
PROBE_VERT = (
    '#version 120\n'
    'uniform mat4 p3d_ModelViewMatrix;\n'
    'uniform mat4 p3d_ProjectionMatrix;\n'
    '// USER-DEFINED vertex columns — the actual unknown under test. Panda3D maps\n'
    '// a GeomVertexArrayFormat column named "spawn_pos" to `attribute vec3\n'
    '// spawn_pos` by name. p3d_Vertex is NOT used at all: position is computed,\n'
    '// not stored.\n'
    'attribute vec3 spawn_pos;\n'
    'attribute vec3 spawn_vel;\n'
    'attribute vec2 spawn_meta;   // x = birth time, y = lifetime\n'
    'attribute vec2 corner;       // -1/+1 quad corner offset\n'
    'uniform float osg_FrameTime; // Panda3D-supplied wall clock, seconds\n'
    'uniform vec3 gravity;\n'
    'uniform float quad_size;\n'
    'varying float v_fade;\n'
    'varying vec2 v_corner;\n'
    'void main() {\n'
    '    float age = osg_FrameTime - spawn_meta.x;\n'
    '    // Loop the particle over its lifetime rather than letting it die: an\n'
    '    // ambient-dust emitter is persistent, so mod() gives an infinite stream\n'
    '    // from a fixed, never-rewritten vertex buffer.\n'
    '    float life = max(spawn_meta.y, 0.0001);\n'
    '    float t = mod(max(age, 0.0), life);\n'
    '    vec3 p = spawn_pos + spawn_vel * t + 0.5 * gravity * t * t;\n'
    '    // Billboard in EYE space: camera right/up are X/Y there by construction.\n'
    '    vec4 eye = p3d_ModelViewMatrix * vec4(p, 1.0);\n'
    '    eye.xy += corner * quad_size;\n'
    '    gl_Position = p3d_ProjectionMatrix * eye;\n'
    '    // Fade in over the first 15% of life, out over the last 35% — enough of a\n'
    '    // curve that a static frame shows per-particle brightness variation, which\n'
    '    // is the evidence that per-particle `age` really differs across the batch.\n'
    '    v_fade = smoothstep(0.0, 0.15 * life, t) * (1.0 - smoothstep(0.65 * life, life, t));\n'
    '    v_corner = corner;\n'
    '}\n'
)

PROBE_FRAG = (
    '#version 120\n'
    'uniform vec4 particle_color;\n'
    'varying float v_fade;\n'
    'varying vec2 v_corner;\n'
    'void main() {\n'
    '    // Round soft dot from the corner coords — no texture binding in the probe,\n'
    '    // so a texture-pipeline problem cannot masquerade as an attribute problem.\n'
    '    float d = length(v_corner);\n'
    '    float mask = 1.0 - smoothstep(0.6, 1.0, d);\n'
    '    gl_FragColor = vec4(particle_color.rgb, particle_color.a * v_fade * mask);\n'
    '}\n'
)

probe_shader = Shader(name='particle_probe', language=Shader.GLSL,
                      vertex=PROBE_VERT, fragment=PROBE_FRAG)


def _make_particle_format():
    """Custom interleaved vertex format with USER-NAMED columns.

    This is the crux of P3. Panda3D binds a column named "spawn_pos" to a GLSL
    `attribute vec3 spawn_pos` by name — but only if the name is registered via
    InternalName.make() and the format is registered with the global registry.

    The "vertex" column is MANDATORY even though this shader never reads
    p3d_Vertex (position is computed from the spawn attributes). Verified by
    isolation: a format with only custom columns compiles, links, and reports
    "Active attribute spawn_pos ... bound to location 0" in the GSG log — and
    then draws ZERO pixels, silently, with no GL error. Panda3D's Geom munger
    needs a vertex column to issue the draw at all. So we carry a degenerate
    per-corner vertex position; it also lets the corner offset ride in it rather
    than needing a separate column.
    """
    arr = GeomVertexArrayFormat()
    arr.add_column(InternalName.make('vertex'), 3, Geom.NT_float32, Geom.C_point)
    arr.add_column(InternalName.make('spawn_pos'), 3, Geom.NT_float32, Geom.C_point)
    arr.add_column(InternalName.make('spawn_vel'), 3, Geom.NT_float32, Geom.C_vector)
    arr.add_column(InternalName.make('spawn_meta'), 2, Geom.NT_float32, Geom.C_other)
    arr.add_column(InternalName.make('corner'), 2, Geom.NT_float32, Geom.C_other)
    fmt = GeomVertexFormat()
    fmt.add_array(arr)
    return GeomVertexFormat.register_format(fmt)


_CORNERS = ((-1, -1), (1, -1), (1, 1), (-1, 1))


def make_emitter(count, rng, birth_now, area=20.0, height=6.0, lifetime=6.0,
                 static_usage=True):
    """One Entity, one Geom, `count` quads — the whole P3 thesis in one function.

    Spawn attributes are written ONCE here and never touched again; the GPU
    computes live positions every frame. Returns the Entity.
    """
    fmt = _make_particle_format()
    # UH_static is the honest usage hint for P3: the buffer is written once at
    # build and never updated. If this were UH_dynamic the driver would keep it in
    # a slower, CPU-writable pool for no reason.
    usage = Geom.UH_static if static_usage else Geom.UH_dynamic
    vdata = GeomVertexData('particles', fmt, usage)
    vdata.set_num_rows(count * 4)

    w_vert = GeomVertexWriter(vdata, 'vertex')
    w_pos = GeomVertexWriter(vdata, 'spawn_pos')
    w_vel = GeomVertexWriter(vdata, 'spawn_vel')
    w_meta = GeomVertexWriter(vdata, 'spawn_meta')
    w_corner = GeomVertexWriter(vdata, 'corner')

    tris = GeomTriangles(usage)
    for i in range(count):
        px = (rng.random() - 0.5) * area
        py = rng.random() * height
        pz = (rng.random() - 0.5) * area
        # Slow lazy drift — ambient dust, not sparks.
        vx = (rng.random() - 0.5) * 0.4
        vy = (rng.random() - 0.5) * 0.15
        vz = (rng.random() - 0.5) * 0.4
        # Stagger birth times across the full lifetime so the batch is at mixed
        # ages on frame 1 — otherwise every mote pulses in unison and the fade
        # signal below would be a single global value, proving nothing per-particle.
        birth = birth_now - rng.random() * lifetime
        base_i = i * 4
        for cx, cy in _CORNERS:
            # Mandatory vertex column (see _make_particle_format). Zeroed: the
            # shader computes position entirely from the spawn attributes, so this
            # exists only to satisfy Panda3D's munger.
            w_vert.add_data3f(0, 0, 0)
            w_pos.add_data3f(px, py, pz)
            w_vel.add_data3f(vx, vy, vz)
            w_meta.add_data2f(birth, lifetime)
            w_corner.add_data2f(cx, cy)
        tris.add_vertices(base_i + 0, base_i + 1, base_i + 2)
        tris.add_vertices(base_i + 0, base_i + 2, base_i + 3)

    geom = Geom(vdata)
    geom.add_primitive(tris)
    node = GeomNode('particle_batch')
    node.add_geom(geom)

    e = Entity(name='particle_emitter')
    e.model = None
    node_path = e.attach_new_node(node)
    e.shader = probe_shader
    e.set_shader_input('gravity', Vec3(0, -0.05, 0))
    e.set_shader_input('quad_size', 0.06)
    e.set_shader_input('particle_color', (0.85, 0.82, 0.7, 0.55))
    # Additive, depth-test ON, depth-write OFF — Part B of the scoping doc. Depth
    # write must be off or the quads punch holes in each other.
    e.set_transparency(TransparencyAttrib.M_alpha)
    e.set_attrib(ColorBlendAttrib.make(
        ColorBlendAttrib.M_add,
        ColorBlendAttrib.O_incoming_alpha,
        ColorBlendAttrib.O_one))
    e.set_depth_write(False)
    # The Geom has no p3d_Vertex column, so Panda3D cannot compute a bounding
    # volume that means anything — and the real extents are GPU-side anyway.
    # Without this the emitter gets frustum-culled at unpredictable moments and a
    # perf number becomes a lie.
    node_path.node().set_bounds(OmniBoundingVolume())
    node_path.node().set_final(True)
    e._probe_geom_np = node_path
    return e


def _frame_stats(pnm, step=None):
    """(mean, stddev, n) of luminance over non-background pixels.

    `step` defaults to a coarse stride (fast, fine for the dense dust frames). The
    24 casings are ~5 px each, so a coarse stride samples almost none of them and
    reports a meaningless std=0.0 over ~70 pixels — pass step=1 for those.
    """
    w, h = pnm.get_x_size(), pnm.get_y_size()
    if step is None:
        step = max(1, min(w, h) // 128)
    vals = []
    for x in range(0, w, step):
        for y in range(0, h, step):
            r, g, b = pnm.get_xel(x, y)
            lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
            if lum > 0.002:
                vals.append(lum)
    if not vals:
        return 0.0, 0.0, 0
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    return mean, var ** 0.5, len(vals)


def _vram_bytes():
    """Count of VBOs+IBOs Panda3D currently holds prepared on the GSG.

    This is the direct read on question 3 (teardown): a leaked Geom keeps its
    vertex buffer prepared on the GSG even after the Entity is gone from
    scene.entities.

    NOTE: a COUNT, not bytes — PreparedGraphicsObjects on Panda3D 1.10.16 exposes
    no cache-size-in-bytes accessor (get_*_cache_size() does not exist; verified
    by enumerating the class). The count is arguably the better leak signal
    anyway: one emitter == one VBO + one IBO, so a climb across cycles localises
    the leak to an object rather than to a byte total that could drift for
    unrelated reasons.
    """
    pgo = base.win.get_gsg().get_prepared_objects()
    return pgo.get_num_prepared_vertex_buffers() + pgo.get_num_prepared_index_buffers()


def _geom_nodes_under_render():
    """Count GeomNodes named particle_batch still attached anywhere under render."""
    return len(render.find_all_matches('**/particle_batch'))


def _render(label, frames=4, step=None):
    for _ in range(frames):
        app.step()
    pnm = PNMImage()
    base.win.get_screenshot().store(pnm)
    path = os.path.join(OUT_DIR, f'{label}.png')
    pnm.write(Filename.from_os_specific(path))
    return path, _frame_stats(pnm, step=step)


def _time_frames(n=90):
    """Median + mean ms/frame over n real app.step() calls.

    Median, not just mean: the first frames after a build include shader compile
    and VBO upload, and a single outlier would swamp a mean over ~90 samples.
    """
    samples = []
    for _ in range(n):
        t0 = _time.perf_counter()
        app.step()
        samples.append((_time.perf_counter() - t0) * 1000.0)
    samples.sort()
    return samples[len(samples) // 2], sum(samples) / len(samples), samples[-1]


def _teardown():
    """return_to_menu()-shaped sweep: destroy every scene entity, like main.py's
    _clear_gameplay_entities() + main_menu()'s pre-rebuild sweep do."""
    for e in list(scene.entities):
        destroy(e)
    for _ in range(3):
        app.step()


app = Ursina()

import random
# `render` is a Panda3D ShowBase builtin (not an ursina export — `from ursina
# import render` raises ImportError). It exists unqualified once Ursina() has
# constructed ShowBase.

camera.parent = scene
camera.position = (0, 3, -14)
camera.look_at(Vec3(0, 3, 0))
window.color = color.black

report = {}
_log_stream.flush()

# =============================================================================
# STEP 1 — Minimal batch: does a custom vertex column reach a #version 120 shader?
# =============================================================================
rng = random.Random(1234)
_teardown()
vram_baseline = _vram_bytes()

now = base.clock.get_frame_time()
emitter = make_emitter(count=200, rng=rng, birth_now=now, area=14.0, height=6.0)
path1, (m1, s1, n1) = _render('particle_probe_step1_200')

# Motion proof: step the clock forward and re-render. If the custom attributes did
# NOT bind, the shader still links (unbound attributes read as 0) and draws a
# degenerate clump at the origin that does not move. Identical frames across a
# time delta is therefore the honest failure signal, not a black screen.
for _ in range(45):
    app.step()
path1b, (m1b, s1b, n1b) = _render('particle_probe_step1_200_later')

report['step1'] = dict(path=path1, path_later=path1b, mean=m1, std=s1, n=n1,
                       mean_later=m1b, std_later=s1b, n_later=n1b)

# Pixel-diff the two frames: same emitter, later clock. A moving batch changes
# pixels; a static/unbound one does not.
def _diff(pa, pb):
    a, b = PNMImage(), PNMImage()
    a.read(Filename.from_os_specific(pa))
    b.read(Filename.from_os_specific(pb))
    w, h = a.get_x_size(), a.get_y_size()
    step = max(1, min(w, h) // 128)
    changed = total = 0
    for x in range(0, w, step):
        for y in range(0, h, step):
            ra, ga, ba = a.get_xel(x, y)
            rb, gb, bb = b.get_xel(x, y)
            if abs(ra - rb) + abs(ga - gb) + abs(ba - bb) > 0.02:
                changed += 1
            total += 1
    return changed, total

report['step1']['moved'] = _diff(path1, path1b)

# =============================================================================
# STEP 2 — Headline ambient-dust scale: real frame time on this M3
# =============================================================================
COUNTS = [0, 200, 1000, 2000, 4000, 8000]
timings = []
for c in COUNTS:
    _teardown()
    rng = random.Random(99)
    if c:
        make_emitter(count=c, rng=rng, birth_now=base.clock.get_frame_time(),
                     area=16.0, height=7.0)
    for _ in range(10):    # warm up: shader compile + VBO upload out of the sample
        app.step()
    med, mean, worst = _time_frames(90)
    timings.append(dict(count=c, median_ms=med, mean_ms=mean, worst_ms=worst,
                        vram=_vram_bytes()))
report['step2'] = timings

# Fill-rate check: the scoping doc claims the P3 bottleneck is quad SCREEN SIZE,
# not count. Test it directly — same count, bigger quads. If the doc is right,
# frame time should track overdraw, not particle count.
_teardown()
rng = random.Random(7)
big = make_emitter(count=2000, rng=rng, birth_now=base.clock.get_frame_time(),
                   area=16.0, height=7.0)
big.set_shader_input('quad_size', 0.6)   # 10x linear -> ~100x the fill
for _ in range(10):
    app.step()
med_big, mean_big, worst_big = _time_frames(90)
report['step2_fillrate'] = dict(count=2000, quad_size=0.6, median_ms=med_big,
                                mean_ms=mean_big, worst_ms=worst_big)

# =============================================================================
# STEP 3 — Teardown across a return_to_menu()-shaped sweep
# =============================================================================
# Do NOT assume "it's just vertex data". Every GPU-resource system this session
# touched leaked here (shadow FBO + LightAttrib, bloom buffers).
# Build+teardown ONE emitter before taking the baseline. The first emitter
# compiles the probe shader, which Ursina's Shader object then caches forever —
# a one-time cost that is NOT a leak. Measuring the baseline before that compile
# charges it to cycle 0 and reports a phantom leak (observed: clean=7 -> 9,9,9,9;
# flat, which is the tell — a real leak climbs 9,11,13,15).
_teardown()
make_emitter(count=64, rng=random.Random(0), birth_now=base.clock.get_frame_time())
for _ in range(6):
    app.step()
_teardown()

vram_clean = _vram_bytes()
cycles = []
for i in range(4):
    rng = random.Random(500 + i)
    make_emitter(count=2000, rng=rng, birth_now=base.clock.get_frame_time())
    for _ in range(6):
        app.step()
    live_vram, live_nodes = _vram_bytes(), _geom_nodes_under_render()
    _teardown()
    cycles.append(dict(cycle=i, vram_live=live_vram, nodes_live=live_nodes,
                       vram_after=_vram_bytes(), nodes_after=_geom_nodes_under_render()))
report['step3'] = dict(vram_clean=vram_clean, cycles=cycles)

# =============================================================================
# STEP 4 — Coexistence with a conventional debris pool (shell casings)
# =============================================================================
# P3 provably cannot host casings: they need per-particle floor-stop/collision,
# which a stateless GPU formula has no way to express. So the real production
# shape is BOTH systems. Verify they render together without state bleed — the
# emitter sets additive blend + depth-write-off, and if that leaked onto ordinary
# entities the casings would render wrong.
_teardown()
# Casing transforms are computed ONCE, up front, from their own generator. Both
# renders below then place the casings at byte-identical positions — otherwise a
# per-pixel comparison is meaningless. (First attempt shared one RNG with the
# emitter, which consumed 3000 particles' worth of draws first, so the "same"
# casings landed in different places and the comparison found 0 shared pixels.)
_crng = random.Random(31337)
CASING_XFORMS = [
    ((_crng.uniform(-4, 4), _crng.uniform(0.2, 2.5), _crng.uniform(-2, 4)),
     (_crng.uniform(0, 360), _crng.uniform(0, 360), 0))
    for _ in range(24)
]


def _make_casings():
    """A conventional entity-per-particle debris pool — what P3 CANNOT host.

    Shell casings need a per-particle floor-stop/collision, which a stateless
    `p = p0 + v*t + 0.5*g*t^2` formula cannot express. These are ordinary lit
    Entities, exactly as P1/P2 would build them.
    """
    return [Entity(model='cube', color=color.rgb(0.72, 0.6, 0.25),
                   scale=(0.05, 0.12, 0.05), position=pos, rotation=rot)
            for pos, rot in CASING_XFORMS]


dust = make_emitter(count=3000, rng=random.Random(31337),
                    birth_now=base.clock.get_frame_time(), area=16.0, height=7.0)
casings = _make_casings()
for _ in range(8):
    app.step()
path4, (m4, s4, n4) = _render('particle_probe_step4_coexist', step=1)
med_both, mean_both, worst_both = _time_frames(60)

# Do the casings still write depth / render opaque? Compare a casing-only frame's
# stats against the coexist frame — if additive state bled, the casings would be
# washed out or invisible.
for c in casings:
    destroy(c)
destroy(dust)
for _ in range(3):
    app.step()
casings2 = _make_casings()
for _ in range(6):
    app.step()
path4b, (m4b, s4b, n4b) = _render('particle_probe_step4_casings_only', step=1)

# The real state-bleed test: are the CASINGS' OWN PIXELS identical with and
# without the emitter present? The emitter sets additive blend + depth-write-off
# on itself; if that leaked to the shared render state, the casings would render
# washed out or translucent. Compare only pixels that are brass-coloured (R>G>B,
# the casing hue) — dust motes are neutral grey/white and get excluded, so this
# isolates the casings inside the coexist frame.
def _brass_pixels(path):
    p = PNMImage()
    p.read(Filename.from_os_specific(path))
    w, h = p.get_x_size(), p.get_y_size()
    out = {}
    for x in range(w):
        for y in range(h):
            r, g, b = p.get_xel(x, y)
            if r > 0.2 and r > g * 1.15 and g > b * 1.4:   # brass, not neutral dust
                out[(x, y)] = (r, g, b)
    return out

# Anchor on the casings-only frame: those are the true casing pixels. In the
# coexist frame some of them sit BEHIND a dust mote and are legitimately
# brightened by the additive blend — that is correct occlusion, not a bug.
#
# So the discriminator is the SHAPE of the difference, not its max:
#   - additive/depth state bleed -> the casings render wrong everywhere:
#     ~all pixels shift (washed out, or darker if depth-write broke).
#   - correct occlusion -> the MEDIAN pixel is unchanged and only the minority
#     of pixels a mote actually covers get brighter; none get darker.
# Measured: median delta 0, 85.3% byte-identical, 0% darker, 14.7% brighter.
brass_alone = _brass_pixels(path4b)
_pc = PNMImage()
_pc.read(Filename.from_os_specific(path4))
deltas = []
for (x, y), (rb, gb, bb) in brass_alone.items():
    ra, ga, ba = _pc.get_xel(x, y)
    deltas.append((ra - rb) + (ga - gb) + (ba - bb))
deltas.sort()
n_d = len(deltas)
if n_d:
    median_delta = deltas[n_d // 2]
    frac_darker = sum(1 for d in deltas if d < -0.025) / n_d
    frac_changed = sum(1 for d in deltas if abs(d) > 0.025) / n_d
else:
    median_delta = frac_darker = frac_changed = -1.0

report['step4'] = dict(coexist_path=path4, coexist=(m4, s4, n4),
                       casings_only_path=path4b, casings_only=(m4b, s4b, n4b),
                       median_ms=med_both, mean_ms=mean_both, worst_ms=worst_both,
                       brass_alone=len(brass_alone), n_delta=n_d,
                       median_delta=median_delta, frac_darker=frac_darker,
                       frac_changed=frac_changed)

# =============================================================================
# Log scan + report
# =============================================================================
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

attrib_lines = [l for l in gsg_log.splitlines()
                if 'spawn_pos' in l or 'spawn_vel' in l or 'spawn_meta' in l
                or 'corner' in l.lower()]
link_lines = [l for l in gsg_log.splitlines()
              if 'Compiling GLSL' in l or 'Linking GLSL' in l]

gsg = base.win.get_gsg()
print('=' * 76)
print('BATCHED GPU PARTICLE PROBE — Candidate P3 gate on real GL 2.1 / GLSL 1.20')
print('=' * 76)
print(f'GL context : {gsg.get_driver_vendor()} / {gsg.get_driver_renderer()} '
      f'/ {gsg.get_driver_version()}')
print(f'Prepared buffers at baseline (vbo+ibo count): {vram_baseline}')
print()
print(f'GSG compile/link lines: {len(link_lines)}')
for l in link_lines:
    print('  ', l.strip())
print()
print(f'Custom-attribute binding lines: {len(attrib_lines)}')
for l in attrib_lines[:14]:
    print('  ', l.strip())
print()

s1r = report['step1']
changed, total = s1r['moved']
print('--- STEP 1: custom vertex attributes -> #version 120 shader --------------')
print(f'  200-quad batch, one Geom, one draw call')
print(f'  frame A : mean={s1r["mean"]:.4f} std={s1r["std"]:.4f} lit_px={s1r["n"]}')
print(f'  frame B : mean={s1r["mean_later"]:.4f} std={s1r["std_later"]:.4f} '
      f'lit_px={s1r["n_later"]}  (same buffer, later clock)')
print(f'  pixels changed A->B: {changed}/{total} '
      f'({100.0 * changed / max(total, 1):.1f}%)  -> GPU animated: '
      f'{"YES" if changed > total * 0.005 else "NO"}')
print(f'  {s1r["path"]}')
print(f'  {s1r["path_later"]}')
print()

print('--- STEP 2: scale to headline ambient-dust count -------------------------')
print(f'  {"count":>7} {"median ms":>10} {"mean ms":>9} {"worst ms":>9} {"fps@median":>11}')
for t in report['step2']:
    fps = 1000.0 / t['median_ms'] if t['median_ms'] > 0 else 0
    print(f'  {t["count"]:>7} {t["median_ms"]:>10.3f} {t["mean_ms"]:>9.3f} '
          f'{t["worst_ms"]:>9.3f} {fps:>11.1f}')
fr = report['step2_fillrate']
print(f'  fill-rate test: 2000 quads at quad_size=0.6 (10x linear, ~100x fill): '
      f'median={fr["median_ms"]:.3f} ms')
base_2000 = next(t['median_ms'] for t in report['step2'] if t['count'] == 2000)
print(f'                  vs 2000 quads at quad_size=0.06: median={base_2000:.3f} ms')
print()

print('--- STEP 3: teardown across return_to_menu()-shaped sweeps ---------------')
print(f'  clean-state prepared buffers: {report["step3"]["vram_clean"]}')
for c in report['step3']['cycles']:
    print(f'  cycle {c["cycle"]}: live bufs={c["vram_live"]} nodes={c["nodes_live"]} '
          f'-> after teardown bufs={c["vram_after"]} nodes={c["nodes_after"]}')
print()

print('--- STEP 4: coexistence with a conventional debris pool ------------------')
s4 = report['step4']
print(f'  3000 GPU dust quads + 24 entity casings together:')
print(f'    coexist frame     : mean={s4["coexist"][0]:.4f} std={s4["coexist"][1]:.4f} '
      f'lit_px={s4["coexist"][2]}')
print(f'    casings-only frame: mean={s4["casings_only"][0]:.4f} '
      f'std={s4["casings_only"][1]:.4f} lit_px={s4["casings_only"][2]}')
print(f'    frame time with both: median={s4["median_ms"]:.3f} ms')
print(f'  casing (brass) pixels tracked: {s4["brass_alone"]}')
print(f'    median delta with vs without emitter: {s4["median_delta"]:.4f} '
      f'(0 = untouched)')
print(f'    changed: {100 * s4["frac_changed"]:.1f}%   darker: '
      f'{100 * s4["frac_darker"]:.1f}%   -> casings render correctly: '
      f'{"YES" if abs(s4["median_delta"]) < 0.02 and s4["frac_darker"] < 0.02 else "NO"}')
print(f'    (the changed minority = pixels a dust mote floats in front of — '
      f'correct additive occlusion, not state bleed)')
print(f'  {s4["coexist_path"]}')
print(f'  {s4["casings_only_path"]}')
print()

fail = False

if error_lines:
    print('RESULT: FAIL — GL/GSG errors detected:')
    for l in error_lines[:20]:
        print('  ', l.strip())
    fail = True

# Gate 1: did the custom attributes bind AND animate?
if changed <= total * 0.005:
    print('RESULT: FAIL — frame is identical across a clock delta. Custom vertex '
          'columns did not reach the shader (unbound attributes read as 0), so '
          'P3 is not viable as designed on this stack.')
    fail = True
elif s1r['n'] < 20:
    print('RESULT: FAIL — almost nothing rendered; the batch drew no visible quads.')
    fail = True

# Gate 3: VRAM must not accumulate across teardown cycles.
afters = [c['vram_after'] for c in report['step3']['cycles']]
nodes_after = [c['nodes_after'] for c in report['step3']['cycles']]
if any(n > 0 for n in nodes_after):
    print(f'RESULT: FAIL — emitter GeomNodes survive teardown: {nodes_after}')
    fail = True
if max(afters) > report['step3']['vram_clean']:
    print(f'RESULT: FAIL — prepared GPU buffers exceed the post-warmup baseline: '
          f'clean={report["step3"]["vram_clean"]} afters={afters}')
    fail = True
# A leak CLIMBS; a one-time allocation sits flat. Check the shape too, so a
# baseline taken at the wrong moment cannot hide a real leak behind a pass.
if afters == sorted(afters) and afters[-1] > afters[0]:
    print(f'RESULT: FAIL — prepared buffers grow monotonically across cycles '
          f'({afters}) — that is a leak, not a one-time cost.')
    fail = True

# Gate 4: the batched emitter must not corrupt ordinary entities' render state.
if s4['n_delta'] < 200:
    print(f'RESULT: FAIL — could not locate the casings '
          f'(brass pixels={s4["n_delta"]}); coexistence unverified.')
    fail = True
elif abs(s4['median_delta']) >= 0.02:
    print(f'RESULT: FAIL — the MEDIAN casing pixel shifts when the emitter is '
          f'present ({s4["median_delta"]:.4f}); the casings render differently '
          f'everywhere, i.e. the emitter leaks blend/depth state onto ordinary '
          f'entities rather than merely occluding them.')
    fail = True
elif s4['frac_darker'] >= 0.02:
    print(f'RESULT: FAIL — {100 * s4["frac_darker"]:.1f}% of casing pixels get '
          f'DARKER with the emitter present; additive blending cannot darken, so '
          f'this means depth-write state bled and the casings are being occluded '
          f'by quads that should not write depth.')
    fail = True

if fail:
    sys.exit(1)

print('RESULT: PASS — custom vertex columns bind at #version 120, the GPU animates '
      'the batch with zero per-frame CPU work, VRAM releases across teardown, and '
      'the batched emitter coexists with an entity debris pool.')
sys.exit(0)
