import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from ursina import *
from ursina.prefabs.editor_camera import EditorCamera
from panda3d.core import loadPrcFileData, AntialiasAttrib
import json
import os
import shutil
import time as _time   # wall-clock for double-click timing (ursina `time` is Panda3D's clock)
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent 

from Scripts.undo_redo import (
    UndoRedoStack, PlaceEntityCommand, DeleteEntityCommand,
    ChangeTextureCommand, ChangeModelCommand, ChangeColourCommand,
    ChangePropertyCommand, ChangeBehaviourCommand, ChangeTriggerActionsCommand,
    ChangePickupConfigCommand, _resolve_model
)
from Scripts.behaviour_tree_factory import BehaviourTreeFactory
from Scripts.session_logger import get_editor_logger
from Scripts.level_io import load_level_data, DEFAULT_MODEL
from Scripts.asset_registry import asset_registry, CATEGORY_DIRS, CATEGORY_EXTENSIONS
from Scripts.weapon import WEAPON_TYPES
from Scripts.editor_hierarchy import HierarchyPanel
from Scripts.editor_gizmo import GizmoController
from Scripts.editor_browser import AssetBrowser

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
        self._play_level_snapshot = None
        self._editor_camera = None
        self._saved_cam_pos = None
        self._saved_cam_rot = None

        # Tool mode: 'move' selects/deselects entities; 'place' left-click places new blocks
        self._tool = 'move'

        # Selection state
        self.selected = set()
        self._box_selecting = False
        self._box_start = None
        self._box_rect = None  # screen-space selection rect visual

        # Transform-gizmo collaborator (v1.6 split) — built in __init__ below.
        self.gizmo = None

        # Inspector / hierarchy panel refs
        self._inspector = None
        self._insp_title = None
        self._insp_fields = {}
        self._insp_tex_swatch = None
        self._insp_tex_name = None
        self._insp_model_label = None
        self._insp_model_field = None

        # Panel collaborators (v1.6 split) — built in __init__ below, alongside
        # the other _build_* calls. The asset browser also owns the picker
        # overlays, drag-ghost, hot-reload and import pipeline.
        self.hierarchy = None
        self.browser = None

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
            on_click=self.toggle_play,
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
        # are disabled in __main__). Counts the editor's own blocks/enemies so the
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

        self._build_inspector()
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

        # Top-left overlay (hint text + its backing panel). Assigned/built by __main__
        # after construction via _attach_hint_background().
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
        insp_w = self._LAYOUT_INSP_W

        # Hierarchy — flush left (panel owns its own layout)
        if self.hierarchy is not None:
            self.hierarchy.apply_layout(aspect, half_w)

        # Inspector — flush right
        if self._inspector is not None:
            self._inspector.x = half_w - insp_w * 0.5
            self._inspector.y = 0
            self._inspector.scale_x = insp_w
            self._inspector.scale_y = self._LAYOUT_PANEL_H

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
        # the same absolute-path resolution the ground texture (below) already uses.
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
    # Inspector panel
    # -------------------------------------------------------------------------

    # Absolute world_scale for inspector Text. A plain Text parented to the small
    # (0.30 x 0.9) panel inherits that tiny scale and renders microscopically — the
    # real reason inspector labels have always looked "invisible" here (the old
    # setBin/z fixes only addressed z-order). Ursina's Button sidesteps this by
    # forcing text_entity.world_scale ~= 20; we do the same so labels are legible.
    _INSP_TITLE_WS = 22
    _INSP_LABEL_WS = 15

    def _build_inspector(self):
        self._inspector = Entity(
            parent=camera.ui,
            model='quad',
            color=color.rgba(0, 0, 0, 0.75),
            scale=(.30, .9),
            position=(.739, 0),
            z=-0.5,
            eternal=True,
        )
        self._insp_title = Text(
            parent=self._inspector,
            text='Inspector',
            position=(0, .42),
            color=color.white,
            origin=(0, 0),
            z=-1,
            eternal=True,
        )
        self._insp_title.world_scale = Vec3(self._INSP_TITLE_WS, self._INSP_TITLE_WS, 1)
        try:
            self._insp_title.setBin('fixed', 41)
        except Exception as e:
            logger.log('ERROR', f"_build_inspector setBin title {type(e).__name__}: {e}")
        # Display-only 3-column x 2-row grid of the six transform fields:
        #   Row 1: Pos X / Pos Y / Pos Z      Row 2: Scale X / Scale Y / Scale Z
        # HP, rotation, colour, texture and enemy_type still live on the entity and
        # round-trip through level.json (see _build_level_data); they are simply no
        # longer editable here. Coordinates are panel-local (quad spans -0.5..0.5).
        grid = [
            [('pos_x', 'Pos X'), ('pos_y', 'Pos Y'), ('pos_z', 'Pos Z')],
            [('scl_x', 'Scale X'), ('scl_y', 'Scale Y'), ('scl_z', 'Scale Z')],
        ]
        col_x = (-.33, 0.0, .33)      # column centres
        row_label_y = (.22, -.08)     # label y per row
        row_field_y = (.13, -.17)     # input-field y per row
        self._insp_fields = {}
        for r, row in enumerate(grid):
            for c, (key, label) in enumerate(row):
                label_text = Text(
                    parent=self._inspector,
                    text=label,
                    position=(col_x[c], row_label_y[r]),
                    color=color.light_gray,
                    origin=(0, 0),
                    z=-1,
                    eternal=True,
                )
                # world_scale so the label is readable (see _INSP_LABEL_WS note above).
                label_text.world_scale = Vec3(self._INSP_LABEL_WS, self._INSP_LABEL_WS, 1)
                # Lift above the panel quad; setBin is a NodePath method — call it on the
                # Entity, never entity.node() (PandaNode has no setBin).
                try:
                    label_text.setBin('fixed', 41)
                except Exception as e:
                    logger.log('ERROR', f"_build_inspector setBin label {type(e).__name__}: {e}")
                field = InputField(
                    parent=self._inspector,
                    position=(col_x[c], row_field_y[r]),
                    scale=(.27, .055),
                    default_value='0',
                    z=-1,
                    eternal=True,
                )
                try:
                    field.setBin('fixed', 41)
                except Exception as e:
                    logger.log('ERROR', f"_build_inspector setBin field {type(e).__name__}: {e}")
                # FIXED (v1.2.4, FIX 4): Enter applied nothing because (1) Ursina's InputField
                # only fires on_submit when the key is in submit_on, which defaults to [] and was
                # never set, and (2) Ursina calls self.on_submit() with NO arguments, so the prior
                # `lambda val, k=key:` raised TypeError the instant it ever did fire. Fix both:
                # enable Enter via submit_on, and use a no-arg callback that reads field.text at
                # call time. k=key/f=field bind the loop variables (no live-entity capture).
                field.submit_on = ['enter']
                field.on_submit = lambda k=key, f=field: self._apply_inspector_value(k, f.text)
                self._insp_fields[key] = field

        # Texture thumbnail (v1.3 Step 4) — click opens the texture picker overlay.
        # Sits below the Pos/Scale grid, panel-local space.
        self._insp_tex_label = Text(
            parent=self._inspector,
            text='Texture',
            position=(-.33, -.30),
            color=color.light_gray,
            origin=(0, 0),
            z=-1,
            eternal=True,
        )
        self._insp_tex_label.world_scale = Vec3(self._INSP_LABEL_WS, self._INSP_LABEL_WS, 1)
        try:
            self._insp_tex_label.setBin('fixed', 41)
        except Exception as e:
            logger.log('ERROR', f"_build_inspector setBin tex label {type(e).__name__}: {e}")

        self._insp_tex_swatch = Entity(
            parent=self._inspector,
            model='quad',
            color=color.white,
            scale=(.12, .08),
            position=(.05, -.30),
            z=-1,
            eternal=True,
        )
        try:
            from ursina.shaders.unlit_shader import unlit_shader as _us
            self._insp_tex_swatch.shader = _us
        except Exception as e:
            logger.log('ERROR', f"_build_inspector swatch shader {type(e).__name__}: {e}")
        try:
            self._insp_tex_swatch.setBin('fixed', 41)
        except Exception as e:
            logger.log('ERROR', f"_build_inspector setBin swatch {type(e).__name__}: {e}")

        self._insp_tex_name = Text(
            parent=self._inspector,
            text='---',
            position=(.20, -.30),
            color=color.white,
            origin=(0, 0),
            z=-1,
            eternal=True,
        )
        self._insp_tex_name.world_scale = Vec3(self._INSP_LABEL_WS, self._INSP_LABEL_WS, 1)
        try:
            self._insp_tex_name.setBin('fixed', 41)
        except Exception as e:
            logger.log('ERROR', f"_build_inspector setBin tex name {type(e).__name__}: {e}")

        # Model field (v1.3 Step 5) — click opens the model picker overlay.
        # Blocks only; hidden whenever the selection includes an enemy (see
        # _update_inspector_model_field). Sits below the Texture row.
        self._insp_model_label = Text(
            parent=self._inspector,
            text='Model',
            position=(-.33, -.38),
            color=color.light_gray,
            origin=(0, 0),
            z=-1,
            eternal=True,
        )
        self._insp_model_label.world_scale = Vec3(self._INSP_LABEL_WS, self._INSP_LABEL_WS, 1)
        try:
            self._insp_model_label.setBin('fixed', 41)
        except Exception as e:
            logger.log('ERROR', f"_build_inspector setBin model label {type(e).__name__}: {e}")

        self._insp_model_field = Entity(
            parent=self._inspector,
            model='quad',
            color=self._THEME_TILE_BG,
            scale=(.27, .08),
            position=(.10, -.38),
            z=-1,
            eternal=True,
        )
        try:
            self._insp_model_field.setBin('fixed', 41)
        except Exception as e:
            logger.log('ERROR', f"_build_inspector setBin model field {type(e).__name__}: {e}")

        self._insp_model_name = Text(
            parent=self._insp_model_field,
            text='---',
            origin=(0, 0),
            color=color.white,
            z=-1,
            eternal=True,
        )
        self._insp_model_name.world_scale = Vec3(self._INSP_LABEL_WS, self._INSP_LABEL_WS, 1)
        try:
            self._insp_model_name.setBin('fixed', 41)
        except Exception as e:
            logger.log('ERROR', f"_build_inspector setBin model name {type(e).__name__}: {e}")

        # Door name field (v1.5 Step 4) — blocks only. A trigger's open_door action
        # resolves its target by matching this string. Sits below the Model row at
        # -.46. Block-only, so it never co-renders with the enemy-only behaviour UI
        # that reuses the same band (mutually exclusive by selection type). Unlike
        # the numeric Pos/Scale fields, clearing this to '' is a LEGAL edit (un-naming
        # a door), so it has its own string commit handler, not _apply_inspector_value.
        self._insp_door_label = Text(
            parent=self._inspector,
            text='Door Name',
            position=(-.30, -.46),
            color=color.light_gray,
            origin=(0, 0),
            z=-1,
            eternal=True,
        )
        self._insp_door_label.world_scale = Vec3(self._INSP_LABEL_WS, self._INSP_LABEL_WS, 1)
        try:
            self._insp_door_label.setBin('fixed', 41)
        except Exception as e:
            logger.log('ERROR', f"_build_inspector setBin door label {type(e).__name__}: {e}")

        self._insp_door_field = InputField(
            parent=self._inspector,
            position=(.12, -.46),
            scale=(.30, .055),
            default_value='',
            z=-1,
            eternal=True,
        )
        try:
            self._insp_door_field.setBin('fixed', 41)
        except Exception as e:
            logger.log('ERROR', f"_build_inspector setBin door field {type(e).__name__}: {e}")
        # submit_on + no-arg callback (InputField gotcha): on_submit fires with no
        # args and only when Enter is in submit_on. Read field.text at call time.
        self._insp_door_field.submit_on = ['enter']
        self._insp_door_field.on_submit = lambda f=self._insp_door_field: self._apply_door_name(f.text)

        self._build_behaviour_ui()
        self._build_trigger_ui()
        self._build_pickup_ui()

    def _update_inspector_door_field(self):
        """Refresh the door-name field from the current selection. Blocks only —
        hidden for empty / enemy / trigger / mixed selections, exactly like the
        model field. Shows the shared name when all selected blocks agree, '---'
        otherwise.
        """
        if getattr(self, '_insp_door_field', None) is None:
            return
        entities = list(self.selected)
        # Only blocks are doors — hide for any selection containing a non-block.
        has_nonblock = any((e in self.enemies or e in self.triggers) for e in entities)
        if not entities or has_nonblock:
            self._insp_door_label.enabled = False
            self._insp_door_field.enabled = False
            return
        self._insp_door_label.enabled = True
        self._insp_door_field.enabled = True
        names = {getattr(e, 'door_name', '') for e in entities}
        # Never assign '' to an InputField via .text here — empty is the field's
        # natural state and InputField handles it (it is NOT a Text with tag parsing,
        # so the Text(text='') align() crash does not apply). A mixed selection shows
        # '---' so the user sees the values differ.
        self._insp_door_field.text = next(iter(names)) if len(names) == 1 else '---'

    def _apply_door_name(self, value_str):
        """Commit the door-name field to every selected block via undo/redo.

        String edit (not numeric): '' is legal (un-naming). '---' means a mixed
        selection was left untouched — applying it would stamp the literal '---'
        onto every block, so skip it. Uses ChangePropertyCommand like the numeric
        fields, targeting the door_name attribute.
        """
        if not self.selected or value_str == '---':
            return
        value = value_str.strip()
        for e in list(self.selected):
            if getattr(e, 'destroy_source', None) is not None:
                continue
            if e in self.enemies or e in self.triggers:   # only blocks are doors
                continue
            old = getattr(e, 'door_name', '')
            if old == value:
                continue
            logger.log('INFO', f"Inspector door_name changed: entity@{[round(p,3) for p in e.position]} {old!r} -> {value!r}")
            cmd = ChangePropertyCommand(e, 'door_name', old, value)
            cmd.execute()
            self._history.push(cmd)

    def _update_inspector(self):
        if not self.selected:
            for f in self._insp_fields.values():
                f.text = ' '
            self._update_inspector_texture_swatch()
            self._update_inspector_model_field()
            self._update_inspector_door_field()
            self._refresh_behaviour_ui()
            self._refresh_trigger_ui()
            self._refresh_pickup_ui()
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
        self._insp_fields['scl_x'].text = shared_or_multi(lambda e: e.scale_x)
        self._insp_fields['scl_y'].text = shared_or_multi(lambda e: e.scale_y)
        self._insp_fields['scl_z'].text = shared_or_multi(lambda e: e.scale_z)
        self._update_inspector_texture_swatch()
        self._update_inspector_model_field()
        self._update_inspector_door_field()
        self._refresh_behaviour_ui()
        self._refresh_trigger_ui()
        self._refresh_pickup_ui()

    def _entity_texture_name(self, e):
        """Best-effort texture name for an entity, '' if none/unreadable."""
        if not getattr(e, 'texture', None):
            return ''
        return getattr(e.texture, 'name', str(e.texture))

    def _update_inspector_texture_swatch(self):
        """Refresh the inspector's texture swatch/name from the current selection.

        Shows the shared texture preview when every selected entity uses the
        same texture, '---' (no preview) on a mixed selection or empty selection.
        """
        if getattr(self, '_insp_tex_swatch', None) is None:
            return
        entities = list(self.selected)
        # Trigger volume colour/texture is editor chrome (not saved) — don't surface
        # it as if it were an editable texture. Treat a trigger-containing selection
        # like empty for the swatch.
        if not entities or any(e in self.triggers for e in entities):
            self._insp_tex_swatch.texture = None
            self._insp_tex_swatch.color = color.dark_gray
            self._insp_tex_name.text = '---'
            return
        names = {self._entity_texture_name(e) for e in entities}
        if len(names) != 1:
            self._insp_tex_swatch.texture = None
            self._insp_tex_swatch.color = color.dark_gray
            self._insp_tex_name.text = '---'
            return
        name = next(iter(names))
        path = asset_registry.get_texture_path(name) if name else None
        if path:
            try:
                self._insp_tex_swatch.texture = Texture(Path(path))
                self._insp_tex_swatch.color = color.white
            except Exception as e:
                logger.log('ERROR', f"_update_inspector_texture_swatch {type(e).__name__}: {e}")
                self._insp_tex_swatch.texture = None
                self._insp_tex_swatch.color = color.magenta
        else:
            # Built-in texture (e.g. 'white_cube') — not in the registry; flat colour preview.
            self._insp_tex_swatch.texture = None
            self._insp_tex_swatch.color = color.white
        self._insp_tex_name.text = name or '---'

    def _entity_model_name(self, e):
        """Best-effort model name for an entity, '' if none/unreadable.

        Mirrors _entity_texture_name: Ursina sets `model.name` to whatever
        string was assigned to Entity.model (built-in name like 'cube', or
        the full path for an imported asset), so this round-trips cleanly.
        """
        if not getattr(e, 'model', None):
            return ''
        return getattr(e.model, 'name', str(e.model))

    def _update_inspector_model_field(self):
        """Refresh the inspector's model field from the current selection.

        Blocks only (v1.3 Step 5) — hidden whenever the selection is empty or
        includes any enemy, so a mixed block+enemy selection can never look
        like it's offering to swap the enemy's model. Shows the shared model
        name when every selected block uses the same model, '---' on a mixed
        selection.
        """
        if getattr(self, '_insp_model_field', None) is None:
            return
        entities = list(self.selected)
        # Blocks only — hide for enemy/trigger/mixed (triggers reuse this lower band
        # for their action editor; only one section is ever enabled).
        has_nonblock = any((e in self.enemies or e in self.triggers) for e in entities)
        if not entities or has_nonblock:
            self._insp_model_label.enabled = False
            self._insp_model_field.enabled = False
            return
        self._insp_model_label.enabled = True
        self._insp_model_field.enabled = True
        names = {self._entity_model_name(e) for e in entities}
        if len(names) != 1:
            self._insp_model_name.text = '---'
            return
        name = next(iter(names))
        self._insp_model_name.text = (Path(name).stem if name else 'cube') or 'cube'

    # -------------------------------------------------------------------------
    # Behaviour-tree config (v1.4 Step 9)
    #
    # Enemy-only. Two stacked sections inside the inspector panel:
    #   1. Preset selector — a row of 4 Buttons (one per BehaviourTreeFactory
    #      preset). The active preset's button is tinted; clicking another pushes
    #      one ChangeBehaviourCommand. Decision PART 0A: inline buttons, not the
    #      asset-picker overlay — there are exactly 4 fixed string presets with no
    #      thumbnail, so the overlay machinery the texture/model picker needs at
    #      asset scale would be overkill.
    #   2. Waypoint list — shown ONLY when the (single) selected enemy's preset is
    #      "patrol_then_attack". Each waypoint is a row of 3 InputFields (x/y/z) +
    #      a "×" delete button; a "+ Waypoint" button appends [0,1,0]. Reordering
    #      (drag-to-reorder) is deliberately DEFERRED for Step 9 (decision PART 0B)
    #      — add/edit/delete only.
    #
    # Gating (decision PART 0D) is list-membership `e in self.enemies`, identical
    # to _update_inspector_model_field and _entity_snapshot — never a name check
    # (Rule 1). The preset buttons apply to ALL selected enemies; the per-waypoint
    # editor only renders for a SINGLE selected enemy (a coordinate UI is
    # meaningless across a heterogeneous multi-selection).
    #
    # All persistent widgets are eternal=True (survive play-in-editor teardown,
    # like the rest of the inspector). Transient per-refresh waypoint rows are NOT
    # eternal — destroy() is a no-op on eternal entities, so they would leak on
    # every rebuild (see gotcha: destroy() no-op on eternal entities).
    # -------------------------------------------------------------------------

    _BEHAV_PRESET_Y   = -0.38   # preset-button row y (reuses the model-field band:
                                # the model field is hidden for enemies, so the two
                                # never share screen space)
    _BEHAV_WP_LABEL_Y = -0.44   # "Waypoints" header y
    _BEHAV_WP_TOP_Y   = -0.49   # first waypoint row y
    _BEHAV_WP_ROW_H   = 0.055   # vertical pitch between waypoint rows

    def _build_behaviour_ui(self):
        """Build the persistent (eternal) behaviour widgets once: the section
        label, the 4 preset buttons, and the "Waypoints" header + "+ Waypoint"
        button. Per-waypoint rows are NOT built here — they are rebuilt every
        refresh by _rebuild_waypoint_rows because their count varies."""
        self._behav_label = Text(
            parent=self._inspector,
            text='Behaviour',
            position=(-.33, -.345),   # just below the Texture row (-.30); shown for
                                      # enemies only, where the Model row (which also
                                      # lives in the lower band) is hidden — so the
                                      # two never overlap on screen.
            color=color.light_gray,
            origin=(0, 0),
            z=-1,
            eternal=True,
            enabled=False,
        )
        self._behav_label.world_scale = Vec3(self._INSP_LABEL_WS, self._INSP_LABEL_WS, 1)
        try:
            self._behav_label.setBin('fixed', 41)
        except Exception as e:
            logger.log('ERROR', f"_build_behaviour_ui setBin label {type(e).__name__}: {e}")

        # Preset buttons — one per BehaviourTreeFactory preset, evenly spaced.
        self._behav_preset_buttons = {}
        presets = BehaviourTreeFactory.PRESETS
        n = len(presets)
        span = 0.86                      # total horizontal span (panel-local)
        step = span / n
        x0 = -span / 2 + step / 2
        for i, preset in enumerate(presets):
            btn = Button(
                parent=self._inspector,
                text=self._behav_preset_short(preset),
                position=(x0 + i * step, self._BEHAV_PRESET_Y),
                scale=(step * 0.92, 0.05),
                color=self._THEME_TILE_BG,
                z=-1,
                eternal=True,
                enabled=False,
            )
            btn.text_entity.world_scale = Vec3(10, 10, 1)
            # Capture preset by default-arg (loop-var binding) — no live-entity capture.
            btn.on_click = lambda p=preset: self._on_preset_click(p)
            try:
                btn.setBin('fixed', 41)
            except Exception as e:
                logger.log('ERROR', f"_build_behaviour_ui setBin btn {type(e).__name__}: {e}")
            self._behav_preset_buttons[preset] = btn

        # Waypoint section header + "+ Waypoint" button (persistent; rows are transient).
        self._behav_wp_label = Text(
            parent=self._inspector,
            text='Waypoints',
            position=(-.33, self._BEHAV_WP_LABEL_Y),
            color=color.light_gray,
            origin=(0, 0),
            z=-1,
            eternal=True,
            enabled=False,
        )
        self._behav_wp_label.world_scale = Vec3(self._INSP_LABEL_WS, self._INSP_LABEL_WS, 1)
        try:
            self._behav_wp_label.setBin('fixed', 41)
        except Exception as e:
            logger.log('ERROR', f"_build_behaviour_ui setBin wp label {type(e).__name__}: {e}")

        self._behav_add_button = Button(
            parent=self._inspector,
            text='+ Waypoint',
            position=(.15, self._BEHAV_WP_LABEL_Y),
            scale=(.30, 0.045),
            color=self._THEME_TILE_BG,
            z=-1,
            eternal=True,
            enabled=False,
        )
        self._behav_add_button.text_entity.world_scale = Vec3(10, 10, 1)
        self._behav_add_button.on_click = self._on_add_waypoint
        try:
            self._behav_add_button.setBin('fixed', 41)
        except Exception as e:
            logger.log('ERROR', f"_build_behaviour_ui setBin add btn {type(e).__name__}: {e}")

        # Transient waypoint-row widgets, rebuilt each refresh. Tracked so they can
        # be destroyed before rebuild (and so input()'s focus check can see the
        # coordinate InputFields).
        self._behav_wp_rows = []      # list of {'fields': [InputField,...], 'del': Button}

    def _behav_preset_short(self, preset):
        """Short button label for a preset (full names overflow a 4-button row)."""
        return {
            'default': 'default',
            'patrol_then_attack': 'patrol',
            'flee_when_low': 'flee',
            'aggressive': 'aggro',
        }.get(preset, preset)

    def _behaviour_enemies(self):
        """Selected entities that are enemies (decision PART 0D: membership test)."""
        return [e for e in self.selected if e in self.enemies]

    def _entity_preset(self, e):
        """Current preset name for an enemy entity, defaulting to 'default'."""
        cfg = getattr(e, 'behaviour_config', None)
        if cfg:
            return cfg.get('tree', 'default')
        return 'default'

    def _refresh_behaviour_ui(self):
        """Show/hide + repopulate all behaviour widgets for the current selection.

        Single refresh path (decision PART 4): called from _update_inspector on
        selection change AND from ChangeBehaviourCommand on undo/redo — there is
        no second refresh route. Hidden (enabled=False, never destroyed) for
        non-enemy / empty selections, mirroring _update_inspector_model_field."""
        if getattr(self, '_behav_label', None) is None:
            return
        enemies = self._behaviour_enemies()
        # The behaviour section and the model field are mutually exclusive: model
        # field shows for blocks only, behaviour for enemies only. The Behaviour
        # label reuses the model row's y-band, so only one is ever enabled.
        show = bool(enemies)
        self._behav_label.enabled = show
        for preset, btn in self._behav_preset_buttons.items():
            btn.enabled = show
        if not show:
            self._set_waypoint_section_visible(False)
            self._clear_waypoint_rows()
            return

        # Highlight the active preset. On a mixed-preset multi-selection no single
        # button is highlighted (all default tint).
        presets = {self._entity_preset(e) for e in enemies}
        active = next(iter(presets)) if len(presets) == 1 else None
        for preset, btn in self._behav_preset_buttons.items():
            btn.color = color.azure if preset == active else self._THEME_TILE_BG

        # Waypoint editor: only for a SINGLE enemy whose preset is patrol_then_attack.
        single = enemies[0] if len(enemies) == 1 else None
        if single is not None and self._entity_preset(single) == 'patrol_then_attack':
            self._set_waypoint_section_visible(True)
            self._rebuild_waypoint_rows(single)
        else:
            self._set_waypoint_section_visible(False)
            self._clear_waypoint_rows()

    def _set_waypoint_section_visible(self, visible):
        self._behav_wp_label.enabled = visible
        self._behav_add_button.enabled = visible

    def _clear_waypoint_rows(self):
        """Destroy all transient waypoint-row widgets. They are NOT eternal, so
        destroy() actually removes them (eternal entities ignore destroy())."""
        for row in getattr(self, '_behav_wp_rows', []):
            for f in row['fields']:
                destroy(f)
            destroy(row['del'])
        self._behav_wp_rows = []

    def _waypoints_of(self, e):
        """Current waypoint list for an enemy (list of [x,y,z] lists), possibly []."""
        cfg = getattr(e, 'behaviour_config', None) or {}
        wps = cfg.get('waypoints')
        return wps if isinstance(wps, list) else []

    def _rebuild_waypoint_rows(self, enemy):
        """Rebuild the per-waypoint editor rows for `enemy` from its config.

        Each row: 3 InputFields (x/y/z) + a "×" delete button. The "×" is DISABLED
        (not hidden — layout stays stable, decision PART 0B) when only one waypoint
        remains, enforcing the minimum of 1. There is no maximum."""
        self._clear_waypoint_rows()
        waypoints = self._waypoints_of(enemy)
        col_x = (-.34, -.12, .10)        # x/y/z field columns (panel-local)
        del_x = .32
        for idx, point in enumerate(waypoints):
            row_y = self._BEHAV_WP_TOP_Y - idx * self._BEHAV_WP_ROW_H
            fields = []
            for axis, cx in enumerate(col_x):
                field = InputField(
                    parent=self._inspector,
                    position=(cx, row_y),
                    scale=(.20, .045),
                    default_value=str(round(point[axis], 3)),
                    z=-1,
                )
                try:
                    field.setBin('fixed', 41)
                except Exception as e:
                    logger.log('ERROR', f"_rebuild_waypoint_rows setBin field {type(e).__name__}: {e}")
                # submit_on must be set or on_submit never fires; callback is no-arg
                # and reads field.text itself (InputField gotcha). i/axis/field bound
                # by default-arg so each row's callback edits the right coordinate.
                field.submit_on = ['enter']
                field.on_submit = (
                    lambda i=idx, a=axis, f=field: self._on_waypoint_edit(i, a, f.text)
                )
                fields.append(field)
            del_btn = Button(
                parent=self._inspector,
                text='x',
                position=(del_x, row_y),
                scale=(.05, .045),
                color=self._THEME_TILE_BG,
                z=-1,
            )
            del_btn.text_entity.world_scale = Vec3(10, 10, 1)
            del_btn.on_click = lambda i=idx: self._on_delete_waypoint(i)
            # Minimum-1 enforcement: disable (don't hide) the last remaining row's ×.
            if len(waypoints) <= 1:
                del_btn.disabled = True
                del_btn.color = color.dark_gray
            try:
                del_btn.setBin('fixed', 41)
            except Exception as e:
                logger.log('ERROR', f"_rebuild_waypoint_rows setBin del {type(e).__name__}: {e}")
            self._behav_wp_rows.append({'fields': fields, 'del': del_btn})

    # --- behaviour edit handlers (each pushes ONE whole-dict ChangeBehaviourCommand) ---

    def _new_config_for(self, enemy, tree=None, waypoints=None):
        """Build the next behaviour_config dict for `enemy`, starting from its
        current config so unrelated keys survive. `tree`/`waypoints` override
        when given. Decision PART 0C: callers snapshot the WHOLE dict."""
        cfg = dict(getattr(enemy, 'behaviour_config', None) or {})
        if tree is not None:
            cfg['tree'] = tree
        if waypoints is not None:
            cfg['waypoints'] = [list(p) for p in waypoints]
        cfg.setdefault('tree', 'default')
        return cfg

    def _on_preset_click(self, preset):
        """Apply `preset` to all selected enemies as one ChangeBehaviourCommand.

        Switching TO patrol_then_attack with no existing waypoints seeds a single
        default [0,1,0] so the waypoint editor has a row to show. Switching AWAY
        from patrol_then_attack KEEPS the waypoints key (decision PART 2) — switch
        back and the route is still there."""
        enemies = self._behaviour_enemies()
        if not enemies:
            return
        # ChangeBehaviourCommand applies the SAME new_config dict to every selected
        # enemy as one undo step. When seeding waypoints for patrol_then_attack on a
        # multi-selection, reuse whichever enemy already has a route (else the
        # default [0,1,0]); the per-waypoint editor is hidden for multi-selections
        # anyway, so a shared seed is the right behaviour.
        waypoints = None
        if preset == 'patrol_then_attack':
            existing = next((self._waypoints_of(e) for e in enemies if self._waypoints_of(e)), None)
            waypoints = existing if existing else [[0, 1, 0]]
        # Base the shared dict on the first enemy so unrelated keys are preserved.
        new_config = self._new_config_for(enemies[0], tree=preset, waypoints=waypoints)
        cmd = ChangeBehaviourCommand(self, enemies, new_config)
        cmd.execute()
        self._history.push(cmd)
        logger.log('INFO', f"Behaviour preset set: {preset} on {len(enemies)} enemies")

    def _on_add_waypoint(self):
        """Append a default [0,1,0] waypoint to the single selected enemy."""
        enemies = self._behaviour_enemies()
        if len(enemies) != 1:
            return
        e = enemies[0]
        waypoints = [list(p) for p in self._waypoints_of(e)] + [[0, 1, 0]]
        new_config = self._new_config_for(e, tree='patrol_then_attack', waypoints=waypoints)
        cmd = ChangeBehaviourCommand(self, [e], new_config)
        cmd.execute()
        self._history.push(cmd)
        logger.log('INFO', f"Waypoint added: now {len(waypoints)} on enemy")

    def _on_delete_waypoint(self, index):
        """Remove waypoint `index` from the single selected enemy (min 1 enforced
        at the button level — this is never reached for the last waypoint)."""
        enemies = self._behaviour_enemies()
        if len(enemies) != 1:
            return
        e = enemies[0]
        waypoints = [list(p) for p in self._waypoints_of(e)]
        if index < 0 or index >= len(waypoints) or len(waypoints) <= 1:
            return
        del waypoints[index]
        new_config = self._new_config_for(e, tree='patrol_then_attack', waypoints=waypoints)
        cmd = ChangeBehaviourCommand(self, [e], new_config)
        cmd.execute()
        self._history.push(cmd)
        logger.log('INFO', f"Waypoint {index} deleted: now {len(waypoints)} on enemy")

    def _on_waypoint_edit(self, index, axis, value_str):
        """Set waypoint `index`'s `axis` coordinate from a submitted field value."""
        enemies = self._behaviour_enemies()
        if len(enemies) != 1:
            return
        try:
            value = float(value_str)
        except (ValueError, TypeError):
            return
        e = enemies[0]
        waypoints = [list(p) for p in self._waypoints_of(e)]
        if index < 0 or index >= len(waypoints) or axis not in (0, 1, 2):
            return
        if waypoints[index][axis] == value:
            return    # no-op edit — don't push an empty undo step
        waypoints[index][axis] = value
        new_config = self._new_config_for(e, tree='patrol_then_attack', waypoints=waypoints)
        cmd = ChangeBehaviourCommand(self, [e], new_config)
        cmd.execute()
        self._history.push(cmd)
        logger.log('INFO', f"Waypoint {index} axis {axis} set to {value}")

    def _behaviour_typing(self):
        """True while any behaviour waypoint InputField is focused — gate Delete /
        bookmark keys exactly like _hier_typing and the inspector-field check."""
        for row in getattr(self, '_behav_wp_rows', []):
            for f in row['fields']:
                if getattr(f, 'active', False):
                    return True
        return False

    def _door_name_typing(self):
        """True while the door-name InputField is focused — gate Delete / bookmark
        keys so typing a name like 'door1' can't recall a camera bookmark or delete
        the selection. The door field lives outside _insp_fields (string, not numeric),
        so the existing inspector-field guard does not cover it."""
        f = getattr(self, '_insp_door_field', None)
        return bool(f is not None and getattr(f, 'active', False))

    # -------------------------------------------------------------------------
    # Trigger action editor (v1.5 Step 6)
    #
    # Trigger-only inspector section, mutually exclusive with the Model field
    # (blocks) and Behaviour section (enemies) — all three reuse the lower band
    # and gate on list membership, so only one is ever enabled. Two sub-lists
    # (on_enter / on_exit); each action is a transient row: a type button that
    # cycles through ACTION_TYPES on click, an optional target InputField (only
    # for open_door), and an [x] remove button. A persistent [+] add button per
    # list appends a default action. Every edit pushes ONE ChangeTriggerActionsCommand
    # snapshotting both whole lists (undo granularity mirrors ChangeBehaviourCommand).
    # -------------------------------------------------------------------------
    ACTION_TYPES = ['kill_plane', 'checkpoint', 'open_door', 'win_condition']

    _TRIG_ENTER_LABEL_Y = -0.30   # "on_enter" header y
    _TRIG_ENTER_TOP_Y   = -0.345  # first on_enter row y
    _TRIG_EXIT_LABEL_Y  = -0.46   # "on_exit" header y (leaves room for ~2 enter rows)
    _TRIG_EXIT_TOP_Y    = -0.505  # first on_exit row y
    _TRIG_ROW_H         = 0.05    # vertical pitch between action rows

    def _build_trigger_ui(self):
        """Persistent trigger-section widgets (two list headers + two add buttons).
        Action rows are transient (rebuilt each refresh, tracked in _trig_rows)."""
        self._trig_enter_label = Text(
            parent=self._inspector, text='on_enter',
            position=(-.33, self._TRIG_ENTER_LABEL_Y),
            color=color.light_gray, origin=(0, 0), z=-1, eternal=True, enabled=False,
        )
        self._trig_enter_label.world_scale = Vec3(self._INSP_LABEL_WS, self._INSP_LABEL_WS, 1)
        self._trig_enter_add = Button(
            parent=self._inspector, text='+',
            position=(.30, self._TRIG_ENTER_LABEL_Y), scale=(.06, .045),
            color=self._THEME_TILE_BG, z=-1, eternal=True, enabled=False,
        )
        self._trig_enter_add.text_entity.world_scale = Vec3(10, 10, 1)
        self._trig_enter_add.on_click = lambda: self._on_add_trigger_action('on_enter')

        self._trig_exit_label = Text(
            parent=self._inspector, text='on_exit',
            position=(-.33, self._TRIG_EXIT_LABEL_Y),
            color=color.light_gray, origin=(0, 0), z=-1, eternal=True, enabled=False,
        )
        self._trig_exit_label.world_scale = Vec3(self._INSP_LABEL_WS, self._INSP_LABEL_WS, 1)
        self._trig_exit_add = Button(
            parent=self._inspector, text='+',
            position=(.30, self._TRIG_EXIT_LABEL_Y), scale=(.06, .045),
            color=self._THEME_TILE_BG, z=-1, eternal=True, enabled=False,
        )
        self._trig_exit_add.text_entity.world_scale = Vec3(10, 10, 1)
        self._trig_exit_add.on_click = lambda: self._on_add_trigger_action('on_exit')

        for w in (self._trig_enter_label, self._trig_enter_add,
                  self._trig_exit_label, self._trig_exit_add):
            try:
                w.setBin('fixed', 41)
            except Exception as e:
                logger.log('ERROR', f"_build_trigger_ui setBin {type(e).__name__}: {e}")

        # Transient row widgets, rebuilt each refresh; tracked so input()'s focus
        # check can see the target InputFields (NOT eternal — they must destroy()).
        self._trig_rows = []   # list of {'type': Button, 'target': InputField|None, 'del': Button}

    def _selected_trigger(self):
        """The single selected trigger, or None (editor shows the action list only
        for a single-trigger selection — multi-edit of action lists is out of scope)."""
        trigs = [e for e in self.selected if e in self.triggers]
        return trigs[0] if len(trigs) == 1 else None

    def _set_trigger_section_visible(self, visible):
        for w in (self._trig_enter_label, self._trig_enter_add,
                  self._trig_exit_label, self._trig_exit_add):
            w.enabled = visible

    def _clear_trigger_rows(self):
        """Destroy all transient trigger-row widgets (NOT eternal, so destroy works)."""
        for row in getattr(self, '_trig_rows', []):
            destroy(row['type'])
            if row['target'] is not None:
                destroy(row['target'])
            destroy(row['del'])
        self._trig_rows = []

    def _refresh_trigger_ui(self):
        """Show/hide + repopulate the trigger action editor for the current selection.
        Single refresh path: called from _update_inspector AND from
        ChangeTriggerActionsCommand on undo/redo."""
        if getattr(self, '_trig_enter_label', None) is None:
            return
        trigger = self._selected_trigger()
        show = trigger is not None
        self._set_trigger_section_visible(show)
        self._clear_trigger_rows()
        if not show:
            return
        self._build_trigger_rows(trigger, 'on_enter',
                                 self._TRIG_ENTER_TOP_Y, getattr(trigger, 'on_enter_actions', []))
        self._build_trigger_rows(trigger, 'on_exit',
                                 self._TRIG_EXIT_TOP_Y, getattr(trigger, 'on_exit_actions', []))

    def _build_trigger_rows(self, trigger, which, top_y, actions):
        """Build the transient action rows for one list (on_enter or on_exit)."""
        for idx, action in enumerate(actions):
            row_y = top_y - idx * self._TRIG_ROW_H
            name = action.get('action', '?')
            # Type button — click cycles to the next action type.
            type_btn = Button(
                parent=self._inspector, text=name,
                position=(-.14, row_y), scale=(.34, .042),
                color=self._THEME_TILE_BG, z=-1,
            )
            type_btn.text_entity.world_scale = Vec3(8, 8, 1)
            type_btn.on_click = lambda w=which, i=idx: self._on_cycle_trigger_action(w, i)
            # Target field — only for open_door (the one action that takes a param).
            target_field = None
            if name == 'open_door':
                target_field = InputField(
                    parent=self._inspector,
                    position=(.18, row_y), scale=(.18, .042),
                    default_value=str(action.get('target', '')), z=-1,
                )
                target_field.submit_on = ['enter']
                target_field.on_submit = (
                    lambda w=which, i=idx, f=target_field: self._on_trigger_target_edit(w, i, f.text)
                )
            del_btn = Button(
                parent=self._inspector, text='x',
                position=(.33, row_y), scale=(.05, .042),
                color=self._THEME_TILE_BG, z=-1,
            )
            del_btn.text_entity.world_scale = Vec3(8, 8, 1)
            del_btn.on_click = lambda w=which, i=idx: self._on_remove_trigger_action(w, i)
            for wdg in (type_btn, del_btn) + ((target_field,) if target_field else ()):
                try:
                    wdg.setBin('fixed', 41)
                except Exception as e:
                    logger.log('ERROR', f"_build_trigger_rows setBin {type(e).__name__}: {e}")
            self._trig_rows.append({'type': type_btn, 'target': target_field, 'del': del_btn})

    def _trigger_lists(self, trigger):
        """Return deep copies of (on_enter, on_exit) for mutation before committing."""
        return ([dict(a) for a in getattr(trigger, 'on_enter_actions', [])],
                [dict(a) for a in getattr(trigger, 'on_exit_actions', [])])

    def _commit_trigger_lists(self, trigger, on_enter, on_exit):
        """Push one ChangeTriggerActionsCommand and execute it (refreshes the UI)."""
        cmd = ChangeTriggerActionsCommand(self, trigger, on_enter, on_exit)
        cmd.execute()
        self._history.push(cmd)

    def _on_add_trigger_action(self, which):
        trigger = self._selected_trigger()
        if trigger is None:
            return
        on_enter, on_exit = self._trigger_lists(trigger)
        target_list = on_enter if which == 'on_enter' else on_exit
        target_list.append({'action': self.ACTION_TYPES[0]})   # default kill_plane
        self._commit_trigger_lists(trigger, on_enter, on_exit)

    def _on_remove_trigger_action(self, which, index):
        trigger = self._selected_trigger()
        if trigger is None:
            return
        on_enter, on_exit = self._trigger_lists(trigger)
        target_list = on_enter if which == 'on_enter' else on_exit
        if 0 <= index < len(target_list):
            target_list.pop(index)
            self._commit_trigger_lists(trigger, on_enter, on_exit)

    def _on_cycle_trigger_action(self, which, index):
        trigger = self._selected_trigger()
        if trigger is None:
            return
        on_enter, on_exit = self._trigger_lists(trigger)
        target_list = on_enter if which == 'on_enter' else on_exit
        if 0 <= index < len(target_list):
            cur = target_list[index].get('action', self.ACTION_TYPES[0])
            nxt = self.ACTION_TYPES[(self.ACTION_TYPES.index(cur) + 1) % len(self.ACTION_TYPES)] \
                if cur in self.ACTION_TYPES else self.ACTION_TYPES[0]
            # Replace the action, dropping params that don't apply to the new type
            # (e.g. a stale 'target' when cycling off open_door).
            new_action = {'action': nxt}
            if nxt == 'open_door':
                new_action['target'] = target_list[index].get('target', '')
            target_list[index] = new_action
            self._commit_trigger_lists(trigger, on_enter, on_exit)

    def _on_trigger_target_edit(self, which, index, value_str):
        trigger = self._selected_trigger()
        if trigger is None:
            return
        on_enter, on_exit = self._trigger_lists(trigger)
        target_list = on_enter if which == 'on_enter' else on_exit
        if 0 <= index < len(target_list) and target_list[index].get('action') == 'open_door':
            target_list[index]['target'] = value_str.strip()
            self._commit_trigger_lists(trigger, on_enter, on_exit)

    def _trigger_typing(self):
        """True while any trigger open_door target InputField is focused — gate
        Delete / bookmark keys, same as the other inspector typing guards."""
        for row in getattr(self, '_trig_rows', []):
            f = row.get('target')
            if f is not None and getattr(f, 'active', False):
                return True
        return False

    # -------------------------------------------------------------------------
    # Pickup config editor (v1.5 Step 13)
    #
    # Pickup-only inspector section, mutually exclusive with the Model field
    # (blocks), Behaviour section (enemies), and trigger action lists — all reuse
    # the lower band and gate on list membership, so only one is ever enabled.
    # Unlike triggers' repeating action lists, a pickup's config is three scalar
    # fields: a Type toggle (ammo/weapon), a Weapon toggle (pistol/shotgun/rifle),
    # and an Amount field (ammo pickups only). Every edit pushes ONE
    # ChangePickupConfigCommand snapshotting the whole config dict (same
    # whole-dict granularity as ChangeTriggerActionsCommand/ChangeBehaviourCommand).
    # -------------------------------------------------------------------------
    PICKUP_TYPES = ['ammo', 'weapon']

    _PICKUP_TYPE_Y   = -0.30   # "Type" row y
    _PICKUP_WEAPON_Y = -0.36   # "Weapon" row y
    _PICKUP_AMOUNT_Y = -0.42   # "Amount" row y (ammo pickups only)

    def _build_pickup_ui(self):
        """Persistent pickup-section widgets: Type toggle, Weapon toggle, Amount field."""
        self._pickup_type_label = Text(
            parent=self._inspector, text='type',
            position=(-.33, self._PICKUP_TYPE_Y),
            color=color.light_gray, origin=(0, 0), z=-1, eternal=True, enabled=False,
        )
        self._pickup_type_label.world_scale = Vec3(self._INSP_LABEL_WS, self._INSP_LABEL_WS, 1)
        self._pickup_type_btn = Button(
            parent=self._inspector, text='ammo',
            position=(.10, self._PICKUP_TYPE_Y), scale=(.34, .042),
            color=self._THEME_TILE_BG, z=-1, eternal=True, enabled=False,
        )
        self._pickup_type_btn.text_entity.world_scale = Vec3(8, 8, 1)
        self._pickup_type_btn.on_click = self._on_cycle_pickup_type

        self._pickup_weapon_label = Text(
            parent=self._inspector, text='weapon',
            position=(-.33, self._PICKUP_WEAPON_Y),
            color=color.light_gray, origin=(0, 0), z=-1, eternal=True, enabled=False,
        )
        self._pickup_weapon_label.world_scale = Vec3(self._INSP_LABEL_WS, self._INSP_LABEL_WS, 1)
        self._pickup_weapon_btn = Button(
            parent=self._inspector, text='pistol',
            position=(.10, self._PICKUP_WEAPON_Y), scale=(.34, .042),
            color=self._THEME_TILE_BG, z=-1, eternal=True, enabled=False,
        )
        self._pickup_weapon_btn.text_entity.world_scale = Vec3(8, 8, 1)
        self._pickup_weapon_btn.on_click = self._on_cycle_pickup_weapon

        self._pickup_amount_label = Text(
            parent=self._inspector, text='amount',
            position=(-.33, self._PICKUP_AMOUNT_Y),
            color=color.light_gray, origin=(0, 0), z=-1, eternal=True, enabled=False,
        )
        self._pickup_amount_label.world_scale = Vec3(self._INSP_LABEL_WS, self._INSP_LABEL_WS, 1)
        self._pickup_amount_field = InputField(
            parent=self._inspector,
            position=(.10, self._PICKUP_AMOUNT_Y), scale=(.34, .042),
            default_value='30', z=-1, eternal=True, enabled=False,
        )
        self._pickup_amount_field.submit_on = ['enter']
        self._pickup_amount_field.on_submit = (
            lambda f=self._pickup_amount_field: self._on_pickup_amount_edit(f.text)
        )

        for w in (self._pickup_type_label, self._pickup_type_btn,
                  self._pickup_weapon_label, self._pickup_weapon_btn,
                  self._pickup_amount_label, self._pickup_amount_field):
            try:
                w.setBin('fixed', 41)
            except Exception as e:
                logger.log('ERROR', f"_build_pickup_ui setBin {type(e).__name__}: {e}")

    def _selected_pickup(self):
        """The single selected pickup, or None (editor shows the config editor only
        for a single-pickup selection — multi-edit is out of scope, same rule as
        _selected_trigger)."""
        picks = [e for e in self.selected if e in self.pickups]
        return picks[0] if len(picks) == 1 else None

    def _set_pickup_section_visible(self, visible, show_amount):
        for w in (self._pickup_type_label, self._pickup_type_btn,
                  self._pickup_weapon_label, self._pickup_weapon_btn):
            w.enabled = visible
        self._pickup_amount_label.enabled = visible and show_amount
        self._pickup_amount_field.enabled = visible and show_amount

    def _refresh_pickup_ui(self):
        """Show/hide + repopulate the pickup config editor for the current selection.
        Single refresh path: called from _update_inspector AND from
        ChangePickupConfigCommand on undo/redo."""
        if getattr(self, '_pickup_type_btn', None) is None:
            return
        pickup = self._selected_pickup()
        show = pickup is not None
        if not show:
            self._set_pickup_section_visible(False, False)
            return
        config = getattr(pickup, 'pickup_config', self._PICKUP_DEFAULT_CONFIG)
        is_ammo = config.get('pickup_type', 'ammo') == 'ammo'
        self._set_pickup_section_visible(True, is_ammo)
        self._pickup_type_btn.text = config.get('pickup_type', 'ammo')
        self._pickup_weapon_btn.text = config.get('weapon_type', 'pistol')
        self._pickup_amount_field.text = str(config.get('amount', 30))

    def _commit_pickup_config(self, pickup, config):
        """Push one ChangePickupConfigCommand and execute it (refreshes the UI)."""
        cmd = ChangePickupConfigCommand(self, pickup, config)
        cmd.execute()
        self._history.push(cmd)

    def _on_cycle_pickup_type(self):
        pickup = self._selected_pickup()
        if pickup is None:
            return
        config = dict(getattr(pickup, 'pickup_config', self._PICKUP_DEFAULT_CONFIG))
        cur = config.get('pickup_type', 'ammo')
        config['pickup_type'] = self.PICKUP_TYPES[(self.PICKUP_TYPES.index(cur) + 1) % len(self.PICKUP_TYPES)] \
            if cur in self.PICKUP_TYPES else self.PICKUP_TYPES[0]
        self._commit_pickup_config(pickup, config)

    def _on_cycle_pickup_weapon(self):
        pickup = self._selected_pickup()
        if pickup is None:
            return
        config = dict(getattr(pickup, 'pickup_config', self._PICKUP_DEFAULT_CONFIG))
        weapon_names = list(WEAPON_TYPES.keys())
        cur = config.get('weapon_type', 'pistol')
        config['weapon_type'] = weapon_names[(weapon_names.index(cur) + 1) % len(weapon_names)] \
            if cur in weapon_names else weapon_names[0]
        self._commit_pickup_config(pickup, config)

    def _on_pickup_amount_edit(self, value_str):
        pickup = self._selected_pickup()
        if pickup is None:
            return
        try:
            amount = int(value_str)
        except ValueError:
            self._refresh_pickup_ui()   # revert the field to the last valid value
            return
        config = dict(getattr(pickup, 'pickup_config', self._PICKUP_DEFAULT_CONFIG))
        config['amount'] = amount
        self._commit_pickup_config(pickup, config)

    def _pickup_typing(self):
        """True while the pickup amount InputField is focused — gate Delete /
        bookmark keys, same as the other inspector typing guards."""
        f = getattr(self, '_pickup_amount_field', None)
        return bool(f is not None and getattr(f, 'active', False))

    def _apply_inspector_value(self, key, value_str):
        # Read current selection at call time (not capture time) so the lambda
        # always operates on whatever is selected when Enter is pressed.
        if not self.selected or value_str in ('---', ''):
            return
        try:
            value = float(value_str)
        except (ValueError, TypeError):
            return
        attr_map = {
            'pos_x': 'x', 'pos_y': 'y', 'pos_z': 'z',
            'scl_x': 'scale_x', 'scl_y': 'scale_y', 'scl_z': 'scale_z',
        }
        attr = attr_map.get(key)
        if not attr:
            return
        for e in list(self.selected):
            # FIXED: guard against stale destroyed entity refs in selection set
            if getattr(e, 'destroy_source', None) is not None:
                continue
            old = getattr(e, attr, 0)
            logger.log('INFO', f"Inspector property changed: entity@{[round(p,3) for p in e.position]} field={key} {old} -> {value}")
            cmd = ChangePropertyCommand(e, attr, old, value)
            cmd.execute()
            self._history.push(cmd)
        # Update only this field in-place; do not clear selection or rebuild
        # the inspector (which would steal focus and prevent further edits).
        field = self._insp_fields.get(key)
        if field is not None:
            field.text = str(round(value, 3))
        if self.gizmo is not None:
            self.gizmo.refresh()

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
        for widget in [self._inspector, self.hierarchy.panel, self._insp_title,
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

    def toggle_play(self):
        if self._play_mode:
            self._exit_play_mode()
        else:
            self._enter_play_mode()

    def _enter_play_mode(self):
        self._play_level_snapshot = self._build_level_data()
        logger.log('INFO', f'Play-in-editor started ({len(self._play_level_snapshot)} entities in snapshot)')
        if self._editor_camera:
            self._saved_cam_pos = Vec3(self._editor_camera.position)
            self._saved_cam_rot = Vec3(self._editor_camera.rotation_x,
                                       self._editor_camera.rotation_y,
                                       self._editor_camera.rotation_z)
        self._set_editor_ui_visible(False)
        self.gizmo.root.enabled = False
        # Disable EditorCamera so it stops driving camera position/rotation.
        # FirstPersonController's __init__ sets camera.parent = self, taking over.
        if self._editor_camera:
            self._editor_camera.enabled = False
        self._play_mode = True
        self._spawn_gameplay_from_snapshot(self._play_level_snapshot)
        mouse.locked = True
        mouse.visible = False

    def _exit_play_mode(self):
        """Tear down gameplay entities, reset game state, and restore editor UI."""
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
        self._play_mode = False
        self._restore_editor_level()
        self._set_editor_ui_visible(True)
        self.gizmo.refresh()
        mouse.locked = False
        mouse.visible = True
        # Re-enable EditorCamera; on_enable reparents camera back to the editor rig.
        if self._editor_camera:
            self._editor_camera.enabled = True
        if self._saved_cam_pos is not None and self._editor_camera:
            self._editor_camera.position = self._saved_cam_pos
            self._editor_camera.rotation_x = self._saved_cam_rot.x
            self._editor_camera.rotation_y = self._saved_cam_rot.y
            self._editor_camera.rotation_z = self._saved_cam_rot.z
            self._saved_cam_pos = None
            self._saved_cam_rot = None

    def _restore_editor_level(self):
        """Rebuild editor blocks/enemies from the play snapshot after exiting play mode.

        Any entity in self.blocks/self.enemies that was destroyed by scene teardown
        is replaced with a fresh editor entity built from the snapshot data.
        """
        if not self._play_level_snapshot:
            return
        # Destroy surviving refs (may still be alive if nothing cleared them)
        for e in self.blocks + self.enemies + self.triggers + self.pickups:
            if getattr(e, 'destroy_source', None) is None:
                destroy(e)
        self.blocks.clear()
        self.enemies.clear()
        self.triggers.clear()
        self.pickups.clear()
        self.selected.clear()

        for entry in load_level_data(self._play_level_snapshot):
            if entry['type'] == 'trigger':
                # v1.5 Step 6: rebuild the editor trigger volume from the snapshot
                # so it survives the F5 play-in-editor round-trip (same role as the
                # enemy/block rebuilds below).
                self._make_trigger_entity(
                    entry['position'], entry['scale'],
                    entry['on_enter'], entry['on_exit'],
                )
                continue
            if entry['type'] == 'pickup':
                # v1.5 Step 13: rebuild the editor pickup sphere from the snapshot
                # so it survives the F5 play-in-editor round-trip (same role as
                # the trigger rebuild above).
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
                    origin_y=-0.5,
                )
                new_entity.enemy_hp   = entry['hp']
                new_entity.enemy_type = entry['enemy_type']
                # v1.4 Step 8: restore behaviour-config on the rebuilt placeholder
                # so it survives the play-in-editor (F5) round-trip — same
                # attribute load_existing_level/_build_level_data use.
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
                    collider='box',
                )
                new_entity._original_color = new_entity.color
                self.blocks.append(new_entity)

        logger.log('INFO', f'Editor level restored: {len(self.blocks)} blocks, {len(self.enemies)} enemies, {len(self.triggers)} triggers, {len(self.pickups)} pickups')
        self._refresh_hierarchy()

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
                    texture=entry['texture'],
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
                self._exit_play_mode()
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
            self._enter_play_mode()
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
            if (any(f.active for f in self._insp_fields.values())
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
            typing = (any(f.active for f in self._insp_fields.values())
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
            if (self._insp_tex_swatch and self.selected
                    and self._is_over_world_panel(self._insp_tex_swatch)):
                self.browser.open_texture_picker()
                return
            # Model field click opens the model picker — blocks only (see
            # open_model_picker's own enemy guard); same priority as the texture
            # swatch, before the blanket inspector-panel guard.
            if (self._insp_model_field and self.selected
                    and not any(e in self.enemies for e in self.selected)
                    and self._is_over_world_panel(self._insp_model_field)):
                self.browser.open_model_picker()
                return
            if self._inspector and self._is_over_panel(self._inspector):
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
                or (self._inspector and self._is_over_panel(self._inspector))
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

            if self._inspector and self._is_over_panel(self._inspector):
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

        with open(self.filename, 'w') as f:
            json.dump(deduped, f, indent=4)
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


if __name__ == '__main__':
    loadPrcFileData('', 'framebuffer-multisample 1\nmultisamples 4')

    # Patch shader source objects BEFORE Ursina() — Ursina's own window/UI entities
    # compile shaders during __init__, so the patch must happen before App() runs.
    from Scripts.compat import patch_shaders_to_glsl120 as _patch_shaders_to_glsl120
    _patch_shaders_to_glsl120()
    app = Ursina(title="Level Editor")

    # Ursina's HotReloader binds F5 to scene.clear() + reload. That fires BEFORE
    # LevelEditor.input('f5'), wiping self.blocks/self.enemies and making the
    # snapshot read destroyed entities. Disable it — we use F5 for play-in-editor.
    try:
        from ursina import application as _ursina_app
        if getattr(_ursina_app, 'hot_reloader', None) is not None:
            _ursina_app.hot_reloader.enabled = False
    except Exception as e:
        logger.log('WARN', f'hot_reloader disable failed: {type(e).__name__}: {e}')

    window.color = color.rgb(50, 50, 60)
    render.setAntialias(AntialiasAttrib.MAuto)
    render2d.setAntialias(AntialiasAttrib.MAuto)
    window.title = 'Level Editor'
    window.exit_button.visible = True
    window.fps_counter.enabled = True
    # Ursina's tiny top-right entity/collider counters are replaced by the editor's
    # own legible, labelled stats strip (LevelEditor._stats_text) — disable them so
    # they don't double up under the new horizontal toolbar.
    try:
        window.entity_counter.enabled = False
        window.collider_counter.enabled = False
    except Exception as e:
        logger.log('WARN', f'disable built-in counters failed: {type(e).__name__}: {e}')
    # Drop the fps counter below the toolbar band so it never overlaps the row.
    try:
        window.fps_counter.y = 0.43
    except Exception as e:
        logger.log('WARN', f'reposition fps_counter failed: {type(e).__name__}: {e}')
    window.borderless = False
    window.size = (1280, 720)

    ground = Entity(
        model='plane',
        collider='box',
        y=-0.5,
        scale=(100, 1, 100),
        texture=Texture(Path(str(PROJECT_ROOT / 'assets/textures/floor_ground_grass.png'))),
        texture_scale=(50, 50),
        eternal=True,
    )

    editor = LevelEditor()
    editor_cam = EditorCamera()
    editor._editor_camera = editor_cam

    editor._hint_text = Text(
        text="Drag from Models tab: Place block/enemy | Shift+LClick: Select | RDrag: Box-select\n"
             "Delete: Remove selected | Ctrl+Z: Undo | Ctrl+Y/Shift+Z: Redo | Esc: Cancel drag\n"
             "Ctrl+S: Save | G: Cycle snap | F5: Play-in-editor | Scroll: browse cards\n"
             "Ctrl+1-5: Save cam bookmark | 1-5: Recall bookmark",
        parent=camera.ui,
        position=(-.88, .48),
        origin=(-.5, .5),
        scale=0.75,
        z=-1,
        eternal=True,
    )
    editor._attach_hint_background()   # dark backing panel sized to the legend (Change D)
    editor._apply_layout()

    app.run()
