"""
editor_core.py — LevelEditor core class (v1.6 split).

The editor core that remains after the v1.6 collaborator extractions: toolbar
buttons + tool modes, selection/box-select, grid snap, trigger/pickup
placeholder factories, level-data serialisation + save/load, prefs/bookmarks,
window-resize layout, stats strip, and the input()/update() dispatchers that
route to the collaborators (hierarchy / gizmo / browser / inspector /
playmode — each in its own Scripts/editor_*.py module, back-ref via self).

Standalone launch lives in Scripts/level_editor.py
(`python Scripts/level_editor.py`), which imports LevelEditor from here.
"""
from ursina import *
from panda3d.core import loadPrcFileData
import json
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

from Scripts.undo_redo import (
    UndoRedoStack, PlaceEntityCommand, DeleteEntityCommand,
)
from Scripts.asset_resolve import resolve_model as _resolve_model
from Scripts.session_logger import get_editor_logger
from Scripts.level_io import load_level_data, DEFAULT_MODEL
from Scripts.editor_hierarchy import HierarchyPanel
from Scripts.editor_gizmo import GizmoController
from Scripts.editor_browser import AssetBrowser
from Scripts.editor_inspector import InspectorPanel
from Scripts.editor_playmode import PlayModeController

logger = get_editor_logger()

loadPrcFileData('', 'model-cache-dir')


EDITOR_GRID_SNAPS = (1.0, 0.5, 0.25, None)


class LevelEditor(Entity):
    # Built-in primitive types displayed in the Models tab alongside real scanned assets.
    # Colours are 0–1 floats (color.rgb() does NOT divide by 255 — see Ursina footgun).
    BUILTIN_MODELS = [
        {'name': 'Cube',  'model': 'cube', 'color': (0.314, 0.471, 0.784), 'scale': (1, 1, 1),     'type': 'block'},
        {'name': 'Stone', 'model': 'cube', 'color': (0.431, 0.431, 0.431), 'scale': (1, 1, 1),     'type': 'block'},
        {'name': 'Metal', 'model': 'cube', 'color': (0.627, 0.627, 0.706), 'scale': (1, 1, 1),     'type': 'block'},
        {'name': 'Wood',  'model': 'cube', 'color': (0.627, 0.392, 0.235), 'scale': (1, 1, 1),     'type': 'block'},
        {'name': 'Enemy', 'model': 'cube', 'color': (0.784, 0.235, 0.235), 'scale': (1.5, 3, 1.5), 'type': 'enemy'},
        # v1.5 Step 6: invisible-in-game trigger volume. In the editor it renders as a
        # semi-transparent orange box (texture_orange_test) so it can be seen/placed;
        # at runtime TriggerZone is visible=False. Default 3x2x3 matches the spec JSON.
        {'name': 'Trigger', 'model': 'cube', 'color': (0.95, 0.55, 0.15), 'scale': (3, 2, 3), 'type': 'trigger'},
        # v1.5 Step 13: invisible-in-game weapon/ammo pickup. Small sphere so it
        # reads as a distinct entity type from Trigger's flat box. At runtime
        # AmmoPickup is visible=False; default config is an ammo pickup for the
        # pistol (matches AmmoPickup's own constructor defaults in weapon.py).
        {'name': 'Pickup', 'model': 'sphere', 'color': (0.25, 0.85, 0.95), 'scale': (0.6, 0.6, 0.6), 'type': 'pickup'},
    ]
    # Lookup by 'type' — assumes exactly one BUILTIN_MODELS entry per type (true
    # for trigger/pickup; block/enemy each have multiple entries and are not
    # looked up this way). Used by _make_pickup_entity for its default model/color/scale.
    BUILTIN_MODELS_BY_TYPE = {asset['type']: asset for asset in reversed(BUILTIN_MODELS)}

    # Editor-only visuals for trigger placeholders. Texture chosen for visibility;
    # alpha makes the volume see-through so geometry inside it stays visible.
    # Colour duplicated from BUILTIN_MODELS[-2]['color'] as its own constant — a
    # BUILTIN_MODELS[-1] lookup broke the moment Pickup was appended after Trigger
    # (v1.5 Step 13), so this reads by name, not fragile list position.
    _TRIGGER_TEXTURE = 'texture_orange_test'
    _TRIGGER_ALPHA   = 0.35
    _TRIGGER_COLOR   = (0.95, 0.55, 0.15)

    # Default pickup config stashed on a freshly-placed placeholder (v1.5 Step 13).
    _PICKUP_DEFAULT_CONFIG = {'pickup_type': 'ammo', 'weapon_type': 'pistol', 'amount': 30}

    def __init__(self):
        """Build all editor UI, load level and prefs; attach to app before calling app.run()."""
        super().__init__()
        self.blocks = []
        self.enemies = []
        # v1.5 Step 6: triggers are a third tracked type, parallel to blocks/enemies.
        # Editor placeholders are semi-transparent volumes carrying on_enter_actions/
        # on_exit_actions raw lists (config-store role); the runtime/F5 factory turns
        # them into live TriggerZones. Named 'trigger_*'; saved as type 'trigger'.
        self.triggers = []
        # v1.5 Step 13: pickups are a fourth tracked type, same config-store role as
        # triggers — placeholder carries a single pickup_config dict (pickup_type/
        # weapon_type/amount); the runtime/F5 factory turns it into a live AmmoPickup.
        self.pickups = []
        self.filename = 'level.json'
        self.current_texture = 'white_cube'
        self.current_mode = 'block'
        self._play_mode = False
        self._editor_camera = None

        # Tool mode: 'move' selects/deselects entities; 'place' left-click places new blocks
        self._tool = 'move'

        # Selection state
        self.selected = set()
        self._box_selecting = False
        self._box_start = None
        self._box_rect = None  # screen-space selection rect visual

        # Transform-gizmo collaborator (v1.6 split) — built in __init__ below.
        self.gizmo = None

        # Panel collaborators (v1.6 split) — built in __init__ below, alongside
        # the other _build_* calls. The asset browser also owns the picker
        # overlays, drag-ghost, hot-reload and import pipeline; the inspector
        # owns the field grid plus the behaviour/trigger/pickup/door sections.
        self.inspector = None
        self.hierarchy = None
        self.browser = None

        # Play-in-editor collaborator (v1.6 split) — no UI of its own, so unlike
        # the panel collaborators it is constructed right here; play_button's
        # on_click binds playmode.toggle_play below. Snapshot and saved-camera
        # state live on the controller; the _play_mode flag stays on the editor.
        self.playmode = PlayModeController(self)

        # Grid snap
        self.snap_values = list(EDITOR_GRID_SNAPS)
        self.snap_index = 0
        self.grid_snap = self.snap_values[0]

        # Bookmarks (loaded from prefs)
        self._bookmarks = {str(i): None for i in range(1, 6)}

        # Undo/redo
        self._history = UndoRedoStack()

        # Build toolbar buttons
        # Compact labels so each button fits in the horizontal strip; positions are
        # set by _apply_layout (the construction-time positions are placeholders).

        self.snap_button = Button(
            parent=camera.ui,
            text='Snap: 1.0',
            scale=(self._TOOLBAR_BTN_W_BASE['snap_button'], self._TOOLBAR_BTN_H),
            position=(.35, self._TOOLBAR_Y),
            on_click=self.cycle_snap,
            color=color.dark_gray,
            text_scale=0.9,
            z=-1,
            eternal=True,
        )

        self.play_button = Button(
            parent=camera.ui,
            text='Play',
            scale=(self._TOOLBAR_BTN_W_BASE['play_button'], self._TOOLBAR_BTN_H),
            position=(.35, self._TOOLBAR_Y),
            on_click=self.playmode.toggle_play,
            color=color.rgb(120, 60, 100),
            # color.rgb takes 0-1 floats in Ursina 8.3.0; these 0-255 values clamp to
            # white, so the label needs black text (default white would be invisible).
            text_color=color.black,
            highlight_text_color=color.black,
            text_scale=0.9,
            z=-1,
            eternal=True,
        )

        # Move/Place tool toggle buttons — mutually exclusive; _set_tool() updates highlight
        # Active background (color.rgb(...)) clamps to white in Ursina 8.3.0, so the
        # active button needs black text; the inactive (dark_gray) one keeps white.
        # _set_tool() keeps both in sync when the mode toggles.
        self._move_button = Button(
            parent=camera.ui,
            text='↖ Move',
            scale=(self._TOOLBAR_BTN_W_BASE['_move_button'], self._TOOLBAR_BTN_H),
            position=(.35, self._TOOLBAR_Y),
            on_click=lambda: self._set_tool('move'),
            color=color.rgb(60, 130, 60),   # highlighted — default active mode
            text_color=color.black,
            highlight_text_color=color.black,
            text_scale=0.9,
            z=-1,
            eternal=True,
        )

        self._place_button = Button(
            parent=camera.ui,
            text='+ Place',
            scale=(self._TOOLBAR_BTN_W_BASE['_place_button'], self._TOOLBAR_BTN_H),
            position=(.35, self._TOOLBAR_Y),
            on_click=lambda: self._set_tool('place'),
            color=color.dark_gray,
            text_color=color.white,
            highlight_text_color=color.white,
            text_scale=0.9,
            z=-1,
            eternal=True,
        )

        # Import Asset button (v1.3 Step 6) — opens the OS native file picker and
        # routes the chosen file through the same copy/route/rebuild/notice pipeline
        # a real OS drag-and-drop would use. Panda3D has no native file-drop support
        # on any released version (confirmed via Panda3D core dev on the official
        # forum, Oct 2023 — open GitHub feature request since Jan 2020), so this
        # button is the working substitute for "drag a file into the editor".
        self._import_button = Button(
            parent=camera.ui,
            text='Import Asset',
            scale=(self._TOOLBAR_BTN_W_BASE['_import_button'], self._TOOLBAR_BTN_H),
            position=(.35, self._TOOLBAR_Y),
            on_click=lambda: self.browser._open_import_dialog(),
            color=color.dark_gray,
            text_color=color.white,
            highlight_text_color=color.white,
            text_scale=0.9,
            z=-1,
            eternal=True,
        )

        # Stats strip — a legible, labelled entity/collider readout beside the toolbar
        # (replaces Ursina's tiny corner window.entity_counter/collider_counter, which
        # are disabled in level_editor.py's launch block). Counts the editor's own blocks/enemies so the
        # labels can stay verbose; updated once a second from update() (see _STATS_*).
        self._stats_text = Text(
            text='entities: 0   colliders: 0',
            parent=camera.ui,
            origin=(0.5, 0),          # right-aligned: grows leftward from the toolbar's left edge
            position=(0.3, self._TOOLBAR_Y),
            scale=0.9,
            color=color.white,
            z=-1,
            eternal=True,
        )
        self._stats_accum = 0.0       # seconds since last stats refresh

        self.model_preview = Entity(
            model='cube',
            color=color.white33,
            texture=self.current_texture,
            visible=False,
            scale=(1, 1, 1),
            eternal=True,
        )

        self.inspector = InspectorPanel(self)
        self.hierarchy = HierarchyPanel(self)
        self.gizmo = GizmoController(self)
        self.browser = AssetBrowser(self)

        self._spawn_marker = Entity(
            name='editor_player_spawn',
            model='cube',
            color=color.rgba(0, 255, 100, 160),
            scale=(0.6, 1.8, 0.6),
            position=(0, 1.4, 0),
            collider=None,
            eternal=True,
        )
        self._spawn_label = Text(
            text='SPAWN',
            parent=self._spawn_marker,
            scale=6,
            color=color.white,
            billboard=True,
            position=(0, 1, 0),
            eternal=True,
        )

        # Top-left overlay (hint text + its backing panel). Assigned/built by
        # level_editor.py's launch block after construction via _attach_hint_background().
        self._hint_text = None
        self._hint_bg = None

        self.load_existing_level()
        self._refresh_stats()   # show real counts immediately, not 0 until the first tick

        # Apply border-anchored layout now, and again whenever the window resizes.
        # Ursina fires `aspectRatioChanged` → window.update_aspect_ratio() which
        # rescales camera.ui children's x positions automatically.  The old
        # `window.on_resize` attribute was never invoked by Ursina, so _apply_layout
        # only ran once.  Fix: wrap update_aspect_ratio so _apply_layout runs AFTER
        # Ursina's own x-rescaling, resetting every managed element to the correct
        # absolute position/size for the new aspect ratio.
        self._apply_layout()
        _orig_update_ar = window.update_aspect_ratio
        _editor = self
        def _patched_update_aspect_ratio():
            _orig_update_ar()
            try:
                _editor._apply_layout()
            except Exception as e:
                logger.log('ERROR', f"resize _apply_layout {type(e).__name__}: {e}")
        window.update_aspect_ratio = _patched_update_aspect_ratio

        # Asset hot-reload (v1.3 Step 3). Register callbacks BEFORE the first poll.
        # Texture is the only category with live-reload logic; model/sound just log
        # (per spec — models are flagged dirty for next placement, not live-replaced).
        self.browser.start_hot_reload()

    # -------------------------------------------------------------------------
    # Theme (one consistent dark palette across all editor chrome)
    # -------------------------------------------------------------------------
    # All editor chrome shares one dark palette via 0–1 float rgba values.
    # Built-in model card colours stay in BUILTIN_MODELS (asset previews, not chrome).
    _THEME_PANEL_BG   = color.rgba(0.0,  0.0,  0.0,  0.75)   # main panel background
    _THEME_TILE_BG    = color.rgba(0.16, 0.16, 0.20, 1.0)    # card/tile backing (dark)
    _THEME_TILE_HOVER = color.rgba(0.31, 0.31, 0.39, 1.0)    # card/tile hover
    _THEME_TILE_SEL   = color.rgba(0.35, 0.51, 0.78, 1.0)    # selected card highlight
    _THEME_TAB_ACTIVE = color.azure                          # active tab button
    _THEME_TAB_IDLE   = color.dark_gray                      # inactive tab button
    _THEME_TEXT       = color.light_gray                     # body text on dark bg

    # -------------------------------------------------------------------------
    # Layout (border-anchored UI repositioning on window resize)
    # -------------------------------------------------------------------------

    # Panel widths (camera.ui units). Kept constant so labels/fields inside
    # the panels retain their proportions; only x-position changes with aspect.
    _LAYOUT_HIER_W = 0.20
    _LAYOUT_INSP_W = 0.30   # ~17% of a 16:9 window — wide enough for the 3-column grid
    _LAYOUT_PANEL_H = 0.9

    # Horizontal toolbar (Texture/Snap/Play/Move/Place) — a single Unity/Blender-style
    # strip that sits in the thin band ABOVE the inspector (inspector top edge is y=0.45
    # at PANEL_H 0.9, so the row lives in y∈[0.45,0.50]). Each button has its own compact
    # width sized to its (shortened) label; heights are uniform. Right-anchored, growing
    # leftward, with a small gap so it never reaches the fps counter in the corner.
    _TOOLBAR_BTN_H   = 0.04
    _TOOLBAR_Y       = 0.475          # row centre (band midpoint between 0.45 and 0.50)
    _TOOLBAR_GAP     = 0.008          # gap between buttons
    _TOOLBAR_RIGHT_PAD = 0.02         # gap from the right screen edge (clears fps counter)
    # Baseline button widths at the reference aspect ratio (16:9). _apply_layout
    # scales these proportionally when the viewport is narrower.
    _TOOLBAR_BTN_W_BASE = {
        'snap_button':    0.12,
        'play_button':    0.10,
        '_move_button':   0.10,
        '_place_button':  0.11,
        '_import_button': 0.14,
    }
    _TOOLBAR_REF_ASPECT = 16 / 9

    def _apply_layout(self):
        """Reposition border-anchored UI to match current window aspect ratio.

        Ursina UI height is fixed at 1 (camera.ui spans y in [-0.5, 0.5]);
        width = aspect_ratio.  Every dynamic element's position AND size is
        computed here relative to the current aspect — nothing is hardcoded at
        construction time.  Called on init and after every window resize (via
        the wrapped window.update_aspect_ratio).

        Camera lens aspect ratio is explicitly refreshed here so the 3D
        perspective always matches the window, even if Panda3D's automatic
        propagation is delayed or skipped on certain macOS resize paths.
        """
        aspect = getattr(window, 'aspect_ratio', 16 / 9)
        if not aspect or aspect <= 0:
            return
        half_w = aspect * 0.5

        # --- Camera lens refresh (BUG A fix) ---
        try:
            camera.perspective_lens.set_aspect_ratio(aspect)
            if camera.orthographic:
                camera.orthographic_lens.set_film_size(
                    camera.fov * aspect, camera.fov)
        except Exception as e:
            logger.log('ERROR', f"_apply_layout lens refresh {type(e).__name__}: {e}")

        hier_w = self._LAYOUT_HIER_W

        # Hierarchy — flush left (panel owns its own layout)
        if self.hierarchy is not None:
            self.hierarchy.apply_layout(aspect, half_w)

        # Inspector — flush right (panel owns its own layout)
        if self.inspector is not None:
            self.inspector.apply_layout(aspect, half_w)

        # Asset browser — full-width strip near the bottom (panel + tabs +
        # scroll indicators own their own layout).
        if self.browser is not None:
            self.browser.apply_layout(aspect, half_w)

        # --- Toolbar (BUG B fix) ---
        # Scale button widths proportionally when the viewport is narrower
        # than the 16:9 reference.  Each button's width is its baseline
        # multiplied by min(1, aspect / ref_aspect), so buttons shrink at
        # narrow widths but never grow beyond their designed size.
        toolbar_order = (
            ('snap_button',    self.snap_button),
            ('play_button',    self.play_button),
            ('_move_button',   self._move_button),
            ('_place_button',  self._place_button),
            ('_import_button', self._import_button),
        )
        scale_factor = min(1.0, aspect / self._TOOLBAR_REF_ASPECT)
        gap = self._TOOLBAR_GAP * scale_factor
        cursor_right = half_w - self._TOOLBAR_RIGHT_PAD
        for attr, btn in reversed(toolbar_order):
            if btn is None:
                continue
            w = self._TOOLBAR_BTN_W_BASE[attr] * scale_factor
            btn.scale_x = w
            btn.scale_y = self._TOOLBAR_BTN_H
            btn.x = cursor_right - w * 0.5
            btn.y = self._TOOLBAR_Y
            cursor_right -= (w + gap)
        self._toolbar_left_x = cursor_right

        # Stats strip — beside the toolbar, on the same row, to its left.
        # Clamp so it stays within the viewport and doesn't overlap the hint
        # text on the left.
        if getattr(self, '_stats_text', None) is not None:
            stats_x = self._toolbar_left_x - 0.02
            self._stats_text.x = stats_x
            self._stats_text.y = self._TOOLBAR_Y

        # Hint text — top-left overlay.  Anchored to the hierarchy panel's
        # right edge so it never overlaps the toolbar at narrow widths.
        hint_left = -half_w + hier_w + 0.01
        if self._hint_text is not None:
            self._hint_text.x = hint_left
            self._hint_text.y = 0.48
        if getattr(self, '_hint_bg', None) is not None and self._hint_text is not None:
            self._position_hint_bg()

    # -------------------------------------------------------------------------
    # Hint-text background (Change D)
    # -------------------------------------------------------------------------

    _HINT_BG_PAD = 0.012   # padding around the hint text block, camera.ui units

    def _attach_hint_background(self):
        """Create a dark backing panel sized to the hint text, so the controls legend
        stays readable over light geometry / sky. Follows the same panel pattern as the
        Inspector/Hierarchy (quad, _THEME_PANEL_BG, parent=camera.ui, eternal). Sized to
        the text — not full-width. Call once after self._hint_text exists."""
        if self._hint_text is None or getattr(self, '_hint_bg', None) is not None:
            return
        self._hint_bg = Entity(
            parent=camera.ui,
            model='quad',
            color=self._THEME_PANEL_BG,
            z=0.01,          # behind the text (text is at z=-1)
            eternal=True,
        )
        self._position_hint_bg()

    def _position_hint_bg(self):
        """Size/position the hint background to wrap the current hint text exactly.

        Ursina's Text exposes .width/.height in camera.ui units once rendered. The hint
        uses origin=(-.5,.5) (top-left anchor), so the block extends right and down from
        its position; centre the quad over that rect and add a little padding.
        """
        t = self._hint_text
        bg = getattr(self, '_hint_bg', None)
        if t is None or bg is None:
            return
        # Text.width/height are in the text's local space; the Text entity's own scale
        # brings them to camera.ui units. Our bg is parented to camera.ui (not the Text),
        # so multiply by the Text scale ourselves.
        w = (getattr(t, 'width', 0) or 0) * t.scale_x
        h = (getattr(t, 'height', 0) or 0) * t.scale_y
        if w <= 0 or h <= 0:
            return
        bg.scale_x = w + self._HINT_BG_PAD * 2
        bg.scale_y = h + self._HINT_BG_PAD * 2
        # origin (-.5,.5): text spans [t.x, t.x+w] in x and [t.y-h, t.y] in y.
        bg.x = t.x + w * 0.5
        bg.y = t.y - h * 0.5

    # -------------------------------------------------------------------------
    # Grid snap
    # -------------------------------------------------------------------------

    def cycle_snap(self):
        self.snap_index = (self.snap_index + 1) % len(self.snap_values)
        self.grid_snap = self.snap_values[self.snap_index]
        label = str(self.grid_snap) if self.grid_snap is not None else 'Off'
        self.snap_button.text = f'Snap: {label}'

    def _set_tool(self, mode):
        """Switch between 'move' (select/deselect) and 'place' (left-click places) modes."""
        self._tool = mode
        self._move_button.color  = color.rgb(60, 130, 60) if mode == 'move'  else color.dark_gray
        self._place_button.color = color.rgb(60, 60, 130) if mode == 'place' else color.dark_gray
        # Active colour clamps to white (color.rgb 0-255 footgun) → black text reads on it;
        # inactive dark_gray needs white text. Keep highlight_text_color in step so hover
        # doesn't flip the label back to an invisible colour.
        move_active = (mode == 'move')
        place_active = (mode == 'place')
        self._move_button.text_color = self._move_button.highlight_text_color = (
            color.black if move_active else color.white)
        self._place_button.text_color = self._place_button.highlight_text_color = (
            color.black if place_active else color.white)

    def _snap(self, position):
        if self.grid_snap is None:
            return list(position)
        return [round(p / self.grid_snap) * self.grid_snap for p in position]

    def _snap_1d(self, value):
        if self.grid_snap is None:
            return value
        return round(value / self.grid_snap) * self.grid_snap

    # -------------------------------------------------------------------------
    # Selection
    # -------------------------------------------------------------------------

    def _snapshot_color(self, entity):
        if not hasattr(entity, '_original_color'):
            entity._original_color = entity.color

    def _select(self, entity, additive=False):
        if not additive:
            self._deselect_all()
        if entity in (self.blocks + self.enemies + self.triggers + self.pickups):
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
        # v1.5 Step 6: trigger snapshot carries the raw action lists + scale so
        # delete→undo restores the volume identically (_restore_entity routes
        # is_trigger through _make_trigger_entity).
        if e in self.triggers:
            return {
                'is_trigger': True,
                'position': e.position,
                'scale': (e.scale_x, e.scale_y, e.scale_z),
                'on_enter': [dict(a) for a in getattr(e, 'on_enter_actions', [])],
                'on_exit':  [dict(a) for a in getattr(e, 'on_exit_actions', [])],
            }
        # v1.5 Step 13: pickup snapshot carries the raw config dict so delete→undo
        # restores the placeholder identically (_restore_entity routes is_pickup
        # through _make_pickup_entity). Same marker pattern as is_trigger above.
        if e in self.pickups:
            return {
                'is_pickup': True,
                'position': e.position,
                'pickup_config': dict(getattr(e, 'pickup_config', {})),
            }
        is_enemy = e in self.enemies
        tex_name = ''
        if hasattr(e, 'texture') and e.texture:
            tex_name = getattr(e.texture, 'name', str(e.texture))
        return {
            'position': e.position,
            'texture': tex_name,
            'color': getattr(e, '_original_color', e.color),
            'rotation': (e.rotation_x, e.rotation_y, e.rotation_z),
            'scale': (e.scale_x, e.scale_y, e.scale_z),
            'enemy_hp': getattr(e, 'enemy_hp', 100),
            'enemy_type': getattr(e, 'enemy_type', 'default'),
            'is_enemy': is_enemy,
        }

    def _make_trigger_entity(self, position, scale, on_enter, on_exit):
        """Build (and track) an editor trigger placeholder — the single construction
        site shared by drag-placement, level load, and F5-restore (DRY; avoids the
        duplicate-parser drift level_io.py was created to kill).

        The placeholder is a semi-transparent orange box so it is visible/clickable
        in the editor (the runtime TriggerZone is visible=False). It is collidable so
        the mouse picker and selection can hit it. on_enter/on_exit are the raw
        action-dict lists stashed verbatim — config-store role, never live callbacks.
        Appends to self.triggers and returns the entity.
        """
        tint = color.rgba(self._TRIGGER_COLOR[0],
                          self._TRIGGER_COLOR[1],
                          self._TRIGGER_COLOR[2],
                          self._TRIGGER_ALPHA)
        # Resolve the volume texture to an ABSOLUTE path. A bare string
        # ('texture_orange_test') only works when application.asset_folder points at
        # the project root; the editor runs standalone with asset_folder='Scripts',
        # so the bare glob misses and the volume renders untextured. PROJECT_ROOT is
        # the same absolute-path resolution the ground texture (level_editor.py's
        # launch block) already uses.
        trig_tex = None
        try:
            tex_path = PROJECT_ROOT / 'assets' / 'textures' / f'{self._TRIGGER_TEXTURE}.png'
            if tex_path.exists():
                trig_tex = Texture(Path(str(tex_path)))
        except Exception as exc:
            logger.log('ERROR', f"_make_trigger_entity texture {type(exc).__name__}: {exc}")
        e = Entity(
            model='cube',
            texture=trig_tex,
            color=tint,
            scale=tuple(scale),
            position=tuple(position),
            collider='box',
            name='trigger_volume',
        )
        e._original_color = tint
        e.on_enter_actions = [dict(a) for a in (on_enter or [])]
        e.on_exit_actions  = [dict(a) for a in (on_exit or [])]
        self.triggers.append(e)
        return e

    def _make_pickup_entity(self, position, pickup_config):
        """Build (and track) an editor pickup placeholder — the single construction
        site shared by drag-placement, level load, F5-restore, and undo/redo
        (same DRY rationale as _make_trigger_entity).

        The placeholder is a small solid sphere so it reads as visibly distinct
        from a trigger's flat box in the editor (the runtime AmmoPickup is
        visible=False). Collidable so the mouse picker and selection can hit it.
        pickup_config is the single raw config dict stashed verbatim — config-store
        role, never a live AmmoPickup. Appends to self.pickups and returns the entity.
        """
        asset = self.BUILTIN_MODELS_BY_TYPE['pickup']
        tint = color.rgb(*asset['color'])
        config = dict(self._PICKUP_DEFAULT_CONFIG)
        config.update(pickup_config or {})
        e = Entity(
            model=asset['model'],
            color=tint,
            scale=asset['scale'],
            position=tuple(position),
            collider='box',
            name='pickup_volume',
        )
        e._original_color = tint
        e.pickup_config = config
        self.pickups.append(e)
        return e

    def _finish_box_select(self):
        if self._box_start is None:
            return
        x0, x1 = sorted([self._box_start.x, mouse.x])
        y0, y1 = sorted([self._box_start.y, mouse.y])
        for e in self.blocks + self.enemies + self.triggers + self.pickups:
            try:
                screen_pos = e.screen_position
                if x0 <= screen_pos.x <= x1 and y0 <= screen_pos.y <= y1:
                    self._snapshot_color(e)
                    self.selected.add(e)
                    e.color = color.orange
            except Exception as exc:
                logger.log('ERROR', f"_finish_box_select {type(e).__name__}: {exc}")
        self._update_inspector()
        self._update_hierarchy_highlight()
        if self._box_rect:
            destroy(self._box_rect)
            self._box_rect = None
        self._box_start = None

    # -------------------------------------------------------------------------
    # Inspector panel (v1.6 split)
    # -------------------------------------------------------------------------

    # The inspector (field grid, texture swatch, model/door fields, and the
    # behaviour/trigger/pickup sections) lives in Scripts/editor_inspector.py
    # (InspectorPanel, back-ref via self.inspector). The delegators below keep
    # the original method names alive: _refresh_behaviour_ui / _refresh_trigger_ui /
    # _refresh_pickup_ui are called by undo_redo.py's commands (hard constraint),
    # the typing guards are aggregated in input(), and the texture/model helpers
    # are called by the asset browser and _build_level_data.

    def _update_inspector(self):
        self.inspector._update_inspector()

    def _update_inspector_texture_swatch(self):
        self.inspector._update_inspector_texture_swatch()

    def _update_inspector_model_field(self):
        self.inspector._update_inspector_model_field()

    def _entity_texture_name(self, e):
        return self.inspector._entity_texture_name(e)

    def _entity_model_name(self, e):
        return self.inspector._entity_model_name(e)

    def _refresh_behaviour_ui(self):
        self.inspector._refresh_behaviour_ui()

    def _refresh_trigger_ui(self):
        self.inspector._refresh_trigger_ui()

    def _refresh_pickup_ui(self):
        self.inspector._refresh_pickup_ui()

    def _behaviour_typing(self):
        return self.inspector._behaviour_typing()

    def _door_name_typing(self):
        return self.inspector._door_name_typing()

    def _trigger_typing(self):
        return self.inspector._trigger_typing()

    def _pickup_typing(self):
        return self.inspector._pickup_typing()

    # -------------------------------------------------------------------------
    # Asset picker overlay / asset browser (v1.6 split)
    # -------------------------------------------------------------------------

    # The texture/model picker overlays live in Scripts/editor_browser.py
    # (AssetBrowser, back-ref via self.browser) together with the browser band.

    # -------------------------------------------------------------------------
    # Hierarchy panel
    # -------------------------------------------------------------------------

    # v1.6 split: the hierarchy panel lives in Scripts/editor_hierarchy.py
    # (HierarchyPanel, back-ref via self.hierarchy). The three delegators below
    # keep the original method names alive — undo_redo.py's commands and the
    # aggregated typing-guard in input() call them by name (de-facto public API).

    def _refresh_hierarchy(self):
        self.hierarchy.refresh()

    def _update_hierarchy_highlight(self):
        self.hierarchy.update_highlight()

    def _hier_typing(self):
        return self.hierarchy.typing()

    def _is_over_panel(self, panel):
        """Return True if the mouse cursor is currently over the given UI panel quad."""
        mx, my = mouse.x, mouse.y
        px, py = panel.x, panel.y
        hw = panel.scale_x * 0.5
        hh = panel.scale_y * 0.5
        return (px - hw) <= mx <= (px + hw) and (py - hh) <= my <= (py + hh)

    def _camera_ui_pos_and_scale(self, entity):
        """Resolve `entity`'s position/scale into camera.ui-local units by walking
        its parent chain, compounding each ancestor's scale/position. Needed because
        Ursina's world_x/world_y are true Panda3D world-space (a different unit
        system than camera.ui's own local space that mouse.x/mouse.y are in) — so
        they can't be compared against mouse coordinates for nested camera.ui widgets."""
        x, y = entity.x, entity.y
        sx, sy = entity.scale_x, entity.scale_y
        node = entity.parent
        while node is not None and node is not camera.ui:
            sx *= node.scale_x
            sy *= node.scale_y
            x = node.x + x * node.scale_x
            y = node.y + y * node.scale_y
            node = node.parent
        return x, y, sx, sy

    def _is_over_world_panel(self, panel):
        """Same as _is_over_panel but for entities nested under another camera.ui
        panel (e.g. the inspector's texture swatch, or texture-picker cells nested
        under the picker panel), whose local .x/.y are relative to their parent."""
        mx, my = mouse.x, mouse.y
        px, py, sx, sy = self._camera_ui_pos_and_scale(panel)
        hw = sx * 0.5
        hh = sy * 0.5
        return (px - hw) <= mx <= (px + hw) and (py - hh) <= my <= (py + hh)

    # v1.6 split: the asset browser band, picker overlays, texture hot-reload,
    # asset import pipeline, drag-ghost placement and the status-notice toast
    # live in Scripts/editor_browser.py (AssetBrowser, back-ref via self.browser).
    # Core input() keeps the dispatch order and calls browser._handle_browser_click /
    # handle_scroll / picker handlers at the same priority steps as before.

    # -------------------------------------------------------------------------
    # Transform gizmos
    # -------------------------------------------------------------------------

    # v1.6 split: the transform gizmo lives in Scripts/editor_gizmo.py
    # (GizmoController, back-ref via self.gizmo). Core's update() drives
    # gizmo.handle_drag()/refresh(); input() Step 1 calls gizmo.try_begin_drag().

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
            except Exception as e:
                logger.log('ERROR', f"_load_prefs {type(e).__name__}: {e}")

    def _save_prefs(self):
        # FIXED: unguarded write failure silently dropped prefs and unwound Ctrl+S
        prefs = {
            'bookmarks': self._bookmarks,
            'grid_snap': self.grid_snap,
        }
        try:
            with open('editor_prefs.json', 'w') as f:
                json.dump(prefs, f, indent=4)
        except Exception as e:
            logger.log('ERROR', f"_save_prefs failed: {type(e).__name__}: {e}")

    # -------------------------------------------------------------------------
    # Play-in-editor
    # -------------------------------------------------------------------------

    def _build_level_data(self):
        """Serialize current blocks, enemies and triggers to a list of dicts for save or play snapshot.

        Defensive filter: skip entities that have already been destroyed (destroy_source set).
        Accessing .color on a dead NodePath raises an assertion in development_mode.
        Drop dead refs from the live lists too so the editor state stays consistent.
        """
        data = []
        live_blocks = [b for b in self.blocks if getattr(b, 'destroy_source', None) is None]
        live_enemies = [e for e in self.enemies if getattr(e, 'destroy_source', None) is None]
        live_triggers = [t for t in self.triggers if getattr(t, 'destroy_source', None) is None]
        live_pickups = [p for p in self.pickups if getattr(p, 'destroy_source', None) is None]
        dropped = ((len(self.blocks) - len(live_blocks))
                   + (len(self.enemies) - len(live_enemies))
                   + (len(self.triggers) - len(live_triggers))
                   + (len(self.pickups) - len(live_pickups)))
        if dropped:
            logger.log('WARN', f'_build_level_data: dropped {dropped} destroyed entity refs')
            self.blocks[:] = live_blocks
            self.enemies[:] = live_enemies
            self.triggers[:] = live_triggers
            self.pickups[:] = live_pickups
        for block in live_blocks:
            actual_color = getattr(block, '_original_color', block.color)
            tex_name = ''
            if hasattr(block, 'texture') and block.texture:
                tex_name = getattr(block.texture, 'name', str(block.texture))
            block_data = {
                'type': 'block',
                'position': [block.x, block.y, block.z],
                'texture': tex_name,
                'colour': [round(actual_color.r, 3), round(actual_color.g, 3), round(actual_color.b, 3)],
                'rotation': [round(block.rotation_x, 2), round(block.rotation_y, 2), round(block.rotation_z, 2)],
                'scale': [round(block.scale_x, 4), round(block.scale_y, 4), round(block.scale_z, 4)],
            }
            # v1.3 Step 7: model field, blocks only. Omit the key entirely at the
            # 'cube' default (chosen over writing 'cube' literally) so re-saving a
            # pre-step-7 level produces no spurious "model": "cube" churn and the
            # schema stays backwards-compatible. _resolve_model sets model.name to
            # the project-relative path for custom assets, so this round-trips.
            model_name = self._entity_model_name(block) or DEFAULT_MODEL
            if model_name != DEFAULT_MODEL:
                block_data['model'] = model_name
            # v1.5 Step 4: door identity. Write the key ONLY when set (same
            # omit-at-default pattern as 'model'/'behaviour') so unnamed blocks
            # produce no spurious "door_name": "" churn and the schema stays
            # backwards-compatible with pre-v1.5 loaders.
            door_name = getattr(block, 'door_name', '')
            if door_name:
                block_data['door_name'] = door_name
            data.append(block_data)
        for enemy in live_enemies:
            enemy_data = {
                'type': 'enemy',
                'position': [enemy.x, enemy.y, enemy.z],
                'hp': getattr(enemy, 'enemy_hp', 100),
                'enemy_type': getattr(enemy, 'enemy_type', 'default'),
                'rotation_y': round(enemy.rotation_y, 2),
            }
            # v1.4 Step 8: write the behaviour key ONLY when this enemy carries a
            # non-empty config (same omit-at-default pattern as 'model' above).
            # Enemies with no custom behaviour write no key at all — so re-saving
            # a pre-v1.4 level produces no spurious "behaviour": null/{} and the
            # schema stays backwards-compatible with old loaders.
            behaviour_config = getattr(enemy, 'behaviour_config', None)
            if behaviour_config:
                enemy_data['behaviour'] = behaviour_config
            data.append(enemy_data)
        for trigger in live_triggers:
            # v1.5 Step 6: serialize the volume's transform + raw action lists.
            # level_io defaults absent on_enter/on_exit to [], so always writing
            # them (even empty) is harmless and keeps the schema explicit. The
            # editor placeholder's colour/texture are editor chrome — NOT saved;
            # the runtime TriggerZone is invisible.
            data.append({
                'type': 'trigger',
                'position': [trigger.x, trigger.y, trigger.z],
                'scale': [round(trigger.scale_x, 4), round(trigger.scale_y, 4), round(trigger.scale_z, 4)],
                'on_enter': [dict(a) for a in getattr(trigger, 'on_enter_actions', [])],
                'on_exit':  [dict(a) for a in getattr(trigger, 'on_exit_actions', [])],
            })
        for pickup in live_pickups:
            # v1.5 Step 13: serialize the pickup's position + raw config dict. The
            # editor placeholder's colour/model are editor chrome — NOT saved; the
            # runtime AmmoPickup is invisible with its own fixed scale.
            config = getattr(pickup, 'pickup_config', self._PICKUP_DEFAULT_CONFIG)
            data.append({
                'type': 'pickup',
                'position': [pickup.x, pickup.y, pickup.z],
                'pickup_type': config.get('pickup_type', 'ammo'),
                'weapon_type': config.get('weapon_type', 'pistol'),
                'amount':      config.get('amount', 30),
            })
        return data

    def _set_editor_ui_visible(self, visible):
        self.inspector.set_visible(visible)
        for widget in [self.hierarchy.panel,
                       self.snap_button,
                       self.play_button, self._move_button, self._place_button,
                       self._import_button,
                       self._stats_text,
                       self._spawn_marker]:
            if widget:
                if getattr(widget, 'destroy_source', None) is not None:
                    logger.log('WARN', f'_set_editor_ui_visible: skipped destroyed widget {widget}')
                    continue
                widget.enabled = visible

        # Asset browser (panel + tab buttons + scroll indicators) — the band
        # owns its own show/hide, including per-tab card visibility.
        self.browser.set_visible(visible)

    # -------------------------------------------------------------------------
    # Play-in-editor (v1.6 split)
    # -------------------------------------------------------------------------
    # toggle_play / _enter_play_mode / _exit_play_mode / _restore_editor_level /
    # _spawn_gameplay_from_snapshot live in Scripts/editor_playmode.py
    # (PlayModeController, back-ref via self.playmode). The _play_mode flag
    # stays HERE on the editor -- core update()/input(), the gizmo and the
    # asset browser all read it; snapshot + saved-camera state moved with the
    # controller.

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
        """Per-frame: drive ghost drag, model preview, gizmo, and highlight in editor mode."""
        if not self._play_mode:
            if self.browser._dragging:
                self.browser._update_ghost()
                self.model_preview.visible = False
            else:
                self.update_model_preview()
            self.gizmo.handle_drag()
            self.gizmo.refresh()
            # Refresh the stats strip ~once a second (matches Ursina's own counter cadence).
            self._stats_accum += time.dt
            if self._stats_accum >= 1.0:
                self._stats_accum = 0.0
                self._refresh_stats()

    def _refresh_stats(self):
        """Update the entity/collider stats readout from the editor's own level data."""
        if getattr(self, '_stats_text', None) is None:
            return
        placed = self.blocks + self.enemies + self.triggers + self.pickups
        entities = len(placed)
        colliders = sum(1 for e in placed if getattr(e, 'collider', None) is not None)
        self._stats_text.text = f'entities: {entities}   colliders: {colliders}'

    def input(self, key):
        """Route keyboard/mouse events to placement, selection, undo/redo, bookmarks, and drag."""
        if self._play_mode:
            if key in ('f5', 'escape'):
                self.playmode._exit_play_mode()
            return

        # Asset picker overlay (texture or model) — while open, it owns input.
        # Escape or a click anywhere are both explicit dismiss/apply paths (no
        # fall-through to the scene below); this is the click-outside guard the
        # picker needs because Ursina broadcasts input() to every entity, not
        # just the topmost one.
        if self.browser.asset_picker_open is not None:
            if key == 'escape':
                self.browser._close_asset_picker()
                return
            if key == 'left mouse down':
                name = self.browser._asset_picker_cell_at_mouse()
                if name is not None:
                    self.browser._asset_picker_on_select(name)
                else:
                    self.browser._close_asset_picker()
                return
            return

        if key == 'f5':
            self.playmode._enter_play_mode()
            return

        # Snap cycle is now on the button; keep 'g' as keyboard shortcut too
        if key == 'g':
            self.cycle_snap()

        if key == 's' and held_keys['control']:
            self.save_level()

        # Undo / Redo
        if key == 'z' and held_keys['control'] and held_keys['shift']:
            cmd = self._history._redo[-1] if self._history._redo else None
            self._history.redo()
            logger.log('INFO', f"Redo executed: {type(cmd).__name__ if cmd else 'nothing'}")
        elif key == 'z' and held_keys['control']:
            cmd = self._history._undo[-1] if self._history._undo else None
            self._history.undo()
            logger.log('INFO', f"Undo executed: {type(cmd).__name__ if cmd else 'nothing'}")
        if key == 'y' and held_keys['control']:
            cmd = self._history._redo[-1] if self._history._redo else None
            self._history.redo()
            logger.log('INFO', f"Redo executed: {type(cmd).__name__ if cmd else 'nothing'}")

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
            # FIXED: scene.focused_entity is never set by Ursina — bookmark keys fired
            # while typing numbers in inspector fields. Check InputField.active instead.
            # Also skip while typing in the hierarchy search box.
            if (any(f.active for f in self.inspector._insp_fields.values())
                    or self._hier_typing() or self._behaviour_typing()
                    or self._door_name_typing() or self._trigger_typing()
                    or self._pickup_typing()):
                return
            for i in range(1, 6):
                if key == str(i):
                    bm = self._bookmarks.get(str(i))
                    if bm and self._editor_camera:
                        self._editor_camera.position = bm['position']
                        self._editor_camera.rotation = bm['rotation']
                    break

        # Delete selected. macOS reports the physical Delete key as 'backspace' to
        # Panda3D, so accept both. Skip while typing in an inspector field (backspace
        # edits text there) or mid-drag (gizmo / box-select / browser drag).
        if key in ('delete', 'backspace'):
            typing = (any(f.active for f in self.inspector._insp_fields.values())
                      or self._hier_typing() or self._behaviour_typing()
                      or self._door_name_typing() or self._trigger_typing()
                      or self._pickup_typing())
            mid_drag = (self.gizmo.drag_axis is not None
                        or self._box_selecting or self.browser._dragging)
            if self.selected and not typing and not mid_drag:
                for e in list(self.selected):
                    snapshot = self._entity_snapshot(e)
                    etype = ('enemy' if e in self.enemies else
                             'trigger' if e in self.triggers else
                             'pickup' if e in self.pickups else 'block')
                    logger.log('INFO', f"Entity deleted: type={etype} pos={[round(p, 3) for p in e.position]}")
                    cmd = DeleteEntityCommand(self, e, snapshot)
                    cmd.execute()
                    self._history.push(cmd)
                self.selected.clear()
                self._update_inspector()
                self._refresh_hierarchy()
                self.gizmo.refresh()
                return

        # Cancel drag with Esc
        if key == 'escape' and self.browser._dragging:
            self.browser._cancel_drag()
            return

        # Commit or cancel drag on left mouse up
        if key == 'left mouse up' and self.browser._dragging:
            hovered = mouse.hovered_entity
            if (self.browser._is_over_browser()
                    or hovered is None
                    or hovered is self.browser._drag_ghost
                    or getattr(hovered, 'parent', None) is camera.ui):
                self.browser._cancel_drag()
            else:
                self.browser._commit_drag()
            return

        # Unified left mouse down handler: gizmo → panels → browser → tool action
        if key == 'left mouse down':
            # Step 1: gizmo handle hit — raycast against tip cubes BEFORE any panel check.
            # setDepthTest(False)/setBin(100) make handles visible through blocks, but the
            # pick ray still hits geometry behind them; explicit raycast takes priority.
            # (Pick logic + the v1.2.4 cursor-ray fix live in GizmoController.try_begin_drag.)
            if self.gizmo.try_begin_drag():
                return

            # Step 2: panel guards — _is_over_panel uses mouse.x/y, no hovered needed.
            # Buttons inside panels are grandchildren of camera.ui; _is_over_panel is
            # more reliable than checking hovered.parent for nested widgets.
            if self.hierarchy.panel and self._is_over_panel(self.hierarchy.panel):
                return
            # Texture swatch click opens the picker — must be checked before the
            # blanket inspector-panel guard below swallows the click silently.
            if (self.inspector._insp_tex_swatch and self.selected
                    and self._is_over_world_panel(self.inspector._insp_tex_swatch)):
                self.browser.open_texture_picker()
                return
            # Model field click opens the model picker — blocks only (see
            # open_model_picker's own enemy guard); same priority as the texture
            # swatch, before the blanket inspector-panel guard.
            if (self.inspector._insp_model_field and self.selected
                    and not any(e in self.enemies for e in self.selected)
                    and self._is_over_world_panel(self.inspector._insp_model_field)):
                self.browser.open_model_picker()
                return
            if self.inspector.panel and self._is_over_panel(self.inspector.panel):
                return

            # FIXED (Item 1): asset browser card click is a panel-class guard and must
            # run with the other panels (AFTER the gizmo hit-test), not at Step 0b before
            # it. A gizmo handle can render over the bottom browser strip; per the v1.2.4
            # gizmo-fix priority chain the handle must win. Cards are camera.ui children,
            # so this stays before the "skip direct camera.ui children" guard below.
            if self.browser._handle_browser_click():
                return

            hovered = mouse.hovered_entity

            # Skip direct camera.ui children (toolbar buttons, browser panel, etc.)
            if hovered and getattr(hovered, 'parent', None) is camera.ui:
                return

            # Step 3: active drag or box-select guard (after gizmo and panel steps)
            if self.gizmo.drag_axis is not None or self._box_selecting:
                return

            # Step 4/5: selection (shift) or tool-mode action
            if held_keys['shift']:
                # Shift+click: add/remove from selection — same in both tool modes
                if hovered in (self.blocks + self.enemies + self.triggers + self.pickups):
                    if hovered in self.selected:
                        self.selected.discard(hovered)
                        hovered.color = getattr(hovered, '_original_color', color.white)
                        self._update_inspector()
                        self._update_hierarchy_highlight()
                    else:
                        self._select(hovered, additive=True)
                elif hovered and hovered.collider:
                    pass  # shift-click on non-tracked surface (e.g. ground) — no-op
                else:
                    self._deselect_all()
            elif self._tool == 'move':
                # FIXED (FIX 3 Move mode): select/deselect entities; clicking a surface
                # never places a new block in Move mode.
                if hovered in (self.blocks + self.enemies + self.triggers + self.pickups):
                    self._select(hovered)
                    return
                if self.selected:
                    self._deselect_all()
                    return
            else:
                # FIXED (FIX 3 Place mode): left-click on any collidable non-editor
                # surface places a new block from the current texture. Shift+click
                # (handled above) selects without placing.
                if hovered and hovered.collider and not getattr(hovered, 'name', '').startswith('editor_'):
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
            over_ui = (
                (self.hierarchy.panel and self._is_over_panel(self.hierarchy.panel))
                or (self.inspector.panel and self._is_over_panel(self.inspector.panel))
                or self.browser._is_over_browser()
            )
            if self._editor_camera:
                self._editor_camera.zoom_speed = 0 if over_ui else 1.25

            # Asset browser vertical scroll (by row)
            if self.browser._is_over_browser():
                self.browser.handle_scroll(key)
                return

            if self.hierarchy.panel and self._is_over_panel(self.hierarchy.panel):
                self.hierarchy.handle_scroll(key)
                return

            if self.inspector.panel and self._is_over_panel(self.inspector.panel):
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

    # DEBT (pre-existing): position_valid() is never called. Left in place per surgical-change
    # rule; remove if a placement-overlap guard is wired in. See docs/audit_v1.2.3.md Deferred.
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

        try:
            with open(self.filename, 'w') as f:
                json.dump(deduped, f, indent=4)
        except Exception as e:
            logger.log('ERROR', f"save_level failed: {type(e).__name__}: {e}")
            return
        print(f'Saved level to {self.filename} ({len(deduped)} entries)')
        logger.log('INFO', f"Level saved: {os.path.abspath(self.filename)} ({len(deduped)} entries)")
        self._history.clear()
        self._save_prefs()

    def load_existing_level(self):
        """Destroy all current editor entities, then reload from level.json if it exists."""
        for e in self.blocks + self.enemies + self.triggers + self.pickups:
            destroy(e)
        self.blocks.clear()
        self.enemies.clear()
        self.triggers.clear()
        self.pickups.clear()
        self.selected.clear()

        try:
            entries = load_level_data(self.filename)
            for entry in entries:
                if entry['type'] == 'trigger':
                    # v1.5 Step 6: editor is now trigger-aware. Build a semi-
                    # transparent volume carrying the raw action lists; it round-
                    # trips through _build_level_data on save.
                    self._make_trigger_entity(
                        entry['position'], entry['scale'],
                        entry['on_enter'], entry['on_exit'],
                    )
                    continue
                if entry['type'] == 'pickup':
                    # v1.5 Step 13: editor is now pickup-aware. Build a small
                    # solid sphere carrying the raw pickup config; it round-trips
                    # through _build_level_data on save.
                    self._make_pickup_entity(entry['position'], {
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
                        origin_y=-0.5
                    )
                    new_entity.enemy_hp   = entry['hp']
                    new_entity.enemy_type = entry['enemy_type']
                    # v1.4 Step 8: store the raw behaviour-config dict (or None)
                    # so it round-trips on save (_build_level_data reads the same
                    # attribute). Editor placeholders are plain Entities with no
                    # alive/collision/game-state context, so we store the config
                    # only — NOT a live tree. Step 9 will add inspector UI to edit
                    # it; this step is storage/round-trip plumbing only.
                    new_entity.behaviour_config = entry['behaviour']
                    new_entity._original_color = color.red
                    self.enemies.append(new_entity)
                else:
                    new_entity = Entity(
                        model=_resolve_model(entry['model']),
                        texture=entry['texture'],
                        position=tuple(entry['position']),
                        rotation=tuple(entry['rotation']),
                        scale=tuple(entry['scale']),
                        color=color.rgb(*entry['colour']),
                        collider='box'
                    )
                    new_entity._original_color = new_entity.color
                    # v1.5 Step 4: round-trip door identity (raw value from the
                    # single parser; '' for unnamed blocks). _build_level_data reads
                    # the same attribute on save.
                    new_entity.door_name = entry['door_name']
                    self.blocks.append(new_entity)
            logger.log('INFO', f"Level loaded: {os.path.abspath(self.filename)} ({len(entries)} entries)")
        except FileNotFoundError:
            logger.log('INFO', f"Level file not found: {self.filename} — starting empty")
            print("No level file found")
        except Exception as e:
            logger.log('ERROR', f"{type(e).__name__}: {e}")
            print(f"Error loading level: {e}")

        self._refresh_hierarchy()
        self._history.clear()
        # Load prefs after snap_button exists
        self._load_prefs()
