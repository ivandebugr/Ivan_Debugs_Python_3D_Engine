from ursina import *
from ursina.prefabs.first_person_controller import FirstPersonController
from Scripts.weapon import Pistol
from Scripts.weapon_inventory import WeaponInventory
from Scripts.health_bar import HealthBar
from Scripts.collision_system import Layers, collision_manager, swept_move_blocked
from Scripts.game import game, Game
from Scripts.ground_shadow import GroundShadow
from Scripts.game_settings import game_settings

# 5 swept-ray heights: feet → knee → waist → shoulder → forehead (entity.y-0.4 to entity.y+1.9)
# True only for debugging swept collision — creates 200 eternal entities, never enable in production
SWEPT_OFFSETS = (Vec3(0, -0.4, 0), Vec3(0, 0.3, 0), Vec3(0, 0.9, 0),
                 Vec3(0, 1.5, 0), Vec3(0, 1.9, 0))

# Headbob tuning: sinusoidal camera offset while grounded + moving, scaled to
# actual movement speed (not just "is a key held") so bumping into a wall stops
# the bob instead of playing it at full speed with zero displacement.
BOB_FREQUENCY   = 9.0
BOB_HEIGHT      = 0.035
BOB_SIDE        = 0.015   # horizontal component is half-frequency (figure-8 gait)
BOB_EASE_SPEED  = 8       # how fast the offset eases toward target/zero each frame


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

        self._bob_time      = 0
        self._bob_offset    = Vec2(0, 0)   # current eased (y, x) camera bob offset
        self._move_fraction = 0            # 0..1 of self.speed, set by handle_horizontal_movement

        # center=(0,0.75,0) keeps feet at entity.y-0.5 and raises top to entity.y+2.0
        self.collider = BoxCollider(self, center=(0, 0.75, 0), size=(0.8, 2.5, 0.8))
        self.skin_width = 0.1
        collision_manager.add(self, Layers.PLAYER)
        self.inventory = WeaponInventory(self)
        # Slot 0 starts with the infinite-ammo Pistol (the pre-inventory default
        # weapon); Shotgun/Rifle are granted by level-placed weapon pickups.
        self.inventory.give(Pistol(self), slot=0)
        self.inventory.switch_to(0)

        self.debug_lines = []
        self.create_collider_visualization()

        self.draw_raycasts = False
        self.show_colliders = False
        self.debug_rays = []

        mouse.locked = True

        self.shadow = GroundShadow(self, scale=(1.0, 1.0))

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
                enabled=False
            )
            self.debug_lines.append(line)

    def update(self):
        """Per-frame: mouse look, gravity, ceiling, horizontal movement, health sync."""
        if mouse.locked:
            self.rotation_y += mouse.velocity[0] * self.mouse_sensitivity[0] * time.dt
            camera.rotation_x -= mouse.velocity[1] * self.mouse_sensitivity[1] * time.dt
            camera.rotation_x = clamp(camera.rotation_x, -90, 90)

        # Triggers and pickups are non-physical (v1.5): a kill-plane/fall-catcher
        # volume below the world must let the player fall THROUGH it so intersects()
        # fires and kills mid-fall, and an AmmoPickup's BoxCollider must never act as
        # a floor/ceiling either — never a floor the player lands on. The horizontal
        # swept test already skips Layers.TRIGGER and Layers.PICKUP (collision
        # authority #2); apply the same rule to the vertical ground/ceiling rays
        # (authority #3) so neither blocks on any axis. Pass instances, never the
        # class (Hard Constraint 2).
        vertical_ignore = ([self]
                           + collision_manager.query_layer(Layers.TRIGGER)
                           + collision_manager.query_layer(Layers.PICKUP))

        ground_hit = raycast(
            self.position + (0, -0.1, 0),
            (0, -1),
            distance=1,
            ignore=vertical_ignore
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
            ignore=vertical_ignore,
            debug=False
        )
        if ceiling_hit.hit and self.vertical_speed > 0:
            penetration = 0.3 - ceiling_hit.distance
            self.vertical_speed = 0
            self.y -= penetration

        self.handle_horizontal_movement()
        self.update_camera_bob()

        self.shadow.update()

        self.health_bar.value = self.health
        # Single death authority: kill planes / bullets only modify health;
        # this check alone decides game over. A post-checkpoint kill-plane
        # respawn (trigger_system._build_kill_plane) charges a health cost
        # instead of zeroing — if that cost lands on 0, the player dies here
        # at the checkpoint (one fall too many, by design).
        if self.health <= 0:
            game.trigger_game_over()

    def input(self, key):
        # Only act on gameplay input while actually PLAYING. During WIN/GAME_OVER the
        # global input() handler presses R to return to the menu, which destroys this
        # Player synchronously — but Ursina then continues the SAME input dispatch into
        # the per-entity loop and calls this Player.input('r'). Without the guard,
        # self.inventory.active_weapon.reload() runs on the just-destroyed Player and
        # raises 'entity has been destroyed by: _clear_gameplay_entities'. Gating on
        # PLAYING makes the R-to-menu and R-to-reload handlers mutually exclusive.
        if game.state != Game.PLAYING:
            return
        if key == 'left mouse down':
            # Intentionally not gated on self.grounded — jump-shooting is standard
            # FPS movement (Quake/Doom/CS-style), not a bug to fix.
            if self.inventory.active_weapon:
                self.inventory.active_weapon.shoot()
        if key in ('1', '2', '3'):
            self.inventory.switch_to(int(key) - 1)
        if key == 'scroll up':
            self.inventory.next_weapon()
        if key == 'scroll down':
            self.inventory.prev_weapon()
        if key == 'space' and self.grounded:
            self.vertical_speed = self.jump_force
        if key == 'r':
            if self.inventory.active_weapon:
                self.inventory.active_weapon.reload()
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
            self._move_fraction = 0
            return
        d = raw.normalized()
        move = d * self.speed * time.dt
        if not self._swept_blocked(self.position, d, move.length()):
            self.position += move
            self._move_fraction = 1
            return
        x = Vec3(move.x, 0, 0)
        z = Vec3(0, 0, move.z)
        if x.length() and not self._swept_blocked(self.position, x.normalized(), abs(move.x)):
            self.position += x
            self._move_fraction = 1
        elif z.length() and not self._swept_blocked(self.position, z.normalized(), abs(move.z)):
            self.position += z
            self._move_fraction = 1
        else:
            self._move_fraction = 0

    def update_camera_bob(self):
        """Sinusoidal camera offset while grounded + moving, eased toward zero
        (never snapped) so toggling mid-stride or hitting a wall doesn't pop the
        camera — same reset discipline as weapon sway.
        """
        target = Vec2(0, 0)
        if game_settings['camera_bob_enabled'] and self.grounded and self._move_fraction:
            self._bob_time += time.dt * BOB_FREQUENCY
            target = Vec2(
                math.sin(self._bob_time * 0.5) * BOB_SIDE,
                abs(math.sin(self._bob_time)) * BOB_HEIGHT,
            )
        self._bob_offset = lerp(self._bob_offset, target, min(time.dt * BOB_EASE_SPEED, 1))
        camera.x = self._bob_offset.x
        camera.y = self._bob_offset.y

    def on_enable(self):
        # FirstPersonController's built-in pink diamond cursor sits at screen
        # center on top of PlayerHUD's crosshair (main.py) — super().on_enable()
        # re-enables it every time (__post_init__ calls this after __init__, so
        # disabling in __init__ doesn't stick), so disable it again after.
        super().on_enable()
        self.cursor.enabled = False

    def on_destroy(self):
        """Unregister from collision_manager when Ursina destroys this entity."""
        collision_manager.remove(self)
        self.shadow.destroy()
