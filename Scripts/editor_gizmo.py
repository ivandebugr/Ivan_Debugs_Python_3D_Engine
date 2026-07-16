"""
editor_gizmo.py — Transform-gizmo collaborator for the level editor (v1.6 split).

Owns the X/Y/Z translate gizmo: handle geometry, per-frame drag translation,
and the mouse-cursor pick that begins a drag. Also owns the X/Y/Z rotation-ring
gizmo (v1.7 B2-ring): ring geometry, angle-drag rotation, and its own pick.
Selection, undo history and grid snap remain core-owned shared state, reached
through the editor back-reference (`self.editor.selected` / `_history` /
`_snap_1d`).

The rotation rings double as the sun light's orientation gizmo (v1.7 Step 4)
with no light-specific code here, by design: refresh() gates only on
`ed.selected`, and handle_rotate_drag() writes rotation_x/y/z — which IS the
sun's aim (editor_core serializes 'direction' from the proxy's live rotation via
level_io.rotation_to_direction). Nothing in this module should ever need to know
a light from a block; if it does, the abstraction has sprung a leak.

Core's input() keeps the dispatch order (v1.2.4 priority chain) and calls
try_begin_drag() as its Step 1; core's update() drives handle_drag()/refresh().
"""

import math

from ursina import *

from Scripts.session_logger import get_editor_logger
from Scripts.undo_redo import MoveEntityCommand, RotateEntityCommand

logger = get_editor_logger()


class GizmoController:
    # Below this screen-space axis length, the drag axis is nearly parallel to
    # the view direction — plane-projection becomes numerically unstable (tiny
    # cursor motion maps to huge world motion, standard gizmo edge case). Fall
    # back to the old velocity model under this threshold.
    _AXIS_SCREEN_LEN_MIN = 0.05

    # Ring gizmo radius (world units) — deliberately larger than the translate
    # shaft length so rings don't visually collide with the tip cubes.
    _RING_RADIUS = 1.6
    _RING_SEGMENTS = 48
    # Extra pick tolerance for rings: a thin line-loop is much easier to miss
    # than a cube tip, so the ring pick sweep checks distance-to-ring instead
    # of relying on a hit against the (visually invisible) mesh line itself.
    _RING_PICK_TOLERANCE = 0.12

    def __init__(self, editor):
        self.editor = editor
        self.root = None
        # Drag state (translate)
        self.drag_axis = None
        self.drag_start_mouse = None
        self.drag_start_pos = None
        # Plane-projection state (set on grab, used by handle_drag each frame).
        self.drag_plane_point = None   # world point on the drag plane (start position of grabbed axis origin)
        self.drag_plane_normal = None  # world-space plane normal
        self.drag_grab_offset = None   # axis-space offset between grabbed point and gizmo origin, per entity
        self._hovered_tip = None       # currently hover-highlighted tip Entity, or None
        # Drag state (rotate) — separate from translate drag state so the two
        # modes can never both be armed at once (try_begin_drag picks one).
        self.rotate_axis = None
        self.rotate_start_rot = None    # {entity: Vec3(rotation)} snapshot at grab
        self.rotate_start_angle = None  # signed angle (radians) of the initial grab point on the ring plane
        self._hovered_ring = None       # currently hover-highlighted ring Entity, or None
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

        self._ring_base_color = {}
        for axis, col, gname in [
            ('x', color.red,   'editor_gizmo_ring_x'),
            ('y', color.green, 'editor_gizmo_ring_y'),
            ('z', color.blue,  'editor_gizmo_ring_z'),
        ]:
            ring = Entity(
                parent=self.root,
                model=self._make_ring_mesh(axis),
                color=col,
                name=gname,
                eternal=True,
            )
            # Same render-on-top treatment as the translate tips (Hard Constraint 8):
            # call setBin/setDepthTest/setDepthWrite on the Entity, never on .node().
            ring.setDepthTest(False)
            ring.setDepthWrite(False)
            ring.setBin('fixed', 100)
            self._ring_base_color[gname] = col

        # Mode separation (v1.7 B-mode): show/enable only the active mode's
        # handles from the start. apply_mode() reads self.editor._gizmo_mode.
        self.apply_mode()

    def apply_mode(self):
        """Show + enable picking on the active mode's handles; hide + disable the
        other mode's entirely (v1.7 B-mode). Translate mode owns the shaft lines
        and tip cubes; rotate mode owns the rings. Disabling an Entity removes it
        from mouse.hovered_entity and from raycast hits (Ursina skips disabled
        entities), so the inactive set is inert to the cursor, not just invisible.
        Called from build() and whenever the mode toggles."""
        translate_on = (self.editor._gizmo_mode == 'translate')
        for e in self.root.children:
            name = e.name or ''
            if name.startswith('editor_gizmo_ring_'):
                e.enabled = not translate_on
            elif name.startswith('editor_gizmo'):
                # shaft lines (editor_gizmo_x/y/z) and tips (editor_gizmo_tip_*)
                e.enabled = translate_on
        # Clear any stale hover highlight left on the set we just hid, so a
        # white tip/ring doesn't persist after the switch.
        if translate_on:
            self._clear_ring_hover()
        else:
            self._clear_tip_hover()

    def _make_ring_mesh(self, axis):
        """Line-loop circle of radius _RING_RADIUS lying in the plane perpendicular
        to `axis` (Ursina has no torus primitive — build the loop by hand)."""
        verts = []
        for i in range(self._RING_SEGMENTS + 1):
            theta = 2 * math.pi * i / self._RING_SEGMENTS
            c, s = math.cos(theta) * self._RING_RADIUS, math.sin(theta) * self._RING_RADIUS
            if axis == 'x':
                verts.append(Vec3(0, c, s))
            elif axis == 'y':
                verts.append(Vec3(c, 0, s))
            else:
                verts.append(Vec3(c, s, 0))
        return Mesh(vertices=verts, mode='line', thickness=3)

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

        Ring pick is checked first (Step 1a): rings have no collider (a line-loop
        mesh is a poor raycast target — thin and easy to miss), so ring grabs are
        detected via closest-approach-to-circle against the cursor ray instead of
        Panda3D's mesh collision. Checking rings before tips matches how they're
        drawn at a larger radius outside the tip cubes, so there's no overlap
        ambiguity in practice.
        """
        if not (self.root and self.root.enabled and self.drag_axis is None
                and self.rotate_axis is None):
            return False
        # Active-drag lock (v1.7 B-lock): never begin a gizmo drag while any drag
        # (splitter/browser/another gizmo grab) already owns the cursor.
        if self.editor._drag_owner is not None:
            return False
        # Mode separation (v1.7 B-mode): only the active mode's handles are
        # pickable. In rotate mode the rings are checked and the tips are inert;
        # in translate mode the reverse. build() also hides the inactive set so
        # the cursor never even sees a handle it can't grab.
        if self.editor._gizmo_mode == 'rotate':
            ring_axis = self._pick_ring()
            if ring_axis is not None:
                self._begin_rotate(ring_axis)
                self.editor._acquire_drag('gizmo')
                return True
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
            self.editor._acquire_drag('gizmo')
            return True
        return False

    def _pick_ring(self):
        """Return the axis ('x'/'y'/'z') of the ring nearest the cursor ray, within
        pick tolerance, or None. Distance is measured from the ray's closest approach
        to the ring's plane against the ring's own circle (not the infinite plane),
        so clicking near the gizmo centre (inside the ring) does not register a hit."""
        best_axis, best_dist = None, self._RING_PICK_TOLERANCE
        origin, direction = self.cursor_ray()
        gizmo_origin = Vec3(self.root.position)
        for axis in ('x', 'y', 'z'):
            normal = self._WORLD_AXES[axis]
            hit_point = self._ray_plane_hit(origin, direction, gizmo_origin, normal)
            if hit_point is None:
                continue
            radial = hit_point - gizmo_origin
            radial_len = radial.length()
            if radial_len < 1e-6:
                continue
            closest_on_ring = gizmo_origin + radial * (self._RING_RADIUS / radial_len)
            dist = (hit_point - closest_on_ring).length()
            if dist < best_dist:
                best_axis, best_dist = axis, dist
        return best_axis

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
    # rotation_x/y/z sign convention, verified empirically against Ursina/Panda3D
    # (positive rotation_x turns +Y toward +Z; positive rotation_y turns +Z toward
    # +X; positive rotation_z turns +X toward -Y — NOT +Y, unlike the other two
    # axes) so ring drag direction matches typing a positive number into Rot X/Y/Z.
    _RING_BASIS = {
        'x': (Vec3(0, 1, 0), Vec3(0, 0, 1)),
        'y': (Vec3(0, 0, 1), Vec3(1, 0, 0)),
        'z': (Vec3(1, 0, 0), Vec3(0, -1, 0)),
    }

    def _ring_angle(self, axis, world_point):
        """Signed angle (radians) of `world_point` projected onto the ring plane
        for `axis`, measured in the (u, v) basis from _RING_BASIS."""
        u, v = self._RING_BASIS[axis]
        radial = world_point - Vec3(self.root.position)
        return math.atan2(radial.dot(v), radial.dot(u))

    def _begin_rotate(self, axis):
        """Arm rotate state for `axis`: snapshot entity rotations and the initial
        grab angle on the ring plane (through the gizmo centre, normal = axis)."""
        self.rotate_axis = axis
        self.rotate_start_rot = {e: Vec3(e.rotation) for e in self.editor.selected}
        gizmo_origin = Vec3(self.root.position)
        normal = self._WORLD_AXES[axis]
        hit_point = self._ray_plane_hit(*self.cursor_ray(), gizmo_origin, normal)
        self.rotate_start_angle = self._ring_angle(axis, hit_point) if hit_point is not None else 0.0

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
        previous tip's colour when hover moves elsewhere. No-op while any drag
        owns the cursor (v1.7 B-lock) or when translate isn't the active mode
        (v1.7 B-mode) — in rotate mode the tips are hidden and inert."""
        if self.editor._drag_owner is not None:
            return
        if self.editor._gizmo_mode != 'translate':
            self._clear_tip_hover()
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

    def _clear_tip_hover(self):
        """Restore any hover-highlighted tip to its base colour and drop the ref.
        Called when leaving translate mode so a mid-hover white tip doesn't stick."""
        if self._hovered_tip is not None:
            self._hovered_tip.color = self._tip_base_color[self._hovered_tip.name]
            self._hovered_tip = None

    def _clear_ring_hover(self):
        """Restore any hover-highlighted ring to its base colour and drop the ref.
        Called when leaving rotate mode so a mid-hover white ring doesn't stick."""
        if self._hovered_ring is not None:
            for e in self.root.children:
                if e.name == self._hovered_ring:
                    e.color = self._ring_base_color[self._hovered_ring]
                    break
            self._hovered_ring = None

    def _update_ring_hover_highlight(self):
        """Brighten the hovered ring before a rotate drag is committed to. Rings have
        no collider (see _pick_ring's docstring), so hover is detected via the same
        closest-approach-to-circle test used for the pick itself, not mouse.hovered_entity.
        No-op while any drag owns the cursor (v1.7 B-lock) or when rotate isn't the
        active mode (v1.7 B-mode) — in translate mode the rings are hidden and inert."""
        if self.editor._drag_owner is not None:
            return
        if self.editor._gizmo_mode != 'rotate':
            self._clear_ring_hover()
            return
        ring_axis = self._pick_ring()
        hovered_name = f'editor_gizmo_ring_{ring_axis}' if ring_axis is not None else None
        if hovered_name == self._hovered_ring:
            return
        if self._hovered_ring is not None:
            for e in self.root.children:
                if e.name == self._hovered_ring:
                    e.color = self._ring_base_color[self._hovered_ring]
                    break
        if hovered_name is not None:
            for e in self.root.children:
                if e.name == hovered_name:
                    e.color = color.white
                    break
        self._hovered_ring = hovered_name

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
        if not ed._play_mode:
            self._update_hover_highlight()
        # Only the mechanism that owns the active-drag lock runs its per-frame
        # drag body. drag_axis is armed exactly once, in try_begin_drag()/input()
        # — the old "re-arm from mouse.hovered_entity if drag_axis is None" branch
        # here is deleted (v1.7 B-lock): it re-evaluated hover mid-interaction,
        # the exact bug the lock removes.
        if self.drag_axis is None:
            return
        if held_keys['left mouse']:
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
            # Mouse released while a drag was armed — finalize: push undo, reset
            # drag state, and release the active-drag lock (v1.7 B-lock).
            for e in ed.selected:
                old_pos = self.drag_start_pos[e]
                new_pos = Vec3(e.position)
                if old_pos != new_pos:
                    etype = ed._entity_type_name(e)
                    logger.log('INFO', f"Entity moved: type={etype} {[round(p,3) for p in old_pos]} -> {[round(p,3) for p in new_pos]}")
                    ed._history.push(MoveEntityCommand(e, old_pos, new_pos))
            self.drag_axis = None
            self.drag_start_mouse = None
            self.drag_start_pos = None
            self.drag_plane_point = None
            self.drag_plane_normal = None
            self.drag_grab_offset = None
            ed._release_drag()

    def handle_rotate_drag(self):
        """Rotate selected entities about the grabbed ring axis; push RotateEntityCommand
        on release. Angle-based sibling of handle_drag(): instead of projecting the
        cursor onto an axis line, project onto the ring plane and measure the signed
        angle swept since grab, then apply that delta to each entity's start rotation."""
        ed = self.editor
        if not ed._play_mode:
            self._update_ring_hover_highlight()
        # rotate_axis is armed exactly once, in try_begin_drag()/input() — the old
        # "re-pick the ring if rotate_axis is None" branch here is deleted (v1.7
        # B-lock), same reasoning as handle_drag: it re-evaluated the pick
        # mid-interaction. Only the lock-owning mechanism runs its per-frame body.
        if self.rotate_axis is None:
            return
        if held_keys['left mouse']:
            gizmo_origin = Vec3(self.root.position)
            normal = self._WORLD_AXES[self.rotate_axis]
            hit_point = self._ray_plane_hit(*self.cursor_ray(), gizmo_origin, normal)
            if hit_point is not None:
                current_angle = self._ring_angle(self.rotate_axis, hit_point)
                delta_deg = math.degrees(current_angle - self.rotate_start_angle)
                attr = f'rotation_{self.rotate_axis}'
                for e in ed.selected:
                    start = self.rotate_start_rot[e]
                    setattr(e, attr, getattr(start, self.rotate_axis) + delta_deg)
                self.refresh()
        else:
            # Mouse released — finalize rotate: push undo, reset state, release lock.
            for e in ed.selected:
                old_rot = self.rotate_start_rot[e]
                new_rot = Vec3(e.rotation)
                if old_rot != new_rot:
                    etype = ed._entity_type_name(e)
                    logger.log('INFO', f"Entity rotated: type={etype} {[round(r,3) for r in old_rot]} -> {[round(r,3) for r in new_rot]}")
                    ed._history.push(RotateEntityCommand(e, old_rot, new_rot))
            ed._update_inspector()
            self.rotate_axis = None
            self.rotate_start_rot = None
            self.rotate_start_angle = None
            ed._release_drag()
