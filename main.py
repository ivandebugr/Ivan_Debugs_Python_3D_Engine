from ursina import *
from Scripts.player_controller import Player
from Scripts.enemy import Enemy
from Scripts.health_bar import HealthBar
from Scripts.collision_system import collision_manager  # IMPROVED: step-5
import json, pyglet

player = None
game_paused = False
pause_menu = None

def load_level():
    try:
        with open('level.json', 'r') as f:
            entities = json.load(f)
        
        for e in scene.entities[:]:  # copy to avoid mid-loop mutation
            try:
                if e.name in ['level_block', 'level_enemy']:
                    destroy(e)
            except Exception:
                pass

        for entity_data in entities:
            if entity_data.get('type') == 'enemy':
                enemy_placeholder = Entity(
                    position=tuple(entity_data['position']),
                    model='cube',
                    color=color.red,
                    y=+3,
                    scale=(1.5, 3, 1.5),
                    name='level_enemy'
                )
            else:
                block = Entity(
                    model='cube',
                    collider='box',
                    texture=entity_data['texture'],
                    position=tuple(entity_data['position']),
                    scale=(1, 1, 1),
                    name='level_block'
                )
    except FileNotFoundError:
        print("No level file found. Create one using the level editor.")

def main_menu():
    for e in scene.entities[:]:  # copy to avoid mid-loop mutation leaving dead NodePaths
        try:
            if e.name not in ['main_camera']:
                destroy(e)
        except Exception:
            pass

    sky = Sky()  # FIX-1d: 'sky_default' texture absent in Ursina 8.3.0; use built-in gradient
    sky.name = 'main_sky'

    camera.name = 'main_camera'
    camera.parent = scene

    ground = Entity(
        model='cube',
        collider='box',
        y=0,
        scale=(100, 1, 100),
        texture='grass',
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
        global player, enemy
        if player:
            destroy(player)
        player = Player(position=(0, 2, 0))
        for placeholder in [e for e in scene.entities if e.name == 'level_enemy']:
            enemy = Enemy(spawn_position=placeholder.position, player=player)
            destroy(placeholder)
        destroy(play_button)
        destroy(quit_button)
        destroy(camera_pivot)
        
        Text('Move: WASD | Jump: Space | Shoot: LMB | Reset: R | Mouse: Esc | Fullscreen: F', 
            position=window.bottom_left + Vec2(0.01, 0.95),
            origin=(-0.5, -0.5))
        mouse.visible = False
        mouse.locked = True
        if hasattr(player, 'weapon') and player.weapon.crosshair:  # FIX-3
            player.weapon.crosshair.visible = True  # FIX-3: show crosshair when gameplay starts

    play_button.on_click = start_game
    quit_button.on_click = application.quit

class PauseMenu(Entity):
    def __init__(self):
        super().__init__(parent=camera.ui)
        self.background = Entity(
            parent=self,
            model='quad',
            color=color.black66,
            scale=(0.6, 0.8),
            z=1
        )
        self.visible = False
        
        self.continue_button = Button(
            text='Continue',
            color=color.black,
            scale=(0.3, 0.1),
            y=0.2,
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
            y=-0.2,
            parent=self
        )
        
        self.continue_button.on_click = self.resume_game
        self.main_menu_button.on_click = self.return_to_main_menu
        self.quit_button.on_click = application.quit
        
    def resume_game(self):
        global game_paused
        self.visible = False
        game_paused = False
        mouse.visible = False
        mouse.locked = True
        application.time_scale = 1
        destroy(self.background)
        destroy(self.continue_button)
        destroy(self.main_menu_button)
        destroy(self.quit_button)
        destroy(self)
        
        invoke(setattr, application, 'time_scale', 1, delay=0.1)

    def return_to_main_menu(self):
        global game_paused, player
        game_paused = False
        
        if player:
            destroy(player)
            player = None
        
        for e in scene.entities:
            if e.name in ['level_block', 'ground', 'main_sky']:
                destroy(e)
        
        destroy(self.background)
        destroy(self.continue_button)
        destroy(self.main_menu_button)
        destroy(self.quit_button)
        destroy(self)
        
        camera.parent = scene
        camera.position = (0, 0, 0)
        camera.rotation = (0, 0, 0)
        
        application.time_scale = 1
        
        main_menu()
        mouse.visible = True
        mouse.locked = False
        
        
if __name__ == '__main__':
    from panda3d.core import AntialiasAttrib, loadPrcFileData  # FIX-2
    loadPrcFileData('', 'framebuffer-multisample 1\nmultisamples 4')  # FIX-2: request MSAA framebuffer before window opens
    app = Ursina(title="Ivan's 3D Engine")
    # FIX-1b: Ursina 8.3.0 made unlit_with_fog_shader the default for every Entity, and Sky()
    # hardcodes shader=unlit_shader. Both shaders use GLSL #version 130/140, but macOS OpenGL 2.1
    # (the only context available on this machine) only supports GLSL 1.20. Patch both shader objects
    # to GLSL 1.20 syntax *before* any entity is created so the first compile succeeds.
    def _patch_shaders_to_glsl120():  # FIX-1b
        from ursina.shaders.unlit_shader import unlit_shader as _us
        from ursina.shaders.unlit_with_fog_shader import unlit_with_fog_shader as _ufs
        _us.vertex = (
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
        _us.fragment = (
            '#version 120\n'
            'uniform sampler2D p3d_Texture0;\n'
            'uniform vec4 p3d_ColorScale;\n'
            'varying vec2 uvs;\n'
            'varying vec4 vertex_color;\n'
            'void main() {\n'
            '    gl_FragColor = texture2D(p3d_Texture0, uvs) * p3d_ColorScale * vertex_color;\n'
            '}\n'
        )
        _us.compiled = False
        _ufs.vertex = (
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
        _ufs.fragment = (
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
        _ufs.compiled = False
    _patch_shaders_to_glsl120()  # FIX-1b
    window.color = color.rgb(50, 50, 60)  # FIX-1a: Ursina 8.3.0 changed default window background to black
    render.setAntialias(AntialiasAttrib.MAuto)   # FIX-2: enable AA on 3D scene
    render2d.setAntialias(AntialiasAttrib.MAuto)  # FIX-2: enable AA on UI / camera.ui
    camera.clip_plane_near = 0.01  # default ~0.1 matches skin_width — geometry at arm's length disappears when looking down at walls
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

    main_menu()

    def on_window_resize():
        if not window.fullscreen:
            window.position = (
                (screen_width - window.size[0]) // 2,
                (screen_height - window.size[1]) // 2
            )

    window.on_resize = on_window_resize()

    def update():
        collision_manager.update()  # IMPROVED: step-5 — per-frame spatial grid update  # VERIFIED: step-5

        for e in scene.entities:
            if isinstance(e, HealthBar) and e.is_3d:
                e.world_scale = (1, 1, 1)
                e.always_on_top = True

    def input(key):
        global game_paused, pause_menu, player
        
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
            if player and hasattr(player, 'enabled'):
                game_paused = not game_paused
                if game_paused:
                    pause_menu = PauseMenu()
                    pause_menu.visible = True
                    mouse.visible = True
                    mouse.locked = False
                    application.time_scale = 0
                    if hasattr(player, 'weapon') and player.weapon.crosshair:  # FIX-3
                        player.weapon.crosshair.visible = False  # FIX-3: hide crosshair while paused
                else:
                    if pause_menu:
                        destroy(pause_menu)
                        pause_menu = None
                    mouse.visible = False
                    mouse.locked = True
                    application.time_scale = 1
                    if hasattr(player, 'weapon') and player.weapon.crosshair:  # FIX-3
                        player.weapon.crosshair.visible = True  # FIX-3: restore crosshair on resume

        if key == 'left mouse' and not window.fullscreen and not game_paused:
            mouse.locked = True
            mouse.visible = False


    app.run()