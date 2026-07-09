from ursina import *
from ursina.shaders.unlit_shader import unlit_shader
from panda3d.core import BitMask32, Camera as PandaCamera, NodePath, Quat
from Scripts.collision_system import (
    AliveEntity, Layers, can_hit, collision_manager
)
from Scripts.asset_resolve import resolve_model as _resolve_model, resolve_sound as _resolve_sound
from Scripts.game_settings import game_settings
import time as _time
import random


POOL_SIZE_PLAYER = 30
POOL_SIZE_ENEMY  = 60

# Weapon sway tuning: mouse-look delta -> local x/y offset, smoothed toward target
# each frame (exponential ease, not a spring) and clamped so a fast flick can't
# throw the viewmodel far enough to obscure the crosshair.
SWAY_MOUSE_SCALE = 0.3
SWAY_MAX         = 0.6
SWAY_SMOOTH      = 8


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
# Viewmodel camera (dual-camera FPS gun rendering)
# ---------------------------------------------------------------------------
#
# Depth tricks (always_on_top / setBin) only change *draw order* — they cannot
# stop the gun's geometry from physically intersecting a wall in world space.
# When the camera is flush against a wall, the gun's far end is literally inside
# the wall, so the wall is drawn over it.
#
# The fix is the standard Unity/Unreal approach: render the gun with a SECOND
# camera in its own later pass that clears ONLY the depth buffer, with depth-test
# ON on the gun, so the viewmodel composites on top of the world regardless of
# world depth AND the gun's own geometry sorts correctly against itself.
# (Clearing *colour* for that pass was an early misstep — it blanked the whole
# world underneath on macOS GL 2.1. Depth-only clear is safe: the world colour is
# already in the shared framebuffer and untouched. See _setup_viewmodel_camera().)
#
# Mechanism (Panda3D camera bitmasks + a dedicated display region):
#   - VIEWMODEL_MASK is a single dedicated draw bit.
#   - The main camera's mask has that bit cleared → it renders everything EXCEPT
#     the gun.
#   - The gun's draw mask is set to ONLY that bit → only the viewmodel camera
#     sees it.
#   - The viewmodel camera shares the main camera's transform (parented to
#     base.cam) and draws in a display region sorted after the world (0) but
#     before the UI (20). The region clears depth first; depth-test/write ON on
#     the gun (Weapon.__init__) then sorts its own overlapping parts and lands the
#     whole gun on top of the world (whose depth was wiped for this pass).

VIEWMODEL_MASK = BitMask32.bit(7)   # dedicated draw bit for viewmodel geometry

_viewmodel_camera = None            # singleton NodePath(PandaCamera); reused across Weapon instances


def _setup_viewmodel_camera():
    """Create (once) the second camera + display region that renders the gun on top.

    Idempotent — safe to call on every Weapon construction (each new game spawns a
    fresh Player → fresh Weapon). Returns the viewmodel camera NodePath, or None if
    the Panda3D window/camera are not ready yet (callers fall back gracefully)."""
    global _viewmodel_camera
    if _viewmodel_camera is not None and not _viewmodel_camera.is_empty():
        return _viewmodel_camera

    base = getattr(application, 'base', None)
    if base is None or not hasattr(base, 'win') or base.win is None:
        return None

    # Main camera stops drawing viewmodel geometry.
    main_cam_node = base.camNode
    main_cam_node.set_camera_mask(main_cam_node.get_camera_mask() & ~VIEWMODEL_MASK)

    # Second camera that renders ONLY the viewmodel layer. Reuse the main lens so
    # fov + aspect ratio always match the world view (and auto-track window resize).
    vm_cam = PandaCamera('viewmodel_camera')
    vm_cam.set_camera_mask(VIEWMODEL_MASK)
    vm_cam.set_lens(base.camLens)

    vm_np = NodePath(vm_cam)
    vm_np.reparent_to(base.cam)   # share the main camera's world transform exactly

    # Dedicated display region, drawn AFTER the world (sort 0) and Ursina's render2d
    # (sort 10) but BEFORE the UI region (sort 20). Clear ONLY the depth buffer for
    # this region (colour clear stays OFF): the gun runs with depth-test ON (set in
    # Weapon.__init__), so a fresh per-pass depth buffer is what lets the gun's own
    # overlapping sub-parts sort correctly AND still land on top of the world (whose
    # colour was already drawn into the shared framebuffer and is NOT touched by a
    # depth-only clear). The earlier "clearing depth blanks the world" finding was a
    # misdiagnosis — clearing *colour* is what blanked it; depth-only is safe.
    dr = base.win.make_display_region()
    dr.set_sort(15)
    dr.set_clear_depth_active(True)   # depth-only — colour clear stays OFF (default)
    dr.set_clear_depth(1.0)
    dr.set_camera(vm_np)

    _viewmodel_camera = vm_np
    return vm_np


# ---------------------------------------------------------------------------
# Weapon (base class — viewmodel rendering, ammo, reload; shoot() is per-subclass)
# ---------------------------------------------------------------------------

class Weapon(Entity):
    """Base class for player weapons: viewmodel camera setup + ammo/reload.

    Subclasses (Pistol, Shotgun, Rifle — v1.5 Steps 8-10) set damage/cooldown/
    speed/ammo class attributes and implement shoot(). `ammo=-1` means infinite.

    view_model/view_scale/view_position/view_rotation are per-subclass viewmodel
    tuning (v1.7 gun model wiring) — each real gun asset has its own origin/size,
    so these are class attributes rather than constructor defaults shared by all.
    """
    damage       = 25
    cooldown     = 0.15
    bullet_speed = 50
    max_ammo     = -1   # -1 = infinite
    reload_time  = 1.0

    view_model    = '3d models/gun.obj'
    view_scale    = 0.06
    view_position = (0.5, -0.5, 0.8)
    view_rotation = (0, 0, 0)
    shoot_sound   = 'blaster'
    # Gun pack MTLs are flat, near-black tactical colors (Kd ~0.02-0.09) meant
    # for a lit renderer; the viewmodel is unlit (no scene lighting to lift
    # them), so they render as near-black. Boost compensates without a
    # lighting model — verified against the actual Kd values in
    # assets/models/*.mtl to stay legible without blowing out to white.
    view_color_boost = 6

    def __init__(self, player, model=None, texture=None,
                 scale=None, position=None, rotation=None, **kwargs):
        kwargs.setdefault('color', Color(*([self.view_color_boost] * 3), 1))
        super().__init__(
            parent=camera,
            model=model or _resolve_model(self.view_model),
            texture=texture,
            scale=scale or self.view_scale,
            position=position or self.view_position,
            rotation=rotation or self.view_rotation,
            **kwargs
        )
        # Dual-camera viewmodel: route the gun onto the dedicated viewmodel draw
        # layer so only the second camera (a later pass) renders it. Depth state
        # alone could not fix the clipping in the single-camera setup, because the
        # gun shared the world pass; isolating it in a later pass is what works.
        _setup_viewmodel_camera()
        self.hide(BitMask32.all_on())   # invisible to the main camera...
        self.show(VIEWMODEL_MASK)       # ...visible only to the viewmodel camera
        # Depth test/write ON: the gun mesh is a single merged Geom (all material
        # groups in one node), so its overlapping sub-parts can only sort correctly
        # via the depth buffer — there are no separable child nodes to setBin. The
        # VM display region clears depth first (see _setup_viewmodel_camera), so the
        # gun still lands on top of the world (world colour already drawn, world
        # depth wiped for this pass) regardless of how close a wall is.
        self.setDepthTest(True)
        self.setDepthWrite(True)
        self.shader = unlit_shader  # vertex colors from MTL; no scene lighting on viewmodel
        self.player       = player
        self.original_pos = Vec3(self.position)
        self.last_shot    = 0
        self.ammo         = self.max_ammo
        self.reloading    = False
        self._sway_offset = Vec2(0, 0)   # smoothed lag behind mouse-look delta

    def update(self):
        """Weapon sway: lags a small offset behind mouse-look delta, smoothed each
        frame, added on top of original_pos/recoil. When the toggle is off the
        offset eases back to zero instead of snapping, so mid-sway disable doesn't
        pop the viewmodel.
        """
        target = Vec2(0, 0)
        if game_settings['weapon_sway_enabled'] and mouse.locked:
            target = Vec2(
                clamp(-mouse.velocity[0] * SWAY_MOUSE_SCALE, -SWAY_MAX, SWAY_MAX),
                clamp(-mouse.velocity[1] * SWAY_MOUSE_SCALE, -SWAY_MAX, SWAY_MAX),
            )
        self._sway_offset = lerp(self._sway_offset, target, min(time.dt * SWAY_SMOOTH, 1))
        self.x = self.original_pos.x + self._sway_offset.x
        self.y = self.original_pos.y + self._sway_offset.y

    def shoot(self):
        """Base shoot: cooldown/ammo gate + single bullet. Shotgun overrides for spread."""
        if not self._ready_to_fire():
            return
        self._spawn_bullet(camera.forward)
        self._consume_shot()

    def _ready_to_fire(self) -> bool:
        """Cooldown/reload/ammo gate shared by every weapon's shoot()."""
        if _time.time() - self.last_shot < self.cooldown:
            return False
        if self.reloading:
            return False
        if self.ammo == 0:
            return False   # dry-click: out of ammo, caller may play a sound
        return True

    def _spawn_bullet(self, direction):
        """Acquire one pooled bullet travelling along `direction`."""
        return _player_bullet_pool.acquire(
            position=self.world_position + camera.forward,
            direction=direction,
            speed=self.bullet_speed,
            damage=self.damage,
            player=self.player
        )

    def _consume_shot(self):
        """Decrement ammo (if finite), play the recoil animation + SFX, stamp last_shot."""
        if self.ammo > 0:
            self.ammo -= 1
        self._play_shoot_animation()
        self._play_shoot_sound()
        self.last_shot = _time.time()

    def reload(self):
        """Restore ammo to max_ammo after reload_time. No-op for infinite-ammo weapons."""
        if self.max_ammo < 0 or self.reloading or self.ammo == self.max_ammo:
            return
        self.reloading = True
        invoke(self._finish_reload, delay=self.reload_time)

    def _finish_reload(self):
        self.ammo      = self.max_ammo
        self.reloading = False

    def _play_shoot_animation(self):
        # z-only: x/y are owned by update()'s sway offset every frame (animating
        # them here would fight that per-frame write and jitter the viewmodel).
        self.animate_z(self.original_pos.z - 0.2, duration=0.05)
        self.animate_z(self.original_pos.z, delay=0.05, duration=0.15, curve=curve.out_quad)

    def _play_shoot_sound(self):
        """One-shot fire-and-forget SFX; auto_destroy=True cleans itself up after playing."""
        Audio(_resolve_sound(self.shoot_sound), auto_destroy=True)


# ---------------------------------------------------------------------------
# Pistol — v1.5 Step 8. Refactor of the original single Weapon class.
# ---------------------------------------------------------------------------

class Pistol(Weapon):
    damage       = 25
    cooldown     = 0.15
    bullet_speed = 50
    max_ammo     = -1   # infinite, matches pre-inventory behaviour

    view_model    = 'Pistol_1'
    view_scale    = 0.3
    view_position = (0.4, -0.35, 0.6)
    view_rotation = (0, 90, 0)


# ---------------------------------------------------------------------------
# Shotgun — v1.5 Step 9. 5 pellets per shot, ±5° spread.
# ---------------------------------------------------------------------------

PELLET_COUNT  = 5
SPREAD_DEGREES = 5

class Shotgun(Weapon):
    damage       = 15
    cooldown     = 0.8
    bullet_speed = 30
    max_ammo     = 8
    reload_time  = 1.5

    view_model    = 'Shotgun_1'
    view_scale    = 0.21   # 1.5x — v1.7 cosmetic pass, viewmodel read as too small
    view_position = (0.45, -0.4, 0.65)
    view_rotation = (0, 90, 0)

    def shoot(self):
        if not self._ready_to_fire():
            return
        for _ in range(PELLET_COUNT):
            self._spawn_bullet(self._spread_direction())
        self._consume_shot()

    def _spread_direction(self):
        """camera.forward jittered by up to ±SPREAD_DEGREES on each of two axes.

        Vec3 (a raw Panda3D LVector3f here) has no rotate() method — build the
        jitter with a Quat axis-angle rotation instead, same approach the mouse-ray
        code uses for deriving vectors from camera axes (mouse.direction was
        removed in Ursina 8.3.0; see brain/Gotchas.md).
        """
        yaw   = random.uniform(-SPREAD_DEGREES, SPREAD_DEGREES)
        pitch = random.uniform(-SPREAD_DEGREES, SPREAD_DEGREES)
        direction = Vec3(camera.forward)
        yaw_quat = Quat()
        yaw_quat.setFromAxisAngle(yaw, Vec3(camera.up))
        direction = Vec3(yaw_quat.xform(direction))
        pitch_quat = Quat()
        pitch_quat.setFromAxisAngle(pitch, Vec3(camera.right))
        direction = Vec3(pitch_quat.xform(direction))
        return direction.normalized()


# ---------------------------------------------------------------------------
# Rifle — v1.5 Step 10. High-speed single bullet, no spread.
# ---------------------------------------------------------------------------

class Rifle(Weapon):
    damage       = 40
    cooldown     = 0.08
    bullet_speed = 80
    max_ammo     = 24
    reload_time  = 1.2
    # Uses the base Weapon.shoot() — single bullet along camera.forward, no spread.

    view_model    = 'AssaultRifle_1'
    view_scale    = 0.27   # 1.5x — v1.7 cosmetic pass, viewmodel read as too small
    view_position = (0.4, -0.4, 0.6)
    view_rotation = (0, 90, 0)
    shoot_sound   = 'blaster_repeater'


# level.json "weapon_type" string → Weapon subclass. Used by AmmoPickup to resolve
# both weapon_pickup (spawn a new weapon) and ammo_pickup (top up an owned one)
# without ever comparing entity.name (Hard Constraint 1) — isinstance() against
# these classes is the dispatch mechanism.
WEAPON_TYPES = {
    'pistol':  Pistol,
    'shotgun': Shotgun,
    'rifle':   Rifle,
}


# ---------------------------------------------------------------------------
# AmmoPickup — v1.5 Step 12. Registered as Layers.PICKUP (not a damage path).
# ---------------------------------------------------------------------------

class AmmoPickup(AliveEntity):
    """Invisible-collider pickup for a weapon or ammo top-up.

    pickup_type='weapon': first time the player overlaps it, gives a fresh
    instance of WEAPON_TYPES[weapon_type] into the player's inventory (first
    empty slot, or does nothing if the inventory is full).
    pickup_type='ammo': adds `amount` to an ALREADY-OWNED weapon of the matching
    type (isinstance() check — no name-based dispatch, per Hard Constraint 1).
    No effect if the player doesn't own that weapon type.

    Registered as Layers.PICKUP via collision_manager.add() (not standalone
    register()) so AliveEntity.die() tears down the spatial-grid entry the same
    way TriggerZone does (brain/Gotchas "CollisionManager.add() vs register()").
    Overlap is checked with self.intersects() per frame — same corrected pattern
    as TriggerZone (intersects()'s first arg is a traversal target, not the
    entity to test against; see trigger_system.py). Dies after one collection —
    pickups are level-placed, not shot at pool frequency, so no pool needed.
    """

    def __init__(self, position, pickup_type='ammo', weapon_type='pistol',
                 amount=30, **kwargs):
        super().__init__(
            position=position,
            scale=(0.6, 0.6, 0.6),
            collider='box',
            visible=False,
            **kwargs,
        )
        collision_manager.add(self, Layers.PICKUP)
        self.pickup_type = pickup_type   # 'weapon' or 'ammo'
        self.weapon_type = weapon_type   # key into WEAPON_TYPES
        self.amount      = amount        # only used for pickup_type == 'ammo'

    def update(self):
        if not self.alive:
            return

        from Scripts.game import game, Game
        if game.state != Game.PLAYING:
            return

        player = game.player
        if player is None:
            return

        hit_info = self.intersects()
        if not (hit_info.hit and player in hit_info.entities):
            return

        if self._collect(player):
            self.die()

    def _collect(self, player) -> bool:
        """Attempt to apply this pickup to `player`; return True if consumed."""
        weapon_cls = WEAPON_TYPES.get(self.weapon_type)
        if weapon_cls is None:
            return False

        inventory = getattr(player, 'inventory', None)
        if inventory is None:
            return False

        if self.pickup_type == 'weapon':
            return self._give_weapon(player, inventory, weapon_cls)
        return self._give_ammo(inventory, weapon_cls)

    def _give_weapon(self, player, inventory, weapon_cls) -> bool:
        for slot, occupant in enumerate(inventory.slots):
            if isinstance(occupant, weapon_cls):
                return False   # already own this weapon type
        for slot, occupant in enumerate(inventory.slots):
            if occupant is None:
                inventory.give(weapon_cls(player), slot)
                return True
        return False   # inventory full

    def _give_ammo(self, inventory, weapon_cls) -> bool:
        for occupant in inventory.slots:
            if isinstance(occupant, weapon_cls):
                if occupant.max_ammo < 0:
                    return False   # infinite-ammo weapon — nothing to add
                occupant.ammo = min(occupant.ammo + self.amount, occupant.max_ammo)
                return True
        return False   # player doesn't own this weapon type — no effect


def get_enemy_bullet_pool() -> BulletPool:
    """Accessor for enemy.py lazy import — avoids circular import at module level."""
    return _enemy_bullet_pool


def reset_bullet_pools():
    """Discard all pooled bullets. Call during scene teardown before main_menu() sweeps entities."""
    _player_bullet_pool.reset()
    _enemy_bullet_pool.reset()
