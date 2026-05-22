from ursina import *
from Scripts.health_bar import HealthBar
from Scripts.collision_system import AliveEntity, Layers, collision_manager

# TUNE: balance / experiment variables — label kept so grep finds them fast during playtesting
ENEMY_HP_DEFAULT         = 100    # TUNE: try 50 for a 2-shot kill at 25 dmg/bullet
ENEMY_SHOOT_COOLDOWN     = 1.0    # TUNE: try 1.5 for more breathing room
ENEMY_DETECTION_RANGE    = 100    # TUNE: try 40 to remove whole-level aggro
ENEMY_ATTACK_RANGE       = 30     # TUNE: try 20 to tighten melee zone
ENEMY_OCCLUSION_INTERVAL = 0.15   # TUNE: raycast throttle — do not go below 0.05

VALID_ENEMY_TYPES = ('default',)  # extend here as new types are added


class Enemy(AliveEntity):
    def __init__(self, spawn_position, player, hp=ENEMY_HP_DEFAULT, enemy_type='default', rotation_y=0):
        """Spawn an enemy at spawn_position; origin_y=-0.5 passed to super so grid cell is correct from frame 0."""
        if enemy_type not in VALID_ENEMY_TYPES:
            raise ValueError(f"Unknown enemy_type {enemy_type!r}; valid: {VALID_ENEMY_TYPES}")

        super().__init__(
            model='cube',
            color=color.red,
            scale=(1.5, 3, 1.5),
            position=spawn_position,
            rotation_y=rotation_y,
            collider='box',
            origin_y=-0.5,        # set here so collision_manager.add registers the correct grid cell
        )
        collision_manager.add(self, Layers.ENEMY)

        self.health      = hp
        self.max_health  = hp
        self.enemy_type  = enemy_type
        self.player      = player
        self.shoot_cooldown   = ENEMY_SHOOT_COOLDOWN
        self.can_shoot   = True
        self.attack_range     = ENEMY_ATTACK_RANGE
        self.detection_range  = ENEMY_DETECTION_RANGE

        self._occluded        = False   # cached result — updated on throttle interval
        self._occlusion_timer = 0.0     # counts down; raycast fires when <= 0

        self.health_bar = HealthBar(
            parent=self,
            max_value=self.max_health,
            value=self.health,
            position=(10, 10, 0),
            scale=(0.5, 0.1),
            is_3d=True,
            bar_origin=(0, 0),
        )

    def update(self):
        """Per-frame: rotate toward player, throttled occlusion check, shoot when in range."""
        if not self.alive or not self.player:
            return

        player_dist = distance(self.position, self.player.position)

        if player_dist <= self.detection_range:
            self.look_at(self.player.position)
            self.rotation_x = 0
            self.rotation_z = 0

            # Throttle occlusion raycast — expensive, no need every frame
            self._occlusion_timer -= time.dt
            if self._occlusion_timer <= 0:
                self._occluded        = self._is_occluded()
                self._occlusion_timer = ENEMY_OCCLUSION_INTERVAL

            if player_dist <= self.attack_range and self.can_shoot:
                self.shoot()

        self.health_bar.world_position = self.world_position + Vec3(0, 3.75, 0)
        self.health_bar.value          = self.health
        self.health_bar.enabled        = player_dist < 200 and not self._occluded

        if self.health <= 0:
            self.die()

    def _is_occluded(self) -> bool:
        """Raycast from self toward player; True if a non-player entity blocks the line of sight."""
        hit = raycast(
            self.position + Vec3(0, 1, 0),
            (self.player.position - self.position).normalized(),
            distance=distance(self.position, self.player.position),
            ignore=[self],
            debug=False,
        )
        return hit.hit and getattr(hit.entity, '_collision_layer', 0) != Layers.PLAYER

    def shoot(self):
        """Fire one enemy bullet toward the player via the pool; resets cooldown via invoke."""
        if not self.can_shoot:
            return
        from Scripts.weapon import get_enemy_bullet_pool  # lazy — breaks weapon↔enemy circular import
        pool = get_enemy_bullet_pool()
        pool.acquire(
            position=self.position + Vec3(0, 1.5, 0),
            target=self.player.position + Vec3(0, 1, 0),
            player=self.player,
            enemy=self,
            speed=10,
        )
        self.can_shoot = False
        invoke(setattr, self, 'can_shoot', True, delay=self.shoot_cooldown)

    def on_die(self):
        """Destroy health bar before super().on_die() destroys self."""
        if hasattr(self, 'health_bar') and self.health_bar:
            destroy(self.health_bar)
