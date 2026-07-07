"""
editor_gizmo.py — Transform-gizmo collaborator for the level editor (v1.6 split).

Owns the X/Y/Z translate gizmo: handle geometry, per-frame drag translation,
and the mouse-cursor pick that begins a drag. Selection, undo history and grid
snap remain core-owned shared state, reached through the editor back-reference
(`self.editor.selected` / `_history` / `_snap_1d`).

Core's input() keeps the dispatch order (v1.2.4 priority chain) and calls
try_begin_drag() as its Step 1; core's update() drives handle_drag()/refresh().
"""

from ursina import *

from Scripts.session_logger import get_editor_logger
from Scripts.undo_redo import MoveEntityCommand

logger = get_editor_logger()


class GizmoController:
    def __init__(self, editor):
        self.editor = editor
        self.root = None
        # Drag state
        self.drag_axis = None
        self.drag_start_mouse = None
        self.drag_start_pos = None
        self.build()

    def build(self):
        self.root = Entity(name='editor_gizmo_root', enabled=False, eternal=True)
        shaft_len = 1.2
        Entity(
            parent=self.root,
            model=Mesh(vertices=[Vec3(0, 0, 0), Vec3(shaft_len, 0, 0)], mode='line', thickness=3),
            color=color.red,
            name='editor_gizmo_x',
            eternal=True,
        )
        Entity(
            parent=self.root,
            model=Mesh(vertices=[Vec3(0, 0, 0), Vec3(0, shaft_len, 0)], mode='line', thickness=3),
            color=color.green,
            name='editor_gizmo_y',
            eternal=True,
        )
        Entity(
            parent=self.root,
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
                parent=self.root,
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

    def cursor_ray(self):
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

    def try_begin_drag(self):
        """Step 1 of core input()'s left-mouse-down priority chain: raycast against
        the tip cubes BEFORE any panel check. setDepthTest(False)/setBin(100) make
        handles visible through blocks, but the pick ray still hits geometry behind
        them; this explicit raycast lets the handle win. Returns True (and arms the
        drag) when a tip was grabbed, False to let input() fall through to panels.
        """
        if not (self.root and self.root.enabled and self.drag_axis is None):
            return False
        axis_map = {
            'editor_gizmo_tip_x': 'x',
            'editor_gizmo_tip_y': 'y',
            'editor_gizmo_tip_z': 'z',
        }
        # FIXED (v1.2.4, FIX 1B): pick through the MOUSE CURSOR, not camera.forward.
        # camera.forward rays through the screen centre, but the editor cursor is
        # free, so the old ray never tested where the user actually clicked — the
        # gizmo could not be grabbed. Ignoring every non-tip entity lets the handle
        # win even when a block overlaps it on screen (verified against a block-in-front).
        origin, direction = self.cursor_ray()
        # e.is_empty() guard: scene.entities can still hold entities destroy()ed
        # earlier this same frame (flush is end-of-frame); reading .name on an
        # emptied NodePath fires the C++ getName() assertion that except cannot
        # catch (CLAUDE.md HC13). Empty NodePaths go straight to the ignore list.
        hit = raycast(origin,
                      direction,
                      distance=200,
                      ignore=[e for e in scene.entities
                              if e.is_empty() or not e.name.startswith('editor_gizmo_tip')])
        if hit.hit and hit.entity and hit.entity.name in axis_map:
            self.drag_axis = axis_map[hit.entity.name]
            self.drag_start_mouse = Vec2(mouse.x, mouse.y)
            self.drag_start_pos = {e: Vec3(e.position) for e in self.editor.selected}
            return True
        return False

    def refresh(self):
        """Position gizmo at centroid of selection; hide when nothing selected or in play mode."""
        ed = self.editor
        if not ed.selected or ed._play_mode:
            self.root.enabled = False
            return
        self.root.enabled = True
        positions = [e.position for e in ed.selected]
        centroid = Vec3(0, 0, 0)
        for p in positions:
            centroid += p
        centroid /= len(positions)
        self.root.position = centroid

    def handle_drag(self):
        """Translate selected entities along the grabbed world axis; push MoveEntityCommand on release."""
        ed = self.editor
        axis_map = {
            'editor_gizmo_x': 'x', 'editor_gizmo_tip_x': 'x',
            'editor_gizmo_y': 'y', 'editor_gizmo_tip_y': 'y',
            'editor_gizmo_z': 'z', 'editor_gizmo_tip_z': 'z',
        }
        _world_axes = {'x': Vec3(1, 0, 0), 'y': Vec3(0, 1, 0), 'z': Vec3(0, 0, 1)}
        if held_keys['left mouse']:
            if self.drag_axis is None:
                if mouse.hovered_entity and mouse.hovered_entity.name in axis_map:
                    self.drag_axis = axis_map[mouse.hovered_entity.name]
                    self.drag_start_mouse = Vec2(mouse.x, mouse.y)
                    self.drag_start_pos = {e: Vec3(e.position) for e in ed.selected}
            else:
                # Project the world axis into screen space to determine drag sign.
                # camera.getRelativePoint gives the axis endpoint in camera-local space;
                # the 2D difference is the screen-space projection of that axis.
                axis_world = _world_axes[self.drag_axis]
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

                for e in ed.selected:
                    raw = getattr(e, self.drag_axis) + magnitude
                    setattr(e, self.drag_axis, ed._snap_1d(raw))
                self.refresh()
        else:
            if self.drag_axis is not None:
                for e in ed.selected:
                    old_pos = self.drag_start_pos[e]
                    new_pos = Vec3(e.position)
                    if old_pos != new_pos:
                        etype = ('enemy' if e in ed.enemies else
                                 'trigger' if e in ed.triggers else
                                 'pickup' if e in ed.pickups else 'block')
                        logger.log('INFO', f"Entity moved: type={etype} {[round(p,3) for p in old_pos]} -> {[round(p,3) for p in new_pos]}")
                        ed._history.push(MoveEntityCommand(e, old_pos, new_pos))
            self.drag_axis = None
            self.drag_start_mouse = None
            self.drag_start_pos = None
