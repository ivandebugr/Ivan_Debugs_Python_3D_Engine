"""
compat.py — Ursina 8.3.0 / macOS OpenGL 2.1 compatibility layer.

Ursina 8.3.0 ships GLSL #version 130/140 shaders. macOS OpenGL 2.1 (the
compatibility context Panda3D gets by default) supports GLSL 1.20 only, so
every stock shader must be rewritten to #version 120 before it compiles.

patch_shaders_to_glsl120() is called TWICE by each entry point (main.py and
Scripts/level_editor.py __main__):
  1. BEFORE Ursina() — the constructor creates entities that trigger shader
     compilation (HC10: the patch must be the first call, before Ursina()).
  2. After all window setup — Ursina() and subsequent window resizes can
     re-initialize shader objects on internal entities.

Important: ursina.shader.imported_shaders holds different object instances
than direct imports. We patch every live instance reachable by entity.py:
both direct-import refs AND imported_shaders dict entries. We set compiled=False
on each so entity.py's "if not value.compiled" guard triggers a fresh recompile
on next assignment. We deliberately do NOT delete ._shader — see the note in
_patch_shader_obj for why (Text reads it without a compiled-guard).

Do NOT upgrade these shaders back to #version 130+ without a verified Core
Profile context (see CLAUDE.md, Ursina 8.3.0 Compatibility).
"""


def _patch_shader_obj(obj, vertex_src, fragment_src):
    obj.vertex = vertex_src
    obj.fragment = fragment_src
    # compiled=False makes entity.py's shader_setter recompile on next assignment
    # (entity.py:827 `if not value.compiled: value.compile()`). Do NOT delete
    # obj._shader: unlike entity.shader_setter, Text.create_text_section reads
    # shader._shader UNCONDITIONALLY (ursina/text.py:251,
    # `setShader(self.shader._shader)`) with no compiled-guard, so any Text that
    # rebuilds its glyphs in the same frame as this patch would hit
    # AttributeError('_shader') during the gap between the del and the recompile.
    # The already-compiled _shader is a valid #version 120 program (the source we
    # re-supply here is byte-identical), so leaving it in place is safe — the
    # recompile still happens, just without a missing-attribute window.
    obj.compiled = False


_UNLIT_VERT = (
    '#version 120\n'
    'uniform mat4 p3d_ModelViewProjectionMatrix;\n'
    'uniform mat4 p3d_ModelViewMatrix;\n'
    'uniform mat4 p3d_ModelMatrix;\n'
    'attribute vec4 p3d_Vertex;\n'
    'attribute vec2 p3d_MultiTexCoord0;\n'
    'varying vec2 uvs;\n'
    'uniform vec2 texture_scale;\n'
    'uniform vec2 texture_offset;\n'
    'attribute vec4 p3d_Color;\n'
    'varying vec4 vertex_color;\n'
    'void main() {\n'
    '    gl_Position = p3d_ModelViewProjectionMatrix * p3d_Vertex;\n'
    '    uvs = (p3d_MultiTexCoord0 * texture_scale) + texture_offset;\n'
    '    vertex_color = p3d_Color;\n'
    '}\n'
)
_UNLIT_FRAG = (
    '#version 120\n'
    'uniform sampler2D p3d_Texture0;\n'
    'uniform vec4 p3d_ColorScale;\n'
    'varying vec2 uvs;\n'
    'varying vec4 vertex_color;\n'
    'void main() {\n'
    '    gl_FragColor = texture2D(p3d_Texture0, uvs) * p3d_ColorScale * vertex_color;\n'
    '}\n'
)
_UFS_VERT = (
    '#version 120\n'
    'uniform mat4 p3d_ModelViewProjectionMatrix;\n'
    'uniform mat4 p3d_ModelViewMatrix;\n'
    'uniform mat4 p3d_ModelMatrix;\n'
    'attribute vec4 p3d_Vertex;\n'
    'attribute vec2 p3d_MultiTexCoord0;\n'
    'varying vec2 uvs;\n'
    'uniform vec2 texture_scale;\n'
    'uniform vec2 texture_offset;\n'
    'attribute vec4 p3d_Color;\n'
    'varying vec4 vertex_color;\n'
    'varying vec3 vertex_world_position;\n'
    'void main() {\n'
    '    gl_Position = p3d_ModelViewProjectionMatrix * p3d_Vertex;\n'
    '    uvs = (p3d_MultiTexCoord0 * texture_scale) + texture_offset;\n'
    '    vertex_color = p3d_Color;\n'
    '    vertex_world_position = (p3d_ModelMatrix * p3d_Vertex).xyz;\n'
    '}\n'
)
_UFS_FRAG = (
    '#version 120\n'
    'uniform sampler2D p3d_Texture0;\n'
    'uniform vec4 p3d_ColorScale;\n'
    'varying vec2 uvs;\n'
    'varying vec4 vertex_color;\n'
    'varying vec3 vertex_world_position;\n'
    'uniform vec3 camera_world_position;\n'
    'uniform vec4 fog_color;\n'
    'uniform float fog_start;\n'
    'uniform float fog_end;\n'
    'void main() {\n'
    '    vec4 fragColor = texture2D(p3d_Texture0, uvs) * p3d_ColorScale * vertex_color;\n'
    '    float distance_to_camera = length(vertex_world_position.xyz - camera_world_position);\n'
    '    float fog_length = fog_end - fog_start;\n'
    '    float t = clamp(distance_to_camera / fog_length, 0.0, 1.0);\n'
    '    fragColor.rgb = mix(fragColor.rgb, fog_color.rgb, t * fog_color.a);\n'
    '    gl_FragColor = fragColor;\n'
    '}\n'
)
_TEXT_VERT = (
    '#version 120\n'
    'uniform mat4 p3d_ModelViewProjectionMatrix;\n'
    'attribute vec4 p3d_Vertex;\n'
    'attribute vec2 p3d_MultiTexCoord0;\n'
    'varying vec2 uvs;\n'
    'attribute vec4 p3d_Color;\n'
    'varying vec4 vertex_color;\n'
    'void main() {\n'
    '    gl_Position = p3d_ModelViewProjectionMatrix * p3d_Vertex;\n'
    '    uvs = p3d_MultiTexCoord0;\n'
    '    vertex_color = p3d_Color;\n'
    '}\n'
)
_TEXT_FRAG = (
    '#version 120\n'
    'uniform sampler2D p3d_Texture0;\n'
    'uniform vec4 p3d_ColorScale;\n'
    'uniform vec4 outline_color;\n'
    'uniform vec2 outline_offset;\n'
    'uniform float outline_power;\n'
    'varying vec2 uvs;\n'
    'varying vec4 vertex_color;\n'
    'void main() {\n'
    '    float dist = texture2D(p3d_Texture0, uvs).a;\n'
    '    vec2 width = vec2(0.5-fwidth(dist), 0.5+fwidth(dist));\n'
    '    float alpha = smoothstep(width.x, width.y, dist);\n'
    '    float scale = 0.354;\n'
    '    vec2 duv = scale * (dFdx(uvs) + dFdy(uvs));\n'
    '    vec4 box = vec4(uvs-duv, uvs+duv);\n'
    '    alpha += 0.5*(smoothstep(width.x, width.y, texture2D(p3d_Texture0, box.xy).a)\n'
    '            +smoothstep(width.x, width.y, texture2D(p3d_Texture0, box.zw).a)\n'
    '            +smoothstep(width.x, width.y, texture2D(p3d_Texture0, box.xw).a)\n'
    '            +smoothstep(width.x, width.y, texture2D(p3d_Texture0, box.zy).a));\n'
    '    alpha /= 3.0;\n'
    '    float outline = pow(texture2D(p3d_Texture0, uvs-outline_offset).a, outline_power);\n'
    '    gl_FragColor = mix(vec4(vertex_color.rgb, outline_color.a * outline), vertex_color, alpha);\n'
    '}\n'
)


def patch_shaders_to_glsl120():
    from ursina import shader as _shader_mod
    from ursina.shaders.unlit_shader import unlit_shader as _us
    from ursina.shaders.unlit_with_fog_shader import unlit_with_fog_shader as _ufs
    from ursina.shaders.text_shader import text_shader as _ts
    # Patch every live instance reachable by entity.py (direct-import refs may differ
    # from imported_shaders dict entries due to Ursina's module registration quirk).
    seen = set()
    for obj, verts, frags in [
        (_us, _UNLIT_VERT, _UNLIT_FRAG),
        (_ufs, _UFS_VERT, _UFS_FRAG),
        (_ts, _TEXT_VERT, _TEXT_FRAG),
    ]:
        if id(obj) not in seen:
            _patch_shader_obj(obj, verts, frags)
            seen.add(id(obj))
    for name, verts, frags in [
        ('unlit_shader', _UNLIT_VERT, _UNLIT_FRAG),
        ('unlit_with_fog_shader', _UFS_VERT, _UFS_FRAG),
        ('text_shader', _TEXT_VERT, _TEXT_FRAG),
    ]:
        obj = _shader_mod.imported_shaders.get(name)
        if obj is not None and id(obj) not in seen:
            _patch_shader_obj(obj, verts, frags)
            seen.add(id(obj))
