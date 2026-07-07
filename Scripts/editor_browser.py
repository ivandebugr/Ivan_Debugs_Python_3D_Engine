"""
editor_browser.py — Asset browser collaborator for the level editor (v1.6 split).

Owns the self-contained bottom band and its satellites: the tabbed asset
browser (Textures | Models | Sounds), the floating texture/model picker
overlays (v1.3 Steps 4-5), texture hot-reload (Step 3), asset import (Step 6),
the drag-ghost placement flow, and the status-notice toast.

Selection, entity lists, undo history and snap stay core-owned and are reached
via the editor back-ref. Method/attribute names are unchanged from the
monolith so the moved bodies stay diffable; core's input() keeps the v1.2.4
dispatch order and calls _handle_browser_click / handle_scroll / the picker
handlers at the same priority steps as before.
"""

import shutil
import time as _time   # wall-clock for double-click timing (ursina `time` is Panda3D's clock)
from pathlib import Path

from ursina import *

from Scripts.session_logger import get_editor_logger
from Scripts.asset_registry import asset_registry, CATEGORY_DIRS, CATEGORY_EXTENSIONS
from Scripts.undo_redo import PlaceEntityCommand, ChangeTextureCommand, ChangeModelCommand

logger = get_editor_logger()


class AssetBrowser:
    # -------------------------------------------------------------------------
    # Asset picker overlay (v1.3 Step 4 texture, Step 5 model)
    #
    # A floating grid of asset thumbnails from asset_registry, opened by clicking
    # an inspector field (texture swatch, model field). One overlay panel is built
    # lazily per category on first open and reused afterwards (entities just
    # toggle .enabled). self.asset_picker_open holds the open category name (or
    # None); core input() consults it to swallow the next click so a dismiss-click
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

    # Display names for the per-category collapsed strip ("Textures (0)").
    _BROWSER_TAB_TITLES = {'texture': 'Textures', 'model': 'Models', 'sound': 'Sounds'}

    def __init__(self, editor):
        self.editor = editor

        # Asset picker overlay — one overlay instance per category, keyed by name.
        self._asset_picker_panels = {}   # {category: panel Entity}
        self._asset_picker_cells = {}    # {category: [(bg_entity, name), ...]}
        self.asset_picker_open = None    # category name while open, else None
        self._asset_picker_on_select = None

        # Drag-and-drop state
        self._drag_asset = None    # asset dict currently being dragged
        self._drag_ghost = None    # semi-transparent ghost Entity
        self._dragging = False     # True once mouse moves after tile click
        self._drag_origin = None   # Vec2 mouse pos when tile was pressed

        # Panel/tab/card state.
        self._browser_panel = None
        self._browser_tab = 'texture'          # active tab: 'texture' | 'model' | 'sound'
        self._browser_tab_buttons = {}         # {category: Button}
        self._browser_cards = {}               # {category: list[(bg, icon, label, name)]}
        self._browser_empty_labels = {}        # {category: Text} shown when a category is empty
        self._browser_scroll = {'texture': 0, 'model': 0, 'sound': 0}
        self._selected_asset = None            # (category, name) of the highlighted card
        self._browser_last_click = (None, 0.0)  # ((category, name), wall-clock t) for dbl-click
        self._browser_card_assets = {}         # {(category, name): asset_dict} for draggable built-in models
        # Scroll indicators (up/down arrows) — created by build().
        self._browser_scroll_up = None
        self._browser_scroll_down = None

        # Texture hot-reload (v1.3 Step 3). subscribe_texture()/unsubscribe_texture()
        # are the hooks the texture picker calls to wire blocks into live reload.
        self._texture_subscribers = {}   # {texture_name: [entity, ...]}
        # Reusable bottom-of-screen toast (created lazily by _show_status_notice).
        self._status_notice = None

        self.build()

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
            icon.color = self.editor._THEME_TILE_BG
        return icon

    def _build_asset_picker(self, category):
        """Build the overlay panel + thumbnail grid once for `category`, sized to
        the current manifest."""
        ed = self.editor
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
            color=ed._THEME_PANEL_BG,
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
        title.world_scale = Vec3(ed.inspector._INSP_LABEL_WS, ed.inspector._INSP_LABEL_WS, 1)

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
                color=ed._THEME_TILE_BG,
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
            cell_label.world_scale = Vec3(ed.inspector._INSP_LABEL_WS * 0.6, ed.inspector._INSP_LABEL_WS * 0.6, 1)
            cells.append((bg, name))

        self._asset_picker_panels[category] = panel
        self._asset_picker_cells[category] = cells

    def _open_asset_picker(self, category, anchor_entity, on_select):
        """Open the `category` asset picker overlay anchored to the left of
        `anchor_entity` (an inspector field/swatch in camera.ui space).
        `on_select(name)` is called when a cell is clicked; the picker closes
        itself either way (selection or dismiss)."""
        if not self.editor.selected:
            return
        if category not in self._asset_picker_panels:
            self._build_asset_picker(category)
        panel = self._asset_picker_panels[category]
        if anchor_entity is not None:
            anchor_x, anchor_y, _, _ = self.editor._camera_ui_pos_and_scale(anchor_entity)
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
            if self.editor._is_over_world_panel(bg):
                return name
        return None

    def open_texture_picker(self):
        """Open the texture picker overlay near the inspector's texture swatch."""
        self._open_asset_picker('texture', self.editor.inspector._insp_tex_swatch, self._apply_texture_pick)

    def close_texture_picker(self):
        self._close_asset_picker()

    @property
    def texture_picker_open(self):
        return self.asset_picker_open == 'texture'

    def _apply_texture_pick(self, name):
        """Apply `name`'s texture to every selected entity as one undo step, then close."""
        ed = self.editor
        entities = [e for e in ed.selected if getattr(e, 'destroy_source', None) is None]
        if entities:
            old_textures = [ed._entity_texture_name(e) or 'white_cube' for e in entities]
            cmd = ChangeTextureCommand(entities, old_textures, name)
            cmd.execute()
            ed._history.push(cmd)
            for e in entities:
                self.subscribe_texture(name, e)
            ed._update_inspector_texture_swatch()
            logger.log('INFO', f"Texture picker applied: {name} to {len(entities)} entities")
        self._close_asset_picker()

    def open_model_picker(self):
        """Open the model picker overlay near the inspector's model field.

        Blocks only — enemy model swapping is deferred to v1.4. Guard here even
        though the caller already gates on selection type, so this can never be
        invoked into applying a model onto an enemy.
        """
        ed = self.editor
        if any(e in ed.enemies for e in ed.selected):
            return
        self._open_asset_picker('model', ed.inspector._insp_model_field, self._apply_model_pick)

    def _apply_model_pick(self, name):
        """Apply `name`'s model to every selected block as one undo step, then close."""
        ed = self.editor
        entities = [e for e in ed.selected
                    if getattr(e, 'destroy_source', None) is None and e not in ed.enemies]
        if entities:
            path = asset_registry.get_model_path(name) or name
            old_models = [ed._entity_model_name(e) or 'cube' for e in entities]
            cmd = ChangeModelCommand(entities, old_models, path)
            cmd.execute()
            ed._history.push(cmd)
            ed._update_inspector_model_field()
            logger.log('INFO', f"Model picker applied: {name} to {len(entities)} entities")
        self._close_asset_picker()

    # -------------------------------------------------------------------------
    # Asset browser band
    #
    # Three tabs (Textures | Models | Sounds) over a vertically scrollable column
    # of thumbnail cards. Textures/Sounds come from asset_registry; Models merges
    # BUILTIN_MODELS (Cube/Stone/Metal/Wood/Enemy) with any real scanned .obj/.gltf
    # files. Drag a Models-tab card to place an entity (same ghost/snap/undo as
    # the former tray). Texture/Sound cards are click-to-select only.
    # -------------------------------------------------------------------------

    def _is_over_browser(self):
        my = mouse.y
        return (self._BROWSER_Y - self._BROWSER_H * 0.5) <= my <= (self._BROWSER_Y + self._BROWSER_H * 0.5)

    def build(self):
        """Build the read-only asset browser panel, tabs, and per-category card columns."""
        ed = self.editor
        # Reflect current disk state at editor startup (Step 2 spec point 8).
        try:
            asset_registry.rebuild()
        except Exception as e:
            logger.log('ERROR', f"AssetBrowser.build rebuild {type(e).__name__}: {e}")

        self._browser_panel = Entity(
            parent=camera.ui,
            model='quad',
            color=ed._THEME_PANEL_BG,
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
        scroll_ind_x = aspect * 0.5 - ed._LAYOUT_INSP_W - 0.03
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
            for asset in self.editor.BUILTIN_MODELS:
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
            color=self.editor._THEME_TEXT,
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
            color=self.editor._THEME_TILE_BG,
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
        self.editor._apply_layout()

    def _browser_cols(self):
        """Number of card columns that fit in the browser's usable width."""
        aspect = getattr(window, 'aspect_ratio', 16 / 9) or 16 / 9
        half_w = aspect * 0.5
        left = -half_w + self.editor._LAYOUT_HIER_W + 0.02
        right = half_w - self.editor._LAYOUT_INSP_W - 0.02
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
        left = -half_w + self.editor._LAYOUT_HIER_W + 0.02
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
            btn.color = self.editor._THEME_TAB_ACTIVE if cat == category else self.editor._THEME_TAB_IDLE
        self._refresh_browser_card_positions()
        self._apply_browser_selection_highlight()
        self._update_browser_scroll_indicators()

    def _apply_browser_selection_highlight(self):
        """Tint the selected card's background; reset all others on the active tab."""
        for bg, icon, label, name in self._browser_cards.get(self._browser_tab, []):
            selected = self._selected_asset == (self._browser_tab, name)
            bg.color = self.editor._THEME_TILE_SEL if selected else self.editor._THEME_TILE_BG

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

    def handle_scroll(self, key):
        """Mouse-wheel vertical scroll (by row) while the cursor is over the
        browser band — called from core input()."""
        cards = self._browser_cards.get(self._browser_tab, [])
        cols = self._browser_cols()
        total_rows = (len(cards) + cols - 1) // cols if cards else 0
        max_scroll = max(0, total_rows - self._browser_visible_rows())
        delta = -1 if key == 'scroll up' else 1
        self._browser_scroll[self._browser_tab] = max(
            0, min(self._browser_scroll[self._browser_tab] + delta, max_scroll))
        self._refresh_browser_card_positions()
        self._update_browser_scroll_indicators()

    def apply_layout(self, aspect, half_w):
        """Reposition the full-width band + tabs + scroll indicators for the
        current aspect ratio — called from core _apply_layout()."""
        if self._browser_panel is not None:
            self._browser_panel.x = 0
            self._browser_panel.y = self._BROWSER_Y
            self._browser_panel.scale_x = aspect
            self._browser_panel.scale_y = self._BROWSER_H

        self._layout_browser_tabs(half_w)

        # Scroll indicators — flush to right of the card grid, at top/bottom.
        scroll_ind_x = half_w - self.editor._LAYOUT_INSP_W - 0.03
        if self._browser_scroll_up is not None:
            self._browser_scroll_up.x = scroll_ind_x
            self._browser_scroll_up.y = self._BROWSER_Y + self._BROWSER_H * 0.5 - 0.02
        if self._browser_scroll_down is not None:
            self._browser_scroll_down.x = scroll_ind_x
            self._browser_scroll_down.y = self._BROWSER_Y - self._BROWSER_H * 0.5 + 0.02
        self._update_browser_scroll_indicators()

    def _layout_browser_tabs(self, half_w):
        """Reposition browser tab buttons relative to the current aspect ratio."""
        tabs = self._browser_tab_buttons
        if not tabs:
            return
        tab_order = ('texture', 'model', 'sound')
        tab_w = 0.12
        tab_gap = 0.01
        start_x = -half_w + self.editor._LAYOUT_HIER_W + tab_w * 0.5 + 0.01
        for i, cat in enumerate(tab_order):
            btn = tabs.get(cat)
            if btn is None:
                continue
            btn.x = start_x + i * (tab_w + tab_gap)
            btn.y = self._TAB_Y
            btn.scale_x = tab_w
            btn.scale_y = 0.035

    def set_visible(self, visible):
        """Show/hide the whole band (panel + tab buttons + scroll indicators) —
        the browser half of core's _set_editor_ui_visible. Card / empty-label
        visibility is delegated to _set_browser_tab so only the active tab shows."""
        browser_widgets = ([self._browser_panel]
                           + list(self._browser_tab_buttons.values())
                           + [self._browser_scroll_up, self._browser_scroll_down])
        for widget in browser_widgets:
            if widget:
                if getattr(widget, 'destroy_source', None) is not None:
                    logger.log('WARN', f'AssetBrowser.set_visible: skipped destroyed browser widget {widget}')
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

    def start_hot_reload(self):
        """Register the registry callbacks and arm the first poll. Called from
        core __init__ AFTER load_existing_level(), preserving the monolith's
        registration order."""
        asset_registry.register_callback('texture', self._on_texture_changed)
        asset_registry.register_callback('model', self._on_model_changed)
        asset_registry.register_callback('sound', self._on_sound_changed)
        self._poll_assets()

    def _poll_assets(self):
        """Poll the asset registry for on-disk changes every 2s; re-arm the timer.

        Hot-reload is fully disabled while play-in-editor is active — the game
        simulation owns those assets. Play-in-editor is tracked by the editor-
        local _play_mode flag (set in _enter_play_mode/_exit_play_mode);
        the editor never sets game.state to PLAYING, so that is the right flag.
        """
        if self.editor._play_mode:
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
        if self.editor._play_mode:
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
        if self.editor._play_mode:
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

    # -------------------------------------------------------------------------
    # Drag-to-place ghost
    # -------------------------------------------------------------------------

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
        pos = self.editor._snap(pos)
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
        ed = self.editor
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
            ed.enemies.append(new_entity)
        elif asset['type'] == 'trigger':
            # v1.5 Step 6: drop a trigger volume with empty action lists; the
            # inspector wires actions afterwards. Shared builder handles tracking.
            new_entity = ed._make_trigger_entity(pos, asset['scale'], [], [])
        elif asset['type'] == 'pickup':
            # v1.5 Step 13: drop a pickup with the default config (ammo/pistol/30);
            # the inspector edits it afterwards. Shared builder handles tracking.
            new_entity = ed._make_pickup_entity(pos, {})
        else:
            new_entity = Entity(
                model=asset['model'],
                color=asset_color,
                scale=asset['scale'],
                position=pos,
                collider='box'
            )
            new_entity._original_color = asset_color
            ed.blocks.append(new_entity)

        cmd = PlaceEntityCommand(ed, new_entity)
        ed._history.push(cmd)
        ed._refresh_hierarchy()
        logger.log('INFO', f"Entity placed: type={asset['type']} pos={[round(p, 3) for p in pos]}")

        destroy(ghost)
        self._drag_ghost = None
        self._drag_asset = None
        self._dragging = False
        self._drag_origin = None
