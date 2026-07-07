"""
editor_inspector.py — Inspector panel collaborator for the level editor (v1.6 split).

Owns the right-hand inspector: the Pos/Scale field grid, texture swatch, model
field, door-name field (v1.5 Step 4), and the three mutually-exclusive lower-band
sections — behaviour-tree config (enemies, v1.4 Step 9), trigger action editor
(v1.5 Step 6), and pickup config editor (v1.5 Step 13). Each section keeps its
single refresh path by design.

Selection, entity lists, undo history and the gizmo stay core-owned and are
reached via the editor back-ref. Core keeps one-line delegators with the
original method names — _refresh_behaviour_ui / _refresh_trigger_ui /
_refresh_pickup_ui are called by undo_redo.py's commands (de-facto public API,
v1.6 hard constraint), and the typing guards are aggregated in core input().
"""

from pathlib import Path

from ursina import *

from Scripts.session_logger import get_editor_logger
from Scripts.asset_registry import asset_registry
from Scripts.behaviour_tree_factory import BehaviourTreeFactory
from Scripts.weapon import WEAPON_TYPES
from Scripts.undo_redo import (
    ChangePropertyCommand, ChangeBehaviourCommand, ChangeTriggerActionsCommand,
    ChangePickupConfigCommand,
)

logger = get_editor_logger()


class InspectorPanel:

    def __init__(self, editor):
        self.editor = editor
        self.panel = None
        self.build()


    # Absolute world_scale for inspector Text. A plain Text parented to the small
    # (0.30 x 0.9) panel inherits that tiny scale and renders microscopically — the
    # real reason inspector labels have always looked "invisible" here (the old
    # setBin/z fixes only addressed z-order). Ursina's Button sidesteps this by
    # forcing text_entity.world_scale ~= 20; we do the same so labels are legible.
    _INSP_TITLE_WS = 22
    _INSP_LABEL_WS = 15

    def build(self):
        self.panel = Entity(
            parent=camera.ui,
            model='quad',
            color=color.rgba(0, 0, 0, 0.75),
            scale=(.30, .9),
            position=(.739, 0),
            z=-0.5,
            eternal=True,
        )
        self._insp_title = Text(
            parent=self.panel,
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
        # Display-only 3-column x 3-row grid of the nine transform fields:
        #   Row 1: Pos X/Y/Z   Row 2: Scale X/Y/Z   Row 3: Rot X/Y/Z
        # HP, colour, texture and enemy_type still live on the entity and
        # round-trip through level.json (see _build_level_data); they are simply
        # not editable here. Coordinates are panel-local (quad spans -0.5..0.5).
        grid = [
            [('pos_x', 'Pos X'), ('pos_y', 'Pos Y'), ('pos_z', 'Pos Z')],
            [('scl_x', 'Scale X'), ('scl_y', 'Scale Y'), ('scl_z', 'Scale Z')],
            [('rot_x', 'Rot X'), ('rot_y', 'Rot Y'), ('rot_z', 'Rot Z')],
        ]
        col_x = (-.33, 0.0, .33)              # column centres
        row_label_y = (.24, .04, -.16)        # label y per row
        row_field_y = (.15, -.05, -.25)       # input-field y per row
        self._insp_fields = {}
        for r, row in enumerate(grid):
            for c, (key, label) in enumerate(row):
                label_text = Text(
                    parent=self.panel,
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
                    parent=self.panel,
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
            parent=self.panel,
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
            parent=self.panel,
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
            parent=self.panel,
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
            parent=self.panel,
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
            parent=self.panel,
            model='quad',
            color=self.editor._THEME_TILE_BG,
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
            parent=self.panel,
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
            parent=self.panel,
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
        entities = list(self.editor.selected)
        # Only blocks are doors — hide for any selection containing a non-block.
        has_nonblock = any((e in self.editor.enemies or e in self.editor.triggers) for e in entities)
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
        if not self.editor.selected or value_str == '---':
            return
        value = value_str.strip()
        for e in list(self.editor.selected):
            if getattr(e, 'destroy_source', None) is not None:
                continue
            if e in self.editor.enemies or e in self.editor.triggers:   # only blocks are doors
                continue
            old = getattr(e, 'door_name', '')
            if old == value:
                continue
            logger.log('INFO', f"Inspector door_name changed: entity@{[round(p,3) for p in e.position]} {old!r} -> {value!r}")
            cmd = ChangePropertyCommand(e, 'door_name', old, value)
            cmd.execute()
            self.editor._history.push(cmd)

    def _update_inspector(self):
        if not self.editor.selected:
            for f in self._insp_fields.values():
                f.text = ' '
            self._update_inspector_texture_swatch()
            self._update_inspector_model_field()
            self._update_inspector_door_field()
            self._refresh_behaviour_ui()
            self._refresh_trigger_ui()
            self._refresh_pickup_ui()
            return
        entities = list(self.editor.selected)

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
        self._insp_fields['rot_x'].text = shared_or_multi(lambda e: e.rotation_x)
        self._insp_fields['rot_y'].text = shared_or_multi(lambda e: e.rotation_y)
        self._insp_fields['rot_z'].text = shared_or_multi(lambda e: e.rotation_z)
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
        entities = list(self.editor.selected)
        # Trigger volume colour/texture is editor chrome (not saved) — don't surface
        # it as if it were an editable texture. Treat a trigger-containing selection
        # like empty for the swatch.
        if not entities or any(e in self.editor.triggers for e in entities):
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
        entities = list(self.editor.selected)
        # Blocks only — hide for enemy/trigger/mixed (triggers reuse this lower band
        # for their action editor; only one section is ever enabled).
        has_nonblock = any((e in self.editor.enemies or e in self.editor.triggers) for e in entities)
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
    # Gating (decision PART 0D) is list-membership `e in self.editor.enemies`, identical
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
            parent=self.panel,
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
                parent=self.panel,
                text=self._behav_preset_short(preset),
                position=(x0 + i * step, self._BEHAV_PRESET_Y),
                scale=(step * 0.92, 0.05),
                color=self.editor._THEME_TILE_BG,
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
            parent=self.panel,
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
            parent=self.panel,
            text='+ Waypoint',
            position=(.15, self._BEHAV_WP_LABEL_Y),
            scale=(.30, 0.045),
            color=self.editor._THEME_TILE_BG,
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
        return [e for e in self.editor.selected if e in self.editor.enemies]

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
            btn.color = color.azure if preset == active else self.editor._THEME_TILE_BG

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
                    parent=self.panel,
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
                parent=self.panel,
                text='x',
                position=(del_x, row_y),
                scale=(.05, .045),
                color=self.editor._THEME_TILE_BG,
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
        self.editor._history.push(cmd)
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
        self.editor._history.push(cmd)
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
        self.editor._history.push(cmd)
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
        self.editor._history.push(cmd)
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
            parent=self.panel, text='on_enter',
            position=(-.33, self._TRIG_ENTER_LABEL_Y),
            color=color.light_gray, origin=(0, 0), z=-1, eternal=True, enabled=False,
        )
        self._trig_enter_label.world_scale = Vec3(self._INSP_LABEL_WS, self._INSP_LABEL_WS, 1)
        self._trig_enter_add = Button(
            parent=self.panel, text='+',
            position=(.30, self._TRIG_ENTER_LABEL_Y), scale=(.06, .045),
            color=self.editor._THEME_TILE_BG, z=-1, eternal=True, enabled=False,
        )
        self._trig_enter_add.text_entity.world_scale = Vec3(10, 10, 1)
        self._trig_enter_add.on_click = lambda: self._on_add_trigger_action('on_enter')

        self._trig_exit_label = Text(
            parent=self.panel, text='on_exit',
            position=(-.33, self._TRIG_EXIT_LABEL_Y),
            color=color.light_gray, origin=(0, 0), z=-1, eternal=True, enabled=False,
        )
        self._trig_exit_label.world_scale = Vec3(self._INSP_LABEL_WS, self._INSP_LABEL_WS, 1)
        self._trig_exit_add = Button(
            parent=self.panel, text='+',
            position=(.30, self._TRIG_EXIT_LABEL_Y), scale=(.06, .045),
            color=self.editor._THEME_TILE_BG, z=-1, eternal=True, enabled=False,
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
        trigs = [e for e in self.editor.selected if e in self.editor.triggers]
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
                parent=self.panel, text=name,
                position=(-.14, row_y), scale=(.34, .042),
                color=self.editor._THEME_TILE_BG, z=-1,
            )
            type_btn.text_entity.world_scale = Vec3(8, 8, 1)
            type_btn.on_click = lambda w=which, i=idx: self._on_cycle_trigger_action(w, i)
            # Target field — only for open_door (the one action that takes a param).
            target_field = None
            if name == 'open_door':
                target_field = InputField(
                    parent=self.panel,
                    position=(.18, row_y), scale=(.18, .042),
                    default_value=str(action.get('target', '')), z=-1,
                )
                target_field.submit_on = ['enter']
                target_field.on_submit = (
                    lambda w=which, i=idx, f=target_field: self._on_trigger_target_edit(w, i, f.text)
                )
            del_btn = Button(
                parent=self.panel, text='x',
                position=(.33, row_y), scale=(.05, .042),
                color=self.editor._THEME_TILE_BG, z=-1,
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
        self.editor._history.push(cmd)

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
            parent=self.panel, text='type',
            position=(-.33, self._PICKUP_TYPE_Y),
            color=color.light_gray, origin=(0, 0), z=-1, eternal=True, enabled=False,
        )
        self._pickup_type_label.world_scale = Vec3(self._INSP_LABEL_WS, self._INSP_LABEL_WS, 1)
        self._pickup_type_btn = Button(
            parent=self.panel, text='ammo',
            position=(.10, self._PICKUP_TYPE_Y), scale=(.34, .042),
            color=self.editor._THEME_TILE_BG, z=-1, eternal=True, enabled=False,
        )
        self._pickup_type_btn.text_entity.world_scale = Vec3(8, 8, 1)
        self._pickup_type_btn.on_click = self._on_cycle_pickup_type

        self._pickup_weapon_label = Text(
            parent=self.panel, text='weapon',
            position=(-.33, self._PICKUP_WEAPON_Y),
            color=color.light_gray, origin=(0, 0), z=-1, eternal=True, enabled=False,
        )
        self._pickup_weapon_label.world_scale = Vec3(self._INSP_LABEL_WS, self._INSP_LABEL_WS, 1)
        self._pickup_weapon_btn = Button(
            parent=self.panel, text='pistol',
            position=(.10, self._PICKUP_WEAPON_Y), scale=(.34, .042),
            color=self.editor._THEME_TILE_BG, z=-1, eternal=True, enabled=False,
        )
        self._pickup_weapon_btn.text_entity.world_scale = Vec3(8, 8, 1)
        self._pickup_weapon_btn.on_click = self._on_cycle_pickup_weapon

        self._pickup_amount_label = Text(
            parent=self.panel, text='amount',
            position=(-.33, self._PICKUP_AMOUNT_Y),
            color=color.light_gray, origin=(0, 0), z=-1, eternal=True, enabled=False,
        )
        self._pickup_amount_label.world_scale = Vec3(self._INSP_LABEL_WS, self._INSP_LABEL_WS, 1)
        self._pickup_amount_field = InputField(
            parent=self.panel,
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
        picks = [e for e in self.editor.selected if e in self.editor.pickups]
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
        config = getattr(pickup, 'pickup_config', self.editor._PICKUP_DEFAULT_CONFIG)
        is_ammo = config.get('pickup_type', 'ammo') == 'ammo'
        self._set_pickup_section_visible(True, is_ammo)
        self._pickup_type_btn.text = config.get('pickup_type', 'ammo')
        self._pickup_weapon_btn.text = config.get('weapon_type', 'pistol')
        self._pickup_amount_field.text = str(config.get('amount', 30))

    def _commit_pickup_config(self, pickup, config):
        """Push one ChangePickupConfigCommand and execute it (refreshes the UI)."""
        cmd = ChangePickupConfigCommand(self, pickup, config)
        cmd.execute()
        self.editor._history.push(cmd)

    def _on_cycle_pickup_type(self):
        pickup = self._selected_pickup()
        if pickup is None:
            return
        config = dict(getattr(pickup, 'pickup_config', self.editor._PICKUP_DEFAULT_CONFIG))
        cur = config.get('pickup_type', 'ammo')
        config['pickup_type'] = self.PICKUP_TYPES[(self.PICKUP_TYPES.index(cur) + 1) % len(self.PICKUP_TYPES)] \
            if cur in self.PICKUP_TYPES else self.PICKUP_TYPES[0]
        self._commit_pickup_config(pickup, config)

    def _on_cycle_pickup_weapon(self):
        pickup = self._selected_pickup()
        if pickup is None:
            return
        config = dict(getattr(pickup, 'pickup_config', self.editor._PICKUP_DEFAULT_CONFIG))
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
        config = dict(getattr(pickup, 'pickup_config', self.editor._PICKUP_DEFAULT_CONFIG))
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
        if not self.editor.selected or value_str in ('---', ''):
            return
        try:
            value = float(value_str)
        except (ValueError, TypeError):
            return
        attr_map = {
            'pos_x': 'x', 'pos_y': 'y', 'pos_z': 'z',
            'scl_x': 'scale_x', 'scl_y': 'scale_y', 'scl_z': 'scale_z',
            'rot_x': 'rotation_x', 'rot_y': 'rotation_y', 'rot_z': 'rotation_z',
        }
        attr = attr_map.get(key)
        if not attr:
            return
        for e in list(self.editor.selected):
            # FIXED: guard against stale destroyed entity refs in selection set
            if getattr(e, 'destroy_source', None) is not None:
                continue
            old = getattr(e, attr, 0)
            logger.log('INFO', f"Inspector property changed: entity@{[round(p,3) for p in e.position]} field={key} {old} -> {value}")
            cmd = ChangePropertyCommand(e, attr, old, value)
            cmd.execute()
            self.editor._history.push(cmd)
        # Update only this field in-place; do not clear selection or rebuild
        # the inspector (which would steal focus and prevent further edits).
        field = self._insp_fields.get(key)
        if field is not None:
            field.text = str(round(value, 3))
        if self.editor.gizmo is not None:
            self.editor.gizmo.refresh()

    def apply_layout(self, aspect, half_w):
        """Reposition the panel for the current aspect ratio — flush right."""
        if self.panel is None:
            return
        insp_w = self.editor._LAYOUT_INSP_W
        self.panel.x = half_w - insp_w * 0.5
        self.panel.y = 0
        self.panel.scale_x = insp_w
        self.panel.scale_y = self.editor._LAYOUT_PANEL_H

    def set_visible(self, visible):
        """Show/hide the panel (+ title; sections cascade with the panel) —
        the inspector half of core's _set_editor_ui_visible."""
        for widget in (self.panel, self._insp_title):
            if widget:
                if getattr(widget, 'destroy_source', None) is not None:
                    logger.log('WARN', f'InspectorPanel.set_visible: skipped destroyed widget {widget}')
                    continue
                widget.enabled = visible
