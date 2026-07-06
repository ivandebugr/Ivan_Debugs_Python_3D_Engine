# trigger_system.py — v1.5 System A: invisible enter/exit trigger volumes.
#
# A TriggerZone is an invisible AABB volume that fires zero-arg callbacks when the
# player enters or leaves it. Overlap is tested per-frame with Ursina's
# Entity.intersects() — this is NOT a fourth collision authority (the three are the
# swept projectile raycast, the swept player movement, and the ground/ceiling
# raycast). intersects() reuses the same Panda3D collision traversal the engine
# already runs; a trigger never blocks movement (swept_move_blocked skips
# Layers.TRIGGER — see collision_system.py).
#
# Layers.TRIGGER is NOT a damage path: it never appears in COLLISION_MATRIX as a
# source or target, so a trigger's hit-mask is 0 and can_hit() is always False for
# it. Detection here is pure spatial overlap, not the damage system.
#
# Design split (see brain/Patterns "Editor is a config store, runtime is a factory
# consumer"): the level editor stores the raw on_enter/on_exit ACTION LISTS on its
# placeholder and builds nothing. Live zero-arg callbacks are constructed only at
# the two factory sites — load_level() in main.py and _spawn_gameplay_from_snapshot()
# in level_editor.py — by calling build_actions() on the stored lists. Never build a
# live TriggerZone with real callbacks at editor-load time.

from ursina import *

from Scripts.collision_system import AliveEntity, Layers, collision_manager
from Scripts.game import game, Game


class TriggerZone(AliveEntity):
    """Invisible AABB volume that fires callbacks on player enter / exit.

    on_enter / on_exit are lists of zero-arg callables (built from level.json
    action dicts by build_actions()). Enter fires the first frame the player's
    collider overlaps this volume; exit fires the first frame it no longer does.
    """

    def __init__(self, position, scale, on_enter=None, on_exit=None, **kwargs):
        super().__init__(
            position=position,
            scale=scale,
            collider='box',
            visible=False,      # invisible in-game — no model, no texture
            **kwargs,
        )
        # Spatial-grid registration so teardown is symmetric: AliveEntity.die()
        # calls collision_manager.remove(self). Standalone register() would leave
        # the spatial grid out of sync (see brain/Gotchas "CollisionManager.add()
        # vs register()").
        collision_manager.add(self, Layers.TRIGGER)

        self.on_enter_callbacks = on_enter or []
        self.on_exit_callbacks  = on_exit or []
        self._player_inside = False

    def update(self):
        # AliveEntity.die() is idempotent but update() can still fire the same
        # frame after die() (deferred destroy) — guard like every AliveEntity.
        if not self.alive:
            return
        # Only react while actually playing — frozen WIN/GAME_OVER/PAUSED states
        # must not re-fire a kill_plane or any other action (Hard Constraint 14).
        if game.state != Game.PLAYING:
            return

        player = game.player
        if player is None:
            return

        # intersects() traverses the scene and returns every overlapping collidable
        # in hit_info.entities. The trigger is "occupied" only when the PLAYER
        # specifically is in that list — walls overlapping the volume must not fire
        # it. (The spec's `self.intersects(player).hit` predates this: intersects()'s
        # first arg is traverse_target, not the entity to test against.)
        hit_info = self.intersects()
        inside = hit_info.hit and player in hit_info.entities

        if inside and not self._player_inside:
            for cb in self.on_enter_callbacks:
                cb()
        elif not inside and self._player_inside:
            for cb in self.on_exit_callbacks:
                cb()
        self._player_inside = inside


# --- Action builders -------------------------------------------------------
#
# Each builder takes a parsed action dict (from level.json) and returns a ZERO-ARG
# callable. The callable reads live game state (game.player, ...) at FIRE time, not
# build time, so it stays correct across respawns / scene changes. New actions
# (checkpoint, open_door, win_condition, play_sound) register here in later steps.


# TUNE: HP lost per kill-plane respawn once a checkpoint is set. 4 falls from
# full health end the run (100 -> 75 -> 50 -> 25 -> 0 = game over).
KILL_PLANE_RESPAWN_COST = 25


def _build_kill_plane(action: dict):
    """`kill_plane`: respawn at the last checkpoint at a health cost, or die.

    Pre-checkpoint (game.respawn_point is None — the player has not crossed a
    `checkpoint` trigger this session): set player.health = 0, the original
    terminal behaviour. Post-checkpoint: teleport the player back to
    respawn_point, zero their fall velocity, and charge KILL_PLANE_RESPAWN_COST
    HP — health cost chosen over brief invulnerability (see brain/Key Decisions,
    pre-v1.6 closure pass): it needs no timer state threaded through the bullet
    damage paths, and repeated falls still end the run.

    Either way this builder only ever MODIFIES HEALTH / POSITION — it must NOT
    call trigger_game_over() itself. Player.update() checks `health <= 0` every
    frame and fires the idempotent game.trigger_game_over(), so a respawn whose
    cost lands on exactly 0 dies through the same single death authority in the
    player controller (one fall too many).
    """
    def fire():
        player = game.player
        if player is None:
            return
        if game.respawn_point is None:
            # No checkpoint crossed yet — terminal, as before v1.6.
            player.health = 0
            return
        player.position = Vec3(game.respawn_point)
        player.vertical_speed = 0
        player.health = max(player.health - KILL_PLANE_RESPAWN_COST, 0)
    return fire


def _find_door(target_name: str):
    """Return the live block entity whose door_name matches, or None.

    Fire-time scan (not build-time): the door block and the trigger are built in
    the same load→start_game flow, so resolving eagerly at build time is
    order-fragile; reading live state at fire time is the trigger_system
    convention. Guard each entity with not is_empty() before reading attributes —
    this scan can run mid-frame after other entities were destroyed, and getName()
    /attribute access on an emptied NodePath fires the C++ assertion that except
    cannot catch (Hard Constraint 13 / brain/Gotchas "NodePath teardown assertion").
    """
    for e in scene.entities[:]:
        try:
            if e.is_empty():
                continue
        except Exception:
            continue
        if getattr(e, 'door_name', '') == target_name:
            return e
    return None


def _build_open_door(action: dict):
    """`open_door`: slide a named door block straight up on enter.

    Resolves the door by `target` (its door_name) at fire time. Slides the door up
    by `height` units (default: its own scale_y, so a wall-height door fully clears
    the doorway) over `duration` seconds, mirroring the existing animate_position
    idiom in weapon.py. The collider tweens WITH the entity, so the door stays solid
    geometry mid-slide — the player can still collide with it while it moves, which
    is correct (the swept/ground rays read its live position every frame).

    Fires once per door: a re-entry must not re-trigger the tween (the player could
    leave and step back into the volume). Marks the resolved entity _door_opened.
    """
    target = action.get('target')
    height = action.get('height')      # optional explicit slide distance
    duration = action.get('duration', 0.6)

    def fire():
        if not target:
            print("[trigger_system] open_door action has no 'target'; skipping")
            return
        door = _find_door(target)
        if door is None:
            print(f"[trigger_system] open_door target {target!r} not found; skipping")
            return
        if getattr(door, '_door_opened', False):
            return
        door._door_opened = True
        slide = height if height is not None else door.scale_y
        door.animate_y(door.y + slide, duration=duration, curve=curve.out_quad)
    return fire


def _build_win_condition(action: dict):
    """`win_condition`: trigger the WIN end-screen on enter.

    Delegates entirely to game.trigger_win() — which already does everything the
    spec's win_condition row describes (sets game.state = Game.WIN, freezes time,
    surfaces the mouse, hides the HUD, builds the EndScreen 'YOU WIN' overlay) and
    is the SAME path the kill-all-enemies win uses (main.update). This action must
    NOT build its own overlay: EndScreen is the single win-screen implementation and
    game.win_screen is its single owner (cleared in _clear_gameplay_entities). A
    second overlay would leak and double-render.

    trigger_win() is idempotent (only fires from PLAYING), so a trigger that re-fires
    on re-entry is harmless — no extra guard needed here.
    """
    def fire():
        game.trigger_win()
    return fire


def _build_checkpoint(action: dict):
    """`checkpoint`: snapshot player.position into game.respawn_point on enter.

    Consumed by `kill_plane` (pre-v1.6 closure pass): once respawn_point is
    set, falling into a kill plane teleports the player back here at a health
    cost instead of ending the game — see _build_kill_plane. Snapshot by value
    (Vec3(...)) so respawn_point freezes the crossing position rather than
    tracking the live player.
    """
    def fire():
        player = game.player
        if player is not None:
            game.respawn_point = Vec3(player.position)
    return fire


# action name → builder fn. 'play_sound' is the remaining spec action (deferred —
# not in this session's Steps 3–6 scope).
ACTION_BUILDERS = {
    'kill_plane':    _build_kill_plane,
    'checkpoint':    _build_checkpoint,
    'open_door':     _build_open_door,
    'win_condition': _build_win_condition,
}


def build_actions(action_list, context=None):
    """Turn a list of level.json action dicts into a list of zero-arg callables.

    Unknown actions are skipped with a printed warning rather than crashing a
    level load. `context` is reserved for actions that need build-time data the
    game singleton can't supply (e.g. open_door's target lookup in a later step).
    """
    callbacks = []
    for action in action_list or []:
        name = action.get('action')
        builder = ACTION_BUILDERS.get(name)
        if builder is None:
            print(f"[trigger_system] unknown action {name!r}; skipping")
            continue
        callbacks.append(builder(action))
    return callbacks
