from ursina import *
from Scripts.lit_shader import lit_shader
from panda3d.core import (
    BitMask32, Camera as PandaCamera, NodePath, Quat,
    FrameBufferProperties, WindowProperties, GraphicsPipe, GraphicsOutput,
    Texture, CardMaker, TransparencyAttrib, OrthographicLens,
    DepthTestAttrib, DepthWriteAttrib,
)
from Scripts.collision_system import (
    AliveEntity, Layers, can_hit, collision_manager
)
from Scripts.asset_resolve import (
    resolve_model as _resolve_model,
    resolve_sound as _resolve_sound,
    resolve_texture as _resolve_texture,
)
from Scripts.game_settings import game_settings
import time as _time
import random


POOL_SIZE_PLAYER = 30
POOL_SIZE_ENEMY  = 60
POOL_SIZE_MUZZLE_FLASH = 4

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
        # NO collider: a player bullet is a raycast *source* (its update() ray plus
        # can_hit() is the single damage authority), never a raycast *target*.
        # A 'box' collider made bullets visible to every ray that didn't ignore
        # them — so the 5 co-located shotgun pellets raycast-killed each other on
        # spawn (5 spawned → 1 alive two frames later; shotgun shipped at ~1/5
        # since v1.5), and stray bullets read as false "ground"/LOS hits. EnemyBullet
        # already carries no collider for the same reason. See v1.7-collision-audit
        # F1/F2. Layer-grid membership is via collision_manager.add(), independent
        # of the Ursina collider, so query_layer() still enumerates bullets.
        super().__init__(
            model='cube',
            color=color.cyan,
            scale=(0.1, 0.1, 0.3),
            position=position,
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
# MuzzleFlash — pooled cosmetic quad, no collision (not an AliveEntity)
# ---------------------------------------------------------------------------

MUZZLE_FLASH_DURATION = 0.06

class MuzzleFlash(Entity):
    """Two overlaid billboard quads flashed at the gun's muzzle on each shot.

    The 'burst' texture is a 512x256 two-panel atlas: the orange/yellow gradient
    RING fills the left half, a white star-BURST the right half (both alpha-cutout
    on a transparent background). Mapping the whole atlas onto one square quad
    squished both panels side-by-side with a seam down the middle — so this splits
    it with UV sub-rects (texture_scale=(0.5,1) + texture_offset) instead of two
    separate texture files: the root quad samples the LEFT half (ring), a child
    quad samples the RIGHT half (starburst), overlaid at the same muzzle point so
    each flash is a richer composite of both elements. The starburst layer is
    rotated a little so it reads as a distinct burst over the ring, not a copy.

    Purely cosmetic (no collider, not registered with collision_manager) — never
    an authority in the raycast damage path. Pooled the same way bullets are:
    position-parked while inactive rather than enabled-toggled (same unstash()
    assertion risk described in weapon.py Hard Constraint 6 applies to any
    pooled entity, not just bullets).
    """

    _PARK = Vec3(0, -10000, 0)

    def __init__(self):
        _tex = _resolve_texture('burst')
        super().__init__(
            model='quad',
            texture=_tex,
            texture_scale=(0.5, 1),        # left half only...
            texture_offset=(0, 0),         # ...the ring
            scale=0.3,
            billboard=True,
            position=MuzzleFlash._PARK,
        )
        # Second layer: the starburst (right half of the atlas), overlaid at the
        # SAME muzzle point so each flash composites both elements. Parented to
        # the ring so it inherits position/scale/billboard.
        self.burst = Entity(
            parent=self,
            model='quad',
            texture=_tex,
            texture_scale=(0.5, 1),        # right half only...
            texture_offset=(0.5, 0),       # ...the starburst
            # NOT billboarded: it inherits the parent's camera-facing orientation.
            # A billboard on a child re-solves facing from the child's own world
            # position and fights the parent transform, which hid it entirely.
        )
        # Both layers depth-TEST ON (so world geometry the flash is behind still
        # occludes it) but depth-WRITE OFF, and ordered by cull bin — ring behind,
        # burst in front. Two coplanar billboards with depth-write ON z-fight and
        # the ring's (opaque-depth) center hides the burst; depth-write OFF + fixed
        # bin order composites them cleanly instead. Same trick as HealthBar's
        # bg/bar/text near-coplanar quads.
        self.setBin('fixed', 0);        self.setDepthTest(True);        self.setDepthWrite(False)
        self.burst.setBin('fixed', 1);  self.burst.setDepthTest(True);  self.burst.setDepthWrite(False)

    def play(self, position, scale=0.3):
        self.position = position
        self.scale = scale
        self.alpha = 1
        self.burst.alpha = 1
        self.animate_scale(scale * 1.6, duration=MUZZLE_FLASH_DURATION, curve=curve.linear)
        # Fade both layers together — animate('alpha') only touches this entity's
        # own quad, so the child burst needs its own fade or it would linger opaque.
        self.animate('alpha', 0, duration=MUZZLE_FLASH_DURATION, curve=curve.linear)
        self.burst.animate('alpha', 0, duration=MUZZLE_FLASH_DURATION, curve=curve.linear)
        invoke(self._park, delay=MUZZLE_FLASH_DURATION)

    def _park(self):
        # Guard against the deferred-callback-vs-teardown race (brain/Gotchas
        # invoke()/Sequence family): play() schedules this via invoke(delay=...);
        # if the pool's reset() destroys this flash inside that 0.06s window (a
        # menu return one frame after a shot), the pending _park would fire on a
        # destroyed entity and crash in position_setter. is_empty() detects the
        # dead NodePath. (Latent before the two-layer split too; guarded here while
        # MuzzleFlash is already being touched.)
        if self.is_empty():
            return
        self.position = MuzzleFlash._PARK


class MuzzleFlashPool:
    """Tiny round-robin pool — flashes are so short-lived a free-list isn't
    needed; cycling through a fixed set of quads is simpler and sufficient."""

    def __init__(self, size=POOL_SIZE_MUZZLE_FLASH):
        self._size    = size
        self._flashes = []   # built lazily on first play(), same as reset() leaves it
        self._next    = 0

    def play(self, position, scale=0.3):
        if not self._flashes:
            self._flashes = [MuzzleFlash() for _ in range(self._size)]
            self._next = 0
        # Drain a stale (externally-destroyed) quad the same way BulletPool does —
        # main_menu()'s entity sweep can destroy parked quads between scenes. The
        # sweep can also take the burst child while leaving the root (or vice
        # versa), so rebuild if EITHER half is a dead NodePath.
        flash = self._flashes[self._next]
        if flash.is_empty() or flash.burst.is_empty():
            if not flash.is_empty():
                destroy(flash.burst)   # root survived, child died — clean rebuild
                destroy(flash)
            self._flashes[self._next] = MuzzleFlash()
            flash = self._flashes[self._next]
        self._next = (self._next + 1) % len(self._flashes)
        flash.play(position, scale=scale)

    def reset(self):
        """Discard all pooled quads. Call during scene teardown, before
        main_menu() sweeps scene.entities — mirrors BulletPool.reset(). The pool
        rebuilds from scratch lazily on the next play() call."""
        for flash in self._flashes:
            if not flash.is_empty():
                # destroy() does NOT cascade to parent= children (brain/Gotchas),
                # so the burst layer must be destroyed explicitly or it leaks.
                if not flash.burst.is_empty():
                    destroy(flash.burst)
                destroy(flash)
        self._flashes = []
        self._next = 0


# ---------------------------------------------------------------------------
# Pools — module-level singletons
# ---------------------------------------------------------------------------

_player_bullet_pool = BulletPool(PlayerBullet, size=POOL_SIZE_PLAYER)
_enemy_bullet_pool  = BulletPool(EnemyBullet,  size=POOL_SIZE_ENEMY)
_muzzle_flash_pool  = MuzzleFlashPool(size=POOL_SIZE_MUZZLE_FLASH)


# ---------------------------------------------------------------------------
# Viewmodel camera (dual-camera FPS gun rendering — private-buffer variant)
# ---------------------------------------------------------------------------
#
# Depth tricks (always_on_top / setBin) only change *draw order* — they cannot
# stop the gun's geometry from physically intersecting a wall in world space.
# When the camera is flush against a wall, the gun's far end is literally inside
# the wall, so the wall is drawn over it. So the gun needs its own pass.
#
# HISTORY / WHY A PRIVATE BUFFER (not a display region):
# The v1.5-v1.7 design rendered the gun with a second camera into its own sort-15
# display region on the WINDOW, relying on `dr.set_clear_depth_active(True)` to
# give that pass a fresh depth buffer. That was proven WRONG on this macOS GL 2.1
# driver (v1.7 frame-dump investigation): a DISPLAY-REGION depth clear does NOT
# actually clear the shared window depth buffer before the VM camera draws, so the
# world's depth (from the sort-0 world pass) survives and the gun depth-tests
# against it — any wall <~0.9u in front of the gun occludes it (the point-blank
# gun-clip bug). Turning the gun's depth-test OFF fixes the world-clip but breaks
# the gun's own sub-part self-sorting (the mesh is one merged Geom; rear faces
# paint over front). The two constraints are irreconcilable on a SHARED depth
# buffer — so the gun gets its OWN.
#
# THE FIX (standard offscreen-viewmodel compositing):
#   1. Render the gun into a PRIVATE offscreen buffer with its own colour+alpha+
#      depth. A buffer's own depth clear IS honoured, so the gun self-sorts
#      correctly against a fresh depth buffer, on a transparent background.
#   2. Composite that buffer's colour over the window via a fullscreen card with
#      DepthTestAttrib.M_always (glDepthFunc(GL_ALWAYS) — genuinely ignores the
#      window depth buffer, unlike a region depth clear or plain depth-off), in a
#      display region sorted after the world (0) / render2d (10) but before the UI
#      (20). Alpha-blended, so the transparent background shows the world and only
#      the gun lands on top.
#
# Mechanism (Panda3D camera bitmasks + an offscreen buffer + a composite region):
#   - VIEWMODEL_MASK is a single dedicated draw bit; the gun uses show_through() it.
#   - base.render.hide(VIEWMODEL_MASK) clears the bit from ALL world geometry in
#     one call (Panda ancestor-hide), so the buffer camera — which renders only
#     VIEWMODEL_MASK — sees the gun and NOT the world. show_through() on the gun
#     (not show()) is required: show() cannot override an ancestor's hide, but
#     show_through() can. Without this the world fills the gun buffer opaque.
#   - The buffer camera shares the main camera's transform (parented to base.cam).
#
# LIFECYCLE (this is the risk surface — see bloom.py / light_lifecycle.py history):
#   - The buffer + composite region + card are built EXACTLY ONCE (idempotent
#     guard). They are raw NodePaths / GraphicsOutputs, NOT scene entities, so
#     main_menu()'s entity sweep cannot destroy them and return_to_menu() teardown
#     never touches them — same discipline as bloom's buffers. base.render.hide()
#     is on the world root, which is never swept, so it persists across menus.
#   - Each new game builds a fresh Weapon; its __init__ re-asserts show_through()
#     on the new gun (cheap) and _setup_viewmodel_camera() early-returns.
#   - Resize: a BF_resizeable buffer does NOT auto-track the window on this stack
#     (measured), so a 'window-event' handler resizes it to match — same event
#     bloom/FilterManager use (Hard Constraint 13: window.on_resize is never called
#     by Ursina; Panda's window-event is the correct hook).

VIEWMODEL_MASK = BitMask32.bit(7)   # dedicated draw bit for viewmodel geometry

_viewmodel_camera = None            # singleton NodePath(PandaCamera); reused across Weapon instances
_viewmodel_buffer = None            # the private offscreen GraphicsBuffer (built once)


def _resize_viewmodel_buffer(*_):
    """Match the offscreen buffer's resolution to the window (window-event hook)."""
    if _viewmodel_buffer is None or _viewmodel_buffer.is_valid() is False:
        return
    base = getattr(application, 'base', None)
    if base is None or base.win is None:
        return
    w, h = base.win.get_x_size(), base.win.get_y_size()
    if w > 0 and h > 0 and (_viewmodel_buffer.get_x_size() != w
                            or _viewmodel_buffer.get_y_size() != h):
        _viewmodel_buffer.set_size(w, h)


def _setup_viewmodel_camera():
    """Build (once) the offscreen viewmodel buffer + composite, returns the VM camera.

    Idempotent — safe to call on every Weapon construction (each new game spawns a
    fresh Player → fresh Weapon). Returns the viewmodel camera NodePath, or None if
    the Panda3D window/camera are not ready yet (callers fall back gracefully)."""
    global _viewmodel_camera, _viewmodel_buffer
    if _viewmodel_camera is not None and not _viewmodel_camera.is_empty():
        return _viewmodel_camera

    base = getattr(application, 'base', None)
    if base is None or not hasattr(base, 'win') or base.win is None:
        return None

    win = base.win
    # Main camera stops drawing viewmodel geometry, and the whole world root drops
    # the VIEWMODEL bit so it never enters the gun-only buffer (the gun re-asserts
    # the bit with show_through() in Weapon.__init__).
    main_cam_node = base.camNode
    main_cam_node.set_camera_mask(main_cam_node.get_camera_mask() & ~VIEWMODEL_MASK)
    base.render.hide(VIEWMODEL_MASK)

    # --- private offscreen buffer: colour+alpha+depth, rendered BEFORE the window
    fb = FrameBufferProperties()
    fb.set_rgba_bits(8, 8, 8, 8)      # alpha needed for the transparent-bg composite
    fb.set_depth_bits(24)
    vm_tex = Texture('viewmodel-colour')
    buf = base.graphicsEngine.make_output(
        win.get_pipe(), 'viewmodel-buffer', -1, fb,
        WindowProperties.size(win.get_x_size(), win.get_y_size()),
        GraphicsPipe.BF_refuse_window | GraphicsPipe.BF_resizeable,
        win.get_gsg(), win)
    if buf is None:
        return None   # driver refused the FBO — caller falls back gracefully
    buf.add_render_texture(vm_tex, GraphicsOutput.RTM_copy_texture, GraphicsOutput.RTP_color)
    buf.set_clear_color_active(True)
    buf.set_clear_color((0, 0, 0, 0))   # transparent — only the gun writes colour
    buf.set_clear_depth_active(True)     # a BUFFER's depth clear IS honoured here
    buf.set_clear_depth(1.0)
    _viewmodel_buffer = buf

    # VM camera: renders ONLY the viewmodel layer, into the buffer. Reuse the main
    # lens so fov + aspect always match the world view.
    vm_cam = PandaCamera('viewmodel_camera')
    vm_cam.set_camera_mask(VIEWMODEL_MASK)
    vm_cam.set_lens(base.camLens)
    vm_np = NodePath(vm_cam)
    vm_np.reparent_to(base.cam)   # share the main camera's world transform exactly
    buf.make_display_region().set_camera(vm_np)

    # --- composite: draw the buffer's colour over the window, on top of the world.
    # A fullscreen card with M_always ignores the window depth buffer entirely (the
    # one primitive that reliably beats world depth on this driver), alpha-blended
    # so the transparent background lets the world through. Its own OrthographicLens
    # camera in a sort-16 region (after world 0 / render2d 10, before UI 20).
    cm = CardMaker('viewmodel-composite')
    cm.set_frame(-1, 1, -1, 1)
    card = NodePath(cm.generate())
    card.set_texture(vm_tex)
    card.set_transparency(TransparencyAttrib.M_alpha)
    card.set_attrib(DepthTestAttrib.make(DepthTestAttrib.M_always), 1000)
    card.set_attrib(DepthWriteAttrib.make(DepthWriteAttrib.M_off), 1000)
    card.set_bin('fixed', 0)

    comp_root = NodePath('viewmodel-composite-root')
    card.reparent_to(comp_root)
    comp_cam = PandaCamera('viewmodel_composite_camera')
    comp_lens = OrthographicLens()
    comp_lens.set_film_size(2, 2)
    comp_lens.set_near_far(-10, 10)
    comp_cam.set_lens(comp_lens)
    comp_np = comp_root.attach_new_node(comp_cam)

    comp_dr = win.make_display_region()
    comp_dr.set_sort(16)
    comp_dr.set_clear_depth_active(False)
    comp_dr.set_clear_color_active(False)   # never wipe the world already drawn
    comp_dr.set_camera(comp_np)

    # Resize the buffer with the window (BF_resizeable does not auto-track here).
    base.accept('window-event', _resize_viewmodel_buffer)

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
    fire_mode    = 'semi'   # 'semi' = once per discrete click, 'auto' = continuous while held

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
        # Offscreen-buffer viewmodel: route the gun onto the dedicated viewmodel
        # draw layer so only the buffer camera renders it into a private buffer,
        # composited on top of the world. Depth state alone could not fix the
        # clipping (the gun shared the window depth buffer); a private buffer is
        # what works. See _setup_viewmodel_camera() for the full why.
        _setup_viewmodel_camera()
        self.hide(BitMask32.all_on())      # invisible to the main camera...
        # show_through (NOT show): base.render.hide(VIEWMODEL_MASK) drops the bit
        # from all world geometry so it stays out of the gun buffer; show() cannot
        # override that ancestor hide, but show_through() can. This is what keeps
        # the buffer gun-only while the gun stays visible to the buffer camera.
        self.show_through(VIEWMODEL_MASK)
        # Depth test/write ON: the gun mesh is a single merged Geom (all material
        # groups in one node), so its overlapping sub-parts can only sort correctly
        # via the depth buffer — there are no separable child nodes to setBin. The
        # private buffer's own depth clear (see _setup_viewmodel_camera) gives this
        # pass a fresh depth buffer, so the gun's parts self-sort AND the whole gun
        # composites on top of the world regardless of how close a wall is.
        self.setDepthTest(True)
        self.setDepthWrite(True)
        # Lit path (v1.7 L1). The gun is parent=camera, and main_menu() sets
        # camera.parent = scene — so the viewmodel sits UNDER scene and inherits the
        # sun's LightAttrib exactly like world geometry (verified: net LightAttrib on
        # a camera-parented entity reports the directional_light). Light math is in
        # eye space, so Panda re-expresses the sun direction per frame and the gun's
        # shading swings as the player turns, instead of being welded to the
        # viewmodel. The MTL diffuse here is near-black (Kd ~0.011-0.09) — it is
        # lit_shader's additive rim/spec, not the multiplied diffuse, that makes
        # these read as metal rather than as a black silhouette.
        self.shader = lit_shader
        self.player       = player
        self.original_pos = Vec3(self.position)
        self.original_rotation_x = self.rotation_x
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
        """Acquire one pooled bullet travelling along `direction`.

        Ballistic origin = the EYE (camera.world_position), NOT the gun muzzle.
        The old origin was gun.world_position + camera.forward (~1.3u ahead of the
        eye), which at point-blank spawns PAST the far face of a wall ≲1u thick —
        the swept ray only protects the path from the spawn point onward, so the
        shot skipped thin cover the player stood flush against (v1.7-collision-audit
        F3 / playtest §2.6: measured spawn at x=-9.32, past a wall face at -8.0).
        Spawning at the eye makes the swept ray cover the whole eye→target segment,
        so thin cover is respected. The muzzle FLASH stays at the gun (cosmetic,
        below); visual origin ≠ ballistic origin, same split as the dual-camera
        viewmodel. The bullet is 0.1u and travels 50u/s, so the ~1.3u it no longer
        skips ahead is imperceptible.
        """
        return _player_bullet_pool.acquire(
            position=camera.world_position,
            direction=direction,
            speed=self.bullet_speed,
            damage=self.damage,
            player=self.player
        )

    def _consume_shot(self):
        """Decrement ammo (if finite), play the recoil animation + SFX + muzzle
        flash, stamp last_shot."""
        if self.ammo > 0:
            self.ammo -= 1
        self._play_shoot_animation()
        self._play_shoot_sound()
        _muzzle_flash_pool.play(self.world_position + camera.forward * 0.8)
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
        # z-only position: x/y are owned by update()'s sway offset every frame
        # (animating them here would fight that per-frame write and jitter the
        # viewmodel). Kick uses rotation_x instead of position, so it composes
        # with both sway (x/y position) and recoil (z position) without fighting
        # either — headbob (player_controller.py) only touches camera.x/y
        # position too, so rotation_x is untouched by anything else.
        # interrupt='finish' on each RETURN tween is load-bearing. animate()
        # interrupts the property's shared animator slot at *creation* time, not
        # when the tween starts interpolating — so with the default
        # interrupt='kill', the return call (created in the same frame as the
        # punch) kills the punch tween the instant it's created, and its delay=
        # only postpones when it *starts*, not when it interrupts. Net: the punch
        # never runs and the property animates rest-to-rest (z_min stays at rest,
        # rotation_x never reaches the kick). 'finish' instead runs the punch
        # tween's finish() at creation, snapping the property to the punch/kick
        # peak (z-0.2 / rotation_x-8) before the return eases it back — so the
        # punch value is actually reached. See Gotchas: "same-frame animate()
        # calls on one property self-cancel via shared animator + interrupt='kill'".
        self.animate_z(self.original_pos.z - 0.2, duration=0.05)
        self.animate_z(self.original_pos.z, delay=0.05, duration=0.15, curve=curve.out_quad, interrupt='finish')
        self.animate_rotation_x(self.original_rotation_x - 8, duration=0.04)
        self.animate_rotation_x(self.original_rotation_x, delay=0.04, duration=0.15, curve=curve.out_quad, interrupt='finish')

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
    fire_mode    = 'auto'   # continuous fire while held; still gated by cooldown
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
    """Discard all pooled bullets and muzzle flashes. Call during scene teardown
    before main_menu() sweeps entities."""
    _player_bullet_pool.reset()
    _enemy_bullet_pool.reset()
    _muzzle_flash_pool.reset()
