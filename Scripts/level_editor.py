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
    ASSETS = [
        {'name': 'Cube',  'model': 'cube', 'color': (80, 120, 200),  'scale': (1, 1, 1),     'type': 'block'},
        {'name': 'Stone', 'model': 'cube', 'color': (110, 110, 110), 'scale': (1, 1, 1),     'type': 'block'},
        {'name': 'Metal', 'model': 'cube', 'color': (160, 160, 180), 'scale': (1, 1, 1),     'type': 'block'},
        {'name': 'Wood',  'model': 'cube', 'color': (160, 100, 60),  'scale': (1, 1, 1),     'type': 'block'},
        {'name': 'Enemy', 'model': 'cube', 'color': (200, 60, 60),   'scale': (1.5, 3, 1.5), 'type': 'enemy'},
    ]

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
        self._hier_scroll_bar = None
        self.hierarchy_scroll = 0

        # Grid snap
        self.snap_values = [1.0, 0.5, 0.25, None]
        self.snap_index = 0
        self.grid_snap = self.snap_values[0]

        # Bookmarks (loaded from prefs)
        self._bookmarks = {str(i): None for i in range(1, 6)}

        # Undo/redo
        self._history = UndoRedoStack()

        # Drag-and-drop state
        self._drag_asset = None    # asset dict currently being dragged
        self._drag_ghost = None    # semi-transparent ghost Entity
        self._dragging = False     # True once mouse moves after tile click
        self._drag_origin = None   # Vec2 mouse pos when tile was pressed

        # Asset tray scroll offset (number of tile widths shifted left)
        self._tray_scroll = 0

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

        self.snap_button = Button(
            parent=camera.ui,
            text='Snap: 1.0',
            scale=(.18, .05),
            position=(.35, .41),
            on_click=self.cycle_snap,
            color=color.dark_gray,
            text_scale=1.2,
            z=-1
        )

        self.play_button = Button(
            parent=camera.ui,
            text='Play (F5)',
            scale=(.18, .05),
            position=(.35, .35),
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
        self._build_asset_tray()
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
    # Texture toggle
    # -------------------------------------------------------------------------

    def toggle_texture(self):
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
        # 8 rows distributed evenly in the usable area below the title.
        # Usable: from +0.38 to -0.43  →  height = 0.81 units (in panel-local space).
        # Transform group: rows 0-6 (pos x/y/z, rot y, scale x/y/z)
        # Entity group: rows 7+ (HP)
        # Separator drawn between group boundary.
        fields = [
            ('pos_x', 'Pos X'), ('pos_y', 'Pos Y'), ('pos_z', 'Pos Z'),
            ('rot_y', 'Rot Y'),
            ('scl_x', 'Scale X'), ('scl_y', 'Scale Y'), ('scl_z', 'Scale Z'),
            ('hp', 'HP'),
        ]
        _panel_top = 0.38
        _panel_height = 0.81
        _num_rows = len(fields)
        # Index of first entity-group row (HP and beyond)
        _entity_group_start = 7
        self._insp_fields = {}
        for i, (key, label) in enumerate(fields):
            y_pos = _panel_top - (i + 0.5) * (_panel_height / _num_rows)
            # Separator line between transform and entity groups
            if i == _entity_group_start:
                sep_y = _panel_top - i * (_panel_height / _num_rows)
                Entity(
                    parent=self._inspector,
                    model=Mesh(
                        vertices=[Vec3(-0.48, sep_y, 0), Vec3(0.48, sep_y, 0)],
                        mode='line', thickness=1
                    ),
                    color=color.rgba(180, 180, 180, 80),
                    z=-1
                )
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

    # Hierarchy layout constants (panel-local space)
    _HIER_TOP    = 0.36   # y of first row
    _HIER_ROW_H  = 0.05   # row pitch
    _HIER_MAX_VISIBLE = 14

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
        # Thin vertical scroll indicator — right edge of panel
        self._hier_scroll_bar = Entity(
            parent=self._hier_panel,
            model='quad',
            color=color.rgba(200, 200, 200, 120),
            scale=(.018, .05),
            position=(.46, self._HIER_TOP),
            z=-1
        )
        self._hier_buttons = []

    def _refresh_hierarchy(self):
        for b in self._hier_buttons:
            destroy(b)
        self._hier_buttons.clear()
        all_entities = self.blocks + self.enemies
        total = len(all_entities)
        max_scroll = max(0, total - self._HIER_MAX_VISIBLE)
        self.hierarchy_scroll = max(0, min(self.hierarchy_scroll, max_scroll))

        visible_slice = all_entities[self.hierarchy_scroll: self.hierarchy_scroll + self._HIER_MAX_VISIBLE]
        for row, e in enumerate(visible_slice):
            is_enemy = e in self.enemies
            label = f"{'E' if is_enemy else 'B'} ({round(e.x,1)},{round(e.y,1)},{round(e.z,1)})"
            btn = Button(
                parent=self._hier_panel,
                text=label,
                scale=(.18, .038),
                position=(-.01, self._HIER_TOP - row * self._HIER_ROW_H),
                color=color.orange if e in self.selected else color.dark_gray,
                text_scale=.75,
                z=-1
            )
            btn.on_click = lambda entity=e: self._select(entity)
            self._hier_buttons.append(btn)

        self._update_hier_scroll_bar(total)

    def _update_hier_scroll_bar(self, total):
        if not self._hier_scroll_bar:
            return
        if total <= self._HIER_MAX_VISIBLE:
            self._hier_scroll_bar.enabled = False
            return
        self._hier_scroll_bar.enabled = True
        # Track height spans from _HIER_TOP down to the last row bottom
        track_top    = self._HIER_TOP
        track_bottom = self._HIER_TOP - self._HIER_MAX_VISIBLE * self._HIER_ROW_H
        track_h      = track_top - track_bottom  # positive

        thumb_ratio  = self._HIER_MAX_VISIBLE / total
        thumb_h      = max(0.03, track_h * thumb_ratio)
        max_scroll   = total - self._HIER_MAX_VISIBLE
        scroll_frac  = self.hierarchy_scroll / max_scroll if max_scroll > 0 else 0
        # Centre of thumb travels from track_top - thumb_h/2  down to  track_bottom + thumb_h/2
        travel       = track_h - thumb_h
        thumb_centre = track_top - thumb_h / 2 - scroll_frac * travel

        self._hier_scroll_bar.scale_y = thumb_h
        self._hier_scroll_bar.y       = thumb_centre

    def _update_hierarchy_highlight(self):
        all_entities = self.blocks + self.enemies
        visible_slice = all_entities[self.hierarchy_scroll: self.hierarchy_scroll + self._HIER_MAX_VISIBLE]
        for btn, e in zip(self._hier_buttons, visible_slice):
            btn.color = color.orange if e in self.selected else color.dark_gray

    def _is_over_panel(self, panel):
        """Return True if the mouse cursor is currently over the given UI panel quad."""
        mx, my = mouse.x, mouse.y
        px, py = panel.x, panel.y
        hw = panel.scale_x * 0.5
        hh = panel.scale_y * 0.5
        return (px - hw) <= mx <= (px + hw) and (py - hh) <= my <= (py + hh)

    # -------------------------------------------------------------------------
    # Asset tray
    # -------------------------------------------------------------------------

    # Tray layout constants
    _TRAY_Y       = -0.45   # centre y of the tray panel in camera.ui space
    _TRAY_H       =  0.12   # panel height
    _TILE_SIZE    =  0.09   # tile width and height in camera.ui space
    _TILE_GAP     =  0.01   # horizontal gap between tiles
    _TILE_PITCH   =  0.10   # _TILE_SIZE + _TILE_GAP

    def _build_asset_tray(self):
        self._tray_panel = Entity(
            parent=camera.ui,
            model='quad',
            color=color.rgba(25, 25, 31, 220),
            scale=(2.0, self._TRAY_H),
            position=(0, self._TRAY_Y),
            z=-0.5
        )

        self._tray_tiles = []   # list of (bg_entity, icon_entity, label_entity, asset_dict)
        assets = LevelEditor.ASSETS
        for i, asset in enumerate(assets):
            self._create_tray_tile(i, asset)

    def _tile_x(self, index):
        """Camera.ui x-position of tile centre at given index, accounting for scroll."""
        first_x = -(len(LevelEditor.ASSETS) - 1) * self._TILE_PITCH * 0.5
        return first_x + index * self._TILE_PITCH - self._tray_scroll * self._TILE_PITCH

    def _create_tray_tile(self, index, asset):
        asset_color = color.rgb(*asset['color'])
        tx = self._tile_x(index)

        bg = Entity(
            parent=camera.ui,
            model='quad',
            color=color.rgba(50, 50, 60, 200),
            scale=(self._TILE_SIZE, self._TILE_SIZE),
            position=(tx, self._TRAY_Y),
            z=-0.6
        )
        icon = Entity(
            parent=camera.ui,
            model='quad',
            color=asset_color,
            scale=(self._TILE_SIZE * 0.65, self._TILE_SIZE * 0.65),
            position=(tx, self._TRAY_Y + 0.01),
            z=-0.7
        )
        label = Text(
            parent=camera.ui,
            text=asset['name'],
            position=(tx, self._TRAY_Y - self._TILE_SIZE * 0.42),
            scale=0.55,
            color=color.light_gray,
            origin=(0, 0),
            z=-0.7
        )
        self._tray_tiles.append((bg, icon, label, asset))

    def _refresh_tray_positions(self):
        for i, (bg, icon, label, asset) in enumerate(self._tray_tiles):
            tx = self._tile_x(i)
            bg.x    = tx
            icon.x  = tx
            label.x = tx

    def _is_over_tray(self):
        my = mouse.y
        return (self._TRAY_Y - self._TRAY_H * 0.5) <= my <= (self._TRAY_Y + self._TRAY_H * 0.5)

    def _tile_at_mouse(self):
        """Return asset dict for the tile the mouse is over, or None."""
        mx, my = mouse.x, mouse.y
        if not self._is_over_tray():
            return None
        for i, (bg, icon, label, asset) in enumerate(self._tray_tiles):
            tx = self._tile_x(i)
            hw = self._TILE_SIZE * 0.5
            hh = self._TILE_SIZE * 0.5
            if (tx - hw) <= mx <= (tx + hw) and (self._TRAY_Y - hh) <= my <= (self._TRAY_Y + hh):
                return asset
        return None

    def _highlight_hovered_tile(self):
        """Brighten the tile the mouse is hovering over."""
        for i, (bg, icon, label, asset) in enumerate(self._tray_tiles):
            tx = self._tile_x(i)
            hw = self._TILE_SIZE * 0.5
            mx, my = mouse.x, mouse.y
            hovered = (tx - hw) <= mx <= (tx + hw) and (self._TRAY_Y - hw) <= my <= (self._TRAY_Y + hw)
            bg.color = color.rgba(80, 80, 100, 220) if hovered else color.rgba(50, 50, 60, 200)

    def _begin_drag(self, asset):
        self._drag_asset = asset
        self._drag_ghost = None   # created lazily when mouse moves over viewport

    def _update_ghost(self):
        """Move ghost to snapped raycast hit point; create it if needed."""
        asset = self._drag_asset
        if asset is None:
            return

        ignore_list = [self._drag_ghost] if self._drag_ghost else []
        # mouse.direction was removed in Ursina 8.3.0; derive ray from screen coords
        ray_dir = (
            camera.forward
            + camera.right * mouse.x * (camera.fov / camera.aspect_ratio) * 0.017453
            + camera.up    * mouse.y * camera.fov * 0.017453
        ).normalized()
        hit = raycast(
            camera.world_position,
            ray_dir,
            distance=200,
            ignore=ignore_list,
            traverse_target=scene,
        )

        if not hit.hit or hit.entity is None:
            if self._drag_ghost:
                self._drag_ghost.visible = False
            return

        # Compute placement position
        pos = list(hit.entity.position + hit.normal)
        pos = self._snap(pos)
        if asset['type'] == 'enemy':
            pos[1] += 1

        asset_color = color.rgb(*asset['color'])
        ghost_color = Color(asset_color.r, asset_color.g, asset_color.b, 0.5)

        if self._drag_ghost is None:
            self._drag_ghost = Entity(
                model=asset['model'],
                color=ghost_color,
                scale=asset['scale'],
                position=pos,
                collider=None,
                name='editor_drag_ghost'
            )
            if asset['type'] == 'enemy':
                self._drag_ghost.origin_y = -0.5
        else:
            self._drag_ghost.visible = True
            self._drag_ghost.position = pos

    def _cancel_drag(self):
        if self._drag_ghost:
            destroy(self._drag_ghost)
            self._drag_ghost = None
        self._drag_asset = None
        self._dragging = False
        self._drag_origin = None

    def _commit_drag(self):
        """Place entity at ghost position and push undo command."""
        asset = self._drag_asset
        ghost = self._drag_ghost

        if ghost is None or not ghost.visible or asset is None:
            self._cancel_drag()
            return

        pos = ghost.position
        asset_color = color.rgb(*asset['color'])

        if asset['type'] == 'enemy':
            new_entity = Entity(
                model=asset['model'],
                color=asset_color,
                scale=asset['scale'],
                position=pos,
                collider='box',
                origin_y=-0.5
            )
            new_entity.enemy_hp = 100
            new_entity.enemy_type = 'default'
            new_entity._original_color = asset_color
            self.enemies.append(new_entity)
        else:
            new_entity = Entity(
                model=asset['model'],
                color=asset_color,
                scale=asset['scale'],
                position=pos,
                collider='box'
            )
            new_entity._original_color = asset_color
            self.blocks.append(new_entity)

        cmd = PlaceEntityCommand(self, new_entity)
        self._history.push(cmd)
        self._refresh_hierarchy()

        destroy(ghost)
        self._drag_ghost = None
        self._drag_asset = None
        self._dragging = False
        self._drag_origin = None

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
        centroid = Vec3(0, 0, 0)
        for p in positions:
            centroid += p
        centroid /= len(positions)
        self._gizmo_root.position = centroid

    def _handle_gizmo_drag(self):
        axis_map = {
            'editor_gizmo_x': 'x', 'editor_gizmo_tip_x': 'x',
            'editor_gizmo_y': 'y', 'editor_gizmo_tip_y': 'y',
            'editor_gizmo_z': 'z', 'editor_gizmo_tip_z': 'z',
        }
        _world_axes = {'x': Vec3(1, 0, 0), 'y': Vec3(0, 1, 0), 'z': Vec3(0, 0, 1)}
        if held_keys['left mouse']:
            if self._gizmo_drag_axis is None:
                if mouse.hovered_entity and mouse.hovered_entity.name in axis_map:
                    self._gizmo_drag_axis = axis_map[mouse.hovered_entity.name]
                    self._gizmo_drag_start_mouse = Vec2(mouse.x, mouse.y)
                    self._gizmo_drag_start_pos = {e: Vec3(e.position) for e in self.selected}
            else:
                # Project the world axis into screen space to determine drag sign.
                # camera.getRelativePoint gives the axis endpoint in camera-local space;
                # the 2D difference is the screen-space projection of that axis.
                axis_world = _world_axes[self._gizmo_drag_axis]
                cam_origin = camera.getRelativePoint(render, Vec3(0, 0, 0))
                cam_tip    = camera.getRelativePoint(render, axis_world)
                axis_screen = Vec2(cam_tip.x - cam_origin.x, cam_tip.y - cam_origin.y)
                axis_len = axis_screen.length()
                if axis_len > 0.0001:
                    axis_screen /= axis_len
                else:
                    axis_screen = Vec2(1, 0)

                mouse_vel = Vec2(mouse.velocity[0], mouse.velocity[1])
                magnitude = mouse_vel.dot(axis_screen) * 200.0  # scale velocity → world units

                for e in self.selected:
                    raw = getattr(e, self._gizmo_drag_axis) + magnitude
                    setattr(e, self._gizmo_drag_axis, self._snap_1d(raw))
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
                       self.texture_button, self.snap_button,
                       self.play_button, self._tray_panel]:
            if widget:
                widget.enabled = visible
        for bg, icon, label, asset in self._tray_tiles:
            bg.enabled    = visible
            icon.enabled  = visible
            label.enabled = visible

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
        from Scripts.game import game, Game
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
        game.state = Game.MAIN_MENU
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
                    rotation=tuple(entry.get('rotation', [0, 0, 0])),
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
            self.model_preview.position = preview_position
            self.model_preview.visible = True
        else:
            self.model_preview.visible = False

    def update(self):
        if not self._play_mode:
            if self._dragging:
                self._update_ghost()
                # Suppress normal model preview while dragging
                self.model_preview.visible = False
            else:
                self.update_model_preview()
                self._highlight_hovered_tile()
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

        # Cancel drag with Esc
        if key == 'escape' and self._dragging:
            self._cancel_drag()
            return

        # Asset tray: begin drag on left mouse down over a tile
        if key == 'left mouse down':
            tile_asset = self._tile_at_mouse()
            if tile_asset is not None:
                self._drag_origin = Vec2(mouse.x, mouse.y)
                self._dragging = True
                self._begin_drag(tile_asset)
                return

        # Commit or cancel drag on left mouse up
        if key == 'left mouse up' and self._dragging:
            if self._is_over_tray():
                self._cancel_drag()
            else:
                self._commit_drag()
            return

        # Place / select with left mouse (only when not dragging from tray)
        if key == 'left mouse down':
            # Guard: ignore clicks while a gizmo drag or box-select is in progress
            if self._gizmo_drag_axis is not None or self._box_selecting:
                return

            hovered = mouse.hovered_entity

            # Skip if a UI widget (button, panel) consumed the click
            if hovered and getattr(hovered, 'parent', None) == camera.ui:
                return

            # Gizmo drag is handled in _handle_gizmo_drag(); skip placement if on gizmo
            if hovered and hovered.name.startswith('editor_gizmo'):
                return

            if held_keys['shift']:
                # Shift+click: add/remove from selection
                if hovered in (self.blocks + self.enemies):
                    if hovered in self.selected:
                        self.selected.discard(hovered)
                        hovered.color = getattr(hovered, '_original_color', color.white)
                        self._update_inspector()
                        self._update_hierarchy_highlight()
                    else:
                        self._select(hovered, additive=True)
                elif hovered and hovered.collider:
                    # shift-click on a non-tracked surface (e.g. ground) — no-op
                    pass
                else:
                    self._deselect_all()
            else:
                # Priority: clicking an entity selects it
                if hovered in (self.blocks + self.enemies):
                    self._select(hovered)
                    return

                # Priority: first click on empty space when something is selected = deselect only
                if self.selected:
                    self._deselect_all()
                    return

                # Nothing selected and clicking a collidable surface = place new block
                if hovered and hovered.collider:
                    position = hovered.position + mouse.normal
                    position = self._snap(position)

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

        # Hierarchy scroll — mouse wheel while cursor over the hierarchy panel or inspector
        if key in ('scroll up', 'scroll down'):
            if self._hier_panel and self._is_over_panel(self._hier_panel):
                total = len(self.blocks) + len(self.enemies)
                max_scroll = max(0, total - self._HIER_MAX_VISIBLE)
                delta = -1 if key == 'scroll up' else 1
                self.hierarchy_scroll = max(0, min(self.hierarchy_scroll + delta, max_scroll))
                self._refresh_hierarchy()
                self._update_hierarchy_highlight()
                return

            # Inspector panel — suppress camera zoom when cursor is over it
            if self._inspector and self._is_over_panel(self._inspector):
                return

            # Tray horizontal scroll — mouse wheel while cursor over tray
            if self._is_over_tray():
                max_scroll = max(0, len(LevelEditor.ASSETS) - 1)
                delta = -1 if key == 'scroll up' else 1
                self._tray_scroll = max(0, min(self._tray_scroll + delta, max_scroll))
                self._refresh_tray_positions()
                return

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

    # Patch shader source objects BEFORE Ursina() — Ursina's own window/UI entities
    # compile shaders during __init__, so the patch must happen before App() runs.
    # Important: ursina.shader.imported_shaders holds different object instances than direct imports.
    # We patch every live instance reachable by entity.py and clear ._shader on pre-compiled ones.
    def _patch_shader_obj(obj, vertex_src, fragment_src):
        obj.vertex = vertex_src
        obj.fragment = fragment_src
        obj.compiled = False
        if hasattr(obj, '_shader'):
            del obj._shader

    _UNLIT_VERT = (
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
    _UNLIT_FRAG = (
        '#version 120\n'
        'uniform sampler2D p3d_Texture0;\n'
        'uniform vec4 p3d_ColorScale;\n'
        'varying vec2 uvs;\n'
        'varying vec4 vertex_color;\n'
        'void main() {\n'
        '    gl_FragColor = texture2D(p3d_Texture0, uvs) * p3d_ColorScale * vertex_color;\n'
        '}\n'
    )
    _UFS_VERT = (
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
    _UFS_FRAG = (
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
    _TEXT_VERT = (
        '#version 120\n'
        'uniform mat4 p3d_ModelViewProjectionMatrix;\n'
        'attribute vec4 p3d_Vertex;\n'
        'attribute vec2 p3d_MultiTexCoord0;\n'
        'varying vec2 uvs;\n'
        'attribute vec4 p3d_Color;\n'
        'varying vec4 vertex_color;\n'
        'void main() {\n'
        '    gl_Position = p3d_ModelViewProjectionMatrix * p3d_Vertex;\n'
        '    uvs = p3d_MultiTexCoord0;\n'
        '    vertex_color = p3d_Color;\n'
        '}\n'
    )
    _TEXT_FRAG = (
        '#version 120\n'
        'uniform sampler2D p3d_Texture0;\n'
        'uniform vec4 p3d_ColorScale;\n'
        'uniform vec4 outline_color;\n'
        'uniform vec2 outline_offset;\n'
        'uniform float outline_power;\n'
        'varying vec2 uvs;\n'
        'varying vec4 vertex_color;\n'
        'void main() {\n'
        '    float dist = texture2D(p3d_Texture0, uvs).a;\n'
        '    vec2 width = vec2(0.5-fwidth(dist), 0.5+fwidth(dist));\n'
        '    float alpha = smoothstep(width.x, width.y, dist);\n'
        '    float scale = 0.354;\n'
        '    vec2 duv = scale * (dFdx(uvs) + dFdy(uvs));\n'
        '    vec4 box = vec4(uvs-duv, uvs+duv);\n'
        '    alpha += 0.5*(smoothstep(width.x, width.y, texture2D(p3d_Texture0, box.xy).a)\n'
        '            +smoothstep(width.x, width.y, texture2D(p3d_Texture0, box.zw).a)\n'
        '            +smoothstep(width.x, width.y, texture2D(p3d_Texture0, box.xw).a)\n'
        '            +smoothstep(width.x, width.y, texture2D(p3d_Texture0, box.zy).a));\n'
        '    alpha /= 3.0;\n'
        '    float outline = pow(texture2D(p3d_Texture0, uvs-outline_offset).a, outline_power);\n'
        '    gl_FragColor = mix(vec4(vertex_color.rgb, outline_color.a * outline), vertex_color, alpha);\n'
        '}\n'
    )

    def _patch_shaders_to_glsl120():
        from ursina import shader as _shader_mod
        from ursina.shaders.unlit_shader import unlit_shader as _us
        from ursina.shaders.unlit_with_fog_shader import unlit_with_fog_shader as _ufs
        from ursina.shaders.text_shader import text_shader as _ts
        seen = set()
        for obj, verts, frags in [
            (_us, _UNLIT_VERT, _UNLIT_FRAG),
            (_ufs, _UFS_VERT, _UFS_FRAG),
            (_ts, _TEXT_VERT, _TEXT_FRAG),
        ]:
            if id(obj) not in seen:
                _patch_shader_obj(obj, verts, frags)
                seen.add(id(obj))
        for name, verts, frags in [
            ('unlit_shader', _UNLIT_VERT, _UNLIT_FRAG),
            ('unlit_with_fog_shader', _UFS_VERT, _UFS_FRAG),
            ('text_shader', _TEXT_VERT, _TEXT_FRAG),
        ]:
            obj = _shader_mod.imported_shaders.get(name)
            if obj is not None and id(obj) not in seen:
                _patch_shader_obj(obj, verts, frags)
                seen.add(id(obj))
    _patch_shaders_to_glsl120()
    app = Ursina(title="Level Editor")

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
        text="Drag tile from tray: Place block/enemy | Shift+LClick: Select | RDrag: Box-select\n"
             "Delete: Remove selected | Ctrl+Z: Undo | Ctrl+Y/Shift+Z: Redo | Esc: Cancel drag\n"
             "Ctrl+S: Save | G: Cycle snap | F5: Play-in-editor | Scroll over tray: scroll tiles\n"
             "Ctrl+1-5: Save cam bookmark | 1-5: Recall bookmark",
        parent=camera.ui,
        position=(-.88, .48),
        origin=(-.5, .5),
        scale=0.75,
        z=-1
    )

    app.run()
