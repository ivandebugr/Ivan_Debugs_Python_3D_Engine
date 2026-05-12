from ursina import *
from Scripts.player_controller import Player
from Scripts.enemy import Enemy, EnemyBullet
from Scripts.weapon import Weapon
from Scripts.health_bar import HealthBar
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

    sky = Sky(texture='sky_default')
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
    app = Ursina(title="Ivan's 3D Engine")
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
        # FIXED: bug-4 - removed duplicate bullet-vs-enemy AABB loop; PlayerBullet.update() raycasts are the single authority
        # for bullet in [e for e in scene.entities if isinstance(e, PlayerBullet) and e.enabled]:
        #     for enemy in [e for e in scene.entities if isinstance(e, Enemy) and e.enabled]:
        #         if bullet.intersects(enemy):
        #             enemy.health -= 25
        #             if enemy.health <= 0:
        #                 destroy(enemy)
        #             destroy(bullet)

        for bullet in [e for e in scene.entities if isinstance(e, EnemyBullet) and e.enabled]:
            if bullet and player and player.enabled:
                if bullet.intersects(player):
                    player.health -= 20
                    destroy(bullet)
                    if player.health <= 0:
                        player.position = (0, 2, 0)
                        player.health = 100
                
        for e in scene.entities:
            if isinstance(e, HealthBar) and e.is_3d:
                e.world_scale = (1,1,1)
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
                else:
                    if pause_menu:
                        destroy(pause_menu)
                        pause_menu = None
                    mouse.visible = False
                    mouse.locked = True
                    application.time_scale = 1

        if key == 'left mouse' and not window.fullscreen and not game_paused:
            mouse.locked = True
            mouse.visible = False


    app.run()