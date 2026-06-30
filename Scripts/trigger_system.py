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


def _build_kill_plane(action: dict):
    """`kill_plane`: set player.health = 0 on enter.

    Player.update() checks `health <= 0` every frame and calls the idempotent
    game.trigger_game_over() — so this builder only needs to zero the health; it
    must NOT call trigger_game_over() itself (keeps the single death authority in
    the player controller).
    """
    def fire():
        player = game.player
        if player is not None:
            player.health = 0
    return fire


# action name → builder fn. Steps 3–5 add 'checkpoint', 'open_door',
# 'win_condition', 'play_sound'.
ACTION_BUILDERS = {
    'kill_plane': _build_kill_plane,
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
