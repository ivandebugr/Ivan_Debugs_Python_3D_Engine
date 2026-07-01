from collections import deque
from pathlib import Path

from Scripts.asset_registry import asset_registry


class Command:
    def execute(self): raise NotImplementedError
    def undo(self):    raise NotImplementedError


def _resolve_texture(name):
    """Resolve a registry texture name to a Texture object via the same
    Texture(Path(path)) constructor the browser thumbnail loader uses.
    Built-in names (e.g. 'white_cube') are passed through as strings —
    Ursina's load_texture handles those by searching internal_textures_folder."""
    if not name:
        return name
    path = asset_registry.get_texture_path(name)
    if path:
        from ursina import Texture
        return Texture(Path(path))
    return name


def _resolve_model(name_or_path):
    """Resolve a model reference to something safe to assign to Entity.model.

    Mirrors _resolve_texture for models. The bug it avoids is the same one the
    v1.3-step4 texture fix solved: assigning a bare path *string* to .model
    sends it through load_model()'s glob-by-name search rooted at
    application.asset_folder, which double-nests against an already-resolved
    project-relative path and fails ('missing model' warning, blank entity).

    Three cases:
      - falsy / 'cube' / any built-in primitive name with no registry entry:
        return the string unchanged. load_model finds Ursina's own built-in
        models (cube, sphere, quad, ...) by name in its internal folder, so the
        default-'cube' fallback must stay a bare string — do NOT force it through
        path resolution.
      - a registry *name* (e.g. 'wall_pillar'): look up its path, load directly.
      - an already-resolved relative path (e.g. 'assets/models/wall_pillar.obj'),
        which is what level.json stores and what the picker passes post-step4:
        load it directly via load_model(filename, folder=parent) — the folder
        override is the model-side equivalent of Texture(Path(path)); it bypasses
        the broken asset_folder glob entirely.

    The returned model NodePath's .name is set to the project-relative path so
    _entity_model_name / _build_level_data serialise it back unchanged.
    """
    if not name_or_path or name_or_path == 'cube':
        return name_or_path

    path = asset_registry.get_model_path(name_or_path)
    if path is None and ('/' in name_or_path or '\\' in name_or_path):
        # Already a path (level.json value / picker output) — use it as-is.
        path = name_or_path
    if path is None:
        # Unknown bare name: a built-in primitive (sphere, diamond, ...) or a
        # genuinely missing asset. Let Ursina's load_model handle/​warn — same
        # as the 'cube' default path.
        return name_or_path

    p = Path(path)
    from ursina.mesh_importer import load_model
    m = load_model(p.name, folder=p.parent)
    if m is None:
        # Load failed (corrupt / unsupported); fall back to the string so the
        # caller sees Ursina's own missing-model warning rather than a crash.
        return name_or_path
    m.name = path
    return m


def _restore_entity(editor, snap):
    """Reconstruct an editor entity from a snapshot dict; add to correct list."""
    from ursina import Entity, color
    # v1.5 Step 6: trigger snapshots route through the editor's shared builder so
    # the volume visuals + raw action lists are reconstructed identically to a fresh
    # placement (single construction site). Detected by the is_trigger marker.
    if snap.get('is_trigger', False):
        return editor._make_trigger_entity(
            snap['position'], snap.get('scale', (3, 2, 3)),
            snap.get('on_enter', []), snap.get('on_exit', []),
        )
    # v1.5 Step 13: pickup snapshots route through the editor's shared builder,
    # same pattern as is_trigger above — single construction site.
    if snap.get('is_pickup', False):
        return editor._make_pickup_entity(snap['position'], snap.get('pickup_config', {}))
    is_enemy = snap.get('is_enemy', False)
    e = Entity(
        model='cube',
        texture=_resolve_texture(snap.get('texture', 'white_cube')),
        position=snap['position'],
        color=snap.get('color', color.white),
        rotation=snap.get('rotation', (0, 0, 0)),
        scale=snap.get('scale', (1, 1, 1)),
        collider='box',
    )
    if is_enemy:
        e.origin_y = -0.5
    e.enemy_hp    = snap.get('enemy_hp', 100)
    e.enemy_type  = snap.get('enemy_type', 'default')
    e._original_color = snap.get('color', color.white)
    if is_enemy:
        editor.enemies.append(e)
    else:
        editor.blocks.append(e)
    return e


class PlaceEntityCommand(Command):
    """Place entity at position from snapshot; undo destroys it, redo recreates it."""

    def __init__(self, editor, entity):
        self.editor = editor
        self.entity = entity
        # v1.5 Step 6: trigger placeholders snapshot differently — _restore_entity
        # routes is_trigger snapshots through editor._make_trigger_entity, so the
        # snapshot carries the raw action lists + scale, not block/enemy fields.
        if getattr(editor, 'triggers', None) is not None and entity in editor.triggers:
            self._snapshot = {
                'is_trigger': True,
                'position': entity.position,
                'scale': entity.scale,
                'on_enter': [dict(a) for a in getattr(entity, 'on_enter_actions', [])],
                'on_exit':  [dict(a) for a in getattr(entity, 'on_exit_actions', [])],
            }
            return
        # v1.5 Step 13: pickup placeholders snapshot differently — same is_pickup
        # marker pattern as is_trigger above.
        if getattr(editor, 'pickups', None) is not None and entity in editor.pickups:
            self._snapshot = {
                'is_pickup': True,
                'position': entity.position,
                'pickup_config': dict(getattr(entity, 'pickup_config', {})),
            }
            return
        is_enemy = entity in editor.enemies
        tex_name = ''
        if hasattr(entity, 'texture') and entity.texture:
            tex_name = getattr(entity.texture, 'name', str(entity.texture))
        self._snapshot = {
            'position': entity.position,
            'texture': tex_name,
            'color': getattr(entity, '_original_color', entity.color),
            'rotation': (entity.rotation_x, entity.rotation_y, entity.rotation_z),
            'scale': entity.scale,
            'enemy_hp': getattr(entity, 'enemy_hp', 100),
            'enemy_type': getattr(entity, 'enemy_type', 'default'),
            'is_enemy': is_enemy,
        }

    def __repr__(self):
        return f"PlaceEntityCommand(pos={list(self._snapshot['position'])}, enemy={self._snapshot.get('is_enemy')}, trigger={self._snapshot.get('is_trigger', False)})"

    def execute(self):
        self.entity = _restore_entity(self.editor, self._snapshot)
        self.editor._refresh_hierarchy()

    def undo(self):
        if self.entity in self.editor.blocks:
            self.editor.blocks.remove(self.entity)
        elif self.entity in self.editor.enemies:
            self.editor.enemies.remove(self.entity)
        elif self.entity in getattr(self.editor, 'triggers', []):
            self.editor.triggers.remove(self.entity)
        elif self.entity in getattr(self.editor, 'pickups', []):
            self.editor.pickups.remove(self.entity)
        from ursina import destroy
        destroy(self.entity)
        self.editor._refresh_hierarchy()


class DeleteEntityCommand(Command):
    """Remove entity from scene; undo restores it from snapshot, redo removes again."""

    def __init__(self, editor, entity, entity_data):
        self.editor      = editor
        self.entity_data = entity_data
        self.entity      = entity
        self._restored   = None

    def __repr__(self):
        pos = self.entity_data.get('position', '?')
        return f"DeleteEntityCommand(pos={pos}, enemy={self.entity_data.get('is_enemy')})"

    def execute(self):
        if self.entity in self.editor.blocks:
            self.editor.blocks.remove(self.entity)
        if self.entity in self.editor.enemies:
            self.editor.enemies.remove(self.entity)
        if self.entity in getattr(self.editor, 'triggers', []):
            self.editor.triggers.remove(self.entity)
        if self.entity in getattr(self.editor, 'pickups', []):
            self.editor.pickups.remove(self.entity)
        from ursina import destroy
        destroy(self.entity)
        self.editor._refresh_hierarchy()

    def undo(self):
        self._restored = _restore_entity(self.editor, self.entity_data)
        self.entity = self._restored
        self.editor._refresh_hierarchy()


class MoveEntityCommand(Command):
    """Move entity to new_pos; undo restores old_pos, redo reapplies new_pos."""

    def __init__(self, entity, old_pos, new_pos):
        self.entity  = entity
        self.old_pos = old_pos
        self.new_pos = new_pos

    def __repr__(self):
        return f"MoveEntityCommand(old={list(self.old_pos)}, new={list(self.new_pos)})"

    def execute(self): self.entity.position = self.new_pos
    def undo(self):    self.entity.position = self.old_pos


class ChangeTextureCommand(Command):
    """Apply new_texture to all entities; undo restores per-entity old textures."""

    def __init__(self, entities, old_textures, new_texture):
        self.entities     = entities
        self.old_textures = old_textures
        self.new_texture  = new_texture

    def __repr__(self):
        return f"ChangeTextureCommand(n={len(self.entities)}, new={self.new_texture!r})"

    def execute(self):
        new_texture = _resolve_texture(self.new_texture)
        for e in self.entities:
            e.texture = new_texture

    def undo(self):
        for e, t in zip(self.entities, self.old_textures):
            e.texture = _resolve_texture(t)


class ChangeModelCommand(Command):
    """Apply new_model to all entities; undo restores per-entity old models."""

    def __init__(self, entities, old_models, new_model):
        self.entities    = entities
        self.old_models  = old_models
        self.new_model   = new_model

    def __repr__(self):
        return f"ChangeModelCommand(n={len(self.entities)}, new={self.new_model!r})"

    def execute(self):
        # Resolve per entity, not once: _resolve_model returns a mesh NodePath
        # and Ursina's model setter reparents that exact node, so one shared
        # instance would attach to only the last entity. (A bare string like
        # 'cube' is returned unchanged and is safe to reuse.)
        for e in self.entities:
            e.model = _resolve_model(self.new_model)

    def undo(self):
        for e, m in zip(self.entities, self.old_models):
            e.model = _resolve_model(m)


class ChangeColourCommand(Command):
    """Apply new_colour to all entities; undo restores per-entity old colours."""

    def __init__(self, entities, old_colours, new_colour):
        self.entities    = entities
        self.old_colours = old_colours
        self.new_colour  = new_colour

    def __repr__(self):
        return f"ChangeColourCommand(n={len(self.entities)}, new={self.new_colour})"

    def execute(self):
        for e in self.entities:
            e.color = self.new_colour

    def undo(self):
        for e, c in zip(self.entities, self.old_colours):
            e.color = c


class ChangePropertyCommand(Command):
    """Set entity.prop to new_val; undo restores old_val."""

    def __init__(self, entity, prop, old_val, new_val):
        self.entity  = entity
        self.prop    = prop
        self.old_val = old_val
        self.new_val = new_val

    def __repr__(self):
        return f"ChangePropertyCommand(prop={self.prop!r}, old={self.old_val}, new={self.new_val})"

    def execute(self): setattr(self.entity, self.prop, self.new_val)
    def undo(self):    setattr(self.entity, self.prop, self.old_val)


def _copy_behaviour_config(config):
    """Deep-ish copy of a behaviour_config dict so a stored snapshot can never
    alias the live dict the UI mutates in place.

    behaviour_config is a flat dict whose only nested value is "waypoints" — a
    list of 3-element [x, y, z] lists. A shallow dict() would share that list
    (and its inner lists) with the live config, so a later in-place waypoint
    edit would silently rewrite the snapshot and break undo. Copy the list and
    each inner coordinate list explicitly. None passes through unchanged
    (an enemy with no custom behaviour)."""
    if config is None:
        return None
    out = dict(config)
    if isinstance(out.get('waypoints'), list):
        out['waypoints'] = [list(p) for p in out['waypoints']]
    return out


class ChangeBehaviourCommand(Command):
    """Apply a new behaviour_config to all selected enemies; undo restores each
    enemy's OWN prior config independently.

    Granularity (v1.4 Step 9, decision PART 0C): the snapshot is the ENTIRE
    behaviour_config dict, before and after — never a single field. A preset
    switch that also seeds or clears the waypoint list is therefore ONE undo
    step, and a single waypoint-coordinate edit is likewise one whole-dict
    snapshot. This trades fine-grained undo for a config dict that can never end
    up internally inconsistent (e.g. a "patrol_then_attack" tree with the
    waypoints half-reverted).

    Multi-enemy (decision PART 0A): like ChangeTextureCommand, this applies the
    SAME new_config to every entity but snapshots each entity's prior config
    individually (old_configs is per-entity), so undo restores each enemy to its
    own previous state, not all to one shared snapshot.

    Both old_configs and new_config are deep-copied at construction so the stored
    snapshots never alias the live dict the inspector mutates in place.

    After (re)applying, calls editor._refresh_behaviour_ui() so an undo/redo that
    changes behaviour rebuilds the inspector's preset buttons + waypoint rows —
    the same in-command refresh pattern PlaceEntityCommand/DeleteEntityCommand use
    with editor._refresh_hierarchy()."""

    def __init__(self, editor, entities, new_config):
        self.editor      = editor
        self.entities    = list(entities)
        self.old_configs = [
            _copy_behaviour_config(getattr(e, 'behaviour_config', None))
            for e in self.entities
        ]
        self.new_config  = _copy_behaviour_config(new_config)

    def __repr__(self):
        tree = (self.new_config or {}).get('tree', None)
        return f"ChangeBehaviourCommand(n={len(self.entities)}, tree={tree!r})"

    def _apply(self):
        for e in self.entities:
            e.behaviour_config = _copy_behaviour_config(self.new_config)
        if self.editor is not None:
            self.editor._refresh_behaviour_ui()

    def execute(self):
        # UndoRedoStack.redo() re-runs execute() (like every other command here),
        # so re-applying the new config on redo needs no separate redo() method.
        self._apply()

    def undo(self):
        for e, cfg in zip(self.entities, self.old_configs):
            e.behaviour_config = _copy_behaviour_config(cfg)
        if self.editor is not None:
            self.editor._refresh_behaviour_ui()


def _copy_actions(actions):
    """Deep copy a trigger action list (list of flat dicts) so a stored snapshot
    never aliases the live list the inspector mutates. None/empty → []."""
    return [dict(a) for a in (actions or [])]


class ChangeTriggerActionsCommand(Command):
    """Apply new on_enter/on_exit action lists to ONE trigger; undo restores the
    prior lists. v1.5 Step 6.

    Whole-list snapshot granularity (same rationale as ChangeBehaviourCommand's
    whole-dict snapshot): add/remove/change-action/edit-target is each ONE undo
    step over both lists, so the trigger's action state can never end up half-
    reverted. Both old and new are deep-copied at construction so the snapshots
    never alias the live lists. Refreshes the trigger inspector on apply/undo —
    the same in-command refresh pattern the other editor commands use."""

    def __init__(self, editor, trigger, new_on_enter, new_on_exit):
        self.editor      = editor
        self.trigger     = trigger
        self.old_enter   = _copy_actions(getattr(trigger, 'on_enter_actions', []))
        self.old_exit    = _copy_actions(getattr(trigger, 'on_exit_actions', []))
        self.new_enter   = _copy_actions(new_on_enter)
        self.new_exit    = _copy_actions(new_on_exit)

    def __repr__(self):
        return f"ChangeTriggerActionsCommand(enter={len(self.new_enter)}, exit={len(self.new_exit)})"

    def _apply(self):
        self.trigger.on_enter_actions = _copy_actions(self.new_enter)
        self.trigger.on_exit_actions  = _copy_actions(self.new_exit)
        if self.editor is not None:
            self.editor._refresh_trigger_ui()

    def execute(self):
        self._apply()

    def undo(self):
        self.trigger.on_enter_actions = _copy_actions(self.old_enter)
        self.trigger.on_exit_actions  = _copy_actions(self.old_exit)
        if self.editor is not None:
            self.editor._refresh_trigger_ui()


class ChangePickupConfigCommand(Command):
    """Apply a new pickup config (pickup_type/weapon_type/amount) to ONE pickup
    placeholder; undo restores the prior config. v1.5 Step 13.

    Whole-dict snapshot granularity (same rationale as ChangeTriggerActionsCommand):
    the three fields always change together as a single undo step. Refreshes the
    pickup inspector on apply/undo — same in-command refresh pattern as the other
    editor commands."""

    def __init__(self, editor, pickup, new_config):
        self.editor      = editor
        self.pickup      = pickup
        self.old_config  = dict(getattr(pickup, 'pickup_config', {}))
        self.new_config  = dict(new_config)

    def __repr__(self):
        return f"ChangePickupConfigCommand(new={self.new_config})"

    def _apply(self, config):
        self.pickup.pickup_config = dict(config)
        if self.editor is not None:
            self.editor._refresh_pickup_ui()

    def execute(self):
        self._apply(self.new_config)

    def undo(self):
        self._apply(self.old_config)


class UndoRedoStack:
    def __init__(self, max_depth=50):
        self._undo = deque(maxlen=max_depth)
        self._redo = deque(maxlen=max_depth)

    def push(self, command):
        self._undo.append(command)
        self._redo.clear()

    def undo(self):
        if not self._undo:
            return
        cmd = self._undo.pop()
        cmd.undo()
        self._redo.append(cmd)

    def redo(self):
        if not self._redo:
            return
        cmd = self._redo.pop()
        cmd.execute()
        self._undo.append(cmd)

    def clear(self):
        self._undo.clear()
        self._redo.clear()
