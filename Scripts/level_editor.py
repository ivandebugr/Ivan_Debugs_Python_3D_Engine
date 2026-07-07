"""
level_editor.py — Standalone entry point for the level editor (v1.6 split).

The LevelEditor class lives in Scripts/editor_core.py; its collaborators in
Scripts/editor_hierarchy / editor_gizmo / editor_browser / editor_inspector /
editor_playmode. This file keeps only the `python Scripts/level_editor.py`
launch: shader patch before Ursina() (Hard Constraint 10), F5 hot-reloader
disable, window setup, ground plane, hint legend, and app.run().
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from ursina import *
from ursina.prefabs.editor_camera import EditorCamera
from panda3d.core import loadPrcFileData, AntialiasAttrib

from Scripts.editor_core import LevelEditor, PROJECT_ROOT
from Scripts.session_logger import get_editor_logger

logger = get_editor_logger()


def _launch():
    """Build the standalone editor app and return it; the caller runs it.

    Order matters and is preserved from the monolith: prc multisample config,
    then the GLSL 1.20 shader patch BEFORE Ursina() — Ursina's own window/UI
    entities compile shaders during __init__ (Hard Constraint 10) — then
    window setup, ground plane, LevelEditor + EditorCamera, hint legend.
    """
    loadPrcFileData('', 'framebuffer-multisample 1\nmultisamples 4')

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

    window.color = color.rgb(50/255, 50/255, 60/255)
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

    Entity(
        model='plane',
        collider='box',
        y=-0.5,
        scale=(100, 1, 100),
        texture=Texture(Path(str(PROJECT_ROOT / 'assets/textures/floor_ground_grass.png'))),
        texture_scale=(50, 50),
        eternal=True,
    )

    editor = LevelEditor()
    editor._editor_camera = EditorCamera()

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

    return app


if __name__ == '__main__':
    app = _launch()
    app.run()
