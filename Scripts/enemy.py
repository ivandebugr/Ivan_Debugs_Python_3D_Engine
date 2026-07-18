from ursina import *
from Scripts.health_bar import HealthBar
from Scripts.collision_system import AliveEntity, Layers, collision_manager, swept_move_blocked

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


class Enemy(AliveEntity):
    def __init__(self, spawn_position, player, hp=ENEMY_HP_DEFAULT, enemy_type='default', rotation_y=0, behaviour_tree=None):
        """Spawn an enemy at spawn_position; origin_y=-0.5 passed to super so grid cell is correct from frame 0."""
        if enemy_type not in VALID_ENEMY_TYPES:
            raise ValueError(f"Unknown enemy_type {enemy_type!r}; valid: {VALID_ENEMY_TYPES}")

from Scripts.collision_system import AliveEntity, Layers, register  # IMPROVED: step-1


class Enemy(AliveEntity):  # IMPROVED: step-2 — AliveEntity for frame-safe destroy
    def __init__(self, spawn_position, player):
        super().__init__(
            model='cube',

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
        register(self, Layers.ENEMY)  # IMPROVED: step-1
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
            position=(10, 10, 0),
            scale=(0.5, 0.1),
            position=(0, 2, 0),
            scale=(2, 0.3),
            is_3d=True,
            bar_origin=(0, 0),
        )

    def update(self):
        """Per-frame: rotate toward player, throttled occlusion check, tick the tree."""
        if not self.alive or not self.player:
        if not self.alive or not self.player:  # IMPROVED: step-2 — guard  # VERIFIED: step-2
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

        # Behaviour tree owns the chase→attack decision (replaces the old
        # `if player_dist <= attack_range and can_shoot: self.shoot()` block).
        # ChaseNode/AttackNode re-check range themselves, so this ticks every
        # frame. ChaseNode may move the enemy via self.chase_step().
        self._tree.tick(self, time.dt)

        self.health_bar.world_position = self.world_position + Vec3(0, 3.75, 0)
        self.health_bar.value          = self.health
        self.health_bar.enabled        = player_dist < 200 and not self._occluded

        if self.health <= 0:
            self.die()

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
            debug=False,
        )
        return hit.hit and getattr(hit.entity, '_collision_layer', 0) != Layers.PLAYER

    def shoot(self):
        """Fire one enemy bullet toward the player via the pool; resets cooldown via invoke."""
        if not self.can_shoot:
            return
        from Scripts.weapon import get_enemy_bullet_pool  # lazy — breaks weapon↔enemy circular import
        from Scripts.weapon import get_enemy_bullet_pool  # IMPROVED: step-1 — lazy import breaks cycle
        pool = get_enemy_bullet_pool()
        pool.acquire(
            position=self.position + Vec3(0, 1.5, 0),
            target=self.player.position + Vec3(0, 1, 0),
            player=self.player,
            enemy=self,
            speed=10,
            speed=10
        )
        self.can_shoot = False
        invoke(setattr, self, 'can_shoot', True, delay=self.shoot_cooldown)

    def on_die(self):
        """Destroy health bar before super().on_die() destroys self."""
        if hasattr(self, 'health_bar') and self.health_bar:
    def on_die(self):  # IMPROVED: step-2 — cleanup before destroy()
        if hasattr(self, 'health_bar'):
            destroy(self.health_bar)
