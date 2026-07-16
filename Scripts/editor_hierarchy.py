"""
editor_hierarchy.py — Hierarchy panel collaborator for the level editor (v1.6 split).

Owns the left-hand entity list: panel chrome, search/filter box, collapsible
sections, per-row colour swatches, scroll bar, and the selection highlight.
Reads the editor's entity lists and selection through a back-reference
(`self.editor`) — it never owns that shared state.

Core keeps one-line delegators with the original method names
(_refresh_hierarchy / _update_hierarchy_highlight / _hier_typing) because
undo_redo.py's commands and core's input() call them by name — a de-facto
public API that must not change (v1.6 hard constraint).
"""

from ursina import *

from Scripts.session_logger import get_editor_logger

logger = get_editor_logger()


class HierarchyPanel:
    # Hierarchy layout constants (panel-local space). One row pitch is shared by every
    # visual slot — section headers AND entity rows — so the scroll thumb, swatches and
    # rows are all placed by the SAME _row_y() formula and cannot drift (Bug A).
    _HIER_TOP    = 0.36   # y of the first visual slot (row index 0)
    _HIER_ROW_H  = 0.05   # uniform pitch between consecutive visual slots
    _HIER_MAX_VISIBLE = 13  # how many slots fit between the search field and the panel bottom
    _HIER_SWATCH_X   = -.085  # panel-local x of a row's colour swatch (left edge of the row)
    _HIER_SWATCH_SIZE = .022

    def __init__(self, editor):
        self.editor = editor
        self.panel = None
        self.buttons = []
        self.scroll_bar = None
        self.scroll = 0
        # Search/filter + collapsible sections (Change B).
        self.search_field = None     # InputField pinned above the list
        self.filter = ''             # lower-cased substring; '' = show all
        # v1.7 Step 4: 'light' joins the fold state as a fifth section (the sun).
        self.collapsed = {'block': False, 'enemy': False, 'trigger': False, 'pickup': False, 'light': False}  # per-section fold state
        self.header_buttons = {}     # {'block': Button, 'enemy': Button}
        self.swatches = []           # per-row colour swatch quads (parallel to self.buttons)
        self.build()

    def _row_y(self, visual_index):
        """THE shared row-index -> panel-local-y formula. Slot 0 is at _HIER_TOP and each
        subsequent visual slot (header or entity row) steps down one _HIER_ROW_H. Rows,
        colour swatches, section headers AND the scroll-thumb track all derive their y from
        this single function so they can never diverge (Bug A consolidation)."""
        return self._HIER_TOP - visual_index * self._HIER_ROW_H

    def _visual_rows(self):
        """Ordered list of visual rows for the current filter + collapse state. Each entry is
        ('header', section) or ('row', entity). Headers always appear; a section's entity rows
        are present only when it is expanded AND match the (case-insensitive substring) filter.
        Section counts shown in the header reflect the FILTERED visible entities, not the raw
        totals — so the list reads honestly while searching."""
        ed = self.editor
        flt = self.filter
        rows = []
        for section, members in (('block', ed.blocks), ('enemy', ed.enemies), ('trigger', ed.triggers), ('pickup', ed.pickups), ('light', ed.lights)):
            if flt:
                matched = [e for e in members if flt in self._label(e).lower()]
            else:
                matched = list(members)
            rows.append(('header', section))
            if not self.collapsed[section]:
                rows.extend(('row', e) for e in matched)
        return rows

    def _label(self, e):
        ed = self.editor
        if e in ed.enemies:
            tag = 'E'
        elif e in ed.triggers:
            tag = 'T'
        elif e in ed.pickups:
            tag = 'P'
        elif e in ed.lights:
            # v1.7 Step 4: the sun is captioned by its AIM, not its position — a
            # directional light's position has no effect on the lighting math (it's
            # editor framing only), so "(x,y,z)" would be actively misleading here.
            # Named rather than tagged: there is one sun and 'Sun' is what the user
            # is looking for when they search the panel.
            return f"Sun (pitch {round(e.rotation_x)}, yaw {round(e.rotation_y)})"
        else:
            tag = 'B'
        return f"{tag} ({round(e.x,1)},{round(e.y,1)},{round(e.z,1)})"

    def build(self):
        self.panel = Entity(
            parent=camera.ui,
            model='quad',
            color=color.rgba(0, 0, 0, 0.75),
            scale=(.20, .9),
            position=(-.779, 0),
            z=-0.5,
            eternal=True,
        )
        Text(
            parent=self.panel,
            text='Hierarchy',
            position=(0, .45),
            scale=(.055, .055),
            color=color.white,
            eternal=True,
        )
        # Search/filter box pinned above the list (Change B.1). Live filter-as-you-type via
        # on_value_changed. Each keystroke re-runs refresh(), but that only ever
        # instantiates the <=_HIER_MAX_VISIBLE rows in the viewport (not the full list), so it
        # stays well under a frame even at 140+ entities (~9ms measured). Counted in the
        # typing-guard so Delete/bookmark keys don't fire while editing here (see core input()).
        self.search_field = InputField(
            parent=self.panel,
            position=(0, .40),
            scale=(.17, .03),
            default_value='',
            z=-1,
            eternal=True,
        )
        try:
            self.search_field.setBin('fixed', 41)
        except Exception as e:
            logger.log('ERROR', f"HierarchyPanel.build setBin search {type(e).__name__}: {e}")
        self.search_field.on_value_changed = self._on_search_changed
        # Thin vertical scroll indicator — right edge of panel
        self.scroll_bar = Entity(
            parent=self.panel,
            model='quad',
            color=color.rgba(0.78, 0.78, 0.78, 0.47),   # 0–1 floats — 0–255 clamps to white
            scale=(.018, .05),
            position=(.46, self._HIER_TOP),
            z=-1,
            eternal=True,
        )
        self.buttons = []
        self.swatches = []
        self.header_buttons = {}

    def apply_layout(self, aspect, half_w):
        """Reposition the panel for the current aspect ratio — flush left.

        Collapsed (v1.7 C1): panel.enabled tracks editor.panel_visible['hierarchy']
        directly (toggled in editor._toggle_panel), so only positioning/sizing —
        driven by the effective (possibly zero-reclaimed) width — happens here.
        """
        if self.panel is None:
            return
        hier_w = self.editor._effective_hier_w
        self.panel.x = -half_w + hier_w * 0.5
        self.panel.y = 0
        self.panel.scale_x = hier_w
        self.panel.scale_y = self.editor._LAYOUT_PANEL_H

    def _on_search_changed(self):
        if self.search_field is None:
            return
        self.filter = self.search_field.text.strip().lower()
        self.scroll = 0
        self.refresh()
        self.update_highlight()

    def toggle_section(self, section):
        self.collapsed[section] = not self.collapsed[section]
        self.scroll = 0
        self.refresh()
        self.update_highlight()

    def refresh(self):
        ed = self.editor
        for b in self.buttons:
            destroy(b)
        self.buttons.clear()
        for s in self.swatches:
            destroy(s)
        self.swatches.clear()
        for b in self.header_buttons.values():
            destroy(b)
        self.header_buttons.clear()

        visual_rows = self._visual_rows()
        total = len(visual_rows)
        max_scroll = max(0, total - self._HIER_MAX_VISIBLE)
        self.scroll = max(0, min(self.scroll, max_scroll))

        # Filtered per-section counts for the header captions.
        flt = self.filter
        def _count(members):
            return sum(1 for e in members if not flt or flt in self._label(e).lower())
        section_count = {'block': _count(ed.blocks), 'enemy': _count(ed.enemies), 'trigger': _count(ed.triggers), 'pickup': _count(ed.pickups), 'light': _count(ed.lights)}
        section_name  = {'block': 'Blocks', 'enemy': 'Enemies', 'trigger': 'Triggers', 'pickup': 'Pickups', 'light': 'Lighting'}

        visible = visual_rows[self.scroll: self.scroll + self._HIER_MAX_VISIBLE]
        for slot, (kind, payload) in enumerate(visible):
            y = self._row_y(slot)
            if kind == 'header':
                section = payload
                # OpenSans (Ursina's default font) has NO triangle glyphs (▾▸▼▶ all render as
                # missing-glyph boxes — verified against the .ttf cmap, same class as the ▶/↖
                # gaps noted in CLAUDE.md). Use ASCII [+]/[-] (both glyphs present) for the
                # collapse state instead.
                tri = '[+]' if self.collapsed[section] else '[-]'
                hdr = Button(
                    parent=self.panel,
                    text=f"{tri} {section_name[section]} ({section_count[section]})",
                    scale=(.18, .038),
                    position=(-.01, y),
                    color=ed._THEME_TILE_HOVER,
                    text_origin=(-.5, 0),   # left-align the caption inside the wide button
                    text_scale=.75,
                    z=-1,
                )
                hdr.on_click = lambda s=section: self.toggle_section(s)
                self.header_buttons[section] = hdr
            else:
                e = payload
                btn = Button(
                    parent=self.panel,
                    text=self._label(e),
                    scale=(.18, .038),
                    position=(-.01, y),
                    color=color.orange if e in ed.selected else color.dark_gray,
                    text_scale=.75,
                    z=-1,
                )
                btn.on_click = lambda entity=e: ed._select(entity)
                self.buttons.append(btn)
                # Colour swatch matching the entity's real colour (Change B.2). Parented to the
                # panel (not the button) and placed by the SAME _row_y(slot) so it stays
                # aligned. z below the button text so it reads as a separate chip. NOT eternal:
                # like the row Buttons these are transient and destroyed/rebuilt every refresh —
                # destroy() is a NO-OP on eternal entities (ursina/destroy.py:27), so an eternal
                # swatch would leak and ghost on every rebuild. Hidden in F5 play mode via the
                # panel's enabled cascade, not eternal. (Persistent chrome in build() stays eternal.)
                swatch_color = getattr(e, '_original_color', e.color)
                swatch = Entity(
                    parent=self.panel,
                    model='quad',
                    color=swatch_color,
                    scale=(self._HIER_SWATCH_SIZE, self._HIER_SWATCH_SIZE),
                    position=(self._HIER_SWATCH_X, y),
                    z=-1.1,
                )
                self.swatches.append(swatch)

        self._update_scroll_bar(total)

    def _update_scroll_bar(self, total):
        if not self.scroll_bar:
            return
        if total <= self._HIER_MAX_VISIBLE:
            self.scroll_bar.enabled = False
            return
        self.scroll_bar.enabled = True
        # Track spans the same slots the rows occupy — bounds derived from _row_y so the
        # thumb can never drift from the rows (Bug A). Top of slot 0 down to bottom of the last.
        track_top    = self._row_y(0) + self._HIER_ROW_H * 0.5
        track_bottom = self._row_y(self._HIER_MAX_VISIBLE - 1) - self._HIER_ROW_H * 0.5
        track_h      = track_top - track_bottom  # positive

        thumb_ratio  = self._HIER_MAX_VISIBLE / total
        thumb_h      = max(0.03, track_h * thumb_ratio)
        max_scroll   = total - self._HIER_MAX_VISIBLE
        scroll_frac  = self.scroll / max_scroll if max_scroll > 0 else 0
        # Centre of thumb travels from track_top - thumb_h/2  down to  track_bottom + thumb_h/2
        travel       = track_h - thumb_h
        thumb_centre = track_top - thumb_h / 2 - scroll_frac * travel

        self.scroll_bar.scale_y = thumb_h
        self.scroll_bar.y       = thumb_centre

    def update_highlight(self):
        """Recolour the currently-built entity rows in place from the same visual-row slice the
        buttons were built from — never rebuilds, so it stays cheap on scroll/select."""
        visual_rows = self._visual_rows()
        visible = visual_rows[self.scroll: self.scroll + self._HIER_MAX_VISIBLE]
        row_entities = [payload for kind, payload in visible if kind == 'row']
        for btn, e in zip(self.buttons, row_entities):
            btn.color = color.orange if e in self.editor.selected else color.dark_gray

    def handle_scroll(self, key):
        """Mouse-wheel scroll while the cursor is over the panel — called from core input()."""
        total = len(self._visual_rows())
        max_scroll = max(0, total - self._HIER_MAX_VISIBLE)
        delta = -1 if key == 'scroll up' else 1
        self.scroll = max(0, min(self.scroll + delta, max_scroll))
        self.refresh()
        self.update_highlight()

    def typing(self):
        """True while the hierarchy search box is focused — so Delete/bookmark/backspace keys
        edit the search text instead of deleting entities or recalling camera bookmarks."""
        return bool(self.search_field and self.search_field.active)
