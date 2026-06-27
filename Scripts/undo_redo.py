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
        return f"PlaceEntityCommand(pos={list(self._snapshot['position'])}, enemy={self._snapshot['is_enemy']})"

    def execute(self):
        self.entity = _restore_entity(self.editor, self._snapshot)
        self.editor._refresh_hierarchy()

    def undo(self):
        if self.entity in self.editor.blocks:
            self.editor.blocks.remove(self.entity)
        elif self.entity in self.editor.enemies:
            self.editor.enemies.remove(self.entity)
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
