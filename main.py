import re
import subprocess
import sys

# Must run before `from ursina import *` — see Scripts/audio_workaround.py.
from Scripts import audio_workaround  # noqa: F401

from ursina import *
from Scripts.player_controller import Player
from Scripts.enemy import Enemy
from Scripts.behaviour_tree_factory import BehaviourTreeFactory
from Scripts.trigger_system import TriggerZone, build_actions
from Scripts.weapon import AmmoPickup
from Scripts.health_bar import HealthBar
from Scripts.collision_system import collision_manager, Layers
from Scripts.game import game, Game
from Scripts.lit_shader import lit_shader
from Scripts.light_lifecycle import destroy_light, is_light
from Scripts.bloom import BloomPipeline
from Scripts import dev_shader_tuning  # TEMPORARY dev-only lit_shader live-tuning; see module docstring
from Scripts import level_io
from Scripts.level_io import load_level_data
from Scripts.asset_resolve import (
    resolve_model as _resolve_model, resolve_texture as _resolve_texture,
    resolve_sound as _resolve_sound,
)
from Scripts.session_logger import get_game_logger
from Scripts.game_settings import (
    RESOLUTIONS, game_settings, save_settings, apply_audio_settings,
)
from Scripts.ui_theme import (
    BG_OVERLAY, TEXT_PRIMARY, TEXT_SECONDARY,
    HUD_MARGIN, BUTTON_SCALE, BUTTON_GAP, FONT_BOLD,
    ACCENT_WIN, ACCENT_LOSE, CROSSHAIR_COLOR,
    BUTTON_TEXTURE, BUTTON_CLICK_SOUND,
    HUD_PANEL_TEXTURE, HUD_PANEL_COLOR,
    ThemedButton,
)
import pyglet

# Shared game-session logger. Writes logs/session_*.log on exit (atexit).
# get_game_logger() returns a single cached instance even though main.py is loaded
# as both __main__ and `main` (via game.py's `from main import`), so teardown from
# both module objects lands in one log. Used by _clear_gameplay_entities()/main_menu()
# to record how far teardown got — the WIN/GAME_OVER → R crash was a C++ NodePath
# assertion (not a Python exception), so step-by-step INFO logging is how future
# sessions trace it.
logger = get_game_logger()

# game_settings is the shared singleton dict from Scripts/game_settings.py (loaded
# once at that module's import time). SettingsMenu mutates it in place and
# persists it via save_settings() so game.py/main_menu() and any future
# screen all see the same in-memory values without re-reading the file.

def _is_live(entity) -> bool:
    """True if `entity`'s NodePath is still attached (not already destroyed).

    Ursina's destroy() empties the NodePath synchronously (removeNode()) but defers
    removal from scene.entities to the next frame's flush. Within one synchronous
    teardown, scene.entities[:] therefore still contains just-destroyed entities whose
    NodePaths are empty. Reading e.name on those calls Panda3D getName() →
    'Assertion failed: !is_empty() at nodePath.I:2102'. Filter with this BEFORE touching
    .name or any NodePath property. This guards the C++ layer, not just Python attrs.
    """
    try:
        return not entity.is_empty()
    except Exception:
        return False


def _get_version_string():
    """Read the latest version header from CHANGELOG.md (e.g. 'v1.7.0').

    Falls back to an empty label if the file or header pattern is missing so a
    packaging change never crashes the main menu over cosmetic text.
    """
    try:
        with open('CHANGELOG.md') as f:
            for line in f:
                match = re.match(r'## \[(.+?)\]', line)
                if match:
                    return f'v{match.group(1)}'
    except Exception as e:
        logger.log('ERROR', f"_get_version_string failed: {type(e).__name__}: {e}")
    return ''


def _apply_debug_stats_setting(enabled: bool):
    """Show/hide Ursina's built-in top-right fps/entity/collider counters as
    one group, per the show_debug_stats setting. Called at boot and whenever
    SettingsMenu toggles it live."""
    window.fps_counter.enabled = enabled
    window.entity_counter.enabled = enabled
    window.collider_counter.enabled = enabled


def _themed_button(**kwargs):
    """ThemedButton() with the shared Kenney texture, click sound, bold title
    font, and click-scale animation.

    Button has no font= constructor kwarg — its text_entity is built internally,
    so the font must be set on text_entity after construction (Text.font_setter
    rebuilds the glyph geometry on assignment, per ursina/text.py).

    color= is a multiplicative tint over the texture (Entity.color_setter ->
    setColorScale), so it defaults to white here instead of the old BG_PANEL —
    tinting the Kenney PNG with a dark panel color would muddy its own shading.
    Callers that still pass color= (e.g. text_color-only calls) get that tint
    applied on top of the texture, which is intentional if ever needed.
    pressed_sound takes a real Audio instance (not a bare string) because
    Button.input() passes strings straight to Audio(), whose clip_setter globs
    under Ursina's own asset folders rather than the registry-resolved path.
    """
    kwargs.setdefault('texture', BUTTON_TEXTURE)
    kwargs.setdefault('color', color.white)
    kwargs.setdefault('pressed_sound', Audio(_resolve_sound(BUTTON_CLICK_SOUND), autoplay=False))
    button = ThemedButton(**kwargs)
    if button.text_entity:
        button.text_entity.font = FONT_BOLD
    return button


class PlayerHUD:
    """Owns all screen-space player UI: crosshair, health bar, hint text.

    Lifetime: created in start_game(), stored as game.hud, destroyed in
    _clear_gameplay_entities(). show()/hide() toggle everything together so
    no per-element visibility is scattered across main.py.
    """

    def __init__(self, player):
        # Reference only — PlayerHUD does not own the Player's lifetime, same
        # convention as the health_bar reference below. Read by update_ammo().
        self.player = player

        self.crosshair = Entity(
            parent=camera.ui,
            model='quad',
            texture=_resolve_texture('crosshair_white_crosshair002'),
            color=CROSSHAIR_COLOR,
            scale=(0.02, 0.02),
            z=-1,
        )
        # The UI region (sort 20) inherits depth-test ON by default (Ursina's
        # Camera._set_up) and there is no depth clear between it and the earlier
        # passes, so without forcing depth-test off the crosshair silently loses
        # the depth test against leftover depth and never renders. This is robust
        # regardless of what earlier regions do to the depth buffer (weapon.py's
        # VM region at sort 15 now clears depth for its own pass — that does not
        # affect this crosshair, which ignores depth entirely). Same fix as the
        # editor gizmo handles (editor_gizmo.py): depth test/write off + fixed bin.
        self.crosshair.setDepthTest(False)
        self.crosshair.setDepthWrite(False)
        self.crosshair.setBin('fixed', 100)

        # Reference to the player's existing HealthBar (created in Player.__init__).
        # PlayerHUD does not own the bar's lifetime — _clear_gameplay_entities() still
        # destroys the Player (which triggers HealthBar teardown). We just borrow the
        # reference so hide()/show() can toggle bar visibility.
        self.health_bar = player.health_bar

        # Corner-anchored off window.bottom_left/bottom_right so Ursina's built-in
        # aspectRatioChanged handler (window.update_aspect_ratio, which rescales every
        # camera.ui child's x by the aspect delta) keeps these pinned to their corners
        # on resize — the same pattern the level editor's border-anchored UI relies on.
        # visible (not enabled) reflects the show_hints setting — enabled=False
        # would stash the model entirely, which show()/hide() below don't undo
        # since they only ever toggle .visible.
        self.hint_text = Text(
            text='Move: WASD | Jump: Space | Shoot: LMB | Reload: R | Mouse: Esc | Fullscreen: F',
            visible=game_settings['show_hints'],
            parent=camera.ui,
            position=window.bottom_left + Vec2(HUD_MARGIN, HUD_MARGIN),
            origin=(-0.5, -0.5),
            scale=0.7,
            color=TEXT_SECONDARY,
            z=-1,
        )

        # Ammo counter — reads game.player.inventory.active_weapon each frame via
        # update_ammo() (called from the global update() below, PLAYING-gated).
        # Polling instead of hooking every shoot()/switch_to()/reload() call site
        # keeps WeaponInventory/Weapon free of any HUD dependency.
        #
        # ammo_panel: HL2-style backdrop behind the counter (Extra/ outlined frame,
        # tinted black + translucent — see ui_theme.HUD_PANEL_COLOR). Corner-pinned
        # to window.bottom_right - margin like the old bare ammo_text was, so
        # window.update_aspect_ratio still rescales it correctly on resize.
        # ammo_text is then centered on the panel's *center* (derived from the
        # panel's own corner anchor + half its scale) via origin=(0,0), instead
        # of a fixed nudge — keeps it centered regardless of string length
        # ('INF' vs 'RELOADING' vs '30/30').
        panel_scale = Vec2(0.2, 0.09)
        panel_anchor = window.bottom_right + Vec2(-HUD_MARGIN, HUD_MARGIN)
        panel_center = panel_anchor - Vec2(panel_scale.x / 2, -panel_scale.y / 2)
        self.ammo_panel = Entity(
            parent=camera.ui,
            model='quad',
            texture=_resolve_texture(HUD_PANEL_TEXTURE),
            color=HUD_PANEL_COLOR,
            scale=panel_scale,
            position=panel_anchor,
            origin=(0.5, -0.5),
            z=0,
        )
        self.ammo_text = Text(
            text='',
            enabled=False,
            parent=camera.ui,
            position=panel_center,
            origin=(0, 0),
            scale=1.2,
            color=TEXT_PRIMARY,
            z=-1,
        )

        self._visible = True

    def update_ammo(self):
        """Refresh the ammo counter from the player's active weapon. No-op if hidden."""
        if not self._visible or not self.ammo_text:
            return
        inventory = getattr(self.player, 'inventory', None)
        weapon = inventory.active_weapon if inventory else None
        if weapon is None:
            self.ammo_text.enabled = False
            if self.ammo_panel:
                self.ammo_panel.enabled = False
            return
        self.ammo_text.enabled = True
        if self.ammo_panel:
            self.ammo_panel.enabled = True
        if weapon.max_ammo < 0:
            self.ammo_text.text = 'INF'
        elif weapon.reloading:
            self.ammo_text.text = 'RELOADING'
        else:
            self.ammo_text.text = f'{weapon.ammo}/{weapon.max_ammo}'

    def show(self):
        self._visible = True
        self.crosshair.visible = True
        if self.health_bar:
            self.health_bar.visible = True
        # hint_text still honours the show_hints setting on top of the pause
        # toggle — re-showing the HUD must not override an explicit opt-out.
        if self.hint_text:
            self.hint_text.visible = game_settings['show_hints']
        if self.ammo_text:
            self.ammo_text.visible = True
        if self.ammo_panel:
            self.ammo_panel.visible = True

    def hide(self):
        self._visible = False
        self.crosshair.visible = False
        if self.health_bar:
            self.health_bar.visible = False
        if self.hint_text:
            self.hint_text.visible = False
        if self.ammo_text:
            self.ammo_text.visible = False
        if self.ammo_panel:
            self.ammo_panel.visible = False

    def destroy(self):
        if self.crosshair:
            destroy(self.crosshair)
            self.crosshair = None
        if self.hint_text:
            destroy(self.hint_text)
            self.hint_text = None
        if self.ammo_text:
            destroy(self.ammo_text)
            self.ammo_text = None
        if self.ammo_panel:
            destroy(self.ammo_panel)
            self.ammo_panel = None
        # health_bar/player lifetimes are owned by Player/start_game() — do not destroy here
        self.health_bar = None
        self.player = None


class EndScreen(Entity):
    """Fullscreen overlay shown on WIN or GAME_OVER. R key returns to menu.

    Children parented to self so `destroy(self)` cascades — no manual sub-entity teardown.
    Lifetime owned by game.win_screen / game.game_over_screen; cleared in
    _clear_gameplay_entities() via game.return_to_menu().
    """

    def __init__(self, title: str, is_win: bool = True):
        super().__init__(parent=camera.ui)
        self.background = Entity(
            parent=self,
            model='quad',
            color=BG_OVERLAY,
            scale=(2, 2),
            z=1,
        )
        accent = ACCENT_WIN if is_win else ACCENT_LOSE
        self.title_text = Text(
            text=title,
            parent=self,
            origin=(0, 0),
            scale=4,
            y=0.1,
            color=accent,
            font=FONT_BOLD,
            z=-1,
        )
        self.hint_text = Text(
            text='Press R to return to menu',
            parent=self,
            origin=(0, 0),
            scale=1.5,
            y=-0.1,
            color=TEXT_SECONDARY,
            z=-1,
        )


def _apply_level_lighting():
    """Create the scene's directional sun from level.json's 'light' entry.

    Called from main_menu() AFTER its scene sweep (which destroys every non-camera
    entity, a DirectionalLight included) — that ordering is why the sun is built
    here rather than once at app init, and it is unchanged from when these values
    were hardcoded. The sweep releases the old sun via destroy_light(), so the
    per-menu recreate does not accumulate lights or shadow buffers
    (Scripts/light_lifecycle.py).

    Falls back to level_io.default_light_entry() when the level has no light entry
    or cannot be read, so a pre-v1.7 level.json — and a missing/corrupt one — lights
    exactly as it did before the sun became editable.

    Intensity multiplies into the light colour: the shader reads
    p3d_LightSource[0].color.rgb directly (Scripts/lit_shader.py), so scaling the
    colour IS the intensity control — no extra uniform or per-entity plumbing.
    """
    entry = None
    try:
        for e in load_level_data('level.json'):
            if e['type'] == 'light':
                entry = e
                break
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"Error reading level lighting, using defaults: {e}")
    if entry is None:
        entry = level_io.default_light_entry()

    intensity = max(0.0, float(entry['intensity']))
    r, g, b = (list(entry['colour']) + [1, 1, 1])[:3]
    sun = DirectionalLight()
    sun.color = color.rgb(
        min(1.0, r * intensity),
        min(1.0, g * intensity),
        min(1.0, b * intensity),
    )
    # look_at consumes the direction vector the same way the pre-v1.7 hardcoded
    # call did. The editor stores that vector; its proxy's rotation is derived
    # from it through the same level_io conversion, so the editor's preview and
    # this light are aimed by one shared definition.
    direction = Vec3(*entry['direction'])
    sun.look_at(direction)
    _apply_sun_shadows(sun, direction)
    return sun


# Shadow-map coverage for the sun's depth camera. The play area is the 100x100
# ground plane (main_menu()); SHADOW_FILM covers a generous slice of it centred on
# the origin. A directional shadow camera is orthographic, so this is world-units
# square, NOT an FoV — too small and geometry outside it casts no shadow; too large
# and every shadow-map texel covers more world and edges coarsen. 60 units frames
# the near play field crisply at 1024^2 (~0.06 world-units/texel).
SHADOW_FILM = 60.0
SHADOW_MAP_SIZE = 1024
# How far back along -direction to place the light node so the whole scene sits in
# front of the depth camera's near plane, plus the near/far that bracket it.
SHADOW_PULLBACK = 60.0
SHADOW_NEAR, SHADOW_FAR = 1.0, 160.0


def _apply_sun_shadows(sun, direction):
    """Turn the sun into a PCF shadow caster and arm lit_shader's shadow path.

    The L3 probe (tools/shadow_fbo_probe.py) established every step here on the
    real GL 2.1 driver: the depth FBO allocates, the shadow uniforms reach the
    #version 120 shader, and — the trap — a DirectionalLight left at the origin
    has the scene BEHIND its near plane, so the depth map comes back empty and
    every fragment trivially reads "lit". Pulling the node back along -direction
    and setting an orthographic film that covers the play area fixes that.

    Teardown is already handled: destroy_light() (Scripts/light_lifecycle.py)
    calls set_shadow_caster(False) to release the buffer, so the per-menu recreate
    does not accumulate FBOs (verified flat across 6 cycles once a caster is live —
    tests/test_light_lifecycle.py).
    """
    d = direction.normalized() if direction.length() > 1e-6 else Vec3(0, -1, 0)
    # Position only moves the depth camera's viewpoint; a DirectionalLight's
    # rotation (set by look_at above) is what drives p3d_LightSource[0].position's
    # direction, so pulling back does NOT change how the scene is lit — only what
    # the shadow camera can see.
    sun.position = -d * SHADOW_PULLBACK

    light = sun._light
    light.set_shadow_caster(True, SHADOW_MAP_SIZE, SHADOW_MAP_SIZE)
    lens = light.get_lens()
    lens.set_film_size(SHADOW_FILM, SHADOW_FILM)
    lens.set_near_far(SHADOW_NEAR, SHADOW_FAR)

    # NOTE: the shader's PCF path is armed by lit_shader's shadow_enabled=1.0
    # DEFAULT, NOT by a live mutation here. Flipping it per menu-cycle via
    # `lit_shader.shadow_enabled = 1.0` walks every entity using the shader through
    # Ursina's Shader.__setattr__ — including entities the menu sweep emptied but
    # has not yet flushed — and set_shader_input on an empty NodePath crashes
    # (!is_empty() at nodePath.I:228). So the flag lives in default_input and every
    # new lit entity inherits it; the shadow map + shadowViewMatrix are supplied
    # per-light by the GSG once the caster above is set. See the lit_shader
    # shadow_enabled note for the unbound-sampler-reads-lit safety argument.
    return sun


def load_level():
    try:
        entities = load_level_data('level.json')
    except FileNotFoundError:
        print("No level file found. Create one using the level editor.")
        return

    # main_menu() calls load_level() in the same synchronous frame after its own
    # destroys, so scene.entities can still hold emptied-but-unflushed NodePaths.
    # _is_live() guards e.name against the getName()-on-empty assertion.
    for e in scene.entities[:]:
        if not _is_live(e):
            continue
        if e.name in ['level_block', 'level_enemy', 'level_trigger', 'level_pickup']:
            destroy(e)

    for entry in entities:
        if entry['type'] == 'light':
            # v1.7 Step 4: lighting is applied by _apply_level_lighting() (called
            # from main_menu before this) — there is no geometry to build for a
            # light. MUST be skipped explicitly: the final branch below is an
            # unconditional `else`, so a light entry would otherwise fall through
            # and spawn a collidable phantom block at the sun's editor position.
            continue
        if entry['type'] == 'enemy':
            enemy_placeholder = Entity(
                position=tuple(entry['position']),
                model='cube',
                color=color.red,
                scale=(1.5, 3, 1.5),
                name='level_enemy'
            )
            enemy_placeholder.enemy_hp       = entry['hp']
            enemy_placeholder.enemy_type     = entry['enemy_type']
            enemy_placeholder.enemy_rotation = entry['rotation_y']
            # v1.4 Step 8: raw behaviour-config dict (or None). Stashed here on
            # the placeholder; start_game() builds the tree from it and passes
            # behaviour_tree= to Enemy. Same two-stage hand-off as hp/type/rot.
            enemy_placeholder.behaviour_config = entry['behaviour']
        elif entry['type'] == 'trigger':
            # v1.5 System A: invisible trigger volume. Config-store role — stash
            # the raw action lists on an invisible placeholder; start_game()
            # builds the live TriggerZone with real callbacks (factory consumer).
            # Same two-stage hand-off as the enemy behaviour config. Placeholder
            # is non-collidable so it can't block movement or shadow the picker.
            trigger_placeholder = Entity(
                position=tuple(entry['position']),
                scale=tuple(entry['scale']),
                visible=False,
                name='level_trigger'
            )
            trigger_placeholder.on_enter_actions = entry['on_enter']
            trigger_placeholder.on_exit_actions  = entry['on_exit']
        elif entry['type'] == 'pickup':
            # v1.5 Step 13: weapon/ammo pickup. Config-store role — stash the raw
            # pickup config on an invisible placeholder; start_game() builds the
            # live AmmoPickup (factory consumer). Same two-stage hand-off as the
            # trigger action lists above. Non-collidable so it can't block
            # movement or shadow the picker.
            pickup_placeholder = Entity(
                position=tuple(entry['position']),
                visible=False,
                name='level_pickup'
            )
            pickup_placeholder.pickup_type = entry['pickup_type']
            pickup_placeholder.weapon_type = entry['weapon_type']
            pickup_placeholder.amount      = entry['amount']
        else:
            block = Entity(
                model=_resolve_model(entry['model']),
                collider='box',
                texture=_resolve_texture(entry['texture']),
                position=tuple(entry['position']),
                color=color.rgb(*entry['colour']),
                rotation=tuple(entry['rotation']),
                scale=tuple(entry['scale']),
                name='level_block',
                shader=lit_shader,   # GLSL 1.20 lit path — lit by the sun + ambient set in main_menu()
            )
            # v1.5 Step 4: lookup-name for open_door. Structural name stays
            # 'level_block' (cleanup sweeps depend on it) — door identity is a
            # SEPARATE attribute the open_door action scans for at fire time.
            block.door_name = entry['door_name']


def _clear_gameplay_entities():
    """
    Canonical gameplay scene teardown. Called exclusively by game.return_to_menu().
    Order matters: sub-entities before owners, AliveEntity.die() before destroy().
    # Teardown order is load-bearing — do not reorder.
    # 1. Pool bullets back  2. Unregister from collision_manager
    # 3. die() all AliveEntities  4. Destroy UI  5. Clear lists/refs
    # 6. Reset time_scale  7. Set state = MAIN_MENU
    # Regression: pause → menu → restart must complete without NodePath assertion.
    """
    # Reset bullet pools FIRST — before any entity they reference is destroyed.
    # Pools are module-level singletons that survive scene transitions.  main_menu()
    # sweeps scene.entities and destroys parked bullets; without reset(), _free still
    # holds dead NodePaths and the next acquire() crashes on _reset() position assignment.
    # reset() clears _free and _built so the pool rebuilds from scratch next session.
    logger.log('INFO', 'teardown: begin _clear_gameplay_entities')
    from Scripts.weapon import reset_bullet_pools
    reset_bullet_pools()
    logger.log('INFO', 'teardown: bullet pools reset')

    if game.hud:
        game.hud.destroy()
        game.hud = None
    logger.log('INFO', 'teardown: hud destroyed')

    if game.win_screen:
        destroy(game.win_screen)
        game.win_screen = None
    if game.game_over_screen:
        destroy(game.game_over_screen)
        game.game_over_screen = None
    logger.log('INFO', 'teardown: end screens destroyed')

    for e in list(game.enemies):
        if getattr(e, 'alive', False):
            e.die()
    game.enemies.clear()
    logger.log('INFO', 'teardown: enemies cleared')

    if game.player:
        if hasattr(game.player, 'inventory') and game.player.inventory:
            game.player.inventory.destroy_all()
        if hasattr(game.player, 'health_bar') and game.player.health_bar:
            # HealthBar.on_destroy() tears down its own bg/bar/text children.
            destroy(game.player.health_bar)
        if hasattr(game.player, 'debug_lines'):
            for line in game.player.debug_lines:
                destroy(line)
        collision_manager.remove(game.player)
        destroy(game.player)
        game.player = None
    logger.log('INFO', 'teardown: player destroyed')

    # Filter dead refs (empty NodePaths) BEFORE reading e.name — the destroys above
    # emptied their NodePaths synchronously but they linger in scene.entities until the
    # next frame's flush. _is_live() avoids the getName()-on-empty NodePath assertion.
    for e in scene.entities[:]:
        if not _is_live(e):
            continue
        if e.name in ('level_block', 'level_enemy', 'ground',
                      'main_sky', 'camera_pivot'):
            destroy(e)
    logger.log('INFO', 'teardown: scene entities swept')

    camera.parent = scene
    camera.position = (0, 0, 0)
    camera.rotation = (0, 0, 0)
    application.time_scale = 1
    logger.log('INFO', 'teardown: _clear_gameplay_entities complete')


def main_menu():
    from Scripts.collision_system import AliveEntity
    logger.log('INFO', 'main_menu: begin scene rebuild')
    for e in scene.entities[:]:
        if isinstance(e, AliveEntity) and e.alive and _is_live(e):
            e.die()
    # _is_live() filters entities destroyed earlier in this same synchronous teardown
    # (empty NodePath, not yet flushed from scene.entities) before reading e.name —
    # otherwise getName() on the empty NodePath asserts (nodePath.I:2102).
    for e in scene.entities[:]:
        if not _is_live(e):
            continue
        if e.name not in ['main_camera']:
            # Lights need destroy_light(), not destroy(): destroy() detaches the
            # entity but leaves the light on render's LightAttrib and keeps its
            # shadow FBO, so every menu rebuild leaked one more lit orphan
            # (on_lights 1->2->3->4 across cycles). See Scripts/light_lifecycle.py.
            if is_light(e):
                destroy_light(e)
            else:
                destroy(e)
    logger.log('INFO', 'main_menu: old scene swept')

    sky = Sky(texture='sky_textures/sky_0.png')
    sky.name = 'main_sky'

    camera.name = 'main_camera'
    camera.parent = scene

    # Scene lighting for the GLSL 1.20 lit path (Scripts/lit_shader.py). Created
    # HERE, after the sweep above — the sweep destroys every non-camera scene
    # entity (a DirectionalLight included), so a sun set once at app init would
    # not survive the first menu rebuild. One directional "sun" fills
    # p3d_LightSource[0]; scene.ambient_color fills p3d_LightModel.ambient. Both
    # are read by lit_shader; unlit geometry (HUD, gun viewmodel) ignores them.
    #
    # Ambient stays hardcoded (still scene config, not level data). The sun's
    # colour/intensity/direction now come from level.json's 'light' entry so the
    # editor can author them (v1.7 Step 4); a level with no entry yields exactly
    # the values this code used to hardcode, so pre-v1.7 levels are unchanged.
    scene.ambient_color = color.rgb(0.35, 0.35, 0.40)
    _apply_level_lighting()

    ground = Entity(
        model='cube',
        collider='box',
        y=0,
        scale=(100, 1, 100),
        texture='assets/textures/floor_ground_grass.png',
        texture_scale=(50, 50),
        name='ground',
        shader=lit_shader,   # GLSL 1.20 lit path — lit by the sun + ambient set in main_menu()
    )

    load_level()

    camera_pivot = Entity(name='camera_pivot')
    camera.parent = camera_pivot
    camera_pivot.position = (0, 10, -30)
    camera_pivot.rotation_x = 15

    def rotate():
        camera_pivot.rotation_y += 10 * time.dt
    camera_pivot.update = rotate

    title_text = Text(
        text="Ivan's 3D Engine",
        parent=camera.ui,
        origin=(0, 0),
        y=0.35,
        scale=3,
        color=TEXT_PRIMARY,
        font=FONT_BOLD,
    )
    version_text = Text(
        text=_get_version_string(),
        parent=camera.ui,
        position=window.bottom_right + Vec2(-HUD_MARGIN, HUD_MARGIN),
        origin=(0.5, -0.5),
        scale=0.8,
        color=TEXT_SECONDARY,
    )

    play_button = _themed_button(text='Play', text_color=TEXT_PRIMARY, scale=BUTTON_SCALE, y=BUTTON_GAP * 1.5)
    editor_button = _themed_button(text='Level Editor', text_color=TEXT_PRIMARY, scale=BUTTON_SCALE, y=BUTTON_GAP * 0.5)
    settings_button = _themed_button(text='Settings', text_color=TEXT_PRIMARY, scale=BUTTON_SCALE, y=-BUTTON_GAP * 0.5)
    quit_button = _themed_button(text='Quit', text_color=TEXT_PRIMARY, scale=BUTTON_SCALE, y=-BUTTON_GAP * 1.5)
    main_menu_buttons = [play_button, editor_button, settings_button, quit_button]

    def launch_level_editor():
        subprocess.Popen([sys.executable, 'Scripts/level_editor.py'])

    editor_button.on_click = launch_level_editor

    def open_settings():
        title_text.enabled = False
        version_text.enabled = False
        for b in main_menu_buttons:
            b.enabled = False

        def on_back():
            title_text.enabled = True
            version_text.enabled = True
            for b in main_menu_buttons:
                b.enabled = True

        SettingsMenu(on_back=on_back)

    settings_button.on_click = open_settings

    def start_game():
        # This branch is currently unreachable: 'r' on WIN/GAME_OVER already calls
        # game.return_to_menu() + main_menu() before rebuilding play_button/start_game,
        # so game.player is always None here. Routed through the canonical teardown
        # path (instead of duplicating its player-cleanup logic inline) so any future
        # fix there doesn't need a second copy — but note return_to_menu()'s scene
        # sweep also destroys the level_block/level_enemy placeholders the loops below
        # convert into live enemies/triggers/pickups. If this branch is ever made
        # reachable, the level must be reloaded before those loops run.
        if game.player:
            game.return_to_menu()
        game.player = Player(position=(0, 2, 0))
        game.enemies = []
        # _is_live() guard is load-bearing: main_menu()'s own load_level() call destroys
        # pre-existing level_block/level_enemy/level_trigger placeholders synchronously
        # right before this function runs. If start_game() is invoked before any frame
        # flushes scene._entities_marked_for_removal (not reachable via normal human
        # mouse-driven play, where at least one frame always renders between main_menu()
        # returning and a click being dispatched — but reachable via any future code path
        # that chains main_menu() straight into start_game() without an intervening
        # frame, e.g. programmatic/scripted invocation), reading e.name on those emptied
        # NodePaths fires the C++ getName() assertion before the filter can reject them.
        # Hard Constraint 13; same bug class as the level_trigger loop's guard below.
        for placeholder in [e for e in scene.entities if _is_live(e) and e.name == 'level_enemy']:
            # v1.4 Step 8: build the behaviour tree from the placeholder's
            # stashed config. config is the full {"tree": ..., "waypoints": ...}
            # dict; the Factory reads "tree" for the preset and "waypoints"
            # straight from it (raw lists — it converts to Vec3 internally, so
            # we must NOT pre-convert). Absent config → behaviour_tree=None →
            # Enemy.__init__ builds the "default" preset itself.
            behaviour_tree = None
            config = getattr(placeholder, 'behaviour_config', None)
            if config:
                behaviour_tree = BehaviourTreeFactory.build(
                    config.get('tree', 'default'), config
                )
            enemy = Enemy(
                spawn_position=placeholder.position,
                player=game.player,
                hp=getattr(placeholder, 'enemy_hp', 100),
                enemy_type=getattr(placeholder, 'enemy_type', 'default'),
                rotation_y=getattr(placeholder, 'enemy_rotation', 0),
                behaviour_tree=behaviour_tree,
            )
            game.enemies.append(enemy)
            destroy(placeholder)
        # v1.5 System A: factory-consume trigger placeholders into live TriggerZones.
        # build_actions() turns the stored raw action lists into zero-arg callbacks
        # HERE (runtime), never at editor-load time. TriggerZone is an AliveEntity,
        # so the main_menu() die()-sweep tears it down on return-to-menu.
        #
        # _is_live() guard is load-bearing: this loop runs AFTER the enemy loop's
        # destroy(placeholder) calls, which empty those NodePaths synchronously but
        # leave them in scene.entities until the frame flush. Reading e.name on an
        # emptied NodePath fires the C++ getName() assertion (nodePath.I:2102) that
        # except cannot catch — Hard Constraint 13.
        for placeholder in [e for e in scene.entities
                            if _is_live(e) and e.name == 'level_trigger']:
            TriggerZone(
                position=placeholder.position,
                scale=placeholder.scale,
                on_enter=build_actions(getattr(placeholder, 'on_enter_actions', [])),
                on_exit=build_actions(getattr(placeholder, 'on_exit_actions', [])),
            )
            destroy(placeholder)
        # v1.5 Step 13: factory-consume pickup placeholders into live AmmoPickups.
        # Same two-stage hand-off as triggers above — config lives on the
        # placeholder, the live entity is built only here (factory site), never
        # at editor-load time. _is_live() guard for the same reason (this loop
        # runs after two prior destroy()-heavy loops in the same sync frame).
        for placeholder in [e for e in scene.entities
                            if _is_live(e) and e.name == 'level_pickup']:
            AmmoPickup(
                position=placeholder.position,
                pickup_type=getattr(placeholder, 'pickup_type', 'ammo'),
                weapon_type=getattr(placeholder, 'weapon_type', 'pistol'),
                amount=getattr(placeholder, 'amount', 30),
            )
            destroy(placeholder)
        destroy(title_text)
        destroy(version_text)
        destroy(play_button)
        destroy(editor_button)
        destroy(settings_button)
        destroy(quit_button)
        destroy(camera_pivot)

        game.hud = PlayerHUD(game.player)
        game.hud.show()
        mouse.visible = False
        mouse.locked = True
        game.start()

    play_button.on_click = start_game
    quit_button.on_click = application.quit


class IntroScreen(Entity):
    """One-time splash shown before the first main_menu() build. No logo art
    exists in assets/ yet, so this is styled title text only — same theme as
    the main menu, distinguished by the 'click to continue' prompt.

    Ursina auto-dispatches .input(key) on every scene entity each frame (same
    mechanism PauseMenu/gameplay entities rely on), so any key or mouse click
    advances past it without touching the __main__ update()/input() functions
    defined later in this file.
    """

    def __init__(self):
        super().__init__(parent=camera.ui)
        self.background = Entity(parent=self, model='quad', color=BG_OVERLAY, scale=(2, 2), z=1)
        self.title_text = Text(
            text="Ivan's 3D Engine", parent=self, origin=(0, 0),
            scale=4, color=TEXT_PRIMARY, font=FONT_BOLD,
        )
        self.hint_text = Text(
            text='Click or press any key to continue', parent=self, origin=(0, 0),
            y=-0.15, scale=1.3, color=TEXT_SECONDARY,
        )

    def input(self, key):
        destroy(self)
        main_menu()


class SettingsMenu(Entity):
    """Resolution + volume overlay. Mutates the module-level `game_settings`
    dict in place and persists on every change via save_settings() — same
    immediate-write pattern as editor_core.py's _save_prefs() (no separate
    'Apply' step to forget).
    """

    def __init__(self, on_back):
        super().__init__(parent=camera.ui)
        self.on_back = on_back

        self.background = Entity(parent=self, model='quad', color=BG_OVERLAY, scale=(2, 2), z=1)

        self.title_text = Text(
            text='Settings', parent=self, origin=(0, 0), y=0.3,
            scale=2.5, color=TEXT_PRIMARY, font=FONT_BOLD,
        )

        res_w, res_h = RESOLUTIONS[game_settings['resolution_index']]
        self.resolution_button = _themed_button(
            text=f'Resolution: {res_w}x{res_h}',
            text_color=TEXT_PRIMARY,
            scale=BUTTON_SCALE, y=0.19, parent=self,
        )
        self.resolution_button.on_click = self.cycle_resolution

        self.sfx_slider = Slider(
            min=0, max=1, default=game_settings['sfx_volume'],
            text='SFX Volume', dynamic=False,
            parent=self, y=0.09, x=-0.09,
        )
        self.sfx_slider.on_value_changed = self.on_sfx_changed

        self.music_slider = Slider(
            min=0, max=1, default=game_settings['music_volume'],
            text='Music Volume', dynamic=False,
            parent=self, y=0.03, x=-0.09,
        )
        self.music_slider.on_value_changed = self.on_music_changed

        self.debug_stats_button = _themed_button(
            text=self._debug_stats_label(),
            text_color=TEXT_PRIMARY,
            scale=BUTTON_SCALE, y=-0.08, parent=self,
        )
        self.debug_stats_button.on_click = self.toggle_debug_stats

        self.hints_button = _themed_button(
            text=self._hints_label(),
            text_color=TEXT_PRIMARY,
            scale=BUTTON_SCALE, y=-0.18, parent=self,
        )
        self.hints_button.on_click = self.toggle_hints

        self.weapon_sway_button = _themed_button(
            text=self._weapon_sway_label(),
            text_color=TEXT_PRIMARY,
            scale=BUTTON_SCALE, y=-0.28, parent=self,
        )
        self.weapon_sway_button.on_click = self.toggle_weapon_sway

        self.camera_bob_button = _themed_button(
            text=self._camera_bob_label(),
            text_color=TEXT_PRIMARY,
            scale=BUTTON_SCALE, y=-0.38, parent=self,
        )
        self.camera_bob_button.on_click = self.toggle_camera_bob

        self.back_button = _themed_button(
            text='Back', text_color=TEXT_PRIMARY,
            scale=BUTTON_SCALE, y=-0.48, parent=self,
        )
        self.back_button.on_click = self.close

    def cycle_resolution(self):
        game_settings['resolution_index'] = (game_settings['resolution_index'] + 1) % len(RESOLUTIONS)
        w, h = RESOLUTIONS[game_settings['resolution_index']]
        self.resolution_button.text = f'Resolution: {w}x{h}'
        if not window.fullscreen:
            window.size = (w, h)
        save_settings(game_settings)

    def on_sfx_changed(self):
        game_settings['sfx_volume'] = self.sfx_slider.value
        apply_audio_settings(game_settings)
        save_settings(game_settings)

    def on_music_changed(self):
        game_settings['music_volume'] = self.music_slider.value
        save_settings(game_settings)

    def _debug_stats_label(self):
        return f"Debug Stats: {'On' if game_settings['show_debug_stats'] else 'Off'}"

    def _hints_label(self):
        return f"Hints: {'On' if game_settings['show_hints'] else 'Off'}"

    def toggle_debug_stats(self):
        game_settings['show_debug_stats'] = not game_settings['show_debug_stats']
        self.debug_stats_button.text = self._debug_stats_label()
        _apply_debug_stats_setting(game_settings['show_debug_stats'])
        save_settings(game_settings)

    def toggle_hints(self):
        game_settings['show_hints'] = not game_settings['show_hints']
        self.hints_button.text = self._hints_label()
        if game.hud and game.hud.hint_text:
            game.hud.hint_text.visible = game_settings['show_hints']
        save_settings(game_settings)

    def _weapon_sway_label(self):
        return f"Weapon Sway: {'On' if game_settings['weapon_sway_enabled'] else 'Off'}"

    def _camera_bob_label(self):
        return f"Camera Bob: {'On' if game_settings['camera_bob_enabled'] else 'Off'}"

    def toggle_weapon_sway(self):
        game_settings['weapon_sway_enabled'] = not game_settings['weapon_sway_enabled']
        self.weapon_sway_button.text = self._weapon_sway_label()
        save_settings(game_settings)

    def toggle_camera_bob(self):
        game_settings['camera_bob_enabled'] = not game_settings['camera_bob_enabled']
        self.camera_bob_button.text = self._camera_bob_label()
        save_settings(game_settings)

    def close(self):
        self.on_back()
        destroy(self)


class PauseMenu(Entity):
    def __init__(self):
        super().__init__(parent=camera.ui)

        # scale=(2,2) fills the full UI space (-0.5→+0.5 on both axes); z=1 stays behind buttons
        self.background = Entity(
            parent=self,
            model='quad',
            color=BG_OVERLAY,
            scale=(2, 2),
            z=1
        )

        self.continue_button = _themed_button(
            text='Continue',
            text_color=TEXT_PRIMARY,
            scale=BUTTON_SCALE,
            y=BUTTON_GAP,
            parent=self
        )
        self.main_menu_button = _themed_button(
            text='Main Menu',
            text_color=TEXT_PRIMARY,
            scale=BUTTON_SCALE,
            y=0.0,
            parent=self
        )
        self.quit_button = _themed_button(
            text='Quit',
            text_color=TEXT_PRIMARY,
            scale=BUTTON_SCALE,
            y=-BUTTON_GAP,
            parent=self
        )

        self.continue_button.on_click = self.resume_game
        self.main_menu_button.on_click = self.return_to_main_menu
        self.quit_button.on_click = application.quit

        if game.hud:
            game.hud.hide()

    def resume_game(self):
        game.resume()
        application.time_scale = 1
        mouse.visible = False
        mouse.locked = True
        if game.hud:
            game.hud.show()
        game.pause_menu = None
        destroy(self)

    def return_to_main_menu(self):
        game.pause_menu = None
        destroy(self)
        game.return_to_menu()
        main_menu()
        mouse.visible = True
        mouse.locked = False


if __name__ == '__main__':
    from panda3d.core import AntialiasAttrib, loadPrcFileData
    loadPrcFileData('', 'framebuffer-multisample 1\nmultisamples 4')
    # Ursina 8.3.0 uses GLSL #version 130/140 shaders. macOS OpenGL 2.1 supports GLSL 1.20 only.
    # Must patch BEFORE Ursina() — the constructor creates entities that trigger shader compilation.
    from Scripts.compat import patch_shaders_to_glsl120 as _patch_shaders_to_glsl120
    print("[main] shader patch 1/2 — pre-Ursina")
    _patch_shaders_to_glsl120()
    print("[main] patch 1/2 complete, calling Ursina()...")
    app = Ursina(title="Ivan's 3D Engine")
    # color.rgb() expects 0-1 floats, not 0-255 ints — (50, 50, 60) clamps every
    # channel to 1.0 (white), silently blowing out the viewport background to pure
    # white instead of the intended dark cool-gray. Same footgun class as CLAUDE.md's
    # documented color.rgb() gotcha; this call predates the v1.5 UI redesign but was
    # undermining its whole premise (flat/washed-out look) until caught here.
    window.color = color.rgb(50/255, 50/255, 60/255)
    # BUG 1 FIX: render/render2d NodePaths are not fully initialized during the
    # window-setup resize sequence; defer setAntialias to the first frame.
    camera.clip_plane_near = 0.01
    window.title = "Ivan's 3D Engine"
    window.exit_button.visible = False
    window.cog_button.enabled = False  # Ursina's built-in dev-mode gear/editor-UI menu toggle
    _apply_debug_stats_setting(game_settings['show_debug_stats'])
    window.fps_limit = 60
    mouse.visible = True

    display = pyglet.display.get_display()
    screen = display.get_default_screen()
    screen_width, screen_height = screen.width, screen.height

    window.borderless = False
    window.resizable = True
    window.fullscreen = False
    window.size = RESOLUTIONS[game_settings['resolution_index']]
    window.multisamples = 16

    window.position = (
        (screen_width - window.size[0]) // 2,
        (screen_height - window.size[1]) // 2
    )

    apply_audio_settings(game_settings)

    # BUG 2 FIX: Ursina() and subsequent window resizes can re-initialize shader
    # objects on internal entities. Re-patch after window setup so all shaders
    # compiled during main_menu() entity creation use GLSL 1.20.
    print("[main] shader patch 2/2 — post-window-setup")
    _patch_shaders_to_glsl120()

    # v1.7 bloom (Candidate B2). Built ONCE, here, and never again: the chain owns
    # 4 offscreen buffers, and this project has twice shipped a leak by allocating
    # GPU resources on a path that main_menu()/return_to_menu() re-runs (shadow
    # FBOs, LightAttrib entries — see Scripts/light_lifecycle.py). Nothing in
    # teardown touches it; the quads are raw NodePaths, not scene entities, so
    # main_menu()'s sweep cannot reach them. Toggle via game.bloom.set_enabled().
    #
    # After window setup on purpose: FilterManager sizes its buffers off the
    # current window, and window.size is set above. It re-sizes them itself on
    # Panda's window-event afterwards.
    game.bloom = BloomPipeline()
    print("[main] bloom pipeline built (4 offscreen buffers, once, at init)")

    def _deferred_antialias(task):
        # BUG 1 FIX cont.: run after first frame so render/render2d NodePaths exist.
        render.setAntialias(AntialiasAttrib.MAuto)
        render2d.setAntialias(AntialiasAttrib.MAuto)
        return task.done

    taskMgr.doMethodLater(0, _deferred_antialias, '_deferred_antialias')

    IntroScreen()

    def update():
        collision_manager.update()
        # WIN condition: PLAYING with no live enemies left on the layer.
        # count_layer reads from collision_manager._tracked (which AliveEntity.die() prunes),
        # not game.enemies (which still holds dead refs). State guard makes this idempotent.
        if game.state == Game.PLAYING and collision_manager.count_layer(Layers.ENEMY) == 0:
            game.trigger_win()
        if game.state == Game.PLAYING and game.hud:
            game.hud.update_ammo()

    def input(key):
        if key == 'f':
            window.fullscreen = not window.fullscreen
            if window.fullscreen:
                window.borderless = True
                window.size = (screen_width, screen_height)
                mouse.locked = True
            else:
                window.size = RESOLUTIONS[game_settings['resolution_index']]
                window.position = (
                    (screen_width - window.size[0]) // 2,
                    (screen_height - window.size[1]) // 2
                )
                window.borderless = False
                mouse.locked = False
                mouse.visible = True

        if key == "escape":
            if game.state == Game.PLAYING:
                game.pause()
                application.time_scale = 0
                mouse.visible = True
                mouse.locked = False
                game.pause_menu = PauseMenu()  # PauseMenu.__init__ calls game.hud.hide()
            elif game.state == Game.PAUSED:
                if game.pause_menu:
                    game.pause_menu.resume_game()

        if key == 'r' and game.state in (Game.WIN, Game.GAME_OVER):
            application.time_scale = 1
            game.return_to_menu()
            main_menu()
            mouse.visible = True
            mouse.locked = False

        if key == 'left mouse' and not window.fullscreen and game.state == Game.PLAYING:
            mouse.locked = True
            mouse.visible = False

        dev_shader_tuning.handle_input(key)  # TEMPORARY — see Scripts/dev_shader_tuning.py


    app.run()
