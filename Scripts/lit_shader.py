"""
lit_shader.py — project-owned GLSL 1.20 lit shader for macOS OpenGL 2.1.

Why this exists
---------------
Ursina's stock lit shaders are unusable on this project's baseline:
  - lit_with_shadows_shader ships `#version 150` (sampler2DShadow, in/out,
    texture(), fragment_color).
  - basic_lighting_shader ships `#version 140` (in/out, texture(), fragColor).
Neither compiles under GLSL 1.20 / GL 2.1 (the macOS ceiling — Hard
Constraint: OpenGL 2.1 / GLSL 1.20). With no working lit path, lit geometry
fell back to unlit rendering, so materials with near-black diffuse (the gun
MTLs, Kd ~0.02-0.09) rendered as flat black — the "black guns" symptom. The
real fix is a lighting model: even a near-black albedo reads as shaded 3D
once ambient + a directional term lift it.

This module is that lit path, hand-written at `#version 120` in
compatibility-profile syntax (attribute/varying, texture2D, gl_FragColor,
Panda3D-supplied p3d_* matrix/light uniforms) so it compiles clean on the
real 2.1 baseline. Verified via the headless GSG harness
(tools/shader_harness.py) — zero compile/link errors on GL 2.1 Metal.

Lighting model
--------------
Ambient + one directional/point light, stylized (v1.7 Candidate L1):

    diffuse  = pow(N·L * 0.5 + 0.5, 2)      Half-Lambert — soft terminator
    spec     = pow(max(N·H, 0), shininess)  Phong lobe, gated by the diffuse term
    rim      = pow(1 - N·V, rim_power)      view-dependent edge light
    lighting = ambient*cool_tint + sun*(diffuse*warm_tint) * atten
    color.rgb = albedo.rgb * lighting + (spec + rim) * sun.color

Half-Lambert (Valve's warped diffuse, used across the Source engine and TF2)
remaps N·L from [-1,1] to [0,1] and squares it, so surfaces facing away from
the sun land near 0.25 instead of hard 0. That is what kills the black-backside
problem at the source — and it is why `ambient_boost` was retuned 0.18 -> 0.06
(see the default_input note below): the old floor existed to rescue exactly the
case Half-Lambert now handles, so keeping both double-lifts the shadow side.

Specular and rim are ADDITIVE and deliberately do NOT multiply albedo — that
is the whole point for this project. The gun MTLs ship near-black diffuse
(Kd ~0.011-0.09, verified in assets/models/*.mtl), so anything multiplied by
albedo stays black no matter how bright the light. An additive lobe puts light
*on top of* a black surface, which is how dark gunmetal reads as metal rather
than as a silhouette.

The flip side of additive: the lobe lands on matte surfaces just as hard as on
metal, since nothing about a low albedo damps it. That is what forced
spec_strength down to 0.05 — see its default_input note below.

- Ambient comes from Panda3D's `p3d_LightModel.ambient` (set via
  `scene.ambient_color` / Ursina's ambient, or defaults to a small lift).
- The single light is Panda3D's `p3d_LightSource[0]`: add a `DirectionalLight`
  (or `PointLight`) to the scene and Panda3D fills the uniform automatically —
  no per-entity setShaderInput plumbing. `position.w == 0` marks a directional
  light (`.position.xyz` is the direction); `w == 1` marks a point light, for
  which we normalise the vector from the surface and apply distance
  attenuation. Both cases are handled so the shader is correct either way.

Integration — deliberately OUTSIDE compat.py's patch loop
---------------------------------------------------------
compat.patch_shaders_to_glsl120() exists to rewrite Ursina's STOCK shaders down
to #version 120 and force their recompile after window setup. This shader is NOT
registered there, on purpose:

  - It is already authored at #version 120, so there is nothing to down-patch.
  - It is referenced only by world geometry (level blocks + ground) created in
    main_menu()/load_level(), which run AFTER window setup — so it compiles
    fresh on first render without needing compat's post-setup recompile trigger.
  - compat's `_patch_shader_obj()` does `del obj._shader`; that reset is only
    race-safe for the stock shaders because of the exact ordering they rely on.
    Putting this shader through the same path inherits a latent
    AttributeError('_shader') window for no benefit.

So the wiring is plain Ursina: `Entity(..., shader=lit_shader)` in main.py, plus
a single DirectionalLight + scene.ambient_color created inside main_menu() (after
its scene sweep, which would otherwise destroy the light) to drive
p3d_LightSource[0] / p3d_LightModel.ambient. Nothing to register.
"""

from ursina.shader import Shader
from ursina.vec2 import Vec2


LIT_VERT = (
    '#version 120\n'
    'uniform mat4 p3d_ModelViewProjectionMatrix;\n'
    'uniform mat4 p3d_ModelViewMatrix;\n'
    'uniform mat4 p3d_ModelMatrix;\n'
    'uniform mat3 p3d_NormalMatrix;\n'
    # The p3d_LightSource struct declaration must be IDENTICAL in both stages —
    # GLSL links uniforms by name across stages and rejects a type mismatch
    # ("Uniform type mismatch 'p3d_LightSource'"). The vertex stage only needs
    # shadowViewMatrix, but shadowMap must be declared here too so the struct
    # layout matches the fragment stage's. This is the exact constraint the L3
    # probe (tools/shadow_fbo_probe.py) found the hard way.
    'uniform struct p3d_LightSourceParameters {\n'
    '    vec4 color;\n'
    '    vec4 position;\n'
    '    vec3 attenuation;\n'
    '    mat4 shadowViewMatrix;\n'
    '    sampler2DShadow shadowMap;\n'
    '} p3d_LightSource[1];\n'
    'attribute vec4 p3d_Vertex;\n'
    'attribute vec3 p3d_Normal;\n'
    'attribute vec2 p3d_MultiTexCoord0;\n'
    'attribute vec4 p3d_Color;\n'
    'uniform vec2 texture_scale;\n'
    'uniform vec2 texture_offset;\n'
    'varying vec2 uvs;\n'
    'varying vec4 vertex_color;\n'
    'varying vec3 world_normal;\n'
    'varying vec3 eye_position;\n'
    'varying vec4 shadow_coord;\n'
    'void main() {\n'
    '    gl_Position = p3d_ModelViewProjectionMatrix * p3d_Vertex;\n'
    '    uvs = (p3d_MultiTexCoord0 * texture_scale) + texture_offset;\n'
    '    vertex_color = p3d_Color;\n'
    '    // Light math is done in eye space: p3d_LightSource positions/directions\n'
    '    // are supplied by Panda3D in eye space, so transform the normal with the\n'
    '    // normal matrix (inverse-transpose of the modelview) to match.\n'
    '    world_normal = normalize(p3d_NormalMatrix * p3d_Normal);\n'
    '    vec4 eye_pos = p3d_ModelViewMatrix * p3d_Vertex;\n'
    '    eye_position = vec3(eye_pos);\n'
    '    // Shadow lookup coordinate. shadowViewMatrix expects EYE-space input\n'
    '    // (p3d_ModelViewMatrix * vertex); feeding it world space silently yields\n'
    '    // an all-zero degenerate depth with no error — the single sharpest trap\n'
    '    // the L3 probe documented. The GSG bakes the light projection + the\n'
    '    // [-1,1]->[0,1] bias into this matrix, so shadow_coord is ready to project.\n'
    '    shadow_coord = p3d_LightSource[0].shadowViewMatrix * eye_pos;\n'
    '}\n'
)

LIT_FRAG = (
    '#version 120\n'
    'uniform sampler2D p3d_Texture0;\n'
    'uniform vec4 p3d_ColorScale;\n'
    'uniform struct p3d_LightModelParameters {\n'
    '    vec4 ambient;\n'
    '} p3d_LightModel;\n'
    # MUST match the vertex-stage declaration field-for-field (see the note there)
    # or the program fails to link with "Uniform type mismatch 'p3d_LightSource'".
    'uniform struct p3d_LightSourceParameters {\n'
    '    vec4 color;\n'
    '    vec4 position;\n'
    '    vec3 attenuation;\n'
    '    mat4 shadowViewMatrix;\n'
    '    sampler2DShadow shadowMap;\n'
    '} p3d_LightSource[1];\n'
    'uniform vec4 ambient_boost;\n'
    'uniform vec4 warm_tint;\n'
    'uniform vec4 cool_tint;\n'
    'uniform vec4 rim_color;\n'
    'uniform float rim_power;\n'
    'uniform float rim_strength;\n'
    'uniform float spec_shininess;\n'
    'uniform float spec_strength;\n'
    'uniform float shadow_bias;\n'
    'uniform float shadow_slope_bias;\n'
    'uniform float shadow_texel;\n'
    'uniform float shadow_strength;\n'
    'uniform float shadow_enabled;\n'
    'varying vec2 uvs;\n'
    'varying vec4 vertex_color;\n'
    'varying vec3 world_normal;\n'
    'varying vec3 eye_position;\n'
    'varying vec4 shadow_coord;\n'
    # --- 9-tap PCF sun-shadow lookup ------------------------------------------
    # 9-16 tap "reads painterly and is still cheap here" on this M3 (per
    # v1.7-lighting-scoping.md L3). A 3x3 kernel is the low end of that band —
    # start here, widen only if the edge still reads hard. `ndotl` is the raw
    # (unwarped) N.L; the slope-scaled bias term grows as the surface turns away
    # from the sun, which is exactly where flat bias leaves acne.
    'float sun_shadow(float ndotl) {\n'
    '    // shadow_enabled is 0.0 until a caster is attached (default_input) —\n'
    '    // with no bound depth map, shadow2DProj returns garbage, so short-circuit\n'
    '    // to fully-lit. This is what keeps the shader correct on the ground/blocks\n'
    '    // BEFORE _apply_level_lighting() turns the sun into a caster.\n'
    '    if (shadow_enabled < 0.5) return 1.0;\n'
    '    // Guard the near plane: fragments behind the light (w <= 0) project to\n'
    '    // nonsense; treat them as lit rather than letting the divide explode.\n'
    '    if (shadow_coord.w <= 0.0) return 1.0;\n'
    '    // Slope-scaled depth bias, pushed into the projective Z before the\n'
    '    // hardware compare. Scaling by w keeps the bias constant in NDC depth\n'
    '    // regardless of distance.\n'
    '    float bias = (shadow_bias + shadow_slope_bias * (1.0 - clamp(ndotl, 0.0, 1.0)))\n'
    '               * shadow_coord.w;\n'
    '    vec4 sc = shadow_coord;\n'
    '    sc.z -= bias;\n'
    '    float lit = 0.0;\n'
    '    // 3x3 PCF. Offsets are applied in projected texel space (multiplied by w\n'
    '    // so a fixed texel step survives the perspective divide the sampler does).\n'
    '    for (int dx = -1; dx <= 1; ++dx) {\n'
    '        for (int dy = -1; dy <= 1; ++dy) {\n'
    '            vec4 o = sc;\n'
    '            o.x += float(dx) * shadow_texel * sc.w;\n'
    '            o.y += float(dy) * shadow_texel * sc.w;\n'
    '            lit += shadow2DProj(p3d_LightSource[0].shadowMap, o).r;\n'
    '        }\n'
    '    }\n'
    '    lit /= 9.0;\n'
    '    // shadow_strength lets a fully-shadowed fragment keep a little sun rather\n'
    '    // than dropping to pure ambient — softens the look and hides residual acne.\n'
    '    return mix(1.0 - shadow_strength, 1.0, lit);\n'
    '}\n'
    'void main() {\n'
    '    vec4 albedo = texture2D(p3d_Texture0, uvs) * p3d_ColorScale * vertex_color;\n'
    '    vec3 N = normalize(world_normal);\n'
    '    // Directional light: position.w == 0, position.xyz is the light direction\n'
    '    // (pointing from the scene toward the light). Point light: w == 1, so build\n'
    '    // the surface->light vector and attenuate by distance.\n'
    '    vec3 to_light = p3d_LightSource[0].position.xyz - eye_position * p3d_LightSource[0].position.w;\n'
    '    float dist = length(to_light);\n'
    '    vec3 L = dist > 0.0 ? to_light / dist : vec3(0.0, 0.0, 1.0);\n'
    '    vec3 att3 = p3d_LightSource[0].attenuation;\n'
    '    float atten = p3d_LightSource[0].position.w == 0.0\n'
    '        ? 1.0\n'
    '        : 1.0 / (att3.x + att3.y * dist + att3.z * dist * dist);\n'
    '    // Eye space: the camera sits at the origin looking down -Z, so the\n'
    '    // surface->eye vector is simply -eye_position normalised.\n'
    '    vec3 V = normalize(-eye_position);\n'
    '    // Half-Lambert: remap N.L from [-1,1] to [0,1] and square. Backsides land\n'
    '    // near 0.25 rather than 0, giving the soft terminator and killing the\n'
    '    // black-backside case that ambient_boost used to paper over.\n'
    '    float ndotl = dot(N, L);\n'
    '    float half_lambert = pow(ndotl * 0.5 + 0.5, 2.0);\n'
    '    // Sun-shadow factor [1-shadow_strength .. 1]. Applied ONLY to the direct\n'
    '    // sun terms below, never to ambient — a shadowed fragment must still get\n'
    '    // the cool ambient fill or it goes black, which is the exact case\n'
    '    // Half-Lambert exists to prevent. Real shadows and Half-Lambert are\n'
    '    // complementary: HL softens the geometric terminator, the shadow map\n'
    '    // occludes the sun behind other geometry.\n'
    '    float shadow = sun_shadow(ndotl);\n'
    '    vec3 sun = p3d_LightSource[0].color.rgb;\n'
    '    vec3 diffuse = sun * warm_tint.rgb * half_lambert * atten * shadow;\n'
    '    // Blinn-Phong half-vector: cheaper than reflect() and better behaved at\n'
    '    // grazing angles. Gated by the raw (unwarped) N.L so the lobe cannot appear\n'
    '    // on geometry actually facing away from the sun — Half-Lambert alone would\n'
    '    // happily light a backside spec.\n'
    '    vec3 H = normalize(L + V);\n'
    '    float spec = pow(max(dot(N, H), 0.0), spec_shininess)\n'
    '               * step(0.0, ndotl) * spec_strength * atten * shadow;\n'
    '    // Rim: brightest where the surface turns away from the eye. Scaled by the\n'
    '    // warped diffuse so the rim only fires on the sunlit side — an ungated rim\n'
    '    // haloes the whole silhouette and reads as fake. Also shadowed: a rim in a\n'
    '    // cast shadow reads as a light leak.\n'
    '    float rim = pow(1.0 - max(dot(N, V), 0.0), rim_power) * rim_strength * half_lambert * shadow;\n'
    '    // Cool ambient vs. warm sun is the whole warm/cool grade — the tints ride\n'
    '    // the existing ambient/diffuse terms rather than a post-multiply, so a\n'
    '    // neutral (1,1,1) pair reproduces the old look exactly.\n'
    '    vec3 lighting = (p3d_LightModel.ambient.rgb + ambient_boost.rgb) * cool_tint.rgb\n'
    '                  + diffuse;\n'
    '    // Spec + rim are ADDITIVE, not albedo-multiplied: near-black gun MTLs\n'
    '    // (Kd ~0.02) would swallow any multiplied highlight and stay black.\n'
    '    vec3 highlight = sun * spec + rim_color.rgb * rim;\n'
    '    gl_FragColor = vec4(albedo.rgb * lighting + highlight, albedo.a);\n'
    '}\n'
)


lit_shader = Shader(
    name='lit_shader',
    language=Shader.GLSL,
    vertex=LIT_VERT,
    fragment=LIT_FRAG,
    default_input={
        'texture_scale': Vec2(1, 1),
        'texture_offset': Vec2(0, 0),
        # Ambient floor, retuned 0.18 -> 0.06 when Half-Lambert landed.
        #
        # 0.18 existed to rescue dark backsides under flat Lambert, where N·L
        # clamps to 0 and the shadow side got ambient only. Half-Lambert now
        # contributes ~0.25 there on its own, so the old floor double-lifts:
        # measured against the real level lighting (scene ambient 0.35, sun
        # intensity 1.0 from level_io.default_light_entry), the darkest surface
        # sits at 0.526 with boost 0.18 vs 0.530 under the old flat model — i.e.
        # the shadow side stayed as washed out as before and the soft terminator
        # bought nothing. At 0.06 it drops to 0.407, so shadows read as shadows
        # again while staying well clear of black.
        #
        # NOT dropped to 0, though the backside no longer needs it: the floor has
        # a second job the retune must not throw away — it is the only lift when
        # there is NO scene light at all (an entity rendered before main_menu()
        # builds the sun). At 0.0 that case renders pure black for every material,
        # not merely dark. 0.06 keeps the safety net at a sixth of its old weight.
        'ambient_boost': (0.06, 0.06, 0.06, 1.0),
        # Warm/cool grade. Sun-side diffuse skews warm, ambient/shadow side skews
        # cool — the classic TF2 read. Retuned via Scripts/dev_shader_tuning.py
        # live-adjustment session, v1.7: stronger than the original ±8% pass.
        'warm_tint': (1.40, 1.34, 1.24, 1.0),
        'cool_tint': (1.26, 1.30, 1.44, 1.0),
        # Rim light. Slightly cool-white so it reads as sky bounce rather than a
        # second sun. rim_power 3 keeps it to the silhouette edge; strength
        # retuned lower via dev_shader_tuning.py, v1.7.
        'rim_color': (0.55, 0.62, 0.75, 1.0),
        'rim_power': 3.0,
        'rim_strength': 0.05,
        # Phong lobe. Tight and weak: mostly for the gun/enemy metals. The MTLs'
        # own Ns (~96) informed the shininess.
        #
        # Retuned 0.35 -> 0.05 (v1.7): 0.35 put a visible specular sheen on the
        # GROUND. The lobe is view-dependent (H = normalize(L + V)), and the
        # ground is one 100x100 plane with a single constant normal, so unlike
        # the curved metals it lights up coherently across the whole floor and
        # slides with the camera. Measured by differencing real-scene frames at
        # 0.35 vs 0.05 (the additive `+ highlight` term means the difference IS
        # the lobe, with albedo cancelled): the sheen peaks at 71/255 luminance
        # looking down ~35deg toward the sun, and falls to <1/255 looking away —
        # i.e. it is strongly orientation-dependent, which is why a single
        # head-on sample reads as "no highlight at all" and hides the bug.
        #
        # Kept global rather than a ground-only override: the near-black gun MTLs
        # (Kd ~0.011-0.09) are the reason the lobe exists, but verified in-game
        # that they still read as metal at 0.05 — texture and geometry carry them,
        # so the lobe does not need to.
        'spec_shininess': 32.0,
        'spec_strength': 0.05,
        # --- Sun shadow map (v1.7 L3) -----------------------------------------
        # shadow_enabled gates the whole PCF path. See the "shadow_enabled is armed
        # at app init" note in main.py for WHY this is a permanent default of 1.0
        # rather than flipped per menu-cycle: mutating it live via Ursina's
        # Shader.__setattr__ walks every entity using this shader, INCLUDING ones
        # emptied-but-not-yet-flushed by a menu sweep, and set_shader_input on an
        # empty NodePath crashes (!is_empty() at nodePath.I:228 — the same teardown
        # assertion class as Hard Constraint #10). With no caster bound (the
        # shader_harness, or any entity rendered before the sun exists) an unbound
        # sampler2DShadow reads 1.0 = fully lit on this GL 2.1 driver, so 1.0 is
        # safe there too — verified via tools/shader_harness.py.
        'shadow_enabled': 1.0,
        # Constant + slope-scaled depth bias, tuned against acne/peter-panning on
        # the real scene (see the tuning note in Scripts/lit_shader.py history).
        # The 1024^2 map over a 45-unit film gives ~0.044 world-units/texel; the
        # constant bias covers flat surfaces, the slope term covers grazing ones
        # where a single constant either acnes (too small) or peter-pans (too big).
        'shadow_bias': 0.0018,
        'shadow_slope_bias': 0.0035,
        # One shadow-map texel in [0,1] UV = 1/mapsize. Drives the PCF tap
        # spacing. 1/1024 spreads the 3x3 kernel over exactly the neighbouring
        # texels — the softest a 3x3 goes without smearing detail.
        'shadow_texel': 1.0 / 1024.0,
        # How dark a fully-occluded fragment gets on its DIRECT term. 0.85 leaves
        # 15% sun in shadow so cast shadows read deep but not crushed-black — the
        # cool ambient fill still lifts them (that lift is intentional, not a leak).
        'shadow_strength': 0.85,
    },
)
