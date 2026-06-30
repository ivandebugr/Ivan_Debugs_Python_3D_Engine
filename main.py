from ursina import *
from Scripts.player_controller import Player
from Scripts.enemy import Enemy
from Scripts.behaviour_tree_factory import BehaviourTreeFactory
from Scripts.trigger_system import TriggerZone, build_actions
from Scripts.health_bar import HealthBar
from Scripts.collision_system import collision_manager, Layers
from Scripts.game import game, Game
from Scripts.level_io import load_level_data
from Scripts.undo_redo import _resolve_model
from Scripts.session_logger import get_game_logger
import pyglet

# Shared game-session logger. Writes logs/session_*.log on exit (atexit).
# get_game_logger() returns a single cached instance even though main.py is loaded
# as both __main__ and `main` (via game.py's `from main import`), so teardown from
# both module objects lands in one log. Used by _clear_gameplay_entities()/main_menu()
# to record how far teardown got — the WIN/GAME_OVER → R crash was a C++ NodePath
# assertion (not a Python exception), so step-by-step INFO logging is how future
# sessions trace it.
logger = get_game_logger()


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


class PlayerHUD:
    """Owns all screen-space player UI: crosshair, health bar, hint text.

    Lifetime: created in start_game(), stored as game.hud, destroyed in
    _clear_gameplay_entities(). show()/hide() toggle everything together so
    no per-element visibility is scattered across main.py.
    """

    def __init__(self, player):
        self.crosshair = Entity(
            parent=camera.ui,
            model='quad',
            texture='circle',
            color=color.red,
            scale=(0.01, 0.01),
            z=-1,
        )

        # Reference to the player's existing HealthBar (created in Player.__init__).
        # PlayerHUD does not own the bar's lifetime — _clear_gameplay_entities() still
        # destroys the Player (which triggers HealthBar teardown). We just borrow the
        # reference so hide()/show() can toggle bar visibility.
        self.health_bar = player.health_bar

        self.hint_text = Text(
            text='Move: WASD | Jump: Space | Shoot: LMB | Reset: R | Mouse: Esc | Fullscreen: F',
            parent=camera.ui,
            position=window.bottom_left + Vec2(0.01, 0.04),
            origin=(-0.5, -0.5),
            scale=0.7,
            z=-1,
        )

        self.ammo_text = None   # placeholder until ammo system is implemented
        self._visible = True

    def show(self):
        self._visible = True
        self.crosshair.visible = True
        if self.health_bar:
            self.health_bar.visible = True
        if self.hint_text:
            self.hint_text.visible = True

    def hide(self):
        self._visible = False
        self.crosshair.visible = False
        if self.health_bar:
            self.health_bar.visible = False
        if self.hint_text:
            self.hint_text.visible = False

    def destroy(self):
        if self.crosshair:
            destroy(self.crosshair)
            self.crosshair = None
        if self.hint_text:
            destroy(self.hint_text)
            self.hint_text = None
        # health_bar lifetime is owned by Player — do not destroy here
        self.health_bar = None


class EndScreen(Entity):
    """Fullscreen overlay shown on WIN or GAME_OVER. R key returns to menu.

    Children parented to self so `destroy(self)` cascades — no manual sub-entity teardown.
    Lifetime owned by game.win_screen / game.game_over_screen; cleared in
    _clear_gameplay_entities() via game.return_to_menu().
    """

    def __init__(self, title: str):
        super().__init__(parent=camera.ui)
        self.background = Entity(
            parent=self,
            model='quad',
            color=color.rgba(0, 0, 0, 0.7),
            scale=(2, 2),
            z=1,
        )
        self.title_text = Text(
            text=title,
            parent=self,
            origin=(0, 0),
            scale=4,
            y=0.1,
            z=-1,
        )
        self.hint_text = Text(
            text='Press R to return to menu',
            parent=self,
            origin=(0, 0),
            scale=1.5,
            y=-0.1,
            z=-1,
        )


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
        if e.name in ['level_block', 'level_enemy', 'level_trigger']:
            destroy(e)

    for entry in entities:
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
        else:
            Entity(
                model=_resolve_model(entry['model']),
                collider='box',
                texture=entry['texture'],
                position=tuple(entry['position']),
                color=color.rgb(*entry['colour']),
                rotation=tuple(entry['rotation']),
                scale=tuple(entry['scale']),
                name='level_block'
            )


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
        if hasattr(game.player, 'weapon') and game.player.weapon:
            destroy(game.player.weapon)
        if hasattr(game.player, 'health_bar') and game.player.health_bar:
            if hasattr(game.player.health_bar, 'text') and game.player.health_bar.text:
                destroy(game.player.health_bar.text)
            destroy(game.player.health_bar)
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
            destroy(e)
    logger.log('INFO', 'main_menu: old scene swept')

    sky = Sky(texture='sky_textures/sky_0.png')
    sky.name = 'main_sky'

    camera.name = 'main_camera'
    camera.parent = scene

    ground = Entity(
        model='cube',
        collider='box',
        y=0,
        scale=(100, 1, 100),
        texture='assets/textures/floor_ground_grass.png',
        texture_scale=(50, 50),
        name='ground'
    )

    load_level()

    camera_pivot = Entity(name='camera_pivot')
    camera.parent = camera_pivot
    camera_pivot.position = (0, 10, -30)
    camera_pivot.rotation_x = 15

    def rotate():
        camera_pivot.rotation_y += 10 * time.dt
    camera_pivot.update = rotate

    play_button = Button(text='Play', color=color.black, scale=(0.2, 0.1), y=0.1)
    quit_button = Button(text='Quit', color=color.black, scale=(0.2, 0.1), y=-0.1)

    def start_game():
        if game.player:
            if hasattr(game.player, 'weapon'):
                destroy(game.player.weapon)
            if hasattr(game.player, 'health_bar'):
                destroy(game.player.health_bar.text)
                destroy(game.player.health_bar)
            collision_manager.remove(game.player)
            destroy(game.player)
        game.player = Player(position=(0, 2, 0))
        game.enemies = []
        for placeholder in [e for e in scene.entities if e.name == 'level_enemy']:
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
        for placeholder in [e for e in scene.entities if e.name == 'level_trigger']:
            TriggerZone(
                position=placeholder.position,
                scale=placeholder.scale,
                on_enter=build_actions(getattr(placeholder, 'on_enter_actions', [])),
                on_exit=build_actions(getattr(placeholder, 'on_exit_actions', [])),
            )
            destroy(placeholder)
        destroy(play_button)
        destroy(quit_button)
        destroy(camera_pivot)

        game.hud = PlayerHUD(game.player)
        game.hud.show()
        mouse.visible = False
        mouse.locked = True
        game.start()

    play_button.on_click = start_game
    quit_button.on_click = application.quit

class PauseMenu(Entity):
    def __init__(self):
        super().__init__(parent=camera.ui)

        # scale=(2,2) fills the full UI space (-0.5→+0.5 on both axes); z=1 stays behind buttons
        self.background = Entity(
            parent=self,
            model='quad',
            color=color.black66,
            scale=(2, 2),
            z=1
        )

        self.continue_button = Button(
            text='Continue',
            color=color.black,
            scale=(0.3, 0.1),
            y=0.15,
            parent=self
        )
        self.main_menu_button = Button(
            text='Main Menu',
            color=color.black,
            scale=(0.3, 0.1),
            y=0.0,
            parent=self
        )
        self.quit_button = Button(
            text='Quit',
            color=color.black,
            scale=(0.3, 0.1),
            y=-0.15,
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
    # Important: ursina.shader.imported_shaders holds different object instances than direct imports.
    # We must patch every live instance reachable by entity.py: both direct-import refs AND
    # imported_shaders dict entries. We also delete ._shader on any already-compiled instance so
    # entity.py's "if not value.compiled" guard triggers a fresh recompile.
    def _patch_shader_obj(obj, vertex_src, fragment_src):
        obj.vertex = vertex_src
        obj.fragment = fragment_src
        obj.compiled = False
        if hasattr(obj, '_shader'):
            del obj._shader

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

    def _patch_shaders_to_glsl120():
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
    print("[main] shader patch 1/2 — pre-Ursina")
    _patch_shaders_to_glsl120()
    print("[main] patch 1/2 complete, calling Ursina()...")
    app = Ursina(title="Ivan's 3D Engine")
    window.color = color.rgb(50, 50, 60)
    # BUG 1 FIX: render/render2d NodePaths are not fully initialized during the
    # window-setup resize sequence; defer setAntialias to the first frame.
    camera.clip_plane_near = 0.01
    window.title = "Ivan's 3D Engine"
    window.exit_button.visible = False
    window.fps_counter.enabled = True
    window.fps_limit = 60
    mouse.visible = True

    display = pyglet.display.get_display()
    screen = display.get_default_screen()
    screen_width, screen_height = screen.width, screen.height

    window.borderless = False
    window.resizable = True
    window.fullscreen = False
    window.size = (1280, 720)
    window.multisamples = 16

    window.position = (
        (screen_width - window.size[0]) // 2,
        (screen_height - window.size[1]) // 2
    )

    # BUG 2 FIX: Ursina() and subsequent window resizes can re-initialize shader
    # objects on internal entities. Re-patch after window setup so all shaders
    # compiled during main_menu() entity creation use GLSL 1.20.
    print("[main] shader patch 2/2 — post-window-setup")
    _patch_shaders_to_glsl120()

    def _deferred_antialias(task):
        # BUG 1 FIX cont.: run after first frame so render/render2d NodePaths exist.
        render.setAntialias(AntialiasAttrib.MAuto)
        render2d.setAntialias(AntialiasAttrib.MAuto)
        return task.done

    taskMgr.doMethodLater(0, _deferred_antialias, '_deferred_antialias')

    main_menu()

    def on_window_resize():
        if not window.fullscreen:
            window.position = (
                (screen_width - window.size[0]) // 2,
                (screen_height - window.size[1]) // 2
            )

    window.on_resize = on_window_resize

    def update():
        collision_manager.update()
        # WIN condition: PLAYING with no live enemies left on the layer.
        # count_layer reads from collision_manager._tracked (which AliveEntity.die() prunes),
        # not game.enemies (which still holds dead refs). State guard makes this idempotent.
        if game.state == Game.PLAYING and collision_manager.count_layer(Layers.ENEMY) == 0:
            game.trigger_win()

    def input(key):
        if key == 'f':
            window.fullscreen = not window.fullscreen
            if window.fullscreen:
                window.borderless = True
                window.size = (screen_width, screen_height)
                mouse.locked = True
            else:
                window.size = (1280, 720)
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


    app.run()
