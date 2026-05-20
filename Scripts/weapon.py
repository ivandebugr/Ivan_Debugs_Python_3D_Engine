from ursina import *
from Scripts.collision_system import (  # IMPROVED: step-1
    AliveEntity, Layers, can_hit, collision_manager
)
import time as _time


# ---------------------------------------------------------------------------
# Bullet pool  (step-3)
# ---------------------------------------------------------------------------

class BulletPool:  # IMPROVED: step-3 — eliminates per-shot Entity allocation
    def __init__(self, bullet_cls, size: int = 30):
        self._cls   = bullet_cls
        self._free  = []
        self._size  = size
        self._built = 0

    # Inactive bullets sit here — far enough that no raycast can reach them
    _PARK = Vec3(0, -10000, 0)

    def acquire(self, **kwargs):
        if self._free:
            b = self._free.pop()
            b._alive = True
            b._reset(**kwargs)   # _reset overwrites position with the real spawn point
        else:
            if self._built >= self._size:
                return None   # pool exhausted — caller skips the shot
            b = self._cls(_pooled=True, **kwargs)
            self._built += 1
        collision_manager.add(b, b._layer)  # IMPROVED: step-1
        return b

    def release(self, bullet):  # IMPROVED: step-3 — teleport instead of stash/unstash
        collision_manager.remove(bullet)
        bullet.position = BulletPool._PARK   # avoids enabled-setter → unstash() assertion
        bullet._alive   = False
        self._free.append(bullet)


# ---------------------------------------------------------------------------
# PlayerBullet  (steps 2, 3)
# ---------------------------------------------------------------------------

class PlayerBullet(AliveEntity):  # IMPROVED: step-2 — AliveEntity for frame-safe destroy
    _layer = Layers.PLAYER_BULLET  # IMPROVED: step-1
    MAX_LIFETIME = 2.0

    def __init__(self, _pooled=False, position=None, direction=None,
                 speed=50, damage=25, player=None):
        super().__init__(
            model='cube',
            color=color.cyan,
            scale=(0.1, 0.1, 0.3),
            position=position,
            collider='box'
        )
        self._reset(position=position, direction=direction,
                    speed=speed, damage=damage, player=player)

    def _reset(self, position, direction, speed=50, damage=25, player=None):  # IMPROVED: step-3
        self.position   = position
        self.direction  = direction.normalized()
        self.speed      = speed
        self.damage     = damage
        self.player     = player
        self.spawn_time = _time.time()

    def update(self):
        if not self._alive:  # IMPROVED: step-2 — guard post-destroy frames  # VERIFIED: step-2
            return
        prev = Vec3(self.position)
        dist = (self.direction * self.speed * time.dt).length() + 0.2
        ignore = [self, self.player] if self.player else [self]
        hit = raycast(prev, self.direction, distance=dist, ignore=ignore, debug=False)
        if hit.hit:
            if can_hit(self, hit.entity):  # IMPROVED: step-1 — replaces isinstance(hit.entity, Enemy)  # VERIFIED: step-1
                hit.entity.health -= self.damage
            self.die()
            return
        self.position += self.direction * self.speed * time.dt
        self.look_at(self.position + self.direction)
        if _time.time() - self.spawn_time > self.MAX_LIFETIME:
            self.die()

    def die(self):  # IMPROVED: step-3 — pool bullets must NOT be destroyed, only disabled
        if not self._alive:
            return
        self._alive = False
        _player_bullet_pool.release(self)   # release() calls collision_manager.remove(); no destroy()


# ---------------------------------------------------------------------------
# EnemyBullet  (moved here from enemy.py to break the circular import)  step-2
# ---------------------------------------------------------------------------

class EnemyBullet(AliveEntity):  # IMPROVED: step-2
    _layer = Layers.ENEMY_BULLET  # IMPROVED: step-1
    MAX_LIFETIME = 2.0

    def __init__(self, _pooled=False, position=None, target=None,
                 player=None, enemy=None, speed=10):
        super().__init__(
            model='cube',
            color=color.yellow,
            scale=(0.2, 0.2, 0.5),
            position=position
        )
        self._reset(position=position, target=target,
                    player=player, enemy=enemy, speed=speed)

    def _reset(self, position, target, player=None, enemy=None, speed=10):  # IMPROVED: step-3
        self.position  = position
        self.direction = (target - position).normalized()
        self.player    = player
        self.enemy     = enemy
        self.speed     = speed
        self.spawn_time = _time.time()
        self.look_at(self.position + self.direction)

    def update(self):
        if not self._alive:  # IMPROVED: step-2 — guard  # VERIFIED: step-2
            return
        if _time.time() - self.spawn_time > self.MAX_LIFETIME:
            self.die()
            return
        hit = raycast(
            self.world_position,
            self.direction,
            distance=1,
            ignore=[self, self.enemy] if self.enemy else [self],
            debug=False
        )
        if hit.hit:
            if can_hit(self, hit.entity):  # IMPROVED: step-1  # VERIFIED: step-1
                self.player.health -= 25   # VERIFIED: single authority for EnemyBullet damage
            self.die()
            return
        self.position += self.direction * self.speed * time.dt

    def die(self):  # IMPROVED: step-3 — pool bullets must NOT be destroyed, only disabled
        if not self._alive:
            return
        self._alive = False
        _enemy_bullet_pool.release(self)   # release() calls collision_manager.remove(); no destroy()


# ---------------------------------------------------------------------------
# Pools — module-level singletons  (step-3)
# ---------------------------------------------------------------------------

_player_bullet_pool = BulletPool(PlayerBullet, size=30)  # IMPROVED: step-3  # VERIFIED: step-3
_enemy_bullet_pool  = BulletPool(EnemyBullet,  size=60)  # IMPROVED: step-3  # VERIFIED: step-3


# ---------------------------------------------------------------------------
# Weapon
# ---------------------------------------------------------------------------

class Weapon(Entity):
    def __init__(self, player, model='cube', texture='white_cube',
                 scale=(0.3, 0.2, 1), position=(0.5, -0.5, 1), **kwargs):
        super().__init__(
            parent=camera,
            model=model,
            texture=texture,
            scale=scale,
            position=position,
            rotation=(0, 0, 0),
            **kwargs
        )
        self.player      = player
        self.original_pos = Vec3(position)
        self.cooldown    = 0.15
        self.last_shot   = 0
        self.damage      = 25

        self.crosshair = Entity(
            parent=camera.ui,
            model='quad',
            texture='circle',
            color=color.red,
            scale=(0.01, 0.01),  # FIX-3: smaller crosshair (halved from 0.02)
            z=-1,
            visible=False  # FIX-3: hidden until gameplay starts; shown in start_game()
        )

    def shoot(self):
        if _time.time() - self.last_shot < self.cooldown:
            return

        bullet = _player_bullet_pool.acquire(  # IMPROVED: step-3 — pool replaces PlayerBullet(...)
            position=self.world_position + camera.forward,
            direction=camera.forward,
            speed=50,
            damage=self.damage,
            player=self.player
        )
        if bullet is None:
            return   # pool exhausted

        self.animate_position(self.original_pos + Vec3(0, 0, -0.2), duration=0.05)
        self.animate_position(self.original_pos, delay=0.05, duration=0.15, curve=curve.out_quad)
        self.last_shot = _time.time()


def get_enemy_bullet_pool() -> BulletPool:  # IMPROVED: step-1 — avoids enemy.py importing weapon at module level
    return _enemy_bullet_pool
