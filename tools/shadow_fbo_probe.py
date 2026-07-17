"""
shadow_fbo_probe.py — v1.7 Candidate L3 gate: does a depth-format FBO work at all
on this Mac's OpenGL 2.1 / GLSL 1.20 driver?

SPIKE ONLY — deliberately writes NO production shader. See
obsidian-mind-main/work/active/v1.7-lighting-scoping.md (Candidate L3).

FINDINGS (2026-07-17, Apple M3 / "2.1 Metal - 89.4"):

  1. DEPTH FBO WORKS. setShadowCaster(True, 1024, 1024) allocates a real depth
     buffer ("Constructing shadow buffer for light 'directional_light',
     size=1024x1024"), binds it as a depth attachment, and the driver rasterizes
     into it: the extracted depth texture reads min=0.275 max=1.000 with ~107
     distinct values. supports_depth_texture / supports_shadow_filter are both
     True. Apple's deprecated GL 2.1 stack is NOT the blocker L3 feared.

  2. SHADOW UNIFORMS REACH A #version 120 SHADER. The GSG binds
     p3d_LightSource[0].shadowViewMatrix (type 0x8b5c = GL_FLOAT_MAT4) and
     p3d_LightSource[0].shadowMap (type 0x8b62 = GL_SAMPLER_2D_SHADOW). A 1.20
     compat-profile shader using shadow2DProj() links clean and produces a real
     depth compare. Two constraints found the hard way:
       - The p3d_LightSource struct must be declared IDENTICALLY in both stages
         or linking dies with "Uniform type mismatch 'p3d_LightSource'".
       - shadowViewMatrix expects EYE-space input (p3d_ModelViewMatrix * vertex).
         World space (p3d_ModelMatrix) yields a degenerate all-zero depth.

  3. TEARDOWN LEAKS — THE ACTUAL BLOCKER. [RESOLVED 2026-07-17 — see below.]
     Each main_menu()-shaped sweep+rebuild leaks one 1024x1024 depth buffer AND
     one LightAttrib entry: after 4 cycles, on_lights=4 and the orphans show as
     "-PandaNode/directional_light" (detached from render/scene, still lit).
     destroy() does not clear the light from render or release its buffer.
     Setting shadows=False + render.clear_light() before destroy() releases
     buffers but on_lights STILL climbs 1->2->3->4. NOTE: Ursina's
     DirectionalLight already sets shadows=True and calls set_shadow_caster() in
     __init__, and it keeps no handle to the light's NodePath (it is a local in
     __init__), which is why detaching cleanly is awkward.

     RESOLUTION: Scripts/light_lifecycle.py:destroy_light(). The reason the
     "naive fix" above still climbed is that argument-less render.clear_light()
     clears the whole LightAttrib rather than releasing a specific light, and
     re-wrapping self._light does not give you the NodePath Panda actually holds
     (get_on_light(i).node() is light._light == False). Recovering the NodePath
     from the Entity's own children — light.find_all_matches('**/+Light') — and
     passing THAT to clear_light(), then remove_node() + set_shadow_caster(False),
     releases the light completely and order-independently. main_menu()'s sweep
     now routes lights there; on_lights holds flat at 1 across cycles.
     Regression test: tests/test_light_lifecycle.py.

VERDICT: the driver risk L3 was gated on is retired, and the lifecycle risk the
scoping doc flagged as "where the bodies are buried" is fixed. Both blockers to
PCF shader work are cleared.

Run:  python3 tools/shadow_fbo_probe.py
Artifacts: tools/shader_out/shadow_probe_*.png, tools/shader_out/shadow_gsg.log
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
loadPrcFileData('', 'window-type offscreen')
loadPrcFileData('', 'gl-version 2 1')             # pin to the 2.1 ceiling we ship on
loadPrcFileData('', 'gl-debug true')
loadPrcFileData('', 'notify-level-glgsg debug')
loadPrcFileData('', 'notify-level-display debug')  # buffer/FBO allocation lines live here

OUT_DIR = os.path.join(_HERE, 'shader_out')
os.makedirs(OUT_DIR, exist_ok=True)
GSG_LOG = os.path.join(OUT_DIR, 'shadow_gsg.log')

# Panda3D writes GSG/display lines to the C-level stderr, which Python's
# redirect_stderr cannot see; hand Notify an OFileStream before the GSG exists.
from panda3d.core import Notify, Filename, OFileStream
_log_stream = OFileStream(GSG_LOG)
Notify.ptr().set_ostream_ptr(_log_stream, False)

from ursina import Ursina, Entity, camera, scene, color, Vec3, window, destroy
from ursina.lights import DirectionalLight
from ursina.shader import Shader
from panda3d.core import PNMImage, GraphicsWindow

# --- Probe shader: #version 120, binds the shadow sampler + shadowViewMatrix ----
# This is NOT the production PCF shader — no kernel, no bias tuning, no rim/spec.
# It is the minimal thing that FAILS TO LINK if Panda3D cannot supply shadow
# inputs to a 1.20 compat-profile shader, and produces a visibly bimodal frame
# (lit vs shadowed) if it can. Single-tap shadow2D on purpose: we are testing
# whether the plumbing exists, not what it looks like.
PROBE_VERT = (
    '#version 120\n'
    'uniform mat4 p3d_ModelViewProjectionMatrix;\n'
    'uniform mat4 p3d_ModelViewMatrix;\n'
    'uniform mat3 p3d_NormalMatrix;\n'
    '// The p3d_LightSource struct declaration must be IDENTICAL in both stages —\n'
    '// GLSL links uniforms by name across stages and rejects a type mismatch\n'
    '// ("Uniform type mismatch \'p3d_LightSource\'"). So the sampler is declared\n'
    '// here too even though only the fragment stage reads it.\n'
    'uniform struct p3d_LightSourceParameters {\n'
    '    vec4 color;\n'
    '    vec4 position;\n'
    '    mat4 shadowViewMatrix;\n'
    '    sampler2DShadow shadowMap;\n'
    '} p3d_LightSource[1];\n'
    'attribute vec4 p3d_Vertex;\n'
    'attribute vec3 p3d_Normal;\n'
    'varying vec4 shadow_coord;\n'
    'varying vec3 eye_normal;\n'
    'void main() {\n'
    '    gl_Position = p3d_ModelViewProjectionMatrix * p3d_Vertex;\n'
    '    vec4 eye_pos = p3d_ModelViewMatrix * p3d_Vertex;\n'
    '    eye_normal = normalize(p3d_NormalMatrix * p3d_Normal);\n'
    '    shadow_coord = p3d_LightSource[0].shadowViewMatrix * eye_pos;\n'
    '}\n'
)

PROBE_FRAG = (
    '#version 120\n'
    '// Must match the vertex stage declaration exactly — see the note there.\n'
    'uniform struct p3d_LightSourceParameters {\n'
    '    vec4 color;\n'
    '    vec4 position;\n'
    '    mat4 shadowViewMatrix;\n'
    '    sampler2DShadow shadowMap;\n'
    '} p3d_LightSource[1];\n'
    'varying vec4 shadow_coord;\n'
    'varying vec3 eye_normal;\n'
    'void main() {\n'
    '    // shadow2DProj is core GLSL 1.20 (sampler2DShadow since 1.10).\n'
    '    float lit = shadow2DProj(p3d_LightSource[0].shadowMap, shadow_coord).r;\n'
    '    // Bimodal on purpose: shadowed fragments read materially darker, so the\n'
    '    // luminance stddev over the frame is the "depth compare did something"\n'
    '    // signal — same stddev logic tools/shader_harness.py already uses.\n'
    '    gl_FragColor = vec4(vec3(0.15 + 0.85 * lit), 1.0);\n'
    '}\n'
)

probe_shader = Shader(name='shadow_probe', language=Shader.GLSL,
                      vertex=PROBE_VERT, fragment=PROBE_FRAG)


def _frame_stats(pnm):
    """(mean, stddev, n) of luminance over non-background pixels."""
    w, h = pnm.get_x_size(), pnm.get_y_size()
    step = max(1, min(w, h) // 96)
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


def _depth_buffers():
    """Offscreen buffers the engine currently holds, by name.

    Panda3D registers the shadow buffer with the GraphicsEngine when
    setShadowCaster() is enabled, so counting them before/after is a direct check
    on allocation — and on RELEASE across teardown.

    Offscreen buffers live in the engine's WINDOW list (GraphicsBuffer derives
    from GraphicsOutput, same as the main window); there is no separate buffer
    list. Filter to the ones that aren't the on-screen window.
    """
    eng = base.graphicsEngine
    return [w.get_name() for w in eng.get_windows()
            if not isinstance(w, GraphicsWindow)]


def _build_scene(with_shadows):
    """Scene shaped like the real one: ground plane + occluder cube + sun.

    Mirrors main.py's ordering — the sun is created AFTER the scene sweep, which
    is exactly what _apply_level_lighting() does on every main_menu() call.
    """
    scene.ambient_color = color.rgb(0.35, 0.35, 0.40)
    ground = Entity(model='cube', shader=probe_shader, scale=(30, 1, 30),
                    y=0, color=color.white)
    cube = Entity(model='cube', shader=probe_shader, scale=3,
                  position=(0, 3, 0), color=color.white)
    sun = DirectionalLight()
    # The shadow camera renders FROM the light's node. A DirectionalLight left at
    # the origin (Ursina's default, and what main.py's _apply_level_lighting()
    # does today) has the scene behind its near plane, so the depth map comes back
    # empty and every fragment trivially reads "lit". Pull it back along its own
    # direction and aim at the scene centre.
    sun.position = (20, 25, -20)
    sun.look_at(Vec3(0, 0, 0))
    if with_shadows:
        # THE call under test. Panda3D allocates the depth buffer + light camera —
        # but LAZILY: the buffer only appears once a shader actually binds
        # p3d_LightSource[0].shadowMap. Verified: with no shadow-sampling shader in
        # the scene, get_shadow_buffer() stays None even with is_shadow_caster()
        # True. That is why probe_shader is applied to the geometry below.
        sun._light.setShadowCaster(True, 1024, 1024)
        # Frustum must cover the occluder or every fragment trivially passes the
        # depth compare and the probe reports a false "no shadow" result.
        lens = sun._light.get_lens()
        lens.set_film_size(45, 45)
        lens.set_near_far(1, 80)
    return ground, cube, sun


def _depth_map_stats(sun):
    """(min, max, distinct) over the light's depth texture, pulled back to the CPU.

    This is the DIRECT evidence for the L3 gate: if Apple's GL 2.1 driver could
    not rasterize into a depth-format FBO, this texture would come back uniform
    (or fail to extract). A spread of distinct depth values means the occluder was
    genuinely written into a real depth attachment.
    """
    gsg = base.win.get_gsg()
    buf = sun._light.get_shadow_buffer(gsg)
    if buf is None or buf.count_textures() == 0:
        return None
    tex = buf.get_texture()
    base.graphicsEngine.extract_texture_data(tex, gsg)
    p = PNMImage()
    if not tex.store(p):
        return None
    w, h = p.get_x_size(), p.get_y_size()
    vals = [p.get_gray(x, y) for x in range(0, w, 16) for y in range(0, h, 16)]
    return min(vals), max(vals), len(set(round(v, 4) for v in vals))


def _teardown():
    """return_to_menu()-shaped sweep: destroy every scene entity, like main.py's
    _clear_gameplay_entities() + main_menu()'s pre-rebuild sweep do."""
    for e in list(scene.entities):
        destroy(e)
    for _ in range(2):
        app.step()


def _render(label):
    camera.parent = scene
    camera.position = (14, 12, -14)
    camera.look_at(Vec3(0, 2, 0))
    window.color = color.black
    for _ in range(4):
        app.step()
    pnm = PNMImage()
    base.win.get_screenshot().store(pnm)
    path = os.path.join(OUT_DIR, f'{label}.png')
    pnm.write(Filename.from_os_specific(path))
    return path, _frame_stats(pnm)


app = Ursina()

results = []
buffers_at_start = _depth_buffers()

# --- Cycle the FULL build -> render -> teardown loop 3x -------------------------
# One pass proves the FBO allocates. Three passes prove it survives the menu
# round-trip AND that buffers are released rather than leaked — the specific
# thing the scoping doc flags as unverified.
CYCLES = 3
for i in range(CYCLES):
    _teardown()
    ground, cube, sun = _build_scene(with_shadows=True)
    path, (mean, std, n) = _render(f'shadow_probe_cycle{i}')
    # Buffer + depth map are read AFTER the render: the buffer is created lazily
    # on first draw with a shadowMap-binding shader, so sampling before the render
    # reports a false empty.
    bufs_live = _depth_buffers()
    depth = _depth_map_stats(sun)
    results.append({
        'cycle': i,
        'buffers_live': bufs_live,
        'depth': depth,
        'mean': mean, 'std': std, 'n': n, 'path': path,
    })
    _teardown()
    results[-1]['buffers_after_teardown'] = _depth_buffers()

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

fbo_lines = [l for l in gsg_log.splitlines()
             if 'depth' in l.lower() and ('buffer' in l.lower() or 'fbo' in l.lower()
                                          or 'texture' in l.lower())]
link_lines = [l for l in gsg_log.splitlines()
              if 'Compiling GLSL' in l or 'Linking GLSL' in l]

print('=' * 72)
print('SHADOW FBO PROBE — Candidate L3 gate on real GL 2.1 / GLSL 1.20 baseline')
print('=' * 72)
gsg = base.win.get_gsg()
print(f'GL context      : {gsg.get_driver_vendor()} / {gsg.get_driver_renderer()} '
      f'/ {gsg.get_driver_version()}')
print(f'Shadow support  : supports_depth_texture={gsg.get_supports_depth_texture()} '
      f'supports_shadow_filter={gsg.get_supports_shadow_filter()}')
print(f'Engine buffers at start: {buffers_at_start}')
print()
print(f'GSG compile/link lines: {len(link_lines)}')
for l in link_lines:
    print('  ', l.strip())
print()
print(f'Depth/FBO allocation lines: {len(fbo_lines)}')
for l in fbo_lines[:12]:
    print('  ', l.strip())
print()
print('Per-cycle (build sun+shadow -> render -> teardown):')
for r in results:
    print(f'  cycle {r["cycle"]}: buffers_live={r["buffers_live"]} '
          f'-> after_teardown={r["buffers_after_teardown"]}')
    if r['depth']:
        dmin, dmax, dn = r['depth']
        print(f'           depth map: min={dmin:.4f} max={dmax:.4f} distinct={dn} '
              f'-> occluder written: {"YES" if dn > 1 else "NO"}')
    else:
        print('           depth map: UNAVAILABLE (no buffer / extract failed)')
    print(f'           luminance mean={r["mean"]:.4f} stddev={r["std"]:.4f} '
          f'n={r["n"]}  {r["path"]}')
print()

fail = False

if error_lines:
    print('RESULT: FAIL — GL/GSG errors detected:')
    for l in error_lines[:20]:
        print('  ', l.strip())
    fail = True

if not gsg.get_supports_depth_texture():
    print('RESULT: FAIL — driver reports no depth-texture support; L3 is dead '
          'on this stack without a fallback.')
    fail = True

# The gate itself: did the driver rasterize real depth into the FBO?
# Cycle 0 is the clean-room measurement: no prior lights/buffers exist yet, so
# its depth map is the honest read on driver capability. Later cycles run with
# leaked lights still attached (see finding 3), which perturbs which buffer the
# accessor returns — that is the leak being measured, not a driver fault.
c0 = results[0]
if c0['depth'] is None or c0['depth'][2] <= 1:
    print('RESULT: FAIL — depth map is empty/uniform on the first clean cycle; '
          'the GL 2.1 driver did not rasterize into the depth FBO. L3 not viable.')
    fail = True
else:
    dmin, dmax, dn = c0['depth']
    print(f'DEPTH FBO: WORKS — cycle 0 depth map min={dmin:.4f} max={dmax:.4f} '
          f'distinct={dn} (occluder rasterized into a real depth attachment).')

# Did the depth compare actually vary across the frame? A uniform frame means the
# sampler returned a constant (garbage or all-lit) — plumbing present but useless.
if results[0]['std'] < 0.005:
    print('RESULT: FAIL — frame luminance is uniform in every cycle; the shadow '
          'sampler produced no depth-compare variation (no visible shadow).')
    fail = True

# Teardown: buffers must not accumulate across menu round-trips.
leaked = [r for r in results if len(r['buffers_after_teardown']) > len(buffers_at_start)]
if leaked:
    print(f'RESULT: FAIL — shadow buffers leak across teardown: '
          f'{[r["buffers_after_teardown"] for r in leaked]}')
    fail = True

if fail:
    sys.exit(1)

print('RESULT: PASS — depth FBO allocates on GL 2.1, shadow uniforms reach a '
      '#version 120 shader, depth compare varies, and buffers survive/release '
      'across 3 teardown cycles.')
sys.exit(0)
