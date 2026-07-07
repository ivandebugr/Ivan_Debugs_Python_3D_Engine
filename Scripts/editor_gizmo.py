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
    # Below this screen-space axis length, the drag axis is nearly parallel to
    # the view direction — plane-projection becomes numerically unstable (tiny
    # cursor motion maps to huge world motion, standard gizmo edge case). Fall
    # back to the old velocity model under this threshold.
    _AXIS_SCREEN_LEN_MIN = 0.05

    def __init__(self, editor):
        self.editor = editor
        self.root = None
        # Drag state
        self.drag_axis = None
        self.drag_start_mouse = None
        self.drag_start_pos = None
        # Plane-projection state (set on grab, used by handle_drag each frame).
        self.drag_plane_point = None   # world point on the drag plane (start position of grabbed axis origin)
        self.drag_plane_normal = None  # world-space plane normal
        self.drag_grab_offset = None   # axis-space offset between grabbed point and gizmo origin, per entity
        self._hovered_tip = None       # currently hover-highlighted tip Entity, or None
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
        self._tip_base_color = {}
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
            self._tip_base_color[gname] = col

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
            self._begin_drag(axis_map[hit.entity.name])
            return True
        return False

    def _begin_drag(self, axis):
        """Arm drag state for `axis`: snapshot entity positions and set up the
        plane-projection frame (plane point/normal + per-entity grab offsets)."""
        self.drag_axis = axis
        self.drag_start_mouse = Vec2(mouse.x, mouse.y)
        self.drag_start_pos = {e: Vec3(e.position) for e in self.editor.selected}
        axis_world = self._WORLD_AXES[axis]
        gizmo_origin = Vec3(self.root.position)
        self.drag_plane_normal = self._drag_plane_normal(axis_world)
        self.drag_plane_point = gizmo_origin
        hit_point = self._ray_plane_hit(*self.cursor_ray(), gizmo_origin, self.drag_plane_normal)
        if hit_point is not None:
            grabbed = self._closest_point_on_line(hit_point, gizmo_origin, axis_world)
            self.drag_grab_offset = (grabbed - gizmo_origin).dot(axis_world)
        else:
            self.drag_grab_offset = 0.0

    _WORLD_AXES = {'x': Vec3(1, 0, 0), 'y': Vec3(0, 1, 0), 'z': Vec3(0, 0, 1)}

    def _drag_plane_normal(self, axis_world):
        """Plane containing `axis_world`, oriented to face the camera as much as
        possible (standard gizmo trick: normal = axis x (axis x view_dir))."""
        view_dir = (camera.world_position - self.root.world_position)
        if view_dir.length() < 0.0001:
            view_dir = Vec3(0, 0, -1)
        view_dir = view_dir.normalized()
        normal = axis_world.cross(axis_world.cross(view_dir))
        if normal.length() < 0.0001:
            # axis parallel to view — arbitrary perpendicular fallback
            normal = axis_world.cross(Vec3(0, 1, 0))
            if normal.length() < 0.0001:
                normal = axis_world.cross(Vec3(1, 0, 0))
        return normal.normalized()

    @staticmethod
    def _ray_plane_hit(ray_origin, ray_dir, plane_point, plane_normal):
        """World-space point where the ray crosses the plane, or None if parallel."""
        denom = ray_dir.dot(plane_normal)
        if abs(denom) < 1e-6:
            return None
        t = (plane_point - ray_origin).dot(plane_normal) / denom
        if t < 0:
            return None
        return ray_origin + ray_dir * t

    @staticmethod
    def _closest_point_on_line(point, line_point, line_dir):
        """Closest point on the infinite line (line_point, line_dir) to `point`."""
        t = (point - line_point).dot(line_dir)
        return line_point + line_dir * t

    def _update_hover_highlight(self):
        """Brighten the hovered tip before a drag is committed to; restore the
        previous tip's colour when hover moves elsewhere. No-op while dragging."""
        if self.drag_axis is not None:
            return
        tip_names = self._tip_base_color.keys()
        hovered = mouse.hovered_entity
        hovered_tip = hovered if (hovered and hovered.name in tip_names) else None
        if hovered_tip is self._hovered_tip:
            return
        if self._hovered_tip is not None:
            self._hovered_tip.color = self._tip_base_color[self._hovered_tip.name]
        if hovered_tip is not None:
            hovered_tip.color = color.white
        self._hovered_tip = hovered_tip

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
        """Translate selected entities along the grabbed world axis; push MoveEntityCommand on release.

        Plane-projection: the grabbed point on the axis stays pinned under the
        cursor regardless of drag speed (fixes the old velocity model, which
        moved the entity based on mouse speed rather than mouse position).
        """
        ed = self.editor
        axis_map = {
            'editor_gizmo_x': 'x', 'editor_gizmo_tip_x': 'x',
            'editor_gizmo_y': 'y', 'editor_gizmo_tip_y': 'y',
            'editor_gizmo_z': 'z', 'editor_gizmo_tip_z': 'z',
        }
        if not ed._play_mode:
            self._update_hover_highlight()
        if held_keys['left mouse']:
            if self.drag_axis is None:
                if mouse.hovered_entity and mouse.hovered_entity.name in axis_map:
                    self._begin_drag(axis_map[mouse.hovered_entity.name])
            else:
                axis_world = self._WORLD_AXES[self.drag_axis]
                # Screen-space axis length gates the near-parallel fallback below
                # (also reused as the drag sign reference for the velocity model).
                cam_origin = camera.getRelativePoint(render, Vec3(0, 0, 0))
                cam_tip    = camera.getRelativePoint(render, axis_world)
                axis_screen = Vec2(cam_tip.x - cam_origin.x, cam_tip.y - cam_origin.y)
                axis_screen_len = axis_screen.length()

                if axis_screen_len > self._AXIS_SCREEN_LEN_MIN:
                    # Plane-projection: intersect the cursor ray with the drag
                    # plane (fixed at grab time), project the hit onto the axis
                    # line, and move by the delta from the grabbed offset.
                    gizmo_origin = Vec3(self.drag_plane_point)
                    hit_point = self._ray_plane_hit(
                        *self.cursor_ray(), self.drag_plane_point, self.drag_plane_normal)
                    if hit_point is not None:
                        grabbed = self._closest_point_on_line(hit_point, gizmo_origin, axis_world)
                        offset = (grabbed - gizmo_origin).dot(axis_world)
                        delta = offset - self.drag_grab_offset
                        for e in ed.selected:
                            start = self.drag_start_pos[e]
                            raw = getattr(start, self.drag_axis) + delta
                            setattr(e, self.drag_axis, ed._snap_1d(raw))
                        self.refresh()
                else:
                    # Near-parallel-to-view fallback: old velocity model, since
                    # plane-projection is numerically unstable at this angle.
                    axis_screen_dir = (axis_screen / axis_screen_len) if axis_screen_len > 0.0001 else Vec2(1, 0)
                    mouse_vel = Vec2(mouse.velocity[0], mouse.velocity[1])
                    magnitude = mouse_vel.dot(axis_screen_dir) * 200.0
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
            self.drag_plane_point = None
            self.drag_plane_normal = None
            self.drag_grab_offset = None
