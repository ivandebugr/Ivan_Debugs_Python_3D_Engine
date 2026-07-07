"""
asset_resolve.py — resolve registry asset names/paths into Ursina objects.

The bridge between the framework-free asset_registry (names -> disk paths) and
Ursina's Texture/model loaders. Relocated out of undo_redo.py in the v1.6 split
(the command classes were carrying asset-loading knowledge that save/load,
spawn and the pickers all need too). asset_registry.py itself must stay free of
Ursina imports, so this thin layer is its own module; Ursina is imported lazily
inside each function.
"""

from pathlib import Path

from Scripts.asset_registry import asset_registry


def resolve_texture(name):
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


def resolve_model(name_or_path):
    """Resolve a model reference to something safe to assign to Entity.model.

    Mirrors resolve_texture for models. The bug it avoids is the same one the
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
