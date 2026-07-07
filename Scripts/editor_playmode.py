"""
editor_playmode.py — Play-in-editor (F5) collaborator for the level editor (v1.6 split).

PlayModeController owns the F5 round-trip: snapshot the editor level
(_enter_play_mode), spawn real gameplay entities from it
(_spawn_gameplay_from_snapshot — the runtime-equivalent of main.start_game()),
tear gameplay down and rebuild the editor placeholders on exit
(_exit_play_mode / _restore_editor_level), saving and restoring the editor
camera around the trip.

State contract: the `_play_mode` flag stays on the editor — core update()/
input(), the gizmo and the asset browser all read it. The level snapshot and
saved-camera state are controller-local (nothing outside this file touches
them). Everything else is reached through the editor back-reference
(`self.editor`), same as the other v1.6 collaborators.

Invariants preserved from the monolith (CHANGELOG [1.2.x] audit fixes):
_exit_play_mode sets game.state = MAIN_MENU *before* the teardown try block,
and the teardown except is ImportError only — never a blanket Exception.
"""

from ursina import *

from Scripts.asset_resolve import resolve_model as _resolve_model, resolve_texture as _resolve_texture
from Scripts.behaviour_tree_factory import BehaviourTreeFactory
from Scripts.level_io import load_level_data
from Scripts.session_logger import get_editor_logger

logger = get_editor_logger()


def _is_live(entity) -> bool:
    """True if `entity`'s NodePath is still attached (not already destroyed).

    Same guard as main._is_live() — reading e.name/isinstance-relevant state on an
    emptied-but-unflushed NodePath fires the C++ getName() assertion (HC10).
    """
    try:
        return not entity.is_empty()
    except Exception:
        return False


class PlayModeController:
    def __init__(self, editor):
        self.editor = editor
        self._play_level_snapshot = None
        self._saved_cam_pos = None
        self._saved_cam_rot = None

    def toggle_play(self):
        if self.editor._play_mode:
            self._exit_play_mode()
        else:
            self._enter_play_mode()

    def _enter_play_mode(self):
        ed = self.editor
        self._play_level_snapshot = ed._build_level_data()
        logger.log('INFO', f'Play-in-editor started ({len(self._play_level_snapshot)} entities in snapshot)')
        if ed._editor_camera:
            self._saved_cam_pos = Vec3(ed._editor_camera.position)
            self._saved_cam_rot = Vec3(ed._editor_camera.rotation_x,
                                       ed._editor_camera.rotation_y,
                                       ed._editor_camera.rotation_z)
        ed._set_editor_ui_visible(False)
        ed.gizmo.root.enabled = False
        # Disable EditorCamera so it stops driving camera position/rotation.
        # FirstPersonController's __init__ sets camera.parent = self, taking over.
        if ed._editor_camera:
            ed._editor_camera.enabled = False
        ed._play_mode = True
        self._spawn_gameplay_from_snapshot(self._play_level_snapshot)
        mouse.locked = True
        mouse.visible = False

    def _exit_play_mode(self):
        """Tear down gameplay entities, reset game state, and restore editor UI."""
        ed = self.editor
        logger.log('INFO', 'Play-in-editor stopped')
        from Scripts.game import game, Game
        game.state = Game.MAIN_MENU  # set before teardown so guards see MAIN_MENU even if teardown raises
        # _clear_gameplay_entities is defined in main — import lazily
        try:
            from main import _clear_gameplay_entities
            _clear_gameplay_entities()
        except ImportError:
            for e in list(game.enemies):
                if getattr(e, 'alive', False):
                    e.die()
            game.enemies.clear()
            if game.player:
                destroy(game.player)
                game.player = None
        # _clear_gameplay_entities()'s name-sweep only covers level_block/level_enemy/
        # ground/main_sky/camera_pivot — it never runs main_menu()'s scene, which is
        # the only other place TriggerZone/AmmoPickup (live AliveEntitys) and parked
        # bullet-pool entities (dead-but-undestroyed AliveEntitys) get swept up. F5
        # exit is a third teardown path that needs the same cleanup done explicitly.
        from Scripts.trigger_system import TriggerZone
        from Scripts.weapon import AmmoPickup, PlayerBullet, EnemyBullet
        for e in scene.entities[:]:
            if not _is_live(e):
                continue
            if isinstance(e, TriggerZone) or isinstance(e, AmmoPickup):
                if e.alive:
                    e.die()
            elif isinstance(e, (PlayerBullet, EnemyBullet)):
                destroy(e)
        ed._play_mode = False
        self._restore_editor_level()
        ed._set_editor_ui_visible(True)
        ed.gizmo.refresh()
        mouse.locked = False
        mouse.visible = True
        # Re-enable EditorCamera; on_enable reparents camera back to the editor rig.
        if ed._editor_camera:
            ed._editor_camera.enabled = True
        if self._saved_cam_pos is not None and ed._editor_camera:
            ed._editor_camera.position = self._saved_cam_pos
            ed._editor_camera.rotation_x = self._saved_cam_rot.x
            ed._editor_camera.rotation_y = self._saved_cam_rot.y
            ed._editor_camera.rotation_z = self._saved_cam_rot.z
            self._saved_cam_pos = None
            self._saved_cam_rot = None

    def _restore_editor_level(self):
        """Rebuild editor blocks/enemies from the play snapshot after exiting play mode.

        Any entity in editor.blocks/editor.enemies that was destroyed by scene
        teardown is replaced with a fresh editor entity built from the snapshot data.
        """
        ed = self.editor
        if not self._play_level_snapshot:
            return
        # Destroy surviving refs (may still be alive if nothing cleared them)
        for e in ed.blocks + ed.enemies + ed.triggers + ed.pickups:
            if getattr(e, 'destroy_source', None) is None:
                destroy(e)
        ed.blocks.clear()
        ed.enemies.clear()
        ed.triggers.clear()
        ed.pickups.clear()
        ed.selected.clear()

        for entry in load_level_data(self._play_level_snapshot):
            if entry['type'] == 'trigger':
                # v1.5 Step 6: rebuild the editor trigger volume from the snapshot
                # so it survives the F5 play-in-editor round-trip (same role as the
                # enemy/block rebuilds below).
                ed._make_trigger_entity(
                    entry['position'], entry['scale'],
                    entry['on_enter'], entry['on_exit'],
                )
                continue
            if entry['type'] == 'pickup':
                # v1.5 Step 13: rebuild the editor pickup sphere from the snapshot
                # so it survives the F5 play-in-editor round-trip (same role as
                # the trigger rebuild above).
                ed._make_pickup_entity(entry['position'], {
                    'pickup_type': entry['pickup_type'],
                    'weapon_type': entry['weapon_type'],
                    'amount':      entry['amount'],
                })
                continue
            if entry['type'] == 'enemy':
                new_entity = Entity(
                    model='cube',
                    color=color.red,
                    scale=(1.5, 3, 1.5),
                    position=tuple(entry['position']),
                    rotation_y=entry['rotation_y'],
                    collider='box',
                    origin_y=-0.5,
                )
                new_entity.enemy_hp   = entry['hp']
                new_entity.enemy_type = entry['enemy_type']
                # v1.4 Step 8: restore behaviour-config on the rebuilt placeholder
                # so it survives the play-in-editor (F5) round-trip — same
                # attribute load_existing_level/_build_level_data use.
                new_entity.behaviour_config = entry['behaviour']
                new_entity._original_color = color.red
                ed.enemies.append(new_entity)
            else:
                new_entity = Entity(
                    model=_resolve_model(entry['model']),
                    texture=_resolve_texture(entry['texture']),
                    position=tuple(entry['position']),
                    rotation=tuple(entry['rotation']),
                    scale=tuple(entry['scale']),
                    color=color.rgb(*entry['colour']),
                    collider='box',
                )
                new_entity._original_color = new_entity.color
                ed.blocks.append(new_entity)

        logger.log('INFO', f'Editor level restored: {len(ed.blocks)} blocks, {len(ed.enemies)} enemies, {len(ed.triggers)} triggers, {len(ed.pickups)} pickups')
        ed._refresh_hierarchy()

    def _spawn_gameplay_from_snapshot(self, level_data):
        from Scripts.player_controller import Player
        from Scripts.enemy import Enemy
        from Scripts.game import game
        from Scripts.trigger_system import TriggerZone, build_actions
        from Scripts.weapon import AmmoPickup

        entries = load_level_data(level_data)

        for entry in entries:
            if entry['type'] == 'block':
                Entity(
                    model=_resolve_model(entry['model']),
                    collider='box',
                    texture=_resolve_texture(entry['texture']),
                    position=tuple(entry['position']),
                    color=color.rgb(*entry['colour']),
                    rotation=tuple(entry['rotation']),
                    scale=tuple(entry['scale']),
                    name='level_block'
                )

        game.player = Player(position=(0, 2, 0))
        game.enemies = []
        for entry in entries:
            if entry['type'] == 'enemy':
                # v1.4 Step 9: build the behaviour tree from the saved config so a
                # patrol enemy edited in the inspector actually patrols its edited
                # waypoints when played via F5 — the SAME hand-off main.py's
                # start_game() does (config is the raw {"tree":..,"waypoints":..}
                # dict; the Factory converts waypoints to Vec3 internally).
                behaviour_tree = None
                config = entry['behaviour']
                if config:
                    behaviour_tree = BehaviourTreeFactory.build(
                        config.get('tree', 'default'), config
                    )
                e = Enemy(
                    spawn_position=tuple(entry['position']),
                    player=game.player,
                    hp=entry['hp'],
                    enemy_type=entry['enemy_type'],
                    rotation_y=entry['rotation_y'],
                    behaviour_tree=behaviour_tree,
                )
                game.enemies.append(e)
        # v1.5 Step 6: factory-consume trigger entries into live TriggerZones — the
        # runtime-equivalent F5 play path, mirroring main.start_game(). build_actions
        # turns the raw action lists into zero-arg callbacks HERE (never at editor-
        # load time). TriggerZone is an AliveEntity, torn down by return-to-menu.
        for entry in entries:
            if entry['type'] == 'trigger':
                TriggerZone(
                    position=tuple(entry['position']),
                    scale=tuple(entry['scale']),
                    on_enter=build_actions(entry['on_enter']),
                    on_exit=build_actions(entry['on_exit']),
                )
        # v1.5 Step 13: factory-consume pickup entries into live AmmoPickups — the
        # F5-play-equivalent of main.start_game()'s pickup loop.
        for entry in entries:
            if entry['type'] == 'pickup':
                AmmoPickup(
                    position=tuple(entry['position']),
                    pickup_type=entry['pickup_type'],
                    weapon_type=entry['weapon_type'],
                    amount=entry['amount'],
                )
        game.start()
