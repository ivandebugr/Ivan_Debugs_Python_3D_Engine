from ursina import *
from itertools import product


# IMPROVED: step-1 — bitmask layer registry replaces CollisionLayer enum + isinstance checks
class Layers:
    PLAYER        = 1 << 0   # 1
    ENEMY         = 1 << 1   # 2
    PLAYER_BULLET = 1 << 2   # 4
    ENEMY_BULLET  = 1 << 3   # 8
    WALL          = 1 << 4   # 16
    PICKUP        = 1 << 5   # 32 -- forward declaration; no entity registers this yet


# IMPROVED: step-1 — declarative hit matrix; no file needs to import enemy/bullet types
COLLISION_MATRIX = {
    Layers.PLAYER_BULLET: Layers.ENEMY | Layers.WALL,
    Layers.ENEMY_BULLET:  Layers.PLAYER | Layers.WALL,
    Layers.PLAYER:        Layers.WALL | Layers.ENEMY_BULLET | Layers.PICKUP,
    Layers.ENEMY:         Layers.WALL | Layers.PLAYER_BULLET,
}

_registry: dict = {}   # entity id → (layer, mask)  # IMPROVED: step-1


def register(entity, layer: int):  # IMPROVED: step-1
    mask = COLLISION_MATRIX.get(layer, 0)
    _registry[id(entity)] = (layer, mask)
    entity._collision_layer = layer
    entity._collision_mask  = mask


def unregister(entity):  # IMPROVED: step-1
    _registry.pop(id(entity), None)


def can_hit(a, b) -> bool:  # IMPROVED: step-1 — replaces isinstance checks in weapon.py
    la = getattr(a, '_collision_layer', 0)
    mb = getattr(b, '_collision_mask',  0)
    return bool(la & mb)


# IMPROVED: step-2 — AliveEntity mixin: frame-safe single-entry destroy
class AliveEntity(Entity):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._alive = True

    @property
    def alive(self) -> bool:
        return self._alive

    def die(self):
        if not self._alive:
            return
        self._alive = False
        collision_manager.remove(self)   # removes from both _registry and _tracked
        self.on_die()
        destroy(self)

    def on_die(self):
        pass


# NOTE: unused — candidate for removal if no call site added by next audit
def swept_cast(origin, direction, distance: float,
               source_entity,
               y_offsets: tuple = (0.1, 1.0, 1.8)):
    """Cast rays at multiple heights; return first hit passing collision matrix."""
    ignore = [source_entity] + [
        e for e in scene.entities
        if getattr(e, '_collision_layer', 0) in (Layers.PLAYER_BULLET, Layers.ENEMY_BULLET)
    ]
    for y in y_offsets:
        h = raycast(Vec3(*origin) + Vec3(0, y, 0), direction,
                    distance=distance, ignore=ignore, debug=False)
        if h.hit and can_hit(source_entity, h.entity):
            return h
    from ursina.hit_info import HitInfo
    return HitInfo()


# IMPROVED: step-5 — CollisionManager with per-frame grid update and typed queries
class CollisionManager:
    def __init__(self, cell_size: float = 5.0):
        self._cell_size   = cell_size
        self._tracked     = set()
        self._cell_map    = {}   # entity id → cell
        self.spatial_grid = {}   # cell → list[entity]

    # -- registration --

    def add(self, entity, layer: int):
        register(entity, layer)
        self._tracked.add(entity)
        self._update_cell(entity)

    def remove(self, entity):
        unregister(entity)
        self._remove_from_grid(entity)
        self._tracked.discard(entity)

    # -- per-frame update --

    def update(self):  # IMPROVED: step-5 — no frame-skip throttle
        for entity in list(self._tracked):
            if not getattr(entity, 'alive', True):
                self._remove_from_grid(entity)
                self._tracked.discard(entity)
                continue
            self._update_cell(entity)

    # -- spatial grid helpers --

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

    def _remove_from_grid(self, entity):  # IMPROVED: step-5 — O(1) via stored cell
        cell = self._cell_map.pop(id(entity), None)
        if cell is not None:
            bucket = self.spatial_grid.get(cell, [])
            if entity in bucket:
                bucket.remove(entity)

    # -- typed queries (replaces scene.entities comprehensions) --

    def query_layer(self, layer: int) -> list:  # IMPROVED: step-5
        """Return all live entities on `layer`."""
        return [e for e in self._tracked
                if getattr(e, '_collision_layer', 0) == layer]

    def query_near(self, position, radius: float, layer: int) -> list:  # IMPROVED: step-5
        """Return entities on `layer` within `radius` of `position`."""
        cell = self._cell(position)
        candidates = []
        for dc in product((-1, 0, 1), repeat=2):
            c = (cell[0] + dc[0], cell[1] + dc[1])
            candidates += self.spatial_grid.get(c, [])
        return [e for e in candidates
                if getattr(e, '_collision_layer', 0) == layer
                and distance(e.position, position) <= radius]


# Module-level singleton used by main.py
collision_manager = CollisionManager()
