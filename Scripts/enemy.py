from ursina import *
from Scripts.health_bar import HealthBar
from Scripts.collision_system import AliveEntity, Layers, collision_manager  # IMPROVED: step-1


class Enemy(AliveEntity):  # IMPROVED: step-2 — AliveEntity for frame-safe destroy
    def __init__(self, spawn_position, player):
        super().__init__(
            model='cube',
            color=color.red,
            scale=(1.5, 3, 1.5),
            position=spawn_position,
            collider='box'
        )
        collision_manager.add(self, Layers.ENEMY)  # IMPROVED: step-1
        self.health     = 100
        self.max_health = 100
        self.player     = player
        self.shoot_cooldown  = 1.0
        self.can_shoot  = True
        self.attack_range    = 30
        self.detection_range = 100

        self.health_bar = HealthBar(
            parent=self,
            max_value=self.max_health,
            value=self.health,
            position=(0, 2, 0),
            scale=(2, 0.3),
            is_3d=True,
            bar_origin=(0, 0)
        )

    def update(self):
        if not self.alive or not self.player:  # IMPROVED: step-2 — guard  # VERIFIED: step-2
            return
        player_dist = distance(self.position, self.player.position)

        if player_dist <= self.detection_range:
            self.look_at(self.player.position)
            self.rotation_x = 0
            self.rotation_z = 0

            if player_dist <= self.attack_range and self.can_shoot:
                self.shoot()

        self.health_bar.world_position = self.world_position + Vec3(0, 2, 0)
        self.health_bar.value = self.health
        self.health_bar.enabled = player_dist < 150 and not self._is_occluded()

        if self.health <= 0:
            self.die()

    def _is_occluded(self) -> bool:
        hit = raycast(
            self.position + Vec3(0, 1, 0),
            (self.player.position - self.position).normalized(),
            distance=distance(self.position, self.player.position),
            ignore=[self],
            debug=False
        )
        return hit.hit and getattr(hit.entity, '_collision_layer', 0) != Layers.PLAYER

    def shoot(self):
        if not self.can_shoot:
            return
        from Scripts.weapon import get_enemy_bullet_pool  # IMPROVED: step-1 — lazy import breaks cycle
        pool = get_enemy_bullet_pool()
        pool.acquire(
            position=self.position + Vec3(0, 1.5, 0),
            target=self.player.position + Vec3(0, 1, 0),
            player=self.player,
            enemy=self,
            speed=10
        )
        self.can_shoot = False
        invoke(setattr, self, 'can_shoot', True, delay=self.shoot_cooldown)

    def on_die(self):  # IMPROVED: step-2 — cleanup before destroy()
        if hasattr(self, 'health_bar'):
            destroy(self.health_bar)
