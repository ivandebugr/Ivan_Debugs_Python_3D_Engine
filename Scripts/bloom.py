"""
bloom.py — project-owned GLSL 1.20 bloom post-process for macOS OpenGL 2.1.

Why this exists (and why it is hand-rolled)
-------------------------------------------
Panda3D ships bloom for free in `CommonFilters.setBloom()`. It is DEAD on this
stack: all four of its bloom passes are `Shader.make(..., Shader.SL_Cg)`, and on
this arm64 build `Shader.make(<minimal Cg>, SL_Cg)` returns None with "Support
for Cg shaders is not enabled" (measured 2026-07-17, logged to brain/Gotchas).
Ursina's own `camera.shader = ...` hook is also unusable here — it allocates a
FilterManager for a SINGLE fullscreen filter, and a single full-res pass can only
afford a handful of taps, which reads as a tight neon edge-glow rather than a
wide soft halo. So we own the chain.

The chain (v1.7 Candidate B2 — see work/active/v1.7-bloom-scoping.md)
---------------------------------------------------------------------
    scene -> [filter-base]  full res, colour + depth
          -> [bright]       1/4 res, threshold
          -> [blur-x]       1/4 res, 9-tap separable Gaussian, horizontal
          -> [blur-y]       1/4 res, 9-tap separable Gaussian, vertical
          -> composite      full res, scene + glow*intensity, drawn in region sort 0

The blur radius comes from DOWNSAMPLING, not from tap count: a 9-tap kernel on a
quarter-res buffer covers 4x the screen distance of the same kernel at full res,
and the bilinear upsample in the composite smooths what is left. That is the
whole trick — it is why B2 gets a wide TF2-style halo for ~57k fragments per blur
pass instead of the unaffordable tap count a full-res wide blur would need.

LDR, on purpose: 8-bit RGBA targets, no HDR float. TF2-era bloom was LDR bloom
with a luminance threshold; the look this project wants predates HDR pipelines.

The HUD, and why nothing here touches it
----------------------------------------
`FilterManager(base.win, base.cam)` redirects ONLY base.cam's display region
(sort 0) into an offscreen buffer and installs the composite quad back into that
same sort-0 region. Ursina's UI lives on a SEPARATE display region at sort 20
(ursina/camera.py:64-65, `ui_display_region.set_sort(20)`), which draws to the
window AFTER the sort-0 quad and was never redirected. So the HUD composites on
top of the bloomed frame, crisp and un-bloomed, BY CONSTRUCTION.

That is a claim about real hardware, not just source-reading: tools/bloom_probe.py
renders a blown-out emitter directly behind HUD text on a real window and asserts
the HUD's pixels are BYTE-IDENTICAL with bloom on vs off. It passes. The one way
to break it is to ever wrap `ui_camera` in a FilterManager too — don't.

Lifecycle — the actual risk of this module
------------------------------------------
This project has leaked FBOs twice in one session (shadow depth buffers and
LightAttrib entries; see Scripts/light_lifecycle.py). Bloom buffers are the same
failure class, so the discipline is structural rather than remembered:

  - Buffers are created EXACTLY ONCE, by BloomPipeline.__init__, at app init.
  - Nothing in this module is called by return_to_menu() / _clear_gameplay_entities().
  - Toggling bloom for menu/editor/F5 goes through set_enabled(), which only flips
    display-region + buffer active flags. It never allocates or frees.
  - The quads are bare NodePaths owned by FilterManager, NOT ursina Entities, so
    they are not in scene.entities and main_menu()'s sweep cannot destroy them.
    (This is load-bearing: the sweep destroys every scene entity whose name is not
    'main_camera'. A quad parented into `scene` would be eaten on the first menu
    round-trip and the composite would render black.)
  - Resize is FilterManager's own job — it subscribes to Panda's 'window-event'
    and calls resizeBuffers() (FilterManager.py:78, 344-348). This is independent
    of the project's window.update_aspect_ratio wrapper (Hard Constraint #13), so
    there is nothing to add here.

tools/bloom_probe.py gate 3 asserts buffer count holds flat across 4
menu-shaped teardown cycles.
"""

from direct.filter.FilterManager import FilterManager
from panda3d.core import Texture
from ursina import camera, application


# --- Tunables ---------------------------------------------------------------
# Luminance above which a pixel starts to glow. 0.75 keeps ordinary lit surfaces
# (the level's mid-grey walls sit ~0.45) out of the bloom entirely and reserves it
# for genuinely blown-out things — muzzle flashes, the sky, white props. Lower it
# and the whole frame hazes over, which is the classic "everything bright halos"
# look the scoping doc explicitly did not want.
BLOOM_THRESHOLD = 0.75
# How hard the blurred glow is added back. Additive, so >1 blows out fast.
BLOOM_INTENSITY = 0.85
# Downsample factor for the bright/blur buffers. 4 => 320x180 at 720p.
BLOOM_DIV = 4

_VERT = (
    '#version 120\n'
    'uniform mat4 p3d_ModelViewProjectionMatrix;\n'
    'attribute vec4 p3d_Vertex;\n'
    'attribute vec2 p3d_MultiTexCoord0;\n'
    'varying vec2 uvs;\n'
    'void main() {\n'
    '    gl_Position = p3d_ModelViewProjectionMatrix * p3d_Vertex;\n'
    '    uvs = p3d_MultiTexCoord0;\n'
    '}\n'
)

# Bright-pass: isolate what glows, and by how much.
#
# The subtract-and-renormalise (rather than a hard `if lum > t` cutoff) is what
# keeps the halo from having a visible hard edge where a surface's luminance
# crosses the threshold: contribution ramps from 0 at the threshold instead of
# jumping to full. `knee` softens that ramp further.
_BRIGHT_FRAG = (
    '#version 120\n'
    'uniform sampler2D tex;\n'
    'uniform float threshold;\n'
    'varying vec2 uvs;\n'
    'void main() {\n'
    '    vec3 c = texture2D(tex, uvs).rgb;\n'
    '    float lum = dot(c, vec3(0.2126, 0.7152, 0.0722));\n'
    '    float knee = 0.15;\n'
    '    float w = smoothstep(threshold, threshold + knee, lum);\n'
    '    gl_FragColor = vec4(c * w, 1.0);\n'
    '}\n'
)

# Separable 9-tap Gaussian. Run twice (x then y) => 81 effective taps for 18
# samples. `direction` is (1,0) or (0,1) in TEXEL units; the caller supplies
# texel size so one shader serves both axes.
#
# Weights are a normalised sigma~2 Gaussian; the loop is unrolled by hand because
# GLSL 1.20 requires constant-bound loops and a hand-rolled sum reads clearer than
# a `for` with a const array (1.20 has no array constructors in all drivers).
_BLUR_FRAG = (
    '#version 120\n'
    'uniform sampler2D tex;\n'
    'uniform vec2 direction;\n'
    'varying vec2 uvs;\n'
    'void main() {\n'
    '    vec3 acc = texture2D(tex, uvs).rgb * 0.2270270270;\n'
    '    acc += texture2D(tex, uvs + direction * 1.0).rgb * 0.1945945946;\n'
    '    acc += texture2D(tex, uvs - direction * 1.0).rgb * 0.1945945946;\n'
    '    acc += texture2D(tex, uvs + direction * 2.0).rgb * 0.1216216216;\n'
    '    acc += texture2D(tex, uvs - direction * 2.0).rgb * 0.1216216216;\n'
    '    acc += texture2D(tex, uvs + direction * 3.0).rgb * 0.0540540541;\n'
    '    acc += texture2D(tex, uvs - direction * 3.0).rgb * 0.0540540541;\n'
    '    acc += texture2D(tex, uvs + direction * 4.0).rgb * 0.0162162162;\n'
    '    acc += texture2D(tex, uvs - direction * 4.0).rgb * 0.0162162162;\n'
    '    gl_FragColor = vec4(acc, 1.0);\n'
    '}\n'
)

# Composite: scene + glow. The glow texture is 1/4 res; sampling it with linear
# filtering at full res IS the upsample, and its softness is free extra blur.
_COMPOSITE_FRAG = (
    '#version 120\n'
    'uniform sampler2D tex;\n'
    'uniform sampler2D glow_tex;\n'
    'uniform float intensity;\n'
    'varying vec2 uvs;\n'
    'void main() {\n'
    '    vec3 scene = texture2D(tex, uvs).rgb;\n'
    '    vec3 glow = texture2D(glow_tex, uvs).rgb;\n'
    '    gl_FragColor = vec4(scene + glow * intensity, 1.0);\n'
    '}\n'
)


# Pass-through composite for the bloom-off state: still required, because
# FilterManager has permanently redirected the scene into an offscreen buffer —
# the quad is now the ONLY thing drawing the scene to the window. "Off" means
# "blit the scene unchanged", never "remove the quad".
_PASSTHROUGH_FRAG = (
    '#version 120\n'
    'uniform sampler2D tex;\n'
    'varying vec2 uvs;\n'
    'void main() {\n'
    '    gl_FragColor = vec4(texture2D(tex, uvs).rgb, 1.0);\n'
    '}\n'
)


def _make_shader(fragment):
    """Build a Panda3D GLSL shader object.

    Panda's Shader.make (not ursina's Shader wrapper) because these live on raw
    FilterManager quad NodePaths, not on ursina Entities — there is no ursina
    entity in this whole module, by design (see the lifecycle note above).
    """
    from panda3d.core import Shader as PandaShader
    return PandaShader.make(PandaShader.SL_GLSL, _VERT, fragment)


class BloomPipeline:
    """Owns the bloom buffer chain for the whole process lifetime.

    Construct ONCE at app init, after the window exists. Never construct a second
    one: two FilterManagers would both try to own base.cam's display region and
    fight over it.
    """

    def __init__(self, threshold=BLOOM_THRESHOLD, intensity=BLOOM_INTENSITY,
                 div=BLOOM_DIV):
        self.manager = FilterManager(base.win, base.cam)

        # Scene colour target. Clamp both axes: a wrapped sample at the frame edge
        # would drag light from the opposite side of the screen into the blur.
        self.scene_tex = Texture('bloom-scene')
        self.scene_tex.set_wrap_u(Texture.WMClamp)
        self.scene_tex.set_wrap_v(Texture.WMClamp)

        self.final_quad = self.manager.renderSceneInto(colortex=self.scene_tex)

        self.bright_tex = Texture('bloom-bright')
        self.bright_tex.set_wrap_u(Texture.WMClamp)
        self.bright_tex.set_wrap_v(Texture.WMClamp)
        self.blur_x_tex = Texture('bloom-blur-x')
        self.blur_x_tex.set_wrap_u(Texture.WMClamp)
        self.blur_x_tex.set_wrap_v(Texture.WMClamp)
        self.blur_y_tex = Texture('bloom-blur-y')
        self.blur_y_tex.set_wrap_u(Texture.WMClamp)
        self.blur_y_tex.set_wrap_v(Texture.WMClamp)

        # renderQuadInto returns the QUAD, but set_enabled() needs the BUFFER to
        # toggle. FilterManager appends each new buffer to manager.buffers, so the
        # buffer for a quad is the one appended by that call — grab it immediately.
        self.bright_quad = self.manager.renderQuadInto(
            'bloom-bright', colortex=self.bright_tex, div=div)
        self._bright_buf = self.manager.buffers[-1]
        self.blur_x_quad = self.manager.renderQuadInto(
            'bloom-blur-x', colortex=self.blur_x_tex, div=div)
        self._blur_x_buf = self.manager.buffers[-1]
        self.blur_y_quad = self.manager.renderQuadInto(
            'bloom-blur-y', colortex=self.blur_y_tex, div=div)
        self._blur_y_buf = self.manager.buffers[-1]

        self.bright_quad.set_shader(_make_shader(_BRIGHT_FRAG))
        self.bright_quad.set_shader_input('tex', self.scene_tex)
        self.bright_quad.set_shader_input('threshold', threshold)

        blur_shader = _make_shader(_BLUR_FRAG)
        self.blur_x_quad.set_shader(blur_shader)
        self.blur_x_quad.set_shader_input('tex', self.bright_tex)
        self.blur_y_quad.set_shader(blur_shader)
        self.blur_y_quad.set_shader_input('tex', self.blur_x_tex)

        self.final_quad.set_shader(_make_shader(_COMPOSITE_FRAG))
        self.final_quad.set_shader_input('tex', self.scene_tex)
        self.final_quad.set_shader_input('glow_tex', self.blur_y_tex)
        self.final_quad.set_shader_input('intensity', intensity)

        self._div = div
        self._enabled = True
        self._apply_blur_directions()

        # Blur offsets are in UV units and therefore depend on buffer size, which
        # FilterManager changes on window resize. Recompute on the same event it
        # listens to. This does NOT allocate — it only rewrites two shader inputs.
        # (Hard Constraint #13: window.on_resize is never called by Ursina, so
        # Panda's own window-event is the correct hook — same one FilterManager
        # itself uses.)
        base.accept('window-event', self._on_window_event)

    def _apply_blur_directions(self):
        """Set the per-axis texel step for the separable blur.

        One texel of the QUARTER-RES buffer. Getting this from the window size
        (rather than hardcoding 1/320) keeps the halo the same apparent width at
        every resolution the settings menu offers.
        """
        w = max(1, base.win.get_x_size() // self._div)
        h = max(1, base.win.get_y_size() // self._div)
        self.blur_x_quad.set_shader_input('direction', (1.0 / w, 0.0))
        self.blur_y_quad.set_shader_input('direction', (0.0, 1.0 / h))

    def _on_window_event(self, win):
        self._apply_blur_directions()

    def set_enabled(self, on):
        """Toggle bloom without touching allocation.

        Disabling has to do two things, not one: stop the intermediate buffers
        from rendering, AND swap the composite quad's shader for a pass-through.
        Skipping the second would leave the last blurred frame smeared over the
        scene forever, since the composite still samples glow_tex.
        """
        if on == self._enabled:
            return
        self._enabled = on
        for buf in (self._bright_buf, self._blur_x_buf, self._blur_y_buf):
            buf.set_active(on)
        if on:
            self.final_quad.set_shader(_make_shader(_COMPOSITE_FRAG))
            self.final_quad.set_shader_input('tex', self.scene_tex)
            self.final_quad.set_shader_input('glow_tex', self.blur_y_tex)
            self.final_quad.set_shader_input('intensity', BLOOM_INTENSITY)
        else:
            self.final_quad.set_shader(_make_shader(_PASSTHROUGH_FRAG))
            self.final_quad.set_shader_input('tex', self.scene_tex)

    @property
    def enabled(self):
        return self._enabled
