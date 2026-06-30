from ursina import *
from ursina.prefabs.first_person_controller import FirstPersonController
from Scripts.weapon import Weapon
from Scripts.health_bar import HealthBar
from Scripts.collision_system import Layers, collision_manager, swept_move_blocked
from Scripts.game import game, Game

# 5 swept-ray heights: feet → knee → waist → shoulder → forehead (entity.y-0.4 to entity.y+1.9)
# True only for debugging swept collision — creates 200 eternal entities, never enable in production
SWEPT_OFFSETS = (Vec3(0, -0.4, 0), Vec3(0, 0.3, 0), Vec3(0, 0.9, 0),
                 Vec3(0, 1.5, 0), Vec3(0, 1.9, 0))


class Player(FirstPersonController):
    def __init__(self, position=(0, 0, 0), **kwargs):
        """Create the player: FPS controller with swept collision, health, and weapon."""
        super().__init__(
            model=None,
            color=color.azure,
            collider='box',
            position=position,
            **kwargs
        )
        self.jump_force = 18
        self.gravity = 45
        self.speed = 8
        self.vertical_speed = 0
        self.mouse_sensitivity = Vec2(4000, 4000)

        # center=(0,0.75,0) keeps feet at entity.y-0.5 and raises top to entity.y+2.0
        self.collider = BoxCollider(self, center=(0, 0.75, 0), size=(0.8, 2.5, 0.8))
        self.skin_width = 0.1
        collision_manager.add(self, Layers.PLAYER)
        self.weapon = Weapon(self)

        self.debug_lines = []
        self.create_collider_visualization()

        self.draw_raycasts = False
        self.show_colliders = False
        self.debug_rays = []

        mouse.locked = True

        self.max_health = 100
        self.health = 100

        self.health_bar = HealthBar(
            parent=camera.ui,
            max_value=self.max_health,
            value=self.health,
            position=(-0.85, -0.39),
            scale=(0.4, 0.05),
            text_position=(0, 1.5),
            text_scale=1.5,
            is_3d=False,
            bar_origin=(0, 0)
        )

    def create_collider_visualization(self):
        corners = [
            Vec3(-0.4, -0.5, -0.4), Vec3(0.4, -0.5, -0.4),
            Vec3(0.4, -0.5,  0.4), Vec3(-0.4, -0.5,  0.4),
            Vec3(-0.4,  2.0, -0.4), Vec3(0.4,  2.0, -0.4),
            Vec3(0.4,  2.0,  0.4), Vec3(-0.4,  2.0,  0.4),
        ]
        edges = [
            (0, 1), (1, 2), (2, 3), (3, 0),
            (4, 5), (5, 6), (6, 7), (7, 4),
            (0, 4), (1, 5), (2, 6), (3, 7)
        ]
        for edge in edges:
            line = Entity(
                model=Mesh(vertices=[corners[edge[0]], corners[edge[1]]], mode='line'),
                color=color.red,
                parent=self,
                eternal=True,
                enabled=False
            )
            self.debug_lines.append(line)

    def update(self):
        """Per-frame: mouse look, gravity, ceiling, horizontal movement, health sync."""
        if mouse.locked:
            self.rotation_y += mouse.velocity[0] * self.mouse_sensitivity[0] * time.dt
            camera.rotation_x -= mouse.velocity[1] * self.mouse_sensitivity[1] * time.dt
            camera.rotation_x = clamp(camera.rotation_x, -90, 90)

        ground_hit = raycast(
            self.position + (0, -0.1, 0),
            (0, -1),
            distance=1,
            ignore=[self]
        )
        self.grounded = ground_hit.hit

        if not self.grounded:
            self.vertical_speed -= self.gravity * time.dt
        else:
            self.vertical_speed = 0
            if ground_hit.hit:
                ground_top = ground_hit.world_point.y
                player_bottom = self.y - 0.5
                if player_bottom < ground_top:
                    self.y += ground_top - player_bottom

        self.y += self.vertical_speed * time.dt

        ceiling_hit = raycast(
            self.position + Vec3(0, 2.0, 0),
            Vec3(0, 1, 0),
            distance=0.3,
            ignore=[self],
            debug=False
        )
        if ceiling_hit.hit and self.vertical_speed > 0:
            penetration = 0.3 - ceiling_hit.distance
            self.vertical_speed = 0
            self.y -= penetration

        self.handle_horizontal_movement()

        self.health_bar.value = self.health
        if self.health <= 0:
            game.trigger_game_over()

    def input(self, key):
        # Only act on gameplay input while actually PLAYING. During WIN/GAME_OVER the
        # global input() handler presses R to return to the menu, which destroys this
        # Player synchronously — but Ursina then continues the SAME input dispatch into
        # the per-entity loop and calls this Player.input('r'). Without the guard,
        # self.position = (...) runs on the just-destroyed NodePath and raises
        # 'entity has been destroyed by: _clear_gameplay_entities'. Gating on PLAYING
        # makes the R-to-menu and R-to-reset handlers mutually exclusive.
        if game.state != Game.PLAYING:
            return
        if key == 'left mouse down':
            self.weapon.shoot()
        if key == 'space' and self.grounded:
            self.vertical_speed = self.jump_force
        if key == 'r':
            self.position = (0, 2, 0)
        if key == 'c':
            self.show_colliders = not self.show_colliders
            self.toggle_collider_visualization()
            self.toggle_raycast_visualization()
        super().input(key)

    def toggle_collider_visualization(self):
        for line in self.debug_lines:
            line.enabled = self.show_colliders

    def toggle_raycast_visualization(self):
        for ray in self.debug_rays:
            ray.enabled = self.show_colliders

    def _swept_blocked(self, origin, direction, distance):
        """Cast 5 rays at SWEPT_OFFSETS heights; returns True if any ray hits a wall.

        Delegates to collision_system.swept_move_blocked (collision authority #2,
        shared with enemy chase movement) — the multi-height raycast loop lives
        there now so player and enemies test walls identically.
        """
        return swept_move_blocked(self, origin, direction, distance,
                                  SWEPT_OFFSETS, self.skin_width)

    def handle_horizontal_movement(self):
        raw = Vec3(self.forward * (held_keys['w'] - held_keys['s']) +
                   self.right * (held_keys['d'] - held_keys['a']))
        if not raw.length_squared():
            return
        d = raw.normalized()
        move = d * self.speed * time.dt
        if not self._swept_blocked(self.position, d, move.length()):
            self.position += move
            return
        x = Vec3(move.x, 0, 0)
        z = Vec3(0, 0, move.z)
        if x.length() and not self._swept_blocked(self.position, x.normalized(), abs(move.x)):
            self.position += x
        elif z.length() and not self._swept_blocked(self.position, z.normalized(), abs(move.z)):
            self.position += z

    def on_destroy(self):
        """Unregister from collision_manager when Ursina destroys this entity."""
        collision_manager.remove(self)
