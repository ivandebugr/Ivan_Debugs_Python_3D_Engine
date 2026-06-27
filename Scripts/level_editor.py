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
    MoveEntityCommand, ChangeTextureCommand, ChangeModelCommand, ChangeColourCommand,
    ChangePropertyCommand, _resolve_model
)
from Scripts.session_logger import SessionLogger
from Scripts.level_io import load_level_data, DEFAULT_MODEL
from Scripts.asset_registry import asset_registry, CATEGORY_DIRS, CATEGORY_EXTENSIONS

logger = SessionLogger()

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
    ]

    def __init__(self):
        """Build all editor UI, load level and prefs; attach to app before calling app.run()."""
        super().__init__()
        self.blocks = []
        self.enemies = []
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

        # Gizmo drag state
        self._gizmo_root = None
        self._gizmo_drag_axis = None
        self._gizmo_drag_start_mouse = None
        self._gizmo_drag_start_pos = None

        # Inspector / hierarchy panel refs
        self._inspector = None
        self._insp_title = None
        self._insp_fields = {}
        self._insp_tex_swatch = None
        self._insp_tex_name = None
        self._insp_model_label = None
        self._insp_model_field = None

        # Asset picker overlay (v1.3 Step 4 texture, Step 5 model) — shared mechanics,
        # one overlay instance per category, keyed by category name.
        self._asset_picker_panels = {}   # {category: panel Entity}
        self._asset_picker_cells = {}    # {category: [(bg_entity, name), ...]}
        self.asset_picker_open = None    # category name while open, else None
        self._asset_picker_on_select = None
        self._hier_panel = None
        self._hier_buttons = []
        self._hier_scroll_bar = None
        self.hierarchy_scroll = 0
        # Hierarchy search/filter + collapsible sections (Change B).
        self._hier_search_field = None     # InputField pinned above the list
        self._hier_filter = ''             # lower-cased substring; '' = show all
        self._hier_collapsed = {'block': False, 'enemy': False}  # per-section fold state
        self._hier_header_buttons = {}     # {'block': Button, 'enemy': Button}
        self._hier_swatches = []           # per-row colour swatch quads (parallel to _hier_buttons)

        # Grid snap
        self.snap_values = list(EDITOR_GRID_SNAPS)
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

        # Asset browser — panel/tab/card state.
        self._browser_panel = None
        self._browser_tab = 'texture'          # active tab: 'texture' | 'model' | 'sound'
        self._browser_tab_buttons = {}         # {category: Button}
        self._browser_cards = {}               # {category: list[(bg, icon, label, name)]}
        self._browser_empty_labels = {}        # {category: Text} shown when a category is empty
        self._browser_scroll = {'texture': 0, 'model': 0, 'sound': 0}
        self._selected_asset = None            # (category, name) of the highlighted card
        self._browser_last_click = (None, 0.0)  # ((category, name), wall-clock t) for dbl-click
        self._browser_card_assets = {}         # {(category, name): asset_dict} for draggable built-in models
        # Scroll indicators (up/down arrows) — created by _build_asset_browser.
        self._browser_scroll_up = None
        self._browser_scroll_down = None

        # Texture hot-reload (v1.3 Step 3). subscribe_texture()/unsubscribe_texture()
        # are the hooks Step 4 (texture picker) calls to wire blocks into live reload.
        self._texture_subscribers = {}   # {texture_name: [entity, ...]}
        # Reusable bottom-of-screen toast (created lazily by _show_status_notice).
        self._status_notice = None

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
            on_click=self._open_import_dialog,
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
        self._build_hierarchy()
        self._build_gizmo()
        self._build_asset_browser()

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
        asset_registry.register_callback('texture', self._on_texture_changed)
        asset_registry.register_callback('model', self._on_model_changed)
        asset_registry.register_callback('sound', self._on_sound_changed)
        self._poll_assets()

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

        # Hierarchy — flush left
        if self._hier_panel is not None:
            self._hier_panel.x = -half_w + hier_w * 0.5
            self._hier_panel.y = 0
            self._hier_panel.scale_x = hier_w
            self._hier_panel.scale_y = self._LAYOUT_PANEL_H

        # Inspector — flush right
        if self._inspector is not None:
            self._inspector.x = half_w - insp_w * 0.5
            self._inspector.y = 0
            self._inspector.scale_x = insp_w
            self._inspector.scale_y = self._LAYOUT_PANEL_H

        # Asset browser — full-width strip near the bottom; cards/tabs are
        # centred about x=0 so only the panel quad's width tracks aspect.
        if self._browser_panel is not None:
            self._browser_panel.x = 0
            self._browser_panel.y = self._BROWSER_Y
            self._browser_panel.scale_x = aspect
            self._browser_panel.scale_y = self._BROWSER_H

        # Browser tab buttons — reposition to left edge, sized to fit.
        self._layout_browser_tabs(half_w)

        # Browser scroll indicators — flush to right of the card grid, at top/bottom.
        scroll_ind_x = half_w - self._LAYOUT_INSP_W - 0.03
        if self._browser_scroll_up is not None:
            self._browser_scroll_up.x = scroll_ind_x
            self._browser_scroll_up.y = self._BROWSER_Y + self._BROWSER_H * 0.5 - 0.02
        if self._browser_scroll_down is not None:
            self._browser_scroll_down.x = scroll_ind_x
            self._browser_scroll_down.y = self._BROWSER_Y - self._BROWSER_H * 0.5 + 0.02
        self._update_browser_scroll_indicators()

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

    def _layout_browser_tabs(self, half_w):
        """Reposition browser tab buttons relative to the current aspect ratio."""
        tabs = getattr(self, '_browser_tab_buttons', None)
        if not tabs:
            return
        tab_order = ('texture', 'model', 'sound')
        tab_w = 0.12
        tab_gap = 0.01
        start_x = -half_w + self._LAYOUT_HIER_W + tab_w * 0.5 + 0.01
        for i, cat in enumerate(tab_order):
            btn = tabs.get(cat)
            if btn is None:
                continue
            btn.x = start_x + i * (tab_w + tab_gap)
            btn.y = self._TAB_Y
            btn.scale_x = tab_w
            btn.scale_y = 0.035

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
            'scale': (e.scale_x, e.scale_y, e.scale_z),
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

    def _update_inspector(self):
        if not self.selected:
            for f in self._insp_fields.values():
                f.text = ' '
            self._update_inspector_texture_swatch()
            self._update_inspector_model_field()
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
        if not entities:
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
        has_enemy = any(e in self.enemies for e in entities)
        if not entities or has_enemy:
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
        if self._gizmo_root is not None:
            self._update_gizmo()

    # -------------------------------------------------------------------------
    # Asset picker overlay (v1.3 Step 4 texture, Step 5 model)
    #
    # A floating grid of asset thumbnails from asset_registry, opened by clicking
    # an inspector field (texture swatch, model field). One overlay panel is built
    # lazily per category on first open and reused afterwards (entities just
    # toggle .enabled). self.asset_picker_open holds the open category name (or
    # None); input() consults it to swallow the next click so a dismiss-click
    # never also reaches scene selection/placement underneath. Texture and model
    # pickers share this exact mechanism — only the thumbnail content (real
    # texture preview vs placeholder cube icon) and the apply callback differ.
    # -------------------------------------------------------------------------

    _ASSETPICK_COLS = 4
    _ASSETPICK_CELL = 0.09
    _ASSETPICK_GAP = 0.015
    _ASSETPICK_PAD = 0.03

    _ASSETPICK_TITLES = {
        'texture': 'Choose Texture',
        'model': 'Choose Model',
    }

    def _asset_picker_names(self, category):
        manifest = {'texture': asset_registry.textures, 'model': asset_registry.models}[category]
        return sorted(manifest.keys())

    def _build_asset_picker_icon(self, category, parent, name):
        """Build the thumbnail icon for one cell. Textures show the real image;
        models show a placeholder cube icon per the v1.3 spec (live 3D
        thumbnails are deferred)."""
        icon = Entity(parent=parent, model='quad', scale=(0.85, 0.85), z=-1, eternal=True)
        try:
            from ursina.shaders.unlit_shader import unlit_shader as _us
            icon.shader = _us
        except Exception as e:
            logger.log('ERROR', f"_build_asset_picker_icon shader {type(e).__name__}: {e}")
        if category == 'texture':
            path = asset_registry.get_texture_path(name)
            try:
                icon.texture = Texture(Path(path))
                icon.color = color.white
            except Exception as e:
                logger.log('ERROR', f"_build_asset_picker_icon texture {name} {type(e).__name__}: {e}")
                icon.texture = None
                icon.color = color.magenta
        else:
            icon.model = 'cube'
            icon.color = self._THEME_TILE_BG
        return icon

    def _build_asset_picker(self, category):
        """Build the overlay panel + thumbnail grid once for `category`, sized to
        the current manifest."""
        names = self._asset_picker_names(category)
        cols = self._ASSETPICK_COLS
        rows = max(1, (len(names) + cols - 1) // cols) if names else 1
        cell = self._ASSETPICK_CELL
        gap = self._ASSETPICK_GAP
        pad = self._ASSETPICK_PAD
        title_strip = 0.05   # panel_h-units reserved for the title row, below the top edge
        panel_w = cols * cell + (cols - 1) * gap + pad * 2
        panel_h = rows * cell + (rows - 1) * gap + pad * 2 + title_strip

        panel = Entity(
            parent=camera.ui,
            model='quad',
            color=self._THEME_PANEL_BG,
            scale=(panel_w, panel_h),
            z=-2,
            enabled=False,
            eternal=True,
        )
        title = Text(
            parent=panel,
            text=self._ASSETPICK_TITLES.get(category, 'Choose Asset'),
            position=(0, 0.5 - (title_strip * 0.5) / panel_h),
            origin=(0, 0),
            color=color.white,
            z=-1,
            eternal=True,
        )
        title.world_scale = Vec3(self._INSP_LABEL_WS, self._INSP_LABEL_WS, 1)

        cells = []  # [(bg_entity, name)]
        top_y = 0.5 - title_strip / panel_h - (pad * 0.5 + cell * 0.5) / panel_h
        left_x = -0.5 + pad / panel_w + (cell / panel_w) * 0.5
        for i, name in enumerate(names):
            r, c = divmod(i, cols)
            cx = left_x + c * (cell + gap) / panel_w
            cy = top_y - r * (cell + gap) / panel_h
            bg = Entity(
                parent=panel,
                model='quad',
                color=self._THEME_TILE_BG,
                scale=(cell / panel_w, cell / panel_h),
                position=(cx, cy),
                z=-1,
                eternal=True,
            )
            self._build_asset_picker_icon(category, bg, name)
            cell_label = Text(
                parent=bg,
                text=name,
                position=(0, -0.46),
                origin=(0, 0),
                color=color.light_gray,
                z=-1,
                eternal=True,
            )
            cell_label.world_scale = Vec3(self._INSP_LABEL_WS * 0.6, self._INSP_LABEL_WS * 0.6, 1)
            cells.append((bg, name))

        self._asset_picker_panels[category] = panel
        self._asset_picker_cells[category] = cells

    def _open_asset_picker(self, category, anchor_entity, on_select):
        """Open the `category` asset picker overlay anchored to the left of
        `anchor_entity` (an inspector field/swatch in camera.ui space).
        `on_select(name)` is called when a cell is clicked; the picker closes
        itself either way (selection or dismiss)."""
        if not self.selected:
            return
        if category not in self._asset_picker_panels:
            self._build_asset_picker(category)
        panel = self._asset_picker_panels[category]
        if anchor_entity is not None:
            anchor_x, anchor_y, _, _ = self._camera_ui_pos_and_scale(anchor_entity)
            panel.x = anchor_x - panel.scale_x * 0.5 - 0.02
            panel.y = anchor_y
        panel.enabled = True
        self.asset_picker_open = category
        self._asset_picker_on_select = on_select

    def _close_asset_picker(self):
        """Hide the open overlay without applying anything."""
        if self.asset_picker_open is not None:
            panel = self._asset_picker_panels.get(self.asset_picker_open)
            if panel is not None:
                panel.enabled = False
        self.asset_picker_open = None
        self._asset_picker_on_select = None

    def _asset_picker_cell_at_mouse(self):
        """Return the asset name under the cursor in the open picker, or None."""
        if self.asset_picker_open is None:
            return None
        for bg, name in self._asset_picker_cells.get(self.asset_picker_open, []):
            if self._is_over_world_panel(bg):
                return name
        return None

    def open_texture_picker(self):
        """Open the texture picker overlay near the inspector's texture swatch."""
        self._open_asset_picker('texture', self._insp_tex_swatch, self._apply_texture_pick)

    def close_texture_picker(self):
        self._close_asset_picker()

    @property
    def texture_picker_open(self):
        return self.asset_picker_open == 'texture'

    def _apply_texture_pick(self, name):
        """Apply `name`'s texture to every selected entity as one undo step, then close."""
        entities = [e for e in self.selected if getattr(e, 'destroy_source', None) is None]
        if entities:
            old_textures = [self._entity_texture_name(e) or 'white_cube' for e in entities]
            cmd = ChangeTextureCommand(entities, old_textures, name)
            cmd.execute()
            self._history.push(cmd)
            for e in entities:
                self.subscribe_texture(name, e)
            self._update_inspector_texture_swatch()
            logger.log('INFO', f"Texture picker applied: {name} to {len(entities)} entities")
        self._close_asset_picker()

    def open_model_picker(self):
        """Open the model picker overlay near the inspector's model field.

        Blocks only — enemy model swapping is deferred to v1.4. Guard here even
        though the caller already gates on selection type, so this can never be
        invoked into applying a model onto an enemy.
        """
        if any(e in self.enemies for e in self.selected):
            return
        self._open_asset_picker('model', self._insp_model_field, self._apply_model_pick)

    def _apply_model_pick(self, name):
        """Apply `name`'s model to every selected block as one undo step, then close."""
        entities = [e for e in self.selected
                    if getattr(e, 'destroy_source', None) is None and e not in self.enemies]
        if entities:
            path = asset_registry.get_model_path(name) or name
            old_models = [self._entity_model_name(e) or 'cube' for e in entities]
            cmd = ChangeModelCommand(entities, old_models, path)
            cmd.execute()
            self._history.push(cmd)
            self._update_inspector_model_field()
            logger.log('INFO', f"Model picker applied: {name} to {len(entities)} entities")
        self._close_asset_picker()

    # -------------------------------------------------------------------------
    # Hierarchy panel
    # -------------------------------------------------------------------------

    # Hierarchy layout constants (panel-local space). One row pitch is shared by every
    # visual slot — section headers AND entity rows — so the scroll thumb, swatches and
    # rows are all placed by the SAME _hier_row_y() formula and cannot drift (Bug A).
    _HIER_TOP    = 0.36   # y of the first visual slot (row index 0)
    _HIER_ROW_H  = 0.05   # uniform pitch between consecutive visual slots
    _HIER_MAX_VISIBLE = 13  # how many slots fit between the search field and the panel bottom
    _HIER_SWATCH_X   = -.085  # panel-local x of a row's colour swatch (left edge of the row)
    _HIER_SWATCH_SIZE = .022

    def _hier_row_y(self, visual_index):
        """THE shared row-index -> panel-local-y formula. Slot 0 is at _HIER_TOP and each
        subsequent visual slot (header or entity row) steps down one _HIER_ROW_H. Rows,
        colour swatches, section headers AND the scroll-thumb track all derive their y from
        this single function so they can never diverge (Bug A consolidation)."""
        return self._HIER_TOP - visual_index * self._HIER_ROW_H

    def _hier_visual_rows(self):
        """Ordered list of visual rows for the current filter + collapse state. Each entry is
        ('header', section) or ('row', entity). Headers always appear; a section's entity rows
        are present only when it is expanded AND match the (case-insensitive substring) filter.
        Section counts shown in the header reflect the FILTERED visible entities, not the raw
        totals — so the list reads honestly while searching."""
        flt = self._hier_filter
        rows = []
        for section, members in (('block', self.blocks), ('enemy', self.enemies)):
            if flt:
                matched = [e for e in members if flt in self._hier_label(e).lower()]
            else:
                matched = list(members)
            rows.append(('header', section))
            if not self._hier_collapsed[section]:
                rows.extend(('row', e) for e in matched)
        return rows

    def _hier_label(self, e):
        is_enemy = e in self.enemies
        return f"{'E' if is_enemy else 'B'} ({round(e.x,1)},{round(e.y,1)},{round(e.z,1)})"

    def _build_hierarchy(self):
        self._hier_panel = Entity(
            parent=camera.ui,
            model='quad',
            color=color.rgba(0, 0, 0, 0.75),
            scale=(.20, .9),
            position=(-.779, 0),
            z=-0.5,
            eternal=True,
        )
        Text(
            parent=self._hier_panel,
            text='Hierarchy',
            position=(0, .45),
            scale=(.055, .055),
            color=color.white,
            eternal=True,
        )
        # Search/filter box pinned above the list (Change B.1). Live filter-as-you-type via
        # on_value_changed. Each keystroke re-runs _refresh_hierarchy, but that only ever
        # instantiates the <=_HIER_MAX_VISIBLE rows in the viewport (not the full list), so it
        # stays well under a frame even at 140+ entities (~9ms measured). Counted in the
        # typing-guard so Delete/bookmark keys don't fire while editing here (see input()).
        self._hier_search_field = InputField(
            parent=self._hier_panel,
            position=(0, .40),
            scale=(.17, .03),
            default_value='',
            z=-1,
            eternal=True,
        )
        try:
            self._hier_search_field.setBin('fixed', 41)
        except Exception as e:
            logger.log('ERROR', f"_build_hierarchy setBin search {type(e).__name__}: {e}")
        self._hier_search_field.on_value_changed = self._on_hier_search_changed
        # Thin vertical scroll indicator — right edge of panel
        self._hier_scroll_bar = Entity(
            parent=self._hier_panel,
            model='quad',
            color=color.rgba(0.78, 0.78, 0.78, 0.47),   # 0–1 floats — 0–255 clamps to white
            scale=(.018, .05),
            position=(.46, self._HIER_TOP),
            z=-1,
            eternal=True,
        )
        self._hier_buttons = []
        self._hier_swatches = []
        self._hier_header_buttons = {}

    def _on_hier_search_changed(self):
        if self._hier_search_field is None:
            return
        self._hier_filter = self._hier_search_field.text.strip().lower()
        self.hierarchy_scroll = 0
        self._refresh_hierarchy()
        self._update_hierarchy_highlight()

    def _toggle_hier_section(self, section):
        self._hier_collapsed[section] = not self._hier_collapsed[section]
        self.hierarchy_scroll = 0
        self._refresh_hierarchy()
        self._update_hierarchy_highlight()

    def _refresh_hierarchy(self):
        for b in self._hier_buttons:
            destroy(b)
        self._hier_buttons.clear()
        for s in self._hier_swatches:
            destroy(s)
        self._hier_swatches.clear()
        for b in self._hier_header_buttons.values():
            destroy(b)
        self._hier_header_buttons.clear()

        visual_rows = self._hier_visual_rows()
        total = len(visual_rows)
        max_scroll = max(0, total - self._HIER_MAX_VISIBLE)
        self.hierarchy_scroll = max(0, min(self.hierarchy_scroll, max_scroll))

        # Filtered per-section counts for the header captions.
        flt = self._hier_filter
        def _count(members):
            return sum(1 for e in members if not flt or flt in self._hier_label(e).lower())
        section_count = {'block': _count(self.blocks), 'enemy': _count(self.enemies)}
        section_name  = {'block': 'Blocks', 'enemy': 'Enemies'}

        visible = visual_rows[self.hierarchy_scroll: self.hierarchy_scroll + self._HIER_MAX_VISIBLE]
        for slot, (kind, payload) in enumerate(visible):
            y = self._hier_row_y(slot)
            if kind == 'header':
                section = payload
                # OpenSans (Ursina's default font) has NO triangle glyphs (▾▸▼▶ all render as
                # missing-glyph boxes — verified against the .ttf cmap, same class as the ▶/↖
                # gaps noted in CLAUDE.md). Use ASCII [+]/[-] (both glyphs present) for the
                # collapse state instead.
                tri = '[+]' if self._hier_collapsed[section] else '[-]'
                hdr = Button(
                    parent=self._hier_panel,
                    text=f"{tri} {section_name[section]} ({section_count[section]})",
                    scale=(.18, .038),
                    position=(-.01, y),
                    color=self._THEME_TILE_HOVER,
                    text_origin=(-.5, 0),   # left-align the caption inside the wide button
                    text_scale=.75,
                    z=-1,
                )
                hdr.on_click = lambda s=section: self._toggle_hier_section(s)
                self._hier_header_buttons[section] = hdr
            else:
                e = payload
                btn = Button(
                    parent=self._hier_panel,
                    text=self._hier_label(e),
                    scale=(.18, .038),
                    position=(-.01, y),
                    color=color.orange if e in self.selected else color.dark_gray,
                    text_scale=.75,
                    z=-1,
                )
                btn.on_click = lambda entity=e: self._select(entity)
                self._hier_buttons.append(btn)
                # Colour swatch matching the entity's real colour (Change B.2). Parented to the
                # panel (not the button) and placed by the SAME _hier_row_y(slot) so it stays
                # aligned. z below the button text so it reads as a separate chip. NOT eternal:
                # like the row Buttons these are transient and destroyed/rebuilt every refresh —
                # destroy() is a NO-OP on eternal entities (ursina/destroy.py:27), so an eternal
                # swatch would leak and ghost on every rebuild. Hidden in F5 play mode via the
                # panel's enabled cascade, not eternal. (Persistent chrome below stays eternal.)
                swatch_color = getattr(e, '_original_color', e.color)
                swatch = Entity(
                    parent=self._hier_panel,
                    model='quad',
                    color=swatch_color,
                    scale=(self._HIER_SWATCH_SIZE, self._HIER_SWATCH_SIZE),
                    position=(self._HIER_SWATCH_X, y),
                    z=-1.1,
                )
                self._hier_swatches.append(swatch)

        self._update_hier_scroll_bar(total)

    def _update_hier_scroll_bar(self, total):
        if not self._hier_scroll_bar:
            return
        if total <= self._HIER_MAX_VISIBLE:
            self._hier_scroll_bar.enabled = False
            return
        self._hier_scroll_bar.enabled = True
        # Track spans the same slots the rows occupy — bounds derived from _hier_row_y so the
        # thumb can never drift from the rows (Bug A). Top of slot 0 down to bottom of the last.
        track_top    = self._hier_row_y(0) + self._HIER_ROW_H * 0.5
        track_bottom = self._hier_row_y(self._HIER_MAX_VISIBLE - 1) - self._HIER_ROW_H * 0.5
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
        """Recolour the currently-built entity rows in place from the same visual-row slice the
        buttons were built from — never rebuilds, so it stays cheap on scroll/select."""
        visual_rows = self._hier_visual_rows()
        visible = visual_rows[self.hierarchy_scroll: self.hierarchy_scroll + self._HIER_MAX_VISIBLE]
        row_entities = [payload for kind, payload in visible if kind == 'row']
        for btn, e in zip(self._hier_buttons, row_entities):
            btn.color = color.orange if e in self.selected else color.dark_gray

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

    def _hier_typing(self):
        """True while the hierarchy search box is focused — so Delete/bookmark/backspace keys
        edit the search text instead of deleting entities or recalling camera bookmarks."""
        return bool(self._hier_search_field and self._hier_search_field.active)

    def _is_over_browser(self):
        my = mouse.y
        return (self._BROWSER_Y - self._BROWSER_H * 0.5) <= my <= (self._BROWSER_Y + self._BROWSER_H * 0.5)

    # -------------------------------------------------------------------------
    # Asset browser
    #
    # Three tabs (Textures | Models | Sounds) over a vertically scrollable column
    # of thumbnail cards. Textures/Sounds come from asset_registry; Models merges
    # BUILTIN_MODELS (Cube/Stone/Metal/Wood/Enemy) with any real scanned .obj/.gltf
    # files. Drag a Models-tab card to place an entity (same ghost/snap/undo as
    # the former tray). Texture/Sound cards are click-to-select only.
    # -------------------------------------------------------------------------

    # Browser layout constants (camera.ui units). Flush to the bottom of the window
    # (reclaims the vertical space the old tray occupied).
    _BROWSER_Y      = -0.40    # centre y of the browser panel
    _BROWSER_H      =  0.20    # panel height (taller than before, fills old tray space)
    _CARD_SIZE      =  0.095   # card width/height
    _CARD_PITCH     =  0.115   # card-to-card spacing (both horizontal and vertical)
    _TAB_Y          = -0.32    # y of the tab buttons (top of the panel)
    _BROWSER_CATEGORIES = ('texture', 'model', 'sound')
    # FIXED (Item 8): named the double-click window (was a bare 0.4 literal in
    # _handle_browser_click) so the next step's maintainer can find/tune it.
    _DOUBLE_CLICK_SEC = 0.4    # max wall-clock gap between the two clicks of a double-click

    def _build_asset_browser(self):
        """Build the read-only asset browser panel, tabs, and per-category card columns."""
        # Reflect current disk state at editor startup (Step 2 spec point 8).
        try:
            asset_registry.rebuild()
        except Exception as e:
            logger.log('ERROR', f"_build_asset_browser rebuild {type(e).__name__}: {e}")

        self._browser_panel = Entity(
            parent=camera.ui,
            model='quad',
            color=self._THEME_PANEL_BG,
            scale=(2.0, self._BROWSER_H),
            position=(0, self._BROWSER_Y),
            z=-0.5,
            eternal=True,
        )

        tab_specs = (('texture', 'Textures'), ('model', 'Models'), ('sound', 'Sounds'))
        for i, (category, label) in enumerate(tab_specs):
            btn = Button(
                parent=camera.ui,
                text=label,
                scale=(0.12, 0.035),
                position=(-0.20 + i * 0.13, self._TAB_Y),
                text_scale=0.85,
                z=-0.7,
                eternal=True,
            )
            btn.on_click = lambda c=category: self._set_browser_tab(c)
            self._browser_tab_buttons[category] = btn

        for category in self._BROWSER_CATEGORIES:
            self._build_browser_cards(category)

        # Scroll indicators — up/down arrow Text that show when more cards exist
        # off-screen. Positioned at the card-column edges; toggled by _update_browser_scroll_indicators.
        # use_tags=False: Ursina's Text treats '<'/'>' as start_tag/end_tag delimiters
        # by default, so text='<' parses as an empty tag with zero lines, which crashes
        # align() (IndexError: list index out of range on linewidths[-1]). Kept for
        # consistency with the other arrow glyphs even though '^'/'v' aren't delimiters.
        aspect = getattr(window, 'aspect_ratio', 16 / 9) or 16 / 9
        scroll_ind_x = aspect * 0.5 - self._LAYOUT_INSP_W - 0.03
        self._browser_scroll_up = Text(
            parent=camera.ui,
            text='^',
            use_tags=False,
            position=(scroll_ind_x, self._BROWSER_Y + self._BROWSER_H * 0.5 - 0.02),
            origin=(0, -0.5),
            scale=1.4,
            color=color.white66,
            z=-0.9,
            eternal=True,
        )
        self._browser_scroll_down = Text(
            parent=camera.ui,
            text='v',
            use_tags=False,
            position=(scroll_ind_x, self._BROWSER_Y - self._BROWSER_H * 0.5 + 0.02),
            origin=(0, 0.5),
            scale=1.4,
            color=color.white66,
            z=-0.9,
            eternal=True,
        )

        self._set_browser_tab('texture')   # default tab

    def _browser_manifest(self, category):
        """Return the {name: path} manifest dict for a browser category."""
        return {
            'texture': asset_registry.textures,
            'model':   asset_registry.models,
            'sound':   asset_registry.sounds,
        }[category]

    # Display names for the per-category collapsed strip ("Textures (0)").
    _BROWSER_TAB_TITLES = {'texture': 'Textures', 'model': 'Models', 'sound': 'Sounds'}

    def _build_browser_cards(self, category):
        """Build (and remember) the card column + empty-state strip for one category.

        For the 'model' category, BUILTIN_MODELS entries are prepended before any
        real scanned assets from asset_registry. Built-in cards use their flat color
        as the thumbnail and store their asset dict in _browser_card_assets so the
        drag-to-place system can retrieve it.
        """
        cards = []
        index = 0

        if category == 'model':
            for asset in self.BUILTIN_MODELS:
                cards.append(self._create_browser_card(category, index, asset['name'], None, builtin_asset=asset))
                index += 1

        manifest = self._browser_manifest(category)
        for name, path in sorted(manifest.items()):
            cards.append(self._create_browser_card(category, index, name, path))
            index += 1
        self._browser_cards[category] = cards

        empty_x, empty_y = self._card_grid_pos(0)
        self._browser_empty_labels[category] = Text(
            parent=camera.ui,
            text=f"{self._BROWSER_TAB_TITLES[category]} (0)",
            position=(empty_x, empty_y),
            origin=(-0.5, 0),
            scale=0.7,
            color=self._THEME_TEXT,
            z=-0.7,
            eternal=True,
        )

    def _create_browser_card(self, category, index, name, path, builtin_asset=None):
        """Create one thumbnail card (bg quad + icon + name label). Returns the tuple.

        For built-in model cards (builtin_asset is not None), the icon shows the asset's
        flat color and the asset dict is stored in _browser_card_assets for drag-to-place.
        """
        cx, cy = self._card_grid_pos(index)
        bg = Entity(
            parent=camera.ui,
            model='quad',
            color=self._THEME_TILE_BG,
            scale=(self._CARD_SIZE, self._CARD_SIZE),
            position=(cx, cy),
            z=-0.6,
            eternal=True,
        )

        icon = Entity(
            parent=camera.ui,
            model='quad',
            scale=(self._CARD_SIZE * 0.78, self._CARD_SIZE * 0.78),
            position=(cx, cy + 0.008),
            z=-0.7,
            eternal=True,
        )
        try:
            from ursina.shaders.unlit_shader import unlit_shader as _us
            icon.shader = _us
        except Exception as e:
            logger.log('ERROR', f"_create_browser_card icon shader {type(e).__name__}: {e}")

        if category == 'texture':
            try:
                icon.texture = Texture(Path(path))
                icon.color = color.white
                self.subscribe_texture(name, icon)
            except Exception as e:
                logger.log('ERROR', f"_create_browser_card texture {name} {type(e).__name__}: {e}")
                icon.texture = None
                icon.color = color.magenta
        elif category == 'model' and builtin_asset is not None:
            icon.texture = None
            icon.color = color.rgb(*builtin_asset['color'])
            self._browser_card_assets[('model', name)] = builtin_asset
        elif category == 'model':
            icon.texture = None
            icon.color = color.rgba(0.47, 0.55, 0.67, 1.0)
        else:
            icon.texture = None
            icon.color = color.rgba(0.59, 0.59, 0.35, 1.0)
            Text(parent=icon, text='\U0001F50A', origin=(0, 0), scale=4, z=-1,
                 color=color.white, eternal=True)

        label = Text(
            parent=camera.ui,
            text=name,
            position=(cx + self._CARD_SIZE * 0.62, cy),
            origin=(-0.5, 0),
            scale=0.5,
            color=color.light_gray,
            z=-0.7,
            eternal=True,
        )
        return (bg, icon, label, name)

    def _refresh_browser_cards(self):
        """Tear down and rebuild every category's card column from the current
        registry manifest (called after an import changes what's on disk).

        Cards are eternal=True (per the established pattern — transient
        per-refresh widgets that AREN'T eternal are the hierarchy panel's rows;
        these are not, so destroy() needs force_destroy=True or it's a no-op).
        """
        for category in self._BROWSER_CATEGORIES:
            for bg, icon, label, name in self._browser_cards.get(category, []):
                if category == 'texture':
                    self.unsubscribe_texture(name, icon)
                destroy(bg, force_destroy=True)
                destroy(icon, force_destroy=True)
                destroy(label, force_destroy=True)
            self._browser_cards[category] = []
            empty_label = self._browser_empty_labels.get(category)
            if empty_label is not None:
                destroy(empty_label, force_destroy=True)
            self._browser_card_assets = {
                k: v for k, v in self._browser_card_assets.items() if k[0] != category
            }
            self._build_browser_cards(category)
        self._set_browser_tab(self._browser_tab)
        self._apply_layout()

    def _browser_cols(self):
        """Number of card columns that fit in the browser's usable width."""
        aspect = getattr(window, 'aspect_ratio', 16 / 9) or 16 / 9
        half_w = aspect * 0.5
        left = -half_w + self._LAYOUT_HIER_W + 0.02
        right = half_w - self._LAYOUT_INSP_W - 0.02
        usable = right - left
        cols = max(1, int(usable / self._CARD_PITCH))
        return cols

    def _browser_visible_rows(self):
        """Number of card rows that fit vertically in the browser panel."""
        return max(1, int((self._BROWSER_H - 0.04) / self._CARD_PITCH))

    def _card_grid_pos(self, index):
        """Camera.ui (x, y) of a card centre at the given linear index."""
        cols = self._browser_cols()
        scroll_row = self._browser_scroll.get(self._browser_tab, 0)
        row, col = divmod(index, cols)
        aspect = getattr(window, 'aspect_ratio', 16 / 9) or 16 / 9
        half_w = aspect * 0.5
        left = -half_w + self._LAYOUT_HIER_W + 0.02
        first_x = left + self._CARD_PITCH * 0.5
        first_y = self._BROWSER_Y + self._BROWSER_H * 0.5 - self._CARD_PITCH * 0.5 - 0.02
        cx = first_x + col * self._CARD_PITCH
        cy = first_y - (row - scroll_row) * self._CARD_PITCH
        return cx, cy

    def _refresh_browser_card_positions(self):
        """Reposition the active tab's cards to reflect the current scroll offset,
        hiding any that fall outside the panel's vertical bounds."""
        panel_top = self._BROWSER_Y + self._BROWSER_H * 0.5
        panel_bot = self._BROWSER_Y - self._BROWSER_H * 0.5
        hh = self._CARD_SIZE * 0.5
        for index, (bg, icon, label, name) in enumerate(self._browser_cards.get(self._browser_tab, [])):
            cx, cy = self._card_grid_pos(index)
            visible = (cy - hh) >= panel_bot and (cy + hh) <= panel_top
            bg.x = cx
            bg.y = cy
            bg.enabled = visible
            icon.x = cx
            icon.y = cy + 0.008
            icon.enabled = visible
            label.x = cx + self._CARD_SIZE * 0.62
            label.y = cy
            label.enabled = visible

    def _set_browser_tab(self, category):
        """Switch the active tab: show that category's cards, hide the others."""
        self._browser_tab = category
        for cat in self._BROWSER_CATEGORIES:
            cards = self._browser_cards.get(cat, [])
            visible = (cat == category)
            for bg, icon, label, name in cards:
                bg.enabled = visible
                icon.enabled = visible
                label.enabled = visible
            empty_label = self._browser_empty_labels.get(cat)
            if empty_label:
                empty_label.enabled = visible and not cards
        # Tab button highlight
        for cat, btn in self._browser_tab_buttons.items():
            btn.color = self._THEME_TAB_ACTIVE if cat == category else self._THEME_TAB_IDLE
        self._refresh_browser_card_positions()
        self._apply_browser_selection_highlight()
        self._update_browser_scroll_indicators()

    def _apply_browser_selection_highlight(self):
        """Tint the selected card's background; reset all others on the active tab."""
        for bg, icon, label, name in self._browser_cards.get(self._browser_tab, []):
            selected = self._selected_asset == (self._browser_tab, name)
            bg.color = self._THEME_TILE_SEL if selected else self._THEME_TILE_BG

    def _update_browser_scroll_indicators(self):
        """Show/hide up/down scroll arrows based on whether more rows exist off-screen."""
        cards = self._browser_cards.get(self._browser_tab, [])
        cols = self._browser_cols()
        total_rows = (len(cards) + cols - 1) // cols if cards else 0
        scroll = self._browser_scroll.get(self._browser_tab, 0)
        visible_rows = self._browser_visible_rows()
        if self._browser_scroll_up:
            self._browser_scroll_up.enabled = (scroll > 0)
        if self._browser_scroll_down:
            self._browser_scroll_down.enabled = (scroll + visible_rows < total_rows)

    def _card_at_mouse(self):
        """Return (category, name) for the card under the cursor on the active tab, or None."""
        if self._browser_panel is None or not self._browser_panel.enabled:
            return None
        mx, my = mouse.x, mouse.y
        hh = self._CARD_SIZE * 0.5
        for index, (bg, icon, label, name) in enumerate(self._browser_cards.get(self._browser_tab, [])):
            if not bg.enabled:
                continue
            cx, cy = self._card_grid_pos(index)
            if (cx - hh) <= mx <= (cx + hh) and (cy - hh) <= my <= (cy + hh):
                return (self._browser_tab, name)
        return None

    def _handle_browser_click(self):
        """Click on a Models-tab card with a built-in asset dict initiates drag-to-place.
        Other cards select only. Returns True if the click landed on a card.
        """
        target = self._card_at_mouse()
        if target is None:
            return False

        self._selected_asset = target
        self._apply_browser_selection_highlight()

        asset = self._browser_card_assets.get(target)
        if asset is not None:
            self._drag_origin = Vec2(mouse.x, mouse.y)
            self._dragging = True
            self._begin_drag(asset)
        else:
            now = _time.time()
            last_target, last_t = self._browser_last_click
            is_double = (target == last_target) and (now - last_t) <= self._DOUBLE_CLICK_SEC
            if is_double:
                category, name = target
                self._browser_last_click = (None, 0.0)
                logger.log('INFO', f"Asset double-clicked: {category}/{name}")
            else:
                self._browser_last_click = (target, now)
        return True

    # -------------------------------------------------------------------------
    # Asset hot-reload (v1.3 Step 3)
    #
    # asset_registry.poll() (driven by _poll_assets on a 2s invoke timer) fires
    # the registered callbacks below when a tracked file's mtime changes. Only
    # textures live-reload; model/sound callbacks log at INFO (per spec).
    #
    # subscribe_texture()/unsubscribe_texture() are the hook points for Step 4
    # (the texture picker): it registers each block under the texture name it
    # applies, and _on_texture_changed re-uploads the new image to every
    # subscriber. The browser's own thumbnail cards auto-subscribe in
    # _create_browser_card, which is what makes hot-reload testable in this step.
    # -------------------------------------------------------------------------

    def _poll_assets(self):
        """Poll the asset registry for on-disk changes every 2s; re-arm the timer.

        Hot-reload is fully disabled while play-in-editor is active — the game
        simulation owns those assets. Play-in-editor is tracked by the editor-
        local self._play_mode flag (set in _enter_play_mode/_exit_play_mode);
        the editor never sets game.state to PLAYING, so that is the right flag.
        """
        if self._play_mode:
            invoke(self._poll_assets, delay=2)
            return
        asset_registry.poll()
        invoke(self._poll_assets, delay=2)

    def subscribe_texture(self, name, entity):
        """Register `entity` to receive live texture updates for texture `name`.

        Step 4 (texture picker) calls this when a texture is applied to a block.
        """
        self._texture_subscribers.setdefault(name, [])
        if entity not in self._texture_subscribers[name]:
            self._texture_subscribers[name].append(entity)

    def unsubscribe_texture(self, name, entity):
        """Stop sending live texture updates for `name` to `entity`.

        Step 4 calls this when a block's texture is changed/removed.
        """
        if name in self._texture_subscribers:
            if entity in self._texture_subscribers[name]:
                self._texture_subscribers[name].remove(entity)

    def _on_texture_changed(self, name, path):
        """Re-upload a changed texture from disk and push it to every subscriber."""
        try:
            # Force a fresh disk read. Ursina's texture setter does value._texture
            # on non-string values, so a raw loader.loadTexture() (a Panda3D
            # Texture with no ._texture) would crash — wrap in ursina.Texture.
            # .reload() re-reads the file into the (pooled) handle, bypassing any
            # stale cached upload.
            fresh = Texture(Path(path))
            fresh._texture.reload()
            updated = 0
            for entity in list(self._texture_subscribers.get(name, [])):
                if getattr(entity, 'destroy_source', None) is not None:
                    continue   # dead entity guard — don't touch destroyed refs
                entity.texture = fresh
                updated += 1
            self._show_status_notice(f'Texture reloaded: {name}')
            logger.log('INFO', f'Texture hot-reload: {name} ({updated} entities updated)')
        except Exception as e:
            logger.log('ERROR', f'_on_texture_changed: {type(e).__name__}: {e}')

    def _on_model_changed(self, name, path):
        """Model live-reload is not implemented (per spec) — log only."""
        logger.log('INFO', f'Model changed on disk: {name} (live reload not implemented)')

    def _on_sound_changed(self, name, path):
        """Sound live-reload is not implemented (per spec) — log only."""
        logger.log('INFO', f'Sound changed on disk: {name} (live reload not implemented)')

    def _show_status_notice(self, text, color_=None, duration=2.0):
        """Show a single reusable bottom-of-screen toast; reset its timer if re-shown.

        `duration=None` makes the notice persistent (no auto-hide timer) — used for
        drag-and-drop error notices (v1.3 Step 6), which stay up until the next notice
        replaces them. `color_` defaults to white (hot-reload's original behaviour).
        """
        if self._status_notice is None:
            self._status_notice = Text(
                text=text,
                parent=camera.ui,
                origin=(0, 0),
                position=(0, self._BROWSER_Y - self._BROWSER_H * 0.5 - 0.02),
                scale=0.9,
                color=color.white,
                z=-1,
                eternal=True,
            )
        self._status_notice.text = text
        self._status_notice.color = color_ if color_ is not None else color.white
        self._status_notice.enabled = True
        # Monotonically rising token: only the latest timer is allowed to hide the
        # notice, so a rapid re-show doesn't get cut short by an earlier timer, and a
        # persistent (duration=None) notice never gets a hide invoked against it.
        self._status_notice_token = getattr(self, '_status_notice_token', 0) + 1
        token = self._status_notice_token
        if duration is not None:
            invoke(self._hide_status_notice, token, delay=duration)

    def _hide_status_notice(self, token):
        """Hide the toast only if no newer notice has been shown since `token`."""
        if token != getattr(self, '_status_notice_token', 0):
            return
        if self._status_notice is not None and getattr(self._status_notice, 'destroy_source', None) is None:
            self._status_notice.enabled = False

    # -------------------------------------------------------------------------
    # Asset import (v1.3 Step 6)
    #
    # Spec called for OS drag-and-drop (base.win.setAcceptDrop + a 'drop-files'
    # event). Neither exists in any released Panda3D version — confirmed via a
    # Panda3D core developer on the official forum (Oct 2023): "It has not been
    # implemented as of yet"; the GitHub feature request has been open since Jan
    # 2020. The working substitute, by user decision: an "Import Asset" toolbar
    # button that opens the OS-native file picker (tkinter.filedialog, stdlib,
    # no new dependency) and feeds the chosen path into the same pipeline a real
    # drop handler would have used. import_asset_file() is that pipeline — it
    # has no UI-framework dependency of its own, so swapping in real OS drop
    # support later (if Panda3D ever ships it) only means replacing the picker
    # call, not this method.
    # -------------------------------------------------------------------------

    def _open_import_dialog(self):
        """Toolbar handler: open the native file picker, import what's chosen."""
        if self._play_mode:
            return   # import is an editor-only action; ignore while playing
        try:
            import tkinter
            from tkinter import filedialog
            root = tkinter.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            path = filedialog.askopenfilename(title='Import Asset')
            root.destroy()
        except Exception as e:
            logger.log('ERROR', f'_open_import_dialog: {type(e).__name__}: {e}')
            return
        if not path:
            return   # user cancelled
        self.import_asset_file(path)

    def import_asset_file(self, src_path):
        """Import one file into the asset pipeline: route by extension, copy
        (never move) into the matching assets/ subfolder, rebuild the registry
        manifest, and show a notice. Safe to call once per file in a batch —
        one bad file never raises out of this method.

        Filename-collision handling (undefined by spec — decision made here):
        skip the copy and show a named error notice rather than overwrite.
        Overwriting silently could clobber an existing asset that's already
        placed in the level; auto-suffixing would let `name` (the registry key,
        derived from the file stem) silently diverge from what the user expects
        to reuse. Skip-with-notice is the only option that can't surprise them.
        """
        if self._play_mode:
            logger.log('INFO', f'import_asset_file: ignored during play-in-editor: {src_path}')
            return
        try:
            src = Path(src_path)
            if not src.is_file():
                self._show_status_notice(f'Import failed: not a file: {src.name}', color_=color.red, duration=None)
                return
            ext = src.suffix.lower()
            category = None
            for cat, extensions in CATEGORY_EXTENSIONS.items():
                if ext in extensions:
                    category = cat
                    break
            if category is None:
                self._show_status_notice(f'Unsupported file type: {src.name}', color_=color.red, duration=None)
                logger.log('WARN', f'import_asset_file: unsupported extension {ext} ({src.name})')
                return

            dest_dir = CATEGORY_DIRS[category]
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / src.name
            if dest.exists():
                self._show_status_notice(f'Import skipped — "{src.name}" already exists in {category}s', color_=color.red, duration=None)
                logger.log('WARN', f'import_asset_file: name collision, skipped: {dest}')
                return

            shutil.copy2(src, dest)
            asset_registry.rebuild()
            self._refresh_browser_cards()
            self._show_status_notice(f'Imported: {src.name}', color_=color.white, duration=2.0)
            logger.log('INFO', f'import_asset_file: imported {src} -> {dest} ({category})')
        except Exception as e:
            self._show_status_notice(f'Import failed: {src_path}', color_=color.red, duration=None)
            logger.log('ERROR', f'import_asset_file: {type(e).__name__}: {e}')

    def _begin_drag(self, asset):
        self._drag_asset = asset
        self._drag_ghost = None   # created lazily when mouse moves over viewport

    def _update_ghost(self):
        """Move ghost to snapped hit position under cursor; create it if needed.

        Only hide the ghost when the ray definitively misses all surfaces
        (hovered is None). For partial/stale results (hovering the ghost
        itself, a UI panel, or any editor-internal entity), keep the ghost
        at its last position — toggling visibility every frame on these
        intermittent results causes flicker.
        """
        asset = self._drag_asset
        if asset is None:
            return

        hovered = mouse.hovered_entity

        # Definitive miss — nothing under the cursor at all. Hide.
        if hovered is None:
            if self._drag_ghost is not None:
                self._drag_ghost.visible = False
            return

        # Stale / invalid placement surface (ghost itself, UI, editor gizmo,
        # spawn marker, etc.). Do not place here, but keep the existing
        # ghost steady at its last good position — do not toggle visibility.
        if (hovered is self._drag_ghost
                or getattr(hovered, 'parent', None) is camera.ui
                or (hovered.name and hovered.name.startswith('editor_'))):
            return

        pos = list(hovered.position + mouse.normal)
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
        logger.log('INFO', f"Entity placed: type={asset['type']} pos={[round(p, 3) for p in pos]}")

        destroy(ghost)
        self._drag_ghost = None
        self._drag_asset = None
        self._dragging = False
        self._drag_origin = None

    # -------------------------------------------------------------------------
    # Transform gizmos
    # -------------------------------------------------------------------------

    def _build_gizmo(self):
        self._gizmo_root = Entity(name='editor_gizmo_root', enabled=False, eternal=True)
        shaft_len = 1.2
        Entity(
            parent=self._gizmo_root,
            model=Mesh(vertices=[Vec3(0, 0, 0), Vec3(shaft_len, 0, 0)], mode='line', thickness=3),
            color=color.red,
            name='editor_gizmo_x',
            eternal=True,
        )
        Entity(
            parent=self._gizmo_root,
            model=Mesh(vertices=[Vec3(0, 0, 0), Vec3(0, shaft_len, 0)], mode='line', thickness=3),
            color=color.green,
            name='editor_gizmo_y',
            eternal=True,
        )
        Entity(
            parent=self._gizmo_root,
            model=Mesh(vertices=[Vec3(0, 0, 0), Vec3(0, 0, shaft_len)], mode='line', thickness=3),
            color=color.blue,
            name='editor_gizmo_z',
            eternal=True,
        )
        for offset, col, gname in [
            (Vec3(shaft_len, 0, 0), color.red,   'editor_gizmo_tip_x'),
            (Vec3(0, shaft_len, 0), color.green, 'editor_gizmo_tip_y'),
            (Vec3(0, 0, shaft_len), color.blue,  'editor_gizmo_tip_z'),
        ]:
            # FIXED: scale 0.12 → 0.24 (2× larger for easier clicking)
            tip = Entity(
                parent=self._gizmo_root,
                model='cube',
                color=col,
                scale=.24,
                position=offset,
                name=gname,
                collider='box',
                eternal=True,
            )
            # FIXED: bin 100 (was 40) + depth test/write off so handles always render
            # on top of scene geometry and are clickable even when overlapped by blocks.
            # Entity IS a NodePath (Ursina subclasses NodePath) — call setBin directly.
            tip.setDepthTest(False)
            tip.setDepthWrite(False)
            tip.setBin('fixed', 100)

    def _cursor_ray(self):
        """Return a world-space (origin, direction) ray through the mouse cursor.

        Mirrors Ursina's own picker (mouse.update): lens.extrude with the cursor in
        normalised film coords (mouse.x*2/aspect, mouse.y*2). Needed for gizmo picking —
        camera.forward only rays through the screen centre, but the editor cursor is free,
        so a screen-centre ray never tested where the user actually clicked.
        """
        from panda3d.core import Point2, Point3
        near_p, far_p = Point3(), Point3()
        camera.lens.extrude(
            Point2(mouse.x * 2 / window.aspect_ratio, mouse.y * 2), near_p, far_p
        )
        origin = Vec3(*render.get_relative_point(camera, near_p))
        far_w  = Vec3(*render.get_relative_point(camera, far_p))
        return origin, (far_w - origin).normalized()

    def _update_gizmo(self):
        """Position gizmo at centroid of selection; hide when nothing selected or in play mode."""
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
        """Translate selected entities along the grabbed world axis; push MoveEntityCommand on release."""
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
                        etype = 'enemy' if e in self.enemies else 'block'
                        logger.log('INFO', f"Entity moved: type={etype} {[round(p,3) for p in old_pos]} -> {[round(p,3) for p in new_pos]}")
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
        """Serialize current blocks and enemies to a list of dicts for save or play snapshot.

        Defensive filter: skip entities that have already been destroyed (destroy_source set).
        Accessing .color on a dead NodePath raises an assertion in development_mode.
        Drop dead refs from the live lists too so the editor state stays consistent.
        """
        data = []
        live_blocks = [b for b in self.blocks if getattr(b, 'destroy_source', None) is None]
        live_enemies = [e for e in self.enemies if getattr(e, 'destroy_source', None) is None]
        dropped = (len(self.blocks) - len(live_blocks)) + (len(self.enemies) - len(live_enemies))
        if dropped:
            logger.log('WARN', f'_build_level_data: dropped {dropped} destroyed entity refs')
            self.blocks[:] = live_blocks
            self.enemies[:] = live_enemies
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
            data.append(block_data)
        for enemy in live_enemies:
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

        # Asset browser (panel + tab buttons + scroll indicators). Card / empty-label
        # visibility is delegated to _set_browser_tab so only the active tab shows.
        browser_widgets = ([self._browser_panel]
                           + list(self._browser_tab_buttons.values())
                           + [self._browser_scroll_up, self._browser_scroll_down])
        for widget in browser_widgets:
            if widget:
                if getattr(widget, 'destroy_source', None) is not None:
                    logger.log('WARN', f'_set_editor_ui_visible: skipped destroyed browser widget {widget}')
                    continue
                widget.enabled = visible
        if visible:
            self._set_browser_tab(self._browser_tab)
        else:
            for cards in self._browser_cards.values():
                for bg, icon, label, name in cards:
                    bg.enabled = icon.enabled = label.enabled = False
            for empty_label in self._browser_empty_labels.values():
                if empty_label:
                    empty_label.enabled = False

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
        self._gizmo_root.enabled = False
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
        self._update_gizmo()
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
        for e in self.blocks + self.enemies:
            if getattr(e, 'destroy_source', None) is None:
                destroy(e)
        self.blocks.clear()
        self.enemies.clear()
        self.selected.clear()

        for entry in load_level_data(self._play_level_snapshot):
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

        logger.log('INFO', f'Editor level restored: {len(self.blocks)} blocks, {len(self.enemies)} enemies')
        self._refresh_hierarchy()

    def _spawn_gameplay_from_snapshot(self, level_data):
        from Scripts.player_controller import Player
        from Scripts.enemy import Enemy
        from Scripts.game import game

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
                e = Enemy(
                    spawn_position=tuple(entry['position']),
                    player=game.player,
                    hp=entry['hp'],
                    enemy_type=entry['enemy_type'],
                    rotation_y=entry['rotation_y'],
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
        """Per-frame: drive ghost drag, model preview, gizmo, and highlight in editor mode."""
        if not self._play_mode:
            if self._dragging:
                self._update_ghost()
                self.model_preview.visible = False
            else:
                self.update_model_preview()
            self._handle_gizmo_drag()
            self._update_gizmo()
            # Refresh the stats strip ~once a second (matches Ursina's own counter cadence).
            self._stats_accum += time.dt
            if self._stats_accum >= 1.0:
                self._stats_accum = 0.0
                self._refresh_stats()

    def _refresh_stats(self):
        """Update the entity/collider stats readout from the editor's own level data."""
        if getattr(self, '_stats_text', None) is None:
            return
        placed = self.blocks + self.enemies
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
        if self.asset_picker_open is not None:
            if key == 'escape':
                self._close_asset_picker()
                return
            if key == 'left mouse down':
                name = self._asset_picker_cell_at_mouse()
                if name is not None:
                    self._asset_picker_on_select(name)
                else:
                    self._close_asset_picker()
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
            if any(f.active for f in self._insp_fields.values()) or self._hier_typing():
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
            typing = any(f.active for f in self._insp_fields.values()) or self._hier_typing()
            mid_drag = (self._gizmo_drag_axis is not None
                        or self._box_selecting or self._dragging)
            if self.selected and not typing and not mid_drag:
                for e in list(self.selected):
                    snapshot = self._entity_snapshot(e)
                    etype = 'enemy' if e in self.enemies else 'block'
                    logger.log('INFO', f"Entity deleted: type={etype} pos={[round(p, 3) for p in e.position]}")
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

        # Commit or cancel drag on left mouse up
        if key == 'left mouse up' and self._dragging:
            hovered = mouse.hovered_entity
            if (self._is_over_browser()
                    or hovered is None
                    or hovered is self._drag_ghost
                    or getattr(hovered, 'parent', None) is camera.ui):
                self._cancel_drag()
            else:
                self._commit_drag()
            return

        # Unified left mouse down handler: gizmo → panels → browser → tool action
        if key == 'left mouse down':
            # Step 1: gizmo handle hit — raycast against tip cubes BEFORE any panel check.
            # setDepthTest(False)/setBin(100) make handles visible through blocks, but the
            # pick ray still hits geometry behind them; explicit raycast takes priority.
            if self._gizmo_root and self._gizmo_root.enabled and self._gizmo_drag_axis is None:
                _axis_map = {
                    'editor_gizmo_tip_x': 'x',
                    'editor_gizmo_tip_y': 'y',
                    'editor_gizmo_tip_z': 'z',
                }
                # FIXED (v1.2.4, FIX 1B): pick through the MOUSE CURSOR, not camera.forward.
                # camera.forward rays through the screen centre, but the editor cursor is
                # free, so the old ray never tested where the user actually clicked — the
                # gizmo could not be grabbed. Ignoring every non-tip entity lets the handle
                # win even when a block overlaps it on screen (verified against a block-in-front).
                _origin, _direction = self._cursor_ray()
                _hit = raycast(_origin,
                               _direction,
                               distance=200,
                               ignore=[e for e in scene.entities
                                       if not e.name.startswith('editor_gizmo_tip')])
                if _hit.hit and _hit.entity and _hit.entity.name in _axis_map:
                    self._gizmo_drag_axis = _axis_map[_hit.entity.name]
                    self._gizmo_drag_start_mouse = Vec2(mouse.x, mouse.y)
                    self._gizmo_drag_start_pos = {e: Vec3(e.position) for e in self.selected}
                    return

            # Step 2: panel guards — _is_over_panel uses mouse.x/y, no hovered needed.
            # Buttons inside panels are grandchildren of camera.ui; _is_over_panel is
            # more reliable than checking hovered.parent for nested widgets.
            if self._hier_panel and self._is_over_panel(self._hier_panel):
                return
            # Texture swatch click opens the picker — must be checked before the
            # blanket inspector-panel guard below swallows the click silently.
            if (self._insp_tex_swatch and self.selected
                    and self._is_over_world_panel(self._insp_tex_swatch)):
                self.open_texture_picker()
                return
            # Model field click opens the model picker — blocks only (see
            # open_model_picker's own enemy guard); same priority as the texture
            # swatch, before the blanket inspector-panel guard.
            if (self._insp_model_field and self.selected
                    and not any(e in self.enemies for e in self.selected)
                    and self._is_over_world_panel(self._insp_model_field)):
                self.open_model_picker()
                return
            if self._inspector and self._is_over_panel(self._inspector):
                return

            # FIXED (Item 1): asset browser card click is a panel-class guard and must
            # run with the other panels (AFTER the gizmo hit-test), not at Step 0b before
            # it. A gizmo handle can render over the bottom browser strip; per the v1.2.4
            # gizmo-fix priority chain the handle must win. Cards are camera.ui children,
            # so this stays before the "skip direct camera.ui children" guard below.
            if self._handle_browser_click():
                return

            hovered = mouse.hovered_entity

            # Skip direct camera.ui children (toolbar buttons, browser panel, etc.)
            if hovered and getattr(hovered, 'parent', None) is camera.ui:
                return

            # Step 3: active drag or box-select guard (after gizmo and panel steps)
            if self._gizmo_drag_axis is not None or self._box_selecting:
                return

            # Step 4/5: selection (shift) or tool-mode action
            if held_keys['shift']:
                # Shift+click: add/remove from selection — same in both tool modes
                if hovered in (self.blocks + self.enemies):
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
                if hovered in (self.blocks + self.enemies):
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
                (self._hier_panel and self._is_over_panel(self._hier_panel))
                or (self._inspector and self._is_over_panel(self._inspector))
                or self._is_over_browser()
            )
            if self._editor_camera:
                self._editor_camera.zoom_speed = 0 if over_ui else 1.25

            # Asset browser vertical scroll (by row)
            if self._is_over_browser():
                cards = self._browser_cards.get(self._browser_tab, [])
                cols = self._browser_cols()
                total_rows = (len(cards) + cols - 1) // cols if cards else 0
                max_scroll = max(0, total_rows - self._browser_visible_rows())
                delta = -1 if key == 'scroll up' else 1
                self._browser_scroll[self._browser_tab] = max(
                    0, min(self._browser_scroll[self._browser_tab] + delta, max_scroll))
                self._refresh_browser_card_positions()
                self._update_browser_scroll_indicators()
                return

            if self._hier_panel and self._is_over_panel(self._hier_panel):
                total = len(self.blocks) + len(self.enemies)
                max_scroll = max(0, total - self._HIER_MAX_VISIBLE)
                delta = -1 if key == 'scroll up' else 1
                self.hierarchy_scroll = max(0, min(self.hierarchy_scroll + delta, max_scroll))
                self._refresh_hierarchy()
                self._update_hierarchy_highlight()
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
        for e in self.blocks + self.enemies:
            destroy(e)
        self.blocks.clear()
        self.enemies.clear()
        self.selected.clear()

        try:
            entries = load_level_data(self.filename)
            for entry in entries:
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
        texture=str(PROJECT_ROOT / 'assets/textures/floor_ground_grass.png'),
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
