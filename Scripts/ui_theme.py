"""Shared HUD/menu palette + spacing constants — v1.5 UI redesign.

Single source of truth for PlayerHUD, PauseMenu, EndScreen (main.py) and
HealthBar (health_bar.py). All colours are 0-1 floats per the Ursina 8.3.0
color.rgb() footgun (CLAUDE.md Compatibility section) — never 0-255 ints here.
"""

from ursina import color, curve
from ursina.prefabs.button import Button
from ursina.scripts.property_generator import generate_properties_for_class

# Bold weight for titles/buttons only — Ursina's bundled OpenSans-Regular.ttf
# has no bold variant, which flattens visual hierarchy against hint/body text.
# Static instance (wght=700) generated from Google Fonts' Inter variable font
# via fontTools.varLib.instancer — Panda3D's FreeType loader renders the
# default (Regular) instance of a variable font, it does not expose axis
# control, so a static-weight file is required (not the variable source).
FONT_BOLD = 'assets/fonts/Inter-Bold.ttf'

# Backgrounds — one dark cool-gray family replaces the four ad hoc
# overlay-black values previously scattered across PauseMenu/EndScreen.
# Spaced further apart than the first pass so panels visibly separate from
# the viewport background (window.color, set in main.py) instead of reading flat.
BG_PANEL       = color.rgb(40/255, 43/255, 48/255)     # button / panel fill
BG_OVERLAY     = color.rgba(8/255, 9/255, 11/255, 0.92)     # pause + end-screen backdrop — darker than BG_PANEL so it reads as a layer above the menu
BORDER_PANEL   = color.rgb(64/255, 67/255, 73/255)     # button border tint (Ursina Button has no border draw, kept for reference/tint use)

# Text
TEXT_PRIMARY   = color.rgb(232/255, 230/255, 223/255)
TEXT_SECONDARY = color.rgb(154/255, 152/255, 143/255)
TEXT_MUTED     = color.rgb(102/255, 100/255, 92/255)

# Health bar — ratio-based full/mid/low, amber-forward (replaces the old
# green/orange/red ramp so it reads as one coherent accent instead of a
# traffic light against the new neutral panels).
HEALTH_FULL    = color.rgb(201/255, 162/255, 39/255)
HEALTH_MID     = color.rgb(201/255, 162/255, 39/255)
HEALTH_LOW     = color.rgb(180/255, 90/255, 40/255)
HEALTH_BG      = color.rgb(38/255, 40/255, 44/255)

# Win / lose accents — same panel layout, differentiated by a single
# accent colour (title text + top border) rather than a redesign.
ACCENT_WIN     = color.rgb(124/255, 201/255, 156/255)
ACCENT_LOSE    = color.rgb(224/255, 132/255, 126/255)

CROSSHAIR_COLOR = color.rgb(216/255, 86/255, 79/255)

# Spacing scale (Ursina UI units, screen = -0.5..0.5 on the short axis).
SPACING_XS   = 0.01
SPACING_SM   = 0.02
SPACING_MD   = 0.04
SPACING_LG   = 0.06

BUTTON_SCALE      = (0.3, 0.1)
BUTTON_GAP        = 0.15   # vertical distance between stacked button centers

HUD_MARGIN        = 0.04   # inset from screen edge for corner-anchored HUD elements

# Kenney UI pack (v1.7) — single reference so the whole game's button skin
# swaps with a one-line edit here instead of per-screen texture paths.
# 'Blue'/'Default' chosen as a neutral baseline over 'Double' (which adds a
# second color stripe meant for contrast pairs we don't use). depth_gradient
# is the raised/drop-shadow render style — button_rectangle_*.png is 192x64
# (3:1), matching BUTTON_SCALE's 0.3:0.1 ratio so it stretches without distortion.
BUTTON_TEXTURE      = 'ui/Blue/Default/button_rectangle_depth_gradient'
BUTTON_CLICK_SOUND  = 'ui/click_001'

# HUD readout backdrop (ammo counter) — Half-Life 2 style: a translucent black
# panel behind the number instead of bare text floating on the world. Reuses
# the theme-agnostic outlined frame from Extra/ rather than a colored button,
# then tints it black + low alpha (the source PNG is opaque white, so color=
# fully repaints it — see button_rectangle_line.png's white fill/transparent
# corners). Same 192x64 (3:1) source as BUTTON_TEXTURE.
HUD_PANEL_TEXTURE = 'ui/Extra/Default/button_rectangle_line'
HUD_PANEL_COLOR   = color.rgba(0, 0, 0, 140/255)

# Click-scale animation duration/dip, shared so every Kenney-themed screen feels
# the same. Kept short so it reads as tactile feedback, not a delay before the
# real on_click callback (fired immediately, in parallel with the animation).
BUTTON_CLICK_ANIM_DURATION = 0.1
BUTTON_CLICK_ANIM_SCALE    = 0.9


@generate_properties_for_class()
class ThemedButton(Button):
    """Button that plays a quick scale-down-then-back on click.

    on_click is overridden (not wrapped at each call site) so every screen —
    main menu, settings, intro, pause, end — gets the animation for free just
    by using _themed_button(). The animation runs via animate_scale and never
    delays the real callback, which is invoked synchronously first.
    """

    def on_click_setter(self, value):
        super().on_click_setter(value)
        self._themed_callback = value

    def on_click_getter(self):
        return self._play_click_anim

    def _play_click_anim(self):
        callback = getattr(self, '_themed_callback', None)
        if callback:
            callback()
        self.animate_scale(
            self.scale * BUTTON_CLICK_ANIM_SCALE,
            duration=BUTTON_CLICK_ANIM_DURATION,
            curve=curve.out_expo_boomerang,
        )
