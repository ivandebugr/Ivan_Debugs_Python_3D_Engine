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
Ambient + one directional/point light, Lambert diffuse:

    color.rgb = albedo.rgb * (ambient + light.color * max(N·L, 0) * atten)

- Ambient comes from Panda3D's `p3d_LightModel.ambient` (set via
  `scene.ambient_color` / Ursina's ambient, or defaults to a small lift).
- The single light is Panda3D's `p3d_LightSource[0]`: add a `DirectionalLight`
  (or `PointLight`) to the scene and Panda3D fills the uniform automatically —
  no per-entity setShaderInput plumbing. `position.w == 0` marks a directional
  light (`.position.xyz` is the direction); `w == 1` marks a point light, for
  which we normalise the vector from the surface and apply distance
  attenuation. Both cases are handled so the shader is correct either way.

Integration
-----------
Registered into compat.patch_shaders_to_glsl120() so it participates in the
same post-window-setup recompile pass as the unlit/text shaders (Hard
Constraint 7 — shaders re-initialised during window setup must be
re-triggered). See compat.py for the mechanism. The source is authored at 120,
so compat re-supplies byte-identical 120 source; the load-bearing effect is the
`._shader` reset that forces a fresh recompile after Ursina()/window setup.
"""

from ursina.shader import Shader
from ursina.vec2 import Vec2


LIT_VERT = (
    '#version 120\n'
    'uniform mat4 p3d_ModelViewProjectionMatrix;\n'
    'uniform mat4 p3d_ModelViewMatrix;\n'
    'uniform mat4 p3d_ModelMatrix;\n'
    'uniform mat3 p3d_NormalMatrix;\n'
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
    'void main() {\n'
    '    gl_Position = p3d_ModelViewProjectionMatrix * p3d_Vertex;\n'
    '    uvs = (p3d_MultiTexCoord0 * texture_scale) + texture_offset;\n'
    '    vertex_color = p3d_Color;\n'
    '    // Light math is done in eye space: p3d_LightSource positions/directions\n'
    '    // are supplied by Panda3D in eye space, so transform the normal with the\n'
    '    // normal matrix (inverse-transpose of the modelview) to match.\n'
    '    world_normal = normalize(p3d_NormalMatrix * p3d_Normal);\n'
    '    eye_position = vec3(p3d_ModelViewMatrix * p3d_Vertex);\n'
    '}\n'
)

LIT_FRAG = (
    '#version 120\n'
    'uniform sampler2D p3d_Texture0;\n'
    'uniform vec4 p3d_ColorScale;\n'
    'uniform struct p3d_LightModelParameters {\n'
    '    vec4 ambient;\n'
    '} p3d_LightModel;\n'
    'uniform struct p3d_LightSourceParameters {\n'
    '    vec4 color;\n'
    '    vec4 position;\n'
    '    vec3 attenuation;\n'
    '} p3d_LightSource[1];\n'
    'uniform vec4 ambient_boost;\n'
    'varying vec2 uvs;\n'
    'varying vec4 vertex_color;\n'
    'varying vec3 world_normal;\n'
    'varying vec3 eye_position;\n'
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
    '    float ndotl = max(dot(N, L), 0.0);\n'
    '    vec3 diffuse = p3d_LightSource[0].color.rgb * ndotl * atten;\n'
    '    // ambient_boost is a small constant floor so unlit-facing surfaces never\n'
    '    // crush to pure black; it is added to Panda3D scene ambient, not a\n'
    '    // replacement for it.\n'
    '    vec3 lighting = p3d_LightModel.ambient.rgb + ambient_boost.rgb + diffuse;\n'
    '    gl_FragColor = vec4(albedo.rgb * lighting, albedo.a);\n'
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
        # Small ambient floor (0.18 grey). Keeps back-facing / shadowed surfaces
        # legible without a second light, and stops near-black MTL diffuse from
        # rendering as pure black even when no scene light is present.
        'ambient_boost': (0.18, 0.18, 0.18, 1.0),
    },
)
