from ursina import *
from Scripts.collision_system import (
    AliveEntity, Layers, can_hit, collision_manager
)
import time as _time


POOL_SIZE_PLAYER = 30
POOL_SIZE_ENEMY  = 60


# ---------------------------------------------------------------------------
# Bullet pool
# ---------------------------------------------------------------------------

class BulletPool:
    def __init__(self, bullet_cls, size: int = 30):
        self._cls   = bullet_cls
        self._free  = []
        self._size  = size
        self._built = 0

    # Inactive bullets sit here — far enough that no raycast can reach them
    _PARK = Vec3(0, -10000, 0)

    def acquire(self, **kwargs):
        # Drain any stale (externally-destroyed) bullets before using one.
        # main_menu()'s nuclear entity sweep can destroy parked bullets while
        # they sit in _free; is_empty() detects the dead NodePath.
        while self._free and self._free[-1].is_empty():
            self._free.pop()
            self._built -= 1   # treat as never built — don't count against pool size

        if self._free:
            b = self._free.pop()
            b._alive = True
            b._reset(**kwargs)
        else:
            if self._built >= self._size:
                return None   # pool exhausted — caller skips the shot
            b = self._cls(**kwargs)
            self._built += 1
        collision_manager.add(b, b._layer)
        return b

    def release(self, bullet):
        collision_manager.remove(bullet)
        bullet.position = BulletPool._PARK   # avoids enabled-setter → unstash() assertion
        bullet._alive   = False
        self._free.append(bullet)

    def active_count(self) -> int:
        """Return number of bullets currently in flight (useful for perf debugging)."""
        return max(self._built - len(self._free), 0)

    def reset(self):
        """Discard all pooled bullets and reset counters.

        Call this during scene teardown (before main_menu() sweeps scene.entities).
        The pool rebuilds from scratch on the next acquire() call.  Without this,
        main_menu()'s entity sweep destroys parked bullets while _free still holds
        the dead references — the next acquire() would pop a destroyed NodePath and
        crash on _reset().
        """
        self._free.clear()
        self._built = 0


# ---------------------------------------------------------------------------
# PlayerBullet
# ---------------------------------------------------------------------------

class PlayerBullet(AliveEntity):
    _layer = Layers.PLAYER_BULLET
    MAX_LIFETIME = 2.0

    def __init__(self, position=None, direction=None,
                 speed=50, damage=25, player=None):
        """Allocate one pooled player bullet; _reset() sets live state."""
        super().__init__(
            model='cube',
            color=color.cyan,
            scale=(0.1, 0.1, 0.3),
            position=position,
            collider='box'
        )
        self._reset(position=position, direction=direction,
                    speed=speed, damage=damage, player=player)

    def _reset(self, position, direction, speed=50, damage=25, player=None):
        self.position   = position
        self.direction  = direction.normalized()
        self.speed      = speed
        self.damage     = damage
        self.player     = player
        self.spawn_time = _time.time()

    def update(self):
        """Advance bullet, raycast for hits, apply damage via can_hit (single authority)."""
        if not self._alive:
            return
        prev = Vec3(self.position)
        dist = (self.direction * self.speed * time.dt).length() + 0.2
        ignore = [self, self.player] if self.player else [self]
        hit = raycast(prev, self.direction, distance=dist, ignore=ignore, debug=False)
        if hit.hit:
            if can_hit(self, hit.entity):
                hit.entity.health -= self.damage
            self.die()
            return
        self.position += self.direction * self.speed * time.dt
        self.look_at(self.position + self.direction)
        if _time.time() - self.spawn_time > self.MAX_LIFETIME:
            self.die()

    def die(self):
        if not self._alive:
            return
        self._alive = False
        _player_bullet_pool.release(self)


# ---------------------------------------------------------------------------
# EnemyBullet  (lives here to break the weapon.py ↔ enemy.py circular import)
# ---------------------------------------------------------------------------

class EnemyBullet(AliveEntity):
    _layer = Layers.ENEMY_BULLET
    MAX_LIFETIME = 2.0

    def __init__(self, position=None, target=None,
                 player=None, enemy=None, speed=10):
        """Allocate one pooled enemy bullet; _reset() sets live state."""
        super().__init__(
            model='cube',
            color=color.yellow,
            scale=(0.2, 0.2, 0.5),
            position=position
        )
        self._reset(position=position, target=target,
                    player=player, enemy=enemy, speed=speed)

    def _reset(self, position, target, player=None, enemy=None, speed=10):
        self.position  = position
        self.direction = (target - position).normalized()
        self.player    = player
        self.enemy     = enemy
        self.speed     = speed
        self.spawn_time = _time.time()
        self.look_at(self.position + self.direction)

    def update(self):
        """Advance bullet, raycast for hits, apply damage via can_hit (single authority)."""
        if not self._alive:
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
            if can_hit(self, hit.entity) and self.player:
                self.player.health -= 25
            self.die()
            return
        self.position += self.direction * self.speed * time.dt

    def die(self):
        if not self._alive:
            return
        self._alive = False
        _enemy_bullet_pool.release(self)


# ---------------------------------------------------------------------------
# Pools — module-level singletons
# ---------------------------------------------------------------------------

_player_bullet_pool = BulletPool(PlayerBullet, size=POOL_SIZE_PLAYER)
_enemy_bullet_pool  = BulletPool(EnemyBullet,  size=POOL_SIZE_ENEMY)


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
        self.player       = player
        self.original_pos = Vec3(position)
        self.cooldown     = 0.15
        self.last_shot    = 0
        self.damage       = 25

    def shoot(self):
        if _time.time() - self.last_shot < self.cooldown:
            return

        bullet = _player_bullet_pool.acquire(
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


def get_enemy_bullet_pool() -> BulletPool:
    """Accessor for enemy.py lazy import — avoids circular import at module level."""
    return _enemy_bullet_pool


def reset_bullet_pools():
    """Discard all pooled bullets. Call during scene teardown before main_menu() sweeps entities."""
    _player_bullet_pool.reset()
    _enemy_bullet_pool.reset()
