from ursina import *
from Scripts.health_bar import HealthBar
from Scripts.collision_system import AliveEntity, Layers, collision_manager, swept_move_blocked
from Scripts.ground_shadow import GroundShadow
from Scripts.asset_resolve import resolve_model as _resolve_model
from Scripts.lit_shader import lit_shader

# TUNE: balance / experiment variables — label kept so grep finds them fast during playtesting
ENEMY_HP_DEFAULT         = 100    # TUNE: try 50 for a 2-shot kill at 25 dmg/bullet
ENEMY_SHOOT_COOLDOWN     = 1.0    # TUNE: try 1.5 for more breathing room
ENEMY_DETECTION_RANGE    = 100    # TUNE: try 40 to remove whole-level aggro
ENEMY_ATTACK_RANGE       = 30     # TUNE: try 20 to tighten melee zone
ENEMY_OCCLUSION_INTERVAL = 0.15   # TUNE: raycast throttle — do not go below 0.05
ENEMY_CHASE_SPEED        = 5      # TUNE: chase move speed (units/s); player.speed is 8 — slower so the player can disengage

# Body-height sweep offsets for chase wall-avoidance — enemy is scale (1.5,3,1.5)
# with origin_y=-0.5, so it spans ~position.y (feet) to position.y+3 (head).
# Three heights (feet / mid / head) feed the SAME swept_move_blocked helper the
# player uses with its own 5 offsets; enemies are simpler so three suffice.
ENEMY_SWEPT_OFFSETS = (Vec3(0, 0.2, 0), Vec3(0, 1.5, 0), Vec3(0, 2.8, 0))

VALID_ENEMY_TYPES = ('default',)  # extend here as new types are added

# Visual-only model (v1.7): a saucer-shaped flying-drone mesh, disc-like with
# no legs/vertical body — chosen for art direction over the placeholder cube,
# not because behaviour is airborne (patrol/chase/attack/flee are all
# ground-based via raycasts). It stays a purely cosmetic child so the box
# collider (Enemy.__init__, scale (1.5,3,1.5)) that every raycast/sweep in
# this file depends on is untouched. Kept centered/scaled to roughly fill
# that collider footprint; source mesh bounds ~1.2w x 0.875h x 0.76d.
ENEMY_VISUAL_MODEL    = 'enemy-flying'
ENEMY_VISUAL_SCALE    = (0.8, 0.9, 0.8)
# Child position is in the parent's LOCAL (unscaled) space — local Y is
# multiplied by the collider cube's scale_y=3 to get world offset, and
# origin_y=-0.5 already makes the cube's own position its feet (verified
# empirically: local (0,1,0) on a scale-3 parent lands at world y=3, no
# extra origin term). local_y=0.5 -> world y=1.5, mid-collider height —
# the drone hovers there rather than sitting on the ground, matching its
# disc/no-legs shape.
ENEMY_VISUAL_POSITION = (0, 0.4, 0)

# Walk-bob (v1.7): cheap fake-locomotion — a sine vertical offset added to the
# cosmetic visual_model's LOCAL y while the enemy is actually translating
# (chase/patrol). No rig/animation needed. The offset rides on top of the
# neutral ENEMY_VISUAL_POSITION.y, and eases back to 0 when the enemy stops so
# it never freezes mid-cycle. Amplitude is in the child's LOCAL space (parent
# scale_y=3 multiplies it to world) — keep it subtle.
ENEMY_BOB_AMPLITUDE = 0.04    # local-y sine amplitude (~0.12 world units at scale 3)
ENEMY_BOB_SPEED     = 9.0     # radians/sec — cadence of the bob
ENEMY_BOB_EASE      = 8.0     # per-sec lerp rate easing the offset to neutral when stopped
ENEMY_BOB_MOVE_EPS  = 1e-4    # min per-frame horizontal move (sq) to count as "moving"


class Enemy(AliveEntity):
    def __init__(self, spawn_position, player, hp=ENEMY_HP_DEFAULT, enemy_type='default', rotation_y=0, behaviour_tree=None):
        """Spawn an enemy at spawn_position; origin_y=-0.5 passed to super so grid cell is correct from frame 0."""
        if enemy_type not in VALID_ENEMY_TYPES:
            raise ValueError(f"Unknown enemy_type {enemy_type!r}; valid: {VALID_ENEMY_TYPES}")

        super().__init__(
            model='cube',
            visible_self=False,   # collision proxy only — visible=False would also hide the visual child below
            scale=(3, 3, 3),
            position=spawn_position,
            rotation_y=rotation_y,
            collider='box',
            origin_y=-0.5,        # set here so collision_manager.add registers the correct grid cell
        )
        collision_manager.add(self, Layers.ENEMY)

        self.visual_model = Entity(
            parent=self,
            model=_resolve_model(ENEMY_VISUAL_MODEL),
            position=ENEMY_VISUAL_POSITION,
            rotation_y=180,   # enemy-flying.glb's forward axis is reversed vs. the collider's — relative offset, stays correct as the parent turns
            scale=ENEMY_VISUAL_SCALE,
            # Lit path (v1.7 L1) — on the visual mesh, not the parent: the parent is
            # the invisible collision proxy (visible_self=False), so shading it would
            # be a no-op. The drone now takes the sun's Half-Lambert falloff plus a
            # rim edge, which is what separates it from the level blocks behind it.
            shader=lit_shader,
        )
        # panda3d-gltf imports enemy-flying.glb with TransparencyAttrib:dual on its
        # ModelRoot (the glTF material's alpha handling), which routes the mesh into
        # the *transparent* render bin — drawn after all opaque geometry and not
        # depth-sorted against walls, so the drone bleeds through walls it stands near
        # (verified via A/B screenshot: dual → mesh pokes through, M_none → clean
        # occlusion). The mesh is opaque, so force it back into the opaque pass to
        # depth-test normally like every other world entity. Clear on the whole
        # subtree — the attrib sits on the ModelRoot child, not the Entity node.
        from panda3d.core import TransparencyAttrib
        self.visual_model.setTransparency(TransparencyAttrib.M_none)
        for _child in self.visual_model.getChildren():
            _child.setTransparency(TransparencyAttrib.M_none)

        self.health      = hp
        self.max_health  = hp
        self.enemy_type  = enemy_type
        self.player      = player
        self.attack_range     = ENEMY_ATTACK_RANGE
        self.detection_range  = ENEMY_DETECTION_RANGE

        # Behaviour tree (v1.4 Step 7): built by BehaviourTreeFactory from a
        # named preset. The "default" preset reproduces the interim Step-3 tree
        # exactly — Selector([Sequence([Chase, Attack]), Idle]) with the same
        # tuned constants — so behaviour is unchanged from before this swap.
        # The `behaviour_tree` param remains a test/injection escape hatch: unit
        # and smoke tests pass a custom tree directly, bypassing the Factory.
        # Lazy import breaks the enemy <-> factory cycle (the Factory imports the
        # ENEMY_* tuned constants from this module): by the time an Enemy is
        # constructed, enemy.py is fully loaded, so the Factory's import resolves.
        # Same pattern as shoot()'s lazy get_enemy_bullet_pool import.
        if behaviour_tree is not None:
            self._tree = behaviour_tree
        else:
            from Scripts.behaviour_tree_factory import BehaviourTreeFactory
            self._tree = BehaviourTreeFactory.build("default", {})

        self._occluded        = False   # cached result — updated on throttle interval
        self._occlusion_timer = 0.0     # counts down; raycast fires when <= 0

        # Walk-bob state (v1.7). _bob_phase advances only while moving; _bob_offset
        # is the applied local-y delta, eased toward 0 when stopped. _prev_position
        # snapshots position each frame so update() can tell if the enemy actually
        # translated (chase_step/patrol_step are the only movers).
        self._bob_phase    = 0.0
        self._bob_offset   = 0.0
        self._prev_position = Vec3(self.position)

        self.shadow = GroundShadow(self, scale=(1.5, 1.5))

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
        """Per-frame: rotate toward player, throttled occlusion check, tick the tree."""
        if not self.alive or not self.player:
            return

        player_dist = distance(self.position, self.player.position)

        # Throttle occlusion raycast — expensive, no need every frame. Gated on
        # 200u because that's the widest range anything reads _occluded (the
        # health-bar line below; the look_at gate reads it only within
        # detection_range, a subset) — no point casting for an enemy the player
        # can't even see a health bar on. Was previously nested under the
        # detection_range gate, so _occluded went stale between 100-200u; casting
        # here keeps it fresh exactly where it's used.
        if player_dist < 200:
            self._occlusion_timer -= time.dt
            if self._occlusion_timer <= 0:
                self._occluded        = self._is_occluded()
                self._occlusion_timer = ENEMY_OCCLUSION_INTERVAL

        # Only turn to face the player when in range AND with line of sight —
        # the same range+LOS gate DetectPlayerNode uses for combat. Gating on
        # bare distance alone made patrollers track the player THROUGH walls
        # while walking their route (brain/Gotchas: detection was pure distance).
        if player_dist <= self.detection_range and not self._occluded:
            self.look_at(self.player.position)
            self.rotation_x = 0
            self.rotation_z = 0

        # Behaviour tree owns the chase→attack decision (replaces the old
        # `if player_dist <= attack_range and can_shoot: self.shoot()` block).
        # ChaseNode/AttackNode re-check range themselves, so this ticks every
        # frame. ChaseNode may move the enemy via self.chase_step().
        self._tree.tick(self, time.dt)

        self._update_bob()

        self.health_bar.world_position = self.world_position + Vec3(0, 3.75, 0)
        self.health_bar.value          = self.health
        self.health_bar.enabled        = player_dist < 200 and not self._occluded

        self.shadow.update()

        if self.health <= 0:
            self.die()

    def _update_bob(self):
        """Drive the visual_model's local-y walk-bob from actual translation.

        Movement is detected by comparing this frame's position to last frame's
        (chase_step/patrol_step are the only movers), not by behaviour-tree
        state — so a chase node that's blocked against a wall and not actually
        translating correctly reads as "not moving" and the bob eases out.
        The bob phase only advances while moving; when stopped, the applied
        offset lerps back to 0 so the mesh settles at neutral instead of
        freezing mid-cycle.
        """
        delta = self.position - self._prev_position
        moving = (delta.x * delta.x + delta.z * delta.z) > ENEMY_BOB_MOVE_EPS
        self._prev_position = Vec3(self.position)

        if moving:
            self._bob_phase += ENEMY_BOB_SPEED * time.dt
            target = math.sin(self._bob_phase) * ENEMY_BOB_AMPLITUDE
        else:
            target = 0.0
        # Ease toward the target every frame — a live sine while moving, a decay
        # to neutral when stopped. lerp factor clamped so a long frame can't
        # overshoot.
        self._bob_offset = lerp(self._bob_offset, target,
                                min(1.0, ENEMY_BOB_EASE * time.dt))
        self.visual_model.y = ENEMY_VISUAL_POSITION[1] + self._bob_offset

    def chase_step(self, direction, dt):
        """Move one frame toward `direction`, avoiding walls (called by ChaseNode).

        Uses the shared swept_move_blocked helper (collision authority #2) — the
        SAME wall test the player uses — instead of duplicating the raycast loop
        (see docs/v1.4-enemy-behaviour-trees.md and brain/Patterns no-duplication
        rule). Mirrors Player.handle_horizontal_movement's move-then-axis-slide:
        if the full move is blocked, try sliding along X, else along Z, so the
        enemy follows walls instead of sticking to them.

        `direction` is the normalized enemy→player vector ChaseNode computed; this
        method only translates it into a wall-aware position update.
        """
        move = direction * ENEMY_CHASE_SPEED * dt
        if not swept_move_blocked(self, self.position, direction, move.length(),
                                  ENEMY_SWEPT_OFFSETS):
            self.position += move
            return
        x = Vec3(move.x, 0, 0)
        z = Vec3(0, 0, move.z)
        if x.length() and not swept_move_blocked(self, self.position, x.normalized(),
                                                 abs(move.x), ENEMY_SWEPT_OFFSETS):
            self.position += x
        elif z.length() and not swept_move_blocked(self, self.position, z.normalized(),
                                                   abs(move.z), ENEMY_SWEPT_OFFSETS):
            self.position += z

    def patrol_step(self, direction, speed, dt):
        """Move one frame toward `direction` at `speed`, avoiding walls (called by PatrolNode).

        Mirrors chase_step's move-then-axis-slide via the SAME shared
        swept_move_blocked helper (collision authority #2) — no duplicated
        raycast loop (see docs/v1.4-enemy-behaviour-trees.md and
        brain/Patterns no-duplication rule). The only difference from
        chase_step is that speed is passed in (PatrolNode's own speed
        parameter) rather than hardcoded to ENEMY_CHASE_SPEED, since a patrol
        route is not tied to the chase constant the way ChaseNode is.

        `direction` is the normalized enemy->waypoint vector PatrolNode
        computed; this method only translates it into a wall-aware position
        update.
        """
        move = direction * speed * dt
        if not swept_move_blocked(self, self.position, direction, move.length(),
                                  ENEMY_SWEPT_OFFSETS):
            self.position += move
            return
        x = Vec3(move.x, 0, 0)
        z = Vec3(0, 0, move.z)
        if x.length() and not swept_move_blocked(self, self.position, x.normalized(),
                                                 abs(move.x), ENEMY_SWEPT_OFFSETS):
            self.position += x
        elif z.length() and not swept_move_blocked(self, self.position, z.normalized(),
                                                   abs(move.z), ENEMY_SWEPT_OFFSETS):
            self.position += z

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

    def can_see_player(self) -> bool:
        """True if nothing solid blocks the enemy→player line of sight.

        Duck-typed LOS accessor called by DetectPlayerNode (behaviour_nodes.py),
        which stays Ursina-import-free and delegates the raycast here — the same
        delegation pattern as chase_step/patrol_step. Reuses the existing
        _is_occluded() sweep (walls block, the player does not; other enemies on
        Layers.ENEMY also block, matching _is_occluded), so no new raycast
        convention is introduced. A live raycast, not the throttled _occluded
        cache, so detection is correct independent of update()'s occlusion timer.
        """
        return not self._is_occluded()

    def shoot(self):
        """Fire one enemy bullet toward the player via the pool.

        No self-gate: AttackNode owns fire cadence via its own per-instance
        wall-clock cooldown (behaviour_nodes.py), which is per-preset
        (aggressive 0.4s, patrol 0.8s, default 1.0s). The old can_shoot flag
        here was a second, fixed 1.0s gate that silently overrode any faster
        preset cadence — removed so the preset's configured cadence governs.
        """
        from Scripts.weapon import get_enemy_bullet_pool  # lazy — breaks weapon↔enemy circular import
        pool = get_enemy_bullet_pool()
        pool.acquire(
            position=self.position + Vec3(0, 1.5, 0),
            target=self.player.position + Vec3(0, 1, 0),
            player=self.player,
            enemy=self,
            speed=10,
        )

    def on_die(self):
        """Destroy health bar and shadow before super().on_die() destroys self."""
        if hasattr(self, 'health_bar') and self.health_bar:
            destroy(self.health_bar)
        if hasattr(self, 'shadow') and self.shadow:
            self.shadow.destroy()
