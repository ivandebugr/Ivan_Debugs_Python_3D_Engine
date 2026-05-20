import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from ursina import *
from ursina.prefabs.editor_camera import EditorCamera
from panda3d.core import loadPrcFileData, AntialiasAttrib
import json
import os

from Scripts.undo_redo import (
    UndoRedoStack, PlaceEntityCommand, DeleteEntityCommand,
    MoveEntityCommand, ChangeTextureCommand, ChangeColourCommand,
    ChangePropertyCommand
)

loadPrcFileData('', 'model-cache-dir')


class LevelEditor(Entity):
    def __init__(self):
        super().__init__()
        self.blocks = []
        self.enemies = []
        self.filename = 'level.json'
        self.current_texture = 'white_cube'
        self.current_mode = 'block'
        self._play_mode = False
        self._play_level_snapshot = None
        self._editor_camera = None

        # Selection state
        self.selected = set()
        self._box_selecting = False
        self._box_start = None
        self._box_rect = None  # screen-space selection rect visual

        # Gizmo drag state
        self._gizmo_root = None
        self._gizmo_drag_axis = None
        self._gizmo_drag_start_mouse = None
        self._gizmo_drag_start_pos = None

        # Inspector / hierarchy panel refs
        self._inspector = None
        self._insp_title = None
        self._insp_fields = {}
        self._hier_panel = None
        self._hier_buttons = []

        # Grid snap
        self.snap_values = [1.0, 0.5, 0.25, None]
        self.snap_index = 0
        self.grid_snap = self.snap_values[0]

        # Bookmarks (loaded from prefs)
        self._bookmarks = {str(i): None for i in range(1, 6)}

        # Undo/redo
        self._history = UndoRedoStack()

        # Build toolbar buttons
        self.texture_button = Button(
            parent=camera.ui,
            text='Texture: White',
            scale=(.18, .05),
            position=(.35, .47),
            on_click=self.toggle_texture,
            color=color.dark_gray,
            text_scale=1.2,
            z=-1
        )

        self.enemy_button = Button(
            parent=camera.ui,
            text='Mode: Block',
            scale=(.18, .05),
            position=(.35, .41),
            on_click=self.toggle_mode,
            color=color.dark_gray,
            text_scale=1.2,
            z=-1
        )

        self.snap_button = Button(
            parent=camera.ui,
            text='Snap: 1.0',
            scale=(.18, .05),
            position=(.35, .35),
            on_click=self.cycle_snap,
            color=color.dark_gray,
            text_scale=1.2,
            z=-1
        )

        self.play_button = Button(
            parent=camera.ui,
            text='Play (F5)',
            scale=(.18, .05),
            position=(.35, .29),
            on_click=self.toggle_play,
            color=color.rgb(120, 60, 100),
            text_scale=1.2,
            z=-1
        )

        self.model_preview = Entity(
            model='cube',
            color=color.white33,
            texture=self.current_texture,
            visible=False,
            scale=(1, 1, 1))

        self._build_inspector()
        self._build_hierarchy()
        self._build_gizmo()
        self.load_existing_level()

    # -------------------------------------------------------------------------
    # Grid snap
    # -------------------------------------------------------------------------

    def cycle_snap(self):
        self.snap_index = (self.snap_index + 1) % len(self.snap_values)
        self.grid_snap = self.snap_values[self.snap_index]
        label = str(self.grid_snap) if self.grid_snap is not None else 'Off'
        self.snap_button.text = f'Snap: {label}'

    def _snap(self, position):
        if self.grid_snap is None:
            return list(position)
        return [round(p / self.grid_snap) * self.grid_snap for p in position]

    def _snap_1d(self, value):
        if self.grid_snap is None:
            return value
        return round(value / self.grid_snap) * self.grid_snap

    # -------------------------------------------------------------------------
    # Mode / texture toggles
    # -------------------------------------------------------------------------

    def toggle_mode(self):
        self.current_mode = 'enemy' if self.current_mode == 'block' else 'block'
        self.enemy_button.text = f'Mode: {self.current_mode.capitalize()}'
        self.model_preview.scale = (1.5, 3, 1.5) if self.current_mode == 'enemy' else (1, 1, 1)
        self.model_preview.texture = self.current_texture if self.current_mode == 'block' else ''

    def toggle_texture(self):
        if self.current_mode == 'block':
            if self.current_texture == 'white_cube':
                self.current_texture = 'grass'
                self.texture_button.text = 'Texture: Grass'
            else:
                self.current_texture = 'white_cube'
                self.texture_button.text = 'Texture: White'
            self.model_preview.texture = self.current_texture

    # -------------------------------------------------------------------------
    # Selection
    # -------------------------------------------------------------------------

    def _snapshot_color(self, entity):
        if not hasattr(entity, '_original_color'):
            entity._original_color = entity.color

    def _select(self, entity, additive=False):
        if not additive:
            self._deselect_all()
        if entity in (self.blocks + self.enemies):
            self._snapshot_color(entity)
            self.selected.add(entity)
            entity.color = color.orange
            self._update_inspector()
            self._update_hierarchy_highlight()

    def _deselect_all(self):
        for e in list(self.selected):
            e.color = getattr(e, '_original_color', color.white)
        self.selected.clear()
        self._update_inspector()
        self._update_hierarchy_highlight()

    def _entity_snapshot(self, e):
        is_enemy = e in self.enemies
        tex_name = ''
        if hasattr(e, 'texture') and e.texture:
            tex_name = getattr(e.texture, 'name', str(e.texture))
        return {
            'position': e.position,
            'texture': tex_name,
            'color': getattr(e, '_original_color', e.color),
            'rotation': (e.rotation_x, e.rotation_y, e.rotation_z),
            'enemy_hp': getattr(e, 'enemy_hp', 100),
            'enemy_type': getattr(e, 'enemy_type', 'default'),
            'is_enemy': is_enemy,
        }

    def _finish_box_select(self):
        if self._box_start is None:
            return
        x0, x1 = sorted([self._box_start.x, mouse.x])
        y0, y1 = sorted([self._box_start.y, mouse.y])
        for e in self.blocks + self.enemies:
            try:
                screen_pos = camera.world_to_screen(e.world_position)
                if screen_pos and x0 <= screen_pos.x <= x1 and y0 <= screen_pos.y <= y1:
                    self._snapshot_color(e)
                    self.selected.add(e)
                    e.color = color.orange
            except Exception:
                pass
        self._update_inspector()
        self._update_hierarchy_highlight()
        if self._box_rect:
            destroy(self._box_rect)
            self._box_rect = None
        self._box_start = None

    # -------------------------------------------------------------------------
    # Inspector panel
    # -------------------------------------------------------------------------

    def _build_inspector(self):
        self._inspector = Entity(
            parent=camera.ui,
            model='quad',
            color=color.rgba(0, 0, 0, 0.75),
            scale=(.22, .9),
            position=(.67, 0),
            z=-0.5
        )
        self._insp_title = Text(
            parent=self._inspector,
            text='Inspector',
            position=(0, .42),
            scale=(.055, .055),
            color=color.white
        )
        fields = [
            ('pos_x', 'Pos X'), ('pos_y', 'Pos Y'), ('pos_z', 'Pos Z'),
            ('rot_y', 'Rot Y'),
            ('scl_x', 'Scale X'), ('scl_y', 'Scale Y'), ('scl_z', 'Scale Z'),
            ('hp', 'HP'),
        ]
        self._insp_fields = {}
        for i, (key, label) in enumerate(fields):
            y_pos = .35 - i * .09
            Text(
                parent=self._inspector,
                text=label,
                position=(-.45, y_pos + .01),
                scale=(.045, .045),
                color=color.light_gray,
                origin=(-.5, 0)
            )
            field = InputField(
                parent=self._inspector,
                position=(.1, y_pos),
                scale=(.15, .04),
                default_value='0'
            )
            field.on_submit = lambda v, k=key: self._inspector_commit(k, v)
            self._insp_fields[key] = field

    def _update_inspector(self):
        if not self.selected:
            for f in self._insp_fields.values():
                f.text = ''
            return
        entities = list(self.selected)

        def shared_or_multi(getter):
            try:
                vals = [getter(e) for e in entities]
                return str(round(vals[0], 3)) if len(set(str(round(v, 3)) for v in vals)) == 1 else '---'
            except Exception:
                return '---'

        self._insp_fields['pos_x'].text = shared_or_multi(lambda e: e.x)
        self._insp_fields['pos_y'].text = shared_or_multi(lambda e: e.y)
        self._insp_fields['pos_z'].text = shared_or_multi(lambda e: e.z)
        self._insp_fields['rot_y'].text = shared_or_multi(lambda e: e.rotation_y)
        self._insp_fields['scl_x'].text = shared_or_multi(lambda e: e.scale_x)
        self._insp_fields['scl_y'].text = shared_or_multi(lambda e: e.scale_y)
        self._insp_fields['scl_z'].text = shared_or_multi(lambda e: e.scale_z)
        self._insp_fields['hp'].text    = shared_or_multi(lambda e: getattr(e, 'enemy_hp', 100))

    def _inspector_commit(self, key, value_str):
        if not self.selected or value_str in ('---', ''):
            return
        try:
            value = float(value_str)
        except ValueError:
            return
        attr_map = {
            'pos_x': 'x', 'pos_y': 'y', 'pos_z': 'z',
            'rot_y': 'rotation_y',
            'scl_x': 'scale_x', 'scl_y': 'scale_y', 'scl_z': 'scale_z',
            'hp': 'enemy_hp'
        }
        attr = attr_map.get(key)
        if not attr:
            return
        for e in self.selected:
            old = getattr(e, attr, 0)
            cmd = ChangePropertyCommand(e, attr, old, value)
            cmd.execute()
            self._history.push(cmd)

    # -------------------------------------------------------------------------
    # Hierarchy panel
    # -------------------------------------------------------------------------

    def _build_hierarchy(self):
        self._hier_panel = Entity(
            parent=camera.ui,
            model='quad',
            color=color.rgba(0, 0, 0, 0.75),
            scale=(.20, .9),
            position=(-.70, 0),
            z=-0.5
        )
        Text(
            parent=self._hier_panel,
            text='Hierarchy',
            position=(0, .42),
            scale=(.055, .055),
            color=color.white
        )
        self._hier_buttons = []

    def _refresh_hierarchy(self):
        for b in self._hier_buttons:
            destroy(b)
        self._hier_buttons.clear()
        all_entities = self.blocks + self.enemies
        for i, e in enumerate(all_entities):
            is_enemy = e in self.enemies
            label = f"{'E' if is_enemy else 'B'} ({round(e.x,1)},{round(e.y,1)},{round(e.z,1)})"
            btn = Button(
                parent=self._hier_panel,
                text=label,
                scale=(.18, .038),
                position=(0, .36 - i * .05),
                color=color.orange if e in self.selected else color.dark_gray,
                text_scale=.75,
                z=-1
            )
            btn.on_click = lambda entity=e: self._select(entity)
            self._hier_buttons.append(btn)

    def _update_hierarchy_highlight(self):
        all_entities = self.blocks + self.enemies
        for btn, e in zip(self._hier_buttons, all_entities):
            btn.color = color.orange if e in self.selected else color.dark_gray

    # -------------------------------------------------------------------------
    # Transform gizmos
    # -------------------------------------------------------------------------

    def _build_gizmo(self):
        self._gizmo_root = Entity(name='editor_gizmo_root', enabled=False)
        shaft_len = 1.2
        Entity(
            parent=self._gizmo_root,
            model=Mesh(vertices=[Vec3(0, 0, 0), Vec3(shaft_len, 0, 0)], mode='line', thickness=3),
            color=color.red,
            name='editor_gizmo_x'
        )
        Entity(
            parent=self._gizmo_root,
            model=Mesh(vertices=[Vec3(0, 0, 0), Vec3(0, shaft_len, 0)], mode='line', thickness=3),
            color=color.green,
            name='editor_gizmo_y'
        )
        Entity(
            parent=self._gizmo_root,
            model=Mesh(vertices=[Vec3(0, 0, 0), Vec3(0, 0, shaft_len)], mode='line', thickness=3),
            color=color.blue,
            name='editor_gizmo_z'
        )
        for offset, col, gname in [
            (Vec3(shaft_len, 0, 0), color.red,   'editor_gizmo_tip_x'),
            (Vec3(0, shaft_len, 0), color.green, 'editor_gizmo_tip_y'),
            (Vec3(0, 0, shaft_len), color.blue,  'editor_gizmo_tip_z'),
        ]:
            Entity(
                parent=self._gizmo_root,
                model='cube',
                color=col,
                scale=.12,
                position=offset,
                name=gname,
                collider='box'
            )

    def _update_gizmo(self):
        if not self.selected or self._play_mode:
            self._gizmo_root.enabled = False
            return
        self._gizmo_root.enabled = True
        positions = [e.position for e in self.selected]
        centroid = sum(positions, Vec3(0, 0, 0)) / len(positions)
        self._gizmo_root.position = centroid

    def _handle_gizmo_drag(self):
        axis_map = {
            'editor_gizmo_x': 'x', 'editor_gizmo_tip_x': 'x',
            'editor_gizmo_y': 'y', 'editor_gizmo_tip_y': 'y',
            'editor_gizmo_z': 'z', 'editor_gizmo_tip_z': 'z',
        }
        if held_keys['left mouse']:
            if self._gizmo_drag_axis is None:
                if mouse.hovered_entity and mouse.hovered_entity.name in axis_map:
                    self._gizmo_drag_axis = axis_map[mouse.hovered_entity.name]
                    self._gizmo_drag_start_mouse = Vec2(mouse.x, mouse.y)
                    self._gizmo_drag_start_pos = {e: Vec3(e.position) for e in self.selected}
            else:
                delta_screen = Vec2(mouse.x, mouse.y) - self._gizmo_drag_start_mouse
                sensitivity = 10.0
                for e in self.selected:
                    base = self._gizmo_drag_start_pos[e]
                    if self._gizmo_drag_axis == 'x':
                        e.x = self._snap_1d(base.x + delta_screen.x * sensitivity)
                    elif self._gizmo_drag_axis == 'y':
                        e.y = self._snap_1d(base.y + delta_screen.y * sensitivity)
                    elif self._gizmo_drag_axis == 'z':
                        e.z = self._snap_1d(base.z + delta_screen.x * sensitivity)
                self._update_gizmo()
        else:
            if self._gizmo_drag_axis is not None:
                for e in self.selected:
                    old_pos = self._gizmo_drag_start_pos[e]
                    new_pos = Vec3(e.position)
                    if old_pos != new_pos:
                        self._history.push(MoveEntityCommand(e, old_pos, new_pos))
            self._gizmo_drag_axis = None
            self._gizmo_drag_start_mouse = None
            self._gizmo_drag_start_pos = None

    # -------------------------------------------------------------------------
    # Camera bookmarks / prefs
    # -------------------------------------------------------------------------

    def _load_prefs(self):
        self._bookmarks = {str(i): None for i in range(1, 6)}
        if os.path.exists('editor_prefs.json'):
            try:
                with open('editor_prefs.json') as f:
                    prefs = json.load(f)
                self._bookmarks = prefs.get('bookmarks', self._bookmarks)
                snap_val = prefs.get('grid_snap', 1.0)
                if snap_val in self.snap_values:
                    self.snap_index = self.snap_values.index(snap_val)
                    self.grid_snap = snap_val
                    label = str(snap_val) if snap_val is not None else 'Off'
                    self.snap_button.text = f'Snap: {label}'
            except Exception:
                pass

    def _save_prefs(self):
        prefs = {
            'bookmarks': self._bookmarks,
            'grid_snap': self.grid_snap,
        }
        with open('editor_prefs.json', 'w') as f:
            json.dump(prefs, f, indent=4)

    # -------------------------------------------------------------------------
    # Play-in-editor
    # -------------------------------------------------------------------------

    def _build_level_data(self):
        data = []
        for block in self.blocks:
            r = getattr(block, '_original_color', block.color).r if block in self.selected else block.color.r
            g = getattr(block, '_original_color', block.color).g if block in self.selected else block.color.g
            b_val = getattr(block, '_original_color', block.color).b if block in self.selected else block.color.b
            actual_color = getattr(block, '_original_color', block.color)
            tex_name = ''
            if hasattr(block, 'texture') and block.texture:
                tex_name = getattr(block.texture, 'name', str(block.texture))
            data.append({
                'type': 'block',
                'position': [block.x, block.y, block.z],
                'texture': tex_name,
                'colour': [round(actual_color.r, 3), round(actual_color.g, 3), round(actual_color.b, 3)],
                'rotation': [round(block.rotation_x, 2), round(block.rotation_y, 2), round(block.rotation_z, 2)],
            })
        for enemy in self.enemies:
            data.append({
                'type': 'enemy',
                'position': [enemy.x, enemy.y, enemy.z],
                'hp': getattr(enemy, 'enemy_hp', 100),
                'enemy_type': getattr(enemy, 'enemy_type', 'default'),
                'rotation_y': round(enemy.rotation_y, 2),
            })
        return data

    def _set_editor_ui_visible(self, visible):
        for widget in [self._inspector, self._hier_panel, self._insp_title,
                       self.texture_button, self.enemy_button, self.snap_button,
                       self.play_button]:
            if widget:
                widget.enabled = visible

    def toggle_play(self):
        if self._play_mode:
            self._exit_play_mode()
        else:
            self._enter_play_mode()

    def _enter_play_mode(self):
        self._play_level_snapshot = self._build_level_data()
        self._set_editor_ui_visible(False)
        self._gizmo_root.enabled = False
        self._play_mode = True
        self._spawn_gameplay_from_snapshot(self._play_level_snapshot)
        mouse.locked = True
        mouse.visible = False

    def _exit_play_mode(self):
        from Scripts.game import game
        # _clear_gameplay_entities is defined in main — import lazily
        try:
            from main import _clear_gameplay_entities
            _clear_gameplay_entities()
        except Exception:
            # Fallback: destroy game entities directly
            for e in list(game.enemies):
                if getattr(e, 'alive', False):
                    e.die()
            game.enemies.clear()
            if game.player:
                destroy(game.player)
                game.player = None
        self._play_mode = False
        self._set_editor_ui_visible(True)
        self._update_gizmo()
        mouse.locked = False
        mouse.visible = True

    def _spawn_gameplay_from_snapshot(self, level_data):
        from Scripts.player_controller import Player
        from Scripts.enemy import Enemy
        from Scripts.game import game

        for entry in level_data:
            if entry['type'] == 'block':
                Entity(
                    model='cube',
                    collider='box',
                    texture=entry.get('texture', 'white_cube'),
                    position=entry['position'],
                    color=color.rgb(*[int(c * 255) for c in entry.get('colour', [1, 1, 1])]),
                    name='level_block'
                )

        game.player = Player(position=(0, 2, 0))
        game.enemies = []
        for entry in level_data:
            if entry['type'] == 'enemy':
                e = Enemy(
                    spawn_position=tuple(entry['position']),
                    player=game.player,
                    hp=entry.get('hp', 100),
                    enemy_type=entry.get('enemy_type', 'default'),
                    rotation_y=entry.get('rotation_y', 0),
                )
                game.enemies.append(e)
        game.start()

    # -------------------------------------------------------------------------
    # Preview and update
    # -------------------------------------------------------------------------

    def update_model_preview(self):
        if mouse.hovered_entity and mouse.hovered_entity.collider:
            if mouse.hovered_entity.name.startswith('editor_gizmo'):
                self.model_preview.visible = False
                return
            preview_position = mouse.hovered_entity.position + mouse.normal
            preview_position = self._snap(preview_position)

            if self.current_mode == 'enemy':
                preview_position[1] += 1

            self.model_preview.position = preview_position
            self.model_preview.visible = True
        else:
            self.model_preview.visible = False

    def update(self):
        if not self._play_mode:
            self.update_model_preview()
            self._handle_gizmo_drag()
            self._update_gizmo()

    def input(self, key):
        if self._play_mode:
            if key in ('f5', 'escape'):
                self._exit_play_mode()
            return

        if key == 'f5':
            self._enter_play_mode()
            return

        # Snap cycle is now on the button; keep 'g' as keyboard shortcut too
        if key == 'g':
            self.cycle_snap()

        if key == 's' and held_keys['control']:
            self.save_level()

        # Undo / Redo
        if key == 'z' and held_keys['control'] and held_keys['shift']:
            self._history.redo()
        elif key == 'z' and held_keys['control']:
            self._history.undo()
        if key == 'y' and held_keys['control']:
            self._history.redo()

        # Camera bookmarks — save
        for i in range(1, 6):
            if key == str(i) and held_keys['control']:
                if self._editor_camera:
                    self._bookmarks[str(i)] = {
                        'position': list(self._editor_camera.position),
                        'rotation': list(self._editor_camera.rotation)
                    }
                    self._save_prefs()
                    Text(f'[{i}] Saved', parent=camera.ui,
                         position=(-.6, .4 - i * .05),
                         duration=2, color=color.green, scale=1.5, z=-1)
                break

        # Camera bookmarks — recall (only when not in a control combo)
        if not held_keys['control']:
            for i in range(1, 6):
                if key == str(i):
                    bm = self._bookmarks.get(str(i))
                    if bm and self._editor_camera:
                        self._editor_camera.position = bm['position']
                        self._editor_camera.rotation = bm['rotation']
                    break

        # Delete selected
        if key == 'delete' and self.selected:
            for e in list(self.selected):
                snapshot = self._entity_snapshot(e)
                cmd = DeleteEntityCommand(self, e, snapshot)
                cmd.execute()
                self._history.push(cmd)
            self.selected.clear()
            self._update_inspector()
            self._refresh_hierarchy()
            self._update_gizmo()
            return

        # Place / select with left mouse
        if key == 'left mouse down':
            hovered = mouse.hovered_entity
            # Gizmo drag is handled in _handle_gizmo_drag(); skip placement if on gizmo
            if hovered and hovered.name.startswith('editor_gizmo'):
                return

            if held_keys['shift']:
                if hovered in (self.blocks + self.enemies):
                    self._select(hovered, additive=True)
                elif hovered in self.selected:
                    # shift-click selected entity deselects it
                    self.selected.discard(hovered)
                    hovered.color = getattr(hovered, '_original_color', color.white)
                    self._update_inspector()
                    self._update_hierarchy_highlight()
            else:
                if hovered in (self.blocks + self.enemies):
                    self._select(hovered)
                elif hovered and hovered.collider:
                    # Place entity on surface
                    position = hovered.position + mouse.normal
                    position = self._snap(position)

                    if self.current_mode == 'enemy':
                        if not self.position_valid(position):
                            return
                        new_entity = Entity(
                            model='cube',
                            color=color.red,
                            texture='white_cube',
                            scale=(1.5, 3, 1.5),
                            position=position,
                            collider='box',
                            origin_y=-0.5
                        )
                        new_entity.enemy_hp = 100
                        new_entity.enemy_type = 'default'
                        new_entity._original_color = color.red
                        self.enemies.append(new_entity)
                        cmd = PlaceEntityCommand(self, new_entity)
                        self._history.push(cmd)
                    else:
                        new_entity = Entity(
                            model='cube',
                            texture=self.current_texture,
                            collider='box',
                            position=position
                        )
                        new_entity._original_color = color.white
                        self.blocks.append(new_entity)
                        cmd = PlaceEntityCommand(self, new_entity)
                        self._history.push(cmd)
                    self._refresh_hierarchy()
                else:
                    self._deselect_all()

        # Box-select with right mouse drag
        if key == 'right mouse down':
            self._box_selecting = True
            self._box_start = Vec2(mouse.x, mouse.y)

        if key == 'right mouse up' and self._box_selecting:
            self._box_selecting = False
            self._finish_box_select()

    # -------------------------------------------------------------------------
    # Position validity
    # -------------------------------------------------------------------------

    def position_valid(self, position):
        for y_offset in [0, 1]:
            check_pos = (position[0], position[1] + y_offset, position[2])
            if any(e.position == check_pos for e in self.blocks + self.enemies):
                return False
        return True

    # -------------------------------------------------------------------------
    # Save / Load
    # -------------------------------------------------------------------------

    def save_level(self):
        data = self._build_level_data()

        seen = set()
        deduped = []
        for item in data:
            key = (item['type'], tuple(round(p, 3) for p in item['position']))
            if key not in seen:
                seen.add(key)
                deduped.append(item)

        with open(self.filename, 'w') as f:
            json.dump(deduped, f, indent=4)
        print(f'Saved level to {self.filename} ({len(deduped)} entries)')
        self._history.clear()
        self._save_prefs()

    def load_existing_level(self):
        for e in self.blocks + self.enemies:
            destroy(e)
        self.blocks.clear()
        self.enemies.clear()
        self.selected.clear()

        try:
            with open(self.filename, 'r') as f:
                entities = json.load(f)
            for entity_data in entities:
                if entity_data.get('type') == 'enemy':
                    new_entity = Entity(
                        model='cube',
                        color=color.red,
                        scale=(1.5, 3, 1.5),
                        position=entity_data['position'],
                        rotation_y=entity_data.get('rotation_y', 0),
                        collider='box',
                        origin_y=-0.5
                    )
                    new_entity.enemy_hp   = entity_data.get('hp', 100)
                    new_entity.enemy_type = entity_data.get('enemy_type', 'default')
                    new_entity._original_color = color.red
                    self.enemies.append(new_entity)
                else:
                    new_entity = Entity(
                        model='cube',
                        texture=entity_data.get('texture', 'white_cube'),
                        position=entity_data['position'],
                        rotation=tuple(entity_data.get('rotation', [0, 0, 0])),
                        color=color.rgb(*[int(c * 255) for c in entity_data.get('colour', [1, 1, 1])]),
                        collider='box'
                    )
                    new_entity._original_color = new_entity.color
                    self.blocks.append(new_entity)
        except FileNotFoundError:
            print("No level file found")

        self._refresh_hierarchy()
        self._history.clear()
        # Load prefs after snap_button exists
        self._load_prefs()


if __name__ == '__main__':
    loadPrcFileData('', 'framebuffer-multisample 1\nmultisamples 4')
    app = Ursina(title="Level Editor")

    # Shader patch applied before any Entity — must not be called again in play-in-editor
    # (level_editor.py already applies it at startup; _spawn_gameplay_from_snapshot relies on this)
    def _patch_shaders_to_glsl120():
        from ursina.shaders.unlit_shader import unlit_shader as _us
        from ursina.shaders.unlit_with_fog_shader import unlit_with_fog_shader as _ufs
        _us.vertex = (
            '#version 120\n'
            'uniform mat4 p3d_ModelViewProjectionMatrix;\n'
            'uniform mat4 p3d_ModelViewMatrix;\n'
            'uniform mat4 p3d_ModelMatrix;\n'
            'attribute vec4 p3d_Vertex;\n'
            'attribute vec2 p3d_MultiTexCoord0;\n'
            'varying vec2 uvs;\n'
            'uniform vec2 texture_scale;\n'
            'uniform vec2 texture_offset;\n'
            'attribute vec4 p3d_Color;\n'
            'varying vec4 vertex_color;\n'
            'void main() {\n'
            '    gl_Position = p3d_ModelViewProjectionMatrix * p3d_Vertex;\n'
            '    uvs = (p3d_MultiTexCoord0 * texture_scale) + texture_offset;\n'
            '    vertex_color = p3d_Color;\n'
            '}\n'
        )
        _us.fragment = (
            '#version 120\n'
            'uniform sampler2D p3d_Texture0;\n'
            'uniform vec4 p3d_ColorScale;\n'
            'varying vec2 uvs;\n'
            'varying vec4 vertex_color;\n'
            'void main() {\n'
            '    gl_FragColor = texture2D(p3d_Texture0, uvs) * p3d_ColorScale * vertex_color;\n'
            '}\n'
        )
        _us.compiled = False
        _ufs.vertex = (
            '#version 120\n'
            'uniform mat4 p3d_ModelViewProjectionMatrix;\n'
            'uniform mat4 p3d_ModelViewMatrix;\n'
            'uniform mat4 p3d_ModelMatrix;\n'
            'attribute vec4 p3d_Vertex;\n'
            'attribute vec2 p3d_MultiTexCoord0;\n'
            'varying vec2 uvs;\n'
            'uniform vec2 texture_scale;\n'
            'uniform vec2 texture_offset;\n'
            'attribute vec4 p3d_Color;\n'
            'varying vec4 vertex_color;\n'
            'varying vec3 vertex_world_position;\n'
            'void main() {\n'
            '    gl_Position = p3d_ModelViewProjectionMatrix * p3d_Vertex;\n'
            '    uvs = (p3d_MultiTexCoord0 * texture_scale) + texture_offset;\n'
            '    vertex_color = p3d_Color;\n'
            '    vertex_world_position = (p3d_ModelMatrix * p3d_Vertex).xyz;\n'
            '}\n'
        )
        _ufs.fragment = (
            '#version 120\n'
            'uniform sampler2D p3d_Texture0;\n'
            'uniform vec4 p3d_ColorScale;\n'
            'varying vec2 uvs;\n'
            'varying vec4 vertex_color;\n'
            'varying vec3 vertex_world_position;\n'
            'uniform vec3 camera_world_position;\n'
            'uniform vec4 fog_color;\n'
            'uniform float fog_start;\n'
            'uniform float fog_end;\n'
            'void main() {\n'
            '    vec4 fragColor = texture2D(p3d_Texture0, uvs) * p3d_ColorScale * vertex_color;\n'
            '    float distance_to_camera = length(vertex_world_position.xyz - camera_world_position);\n'
            '    float fog_length = fog_end - fog_start;\n'
            '    float t = clamp(distance_to_camera / fog_length, 0.0, 1.0);\n'
            '    fragColor.rgb = mix(fragColor.rgb, fog_color.rgb, t * fog_color.a);\n'
            '    gl_FragColor = fragColor;\n'
            '}\n'
        )
        _ufs.compiled = False
    _patch_shaders_to_glsl120()

    window.color = color.rgb(50, 50, 60)
    render.setAntialias(AntialiasAttrib.MAuto)
    render2d.setAntialias(AntialiasAttrib.MAuto)
    window.title = 'Level Editor'
    window.exit_button.visible = True
    window.fps_counter.enabled = True
    window.borderless = False
    window.size = (1280, 720)

    ground = Entity(
        model='plane',
        collider='box',
        y=-0.5,
        scale=(100, 1, 100),
        texture='grass'
    )

    editor = LevelEditor()
    editor_cam = EditorCamera()
    editor._editor_camera = editor_cam

    Text(
        text="LClick: Place/Select | Shift+LClick: Multi-select | RDrag: Box-select\n"
             "Delete: Remove selected | Ctrl+Z: Undo | Ctrl+Y/Shift+Z: Redo\n"
             "Ctrl+S: Save | G: Cycle snap | F5: Play-in-editor\n"
             "Ctrl+1-5: Save cam bookmark | 1-5: Recall bookmark",
        parent=camera.ui,
        position=(-.97, .48),
        origin=(-.5, .5),
        scale=0.75,
        z=-1
    )

    app.run()
