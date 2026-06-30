# collision_system.py — bitmask layer registry, AliveEntity lifecycle, spatial grid
# Last audited: 2026-05-22 — 0 open items
#
# Three collision authorities (never add a fourth):
#   1. Swept projectile raycast  — PlayerBullet/EnemyBullet.update() in weapon.py
#   2. Swept character movement  — swept_move_blocked() here; called by
#                                  Player._swept_blocked() and Enemy.chase_step()
#   3. Ground/ceiling raycast   — Player.update() in player_controller.py
#
# Authority #2 is the shared multi-height sweep test (swept_move_blocked below).
# It was extracted from Player._swept_blocked so player movement (Step <1.4) and
# enemy chase movement (ChaseNode, v1.4 Step 3) test walls the SAME way instead
# of duplicating the raycast loop. This is not a new authority — it IS authority
# #2, now callable by both characters.
#
# External code uses query_layer() / query_near() — not _tracked or spatial_grid directly.

from ursina import *
from itertools import product

__all__ = [
    'Layers',
    'COLLISION_MATRIX',
    'register',
    'unregister',
    'can_hit',
    'swept_move_blocked',
    'AliveEntity',
    'CollisionManager',
    'collision_manager',
]


class Layers:
    """Bitmask constants for every entity type that participates in collision."""
    PLAYER        = 1 << 0   # 1
    ENEMY         = 1 << 1   # 2
    PLAYER_BULLET = 1 << 2   # 4
    ENEMY_BULLET  = 1 << 3   # 8
    WALL          = 1 << 4   # 16
    PICKUP        = 1 << 5   # 32 — forward declaration; no entity registers this yet
    TRIGGER       = 1 << 6   # 64 — invisible enter/exit volumes (v1.5); NOT a damage
                             #      path. Never appears in COLLISION_MATRIX, and
                             #      swept_move_blocked skips it so triggers don't
                             #      physically block movement. Detection is per-frame
                             #      self.intersects(player), not a raycast authority.


# Declarative hit matrix — no file needs to import enemy/bullet types.
COLLISION_MATRIX = {
    Layers.PLAYER_BULLET: Layers.ENEMY | Layers.WALL,
    Layers.ENEMY_BULLET:  Layers.PLAYER | Layers.WALL,
    Layers.PLAYER:        Layers.WALL | Layers.ENEMY_BULLET | Layers.PICKUP,
    Layers.ENEMY:         Layers.WALL | Layers.PLAYER_BULLET,
}

_registry: dict = {}   # entity id → (layer, mask)


def register(entity, layer: int):
    """Assign `layer` to `entity` and cache its hit-mask from COLLISION_MATRIX."""
    mask = COLLISION_MATRIX.get(layer, 0)
    _registry[id(entity)] = (layer, mask)
    entity._collision_layer = layer
    entity._collision_mask  = mask


def unregister(entity):
    """Remove `entity` from the registry; safe to call if not registered."""
    _registry.pop(id(entity), None)


def can_hit(a, b) -> bool:
    """Return True if `a` is allowed to deal damage to `b`.

    Intentionally asymmetric: a bullet can_hit an enemy, but the enemy does not
    can_hit the bullet.  Unregistered entities (walls) have layer/mask 0 → False.
    """
    la = getattr(a, '_collision_layer', 0)
    mb = getattr(b, '_collision_mask',  0)
    return bool(la & mb)


def swept_move_blocked(mover, origin, direction, dist, offsets, skin_width=0.0) -> bool:
    """Multi-height swept wall test — collision authority #2 (shared).

    Cast one ray per height in `offsets` from `origin + offset` along `direction`
    for `dist + skin_width`; return True if ANY ray hits something solid. This is
    the exact sweep `Player._swept_blocked` used to do inline, lifted here so the
    player (movement) and enemies (ChaseNode/PatrolNode chase movement, v1.4)
    avoid walls identically instead of each owning a copy of the loop.

    In-flight bullets are always ignored (a character must never be blocked by a
    bullet mid-air), trigger volumes are always ignored (Layers.TRIGGER is an
    enter/exit detection volume, never a physical blocker — v1.5), and `mover`
    ignores itself. Walls are unregistered, so they are NOT in the ignore lists
    and correctly block the sweep.

    Args:
        mover:      the entity being moved (added to the raycast ignore list).
        origin:     sweep start position (the mover's current position).
        direction:  normalized travel direction.
        dist:       intended travel distance this frame (before skin padding).
        offsets:    iterable of Vec3 height offsets — the body heights to test.
        skin_width: extra padding added to `dist` so the mover stops just shy of
                    the wall instead of touching it (Player uses 0.1).

    Returns:
        True if the move would drive any sampled height into a wall.
    """
    ignore = ([mover]
              + collision_manager.query_layer(Layers.PLAYER_BULLET)
              + collision_manager.query_layer(Layers.ENEMY_BULLET)
              + collision_manager.query_layer(Layers.TRIGGER))
    for offset in offsets:
        if raycast(origin + offset, direction,
                   distance=dist + skin_width,
                   ignore=ignore, debug=False).hit:
            return True
    return False


class AliveEntity(Entity):
    """Entity subclass with an idempotent die() / on_die() lifecycle.

    update() must guard with `if not self.alive: return` because destroy() is
    deferred — the entity stays in scene.entities until end-of-frame flush.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._alive = True

    @property
    def alive(self) -> bool:
        """True until die() has been called."""
        return self._alive

    def die(self):
        """Idempotent: mark dead, run on_die(), then queue Ursina destroy."""
        if not self._alive:
            return
        self._alive = False
        collision_manager.remove(self)
        self.on_die()
        destroy(self)

    def on_die(self):
        """Override to clean up sub-entities before super().die() destroys self."""
        pass


class CollisionManager:
    """Spatial grid registry for tracked entities.

    External callers use query_layer() and query_near().
    _tracked and spatial_grid are internal — do not access them directly.
    """

    def __init__(self, cell_size: float = 5.0):
        self._cell_size   = cell_size
        self._tracked     = set()
        self._cell_map    = {}   # entity id → cell key
        self.spatial_grid = {}   # cell key → list[entity]

    def add(self, entity, layer: int):
        """Register `entity` at `layer` and add it to the spatial grid."""
        register(entity, layer)
        self._tracked.add(entity)
        self._update_cell(entity)

    def remove(self, entity):
        """Unregister `entity` and remove it from the spatial grid."""
        unregister(entity)
        self._remove_from_grid(entity)
        self._tracked.discard(entity)

    def update(self):
        """Rebuild grid positions for all tracked entities; remove dead ones."""
        for entity in list(self._tracked):
            if not getattr(entity, 'alive', True):
                self._remove_from_grid(entity)
                self._tracked.discard(entity)
                continue
            self._update_cell(entity)

    def _cell(self, position) -> tuple:
        cs = self._cell_size
        return (int(position.x // cs), int(position.z // cs))

    def _update_cell(self, entity):
        new_cell = self._cell(entity.position)
        old_cell = self._cell_map.get(id(entity))
        if new_cell == old_cell:
            return
        if old_cell is not None:
            bucket = self.spatial_grid.get(old_cell, [])
            if entity in bucket:
                bucket.remove(entity)
        self._cell_map[id(entity)] = new_cell
        self.spatial_grid.setdefault(new_cell, []).append(entity)

    def _remove_from_grid(self, entity):
        cell = self._cell_map.pop(id(entity), None)
        if cell is not None:
            bucket = self.spatial_grid.get(cell, [])
            if entity in bucket:
                bucket.remove(entity)

    def query_layer(self, layer: int) -> list:
        """Return a snapshot list of all live tracked entities on `layer`."""
        return [e for e in self._tracked
                if getattr(e, '_collision_layer', 0) == layer]

    def count_layer(self, layer: int) -> int:
        """Count live tracked entities on `layer` without allocating a list."""
        return sum(1 for e in self._tracked
                   if getattr(e, '_collision_layer', 0) == layer)

    def query_near(self, position, radius: float, layer: int) -> list:
        """Return entities on `layer` within `radius` of `position`."""
        cell = self._cell(position)
        candidates = []
        for dc in product((-1, 0, 1), repeat=2):
            c = (cell[0] + dc[0], cell[1] + dc[1])
            candidates += self.spatial_grid.get(c, [])
        return [e for e in candidates
                if getattr(e, '_collision_layer', 0) == layer
                and distance(e.position, position) <= radius]


collision_manager = CollisionManager()
