"""editor_ux_probe.py — drive the real level editor for the v1.7 UX audit.

Loads the CURATED level (levels/v1.json copied over level.json in this isolated
worktree) so texture round-trip fidelity can be tested against known-good data.
Screenshots every major UI state + tests five code-read hypotheses:
  H1 place-mode click on a large surface places at hovered.position + normal
     (entity CENTER, not the hit point)
  H2 move-mode still shows the white placement-preview cube on hover
  H3 camera-bookmark recall fires while typing digits into the sun (light)
     inspector fields (_light_typing missing from that one guard)
  H4 editor load->save round-trip: do project textures survive?
  H5 save_level dedup silently drops co-located same-type entities
"""

import json
import math
import os
import shutil
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

SHOTS_DIR = os.path.join(os.path.dirname(PROJECT_ROOT), 'shots_editor')
os.makedirs(SHOTS_DIR, exist_ok=True)

shutil.copyfile(os.path.join(PROJECT_ROOT, 'levels', 'v1.json'),
                os.path.join(PROJECT_ROOT, 'level.json'))

from Scripts import audio_workaround  # noqa: F401
from tests.smoke_test_harness import GameTestHarness

REPORT = {'findings': [], 'measurements': {}}
_shot_n = 0


def log(msg):
    print(f"[eprobe] {msg}", flush=True)


def finding(msg):
    REPORT['findings'].append(msg)
    print(f"[FINDING] {msg}", flush=True)


def measure(key, value):
    REPORT['measurements'][key] = value
    print(f"[measure] {key} = {value}", flush=True)


h = GameTestHarness()


def shot(name):
    global _shot_n
    from ursina import application
    from panda3d.core import Filename
    _shot_n += 1
    fname = f"{_shot_n:02d}_{name}.png"
    application.base.win.saveScreenshot(Filename.from_os_specific(os.path.join(SHOTS_DIR, fname)))
    log(f"shot -> {fname}")


log("launching editor ...")
h.launch_editor()
h.step(10)

from ursina import held_keys, mouse, camera, scene, Vec3, Entity, color, destroy
from ursina.prefabs.input_field import InputField

editor = next((e for e in scene.entities if type(e).__name__ == 'LevelEditor'), None)
assert editor is not None, "LevelEditor instance not found"

measure('counts', {'blocks': len(editor.blocks), 'enemies': len(editor.enemies),
                   'triggers': len(editor.triggers), 'pickups': len(editor.pickups),
                   'lights': len(editor.lights)})
shot('default_view')

# --- H4: texture round-trip ---------------------------------------------------
marker = next((b for b in editor.blocks
               if abs(b.x + 14) < 0.1 and abs(b.z - 18) < 0.1), None)
if marker is None:
    finding("H4: marker block (-14,0.75,18) not found after load")
else:
    tex = getattr(marker, 'texture', None)
    measure('H4_marker_texture_after_load', getattr(tex, 'name', str(tex)) if tex else None)
    data = editor._build_level_data()
    entry = next((d for d in data if d['type'] == 'block'
                  and abs(d['position'][0] + 14) < 0.1 and abs(d['position'][2] - 18) < 0.1), None)
    measure('H4_marker_texture_on_save', entry['texture'] if entry else 'entry missing')
    if entry is not None and entry['texture'] in ('', None):
        finding("H4 CONFIRMED: texture_orange_test.png loads as None/blank in the editor and re-saves "
                "as '' — an editor open+save cycle strips project textures (explains level.json's four "
                "blanked marker textures)")

from Scripts.asset_resolve import resolve_texture
res = resolve_texture('texture_orange_test.png')
measure('H4_resolve_texture_direct', getattr(res, 'name', str(res)) if res else None)

# --- selection states + screenshots ------------------------------------------
def select_and_shot(entity, name):
    editor._deselect_all()
    editor._select(entity)
    h.step(3)
    shot(name)

if editor.blocks:
    select_and_shot(editor.blocks[8] if len(editor.blocks) > 8 else editor.blocks[0], 'select_block')
if editor.enemies:
    patrol = next((e for e in editor.enemies
                   if (getattr(e, 'behaviour_config', None) or {}).get('tree') == 'patrol_then_attack'),
                  editor.enemies[0])
    select_and_shot(patrol, 'select_enemy_patrol')
if editor.triggers:
    trig = next((t for t in editor.triggers if getattr(t, 'on_enter_actions', [])), editor.triggers[0])
    select_and_shot(trig, 'select_trigger')
if editor.pickups:
    select_and_shot(editor.pickups[0], 'select_pickup')
if editor.lights:
    select_and_shot(editor.lights[0], 'select_sun')

editor._deselect_all()
if editor.blocks and editor.enemies:
    editor._select(editor.blocks[0])
    editor._select(editor.enemies[0], additive=True)
    if editor.triggers:
        editor._select(editor.triggers[0], additive=True)
    h.step(3)
    shot('multi_select_mixed')

editor._deselect_all()
if editor.blocks:
    editor._select(editor.blocks[0])
h.press('r')
h.step(3)
measure('gizmo_mode_after_r', editor._gizmo_mode)
shot('rotate_mode_rings')
h.press('t')
h.step(2)

editor.browser._set_browser_tab('model')
h.step(3)
shot('browser_models_tab')
editor.browser._set_browser_tab('texture')
h.step(2)

editor._deselect_all()
if editor.blocks:
    editor._select(editor.blocks[0])
    editor.browser.open_texture_picker()
    h.step(3)
    shot('texture_picker_overlay')
    editor.browser._close_asset_picker()
    h.step(2)

# --- H2: move-mode preview cube on hover -------------------------------------
# mouse.normal is a read-only property on Mouse — temporarily replace the CLASS
# property with a plain value for these two synthetic-hover tests, restore after.
MouseCls = type(mouse)
_orig_normal_prop = MouseCls.normal
MouseCls.normal = Vec3(0, 1, 0)

editor._deselect_all()
editor._set_tool('move')
tgt = editor.blocks[0] if editor.blocks else None
if tgt is not None:
    mouse.hovered_entity = tgt
    editor.update_model_preview()
    measure('H2_preview_visible_in_move_mode', bool(editor.model_preview.visible))
    if editor.model_preview.visible:
        finding("H2 CONFIRMED: the white placement-preview cube shows on hover even in Move mode "
                "(update_model_preview is not gated on _tool) — implies a click will place, but "
                "Move-mode click selects/deselects")
    shot('move_mode_preview_cube')

# --- H1: place on a large surface --------------------------------------------
editor._set_tool('place')
ground = next((e for e in scene.entities
               if getattr(e, 'scale_x', 0) == 100 and abs(getattr(e, 'y', 99) + 0.5) < 0.01), None)
measure('H1_ground_found', ground is not None)
if ground is not None:
    n_before = len(editor.blocks)
    mouse.hovered_entity = ground
    mouse.x, mouse.y = 0.0, 0.1
    editor.input('left mouse down')
    h.step(2)
    if len(editor.blocks) > n_before:
        placed = editor.blocks[-1]
        measure('H1_placed_at', tuple(round(v, 2) for v in placed.position))
        gp = ground.position
        if abs(placed.x - gp.x) < 0.6 and abs(placed.z - gp.z) < 0.6:
            finding(f"H1 CONFIRMED: Place-mode click on the 100x100 ground placed the block at "
                    f"{tuple(round(v,2) for v in placed.position)} = ground CENTER + normal, regardless "
                    f"of where the cursor pointed — placement uses hovered.position + mouse.normal, "
                    f"not mouse.world_point")
        editor._history.undo()
        h.step(2)
    else:
        measure('H1_placed', 'no block placed (guard swallowed the click)')
editor._set_tool('move')
MouseCls.normal = _orig_normal_prop   # restore the real property

# --- H3: bookmark recall while typing in the sun intensity field -------------
if editor.lights:
    editor._deselect_all()
    editor._select(editor.lights[0])
    h.step(2)
    cam = editor._editor_camera
    if cam is not None:
        editor._bookmarks['3'] = {'position': [5, 40, -60], 'rotation': [30, 0, 0]}
        light_fields = [w for w in editor.inspector._light_section_widgets()
                        if isinstance(w, InputField)]
        measure('H3_light_inputfields_found', len(light_fields))
        if light_fields:
            light_fields[0].active = True
            pos_before = tuple(round(v, 1) for v in cam.position)
            h.press('3')
            h.step(2)
            pos_after = tuple(round(v, 1) for v in cam.position)
            light_fields[0].active = False
            measure('H3_cam_before_after', (pos_before, pos_after))
            if pos_after == (5.0, 40.0, -60.0):
                finding("H3 CONFIRMED: typing '3' into a sun inspector field recalled camera bookmark 3 "
                        "(bookmark-recall typing guard omits _light_typing; the delete guard and "
                        "_any_field_typing both include it)")

# --- H5: save dedup drops co-located same-type entities ----------------------
b1 = Entity(model='cube', texture='white_cube', collider='box', position=(30, 5, 30))
b1._original_color = color.white
editor.blocks.append(b1)
b2 = Entity(model='cube', texture='white_cube', collider='box', position=(30, 5, 30))
b2._original_color = color.white
editor.blocks.append(b2)
data = editor._build_level_data()
stacked = [d for d in data if d['type'] == 'block' and d['position'][:3] == [30, 5, 30]]
seen, deduped = set(), []
for item in data:
    k = (item['type'], tuple(round(p, 3) for p in item['position']))
    if k not in seen:
        seen.add(k)
        deduped.append(item)
kept = sum(1 for d in deduped if d['type'] == 'block' and d['position'][:3] == [30, 5, 30])
measure('H5_colocated_in_data', len(stacked))
measure('H5_colocated_after_dedup', kept)
if len(stacked) == 2 and kept == 1:
    finding("H5 CONFIRMED: two identical-position blocks serialize as 2 entries but save_level's "
            "dedup writes only 1 — silent data loss for deliberately co-located entities")
for b in (b1, b2):
    editor.blocks.remove(b)
    destroy(b)

# --- collapsed panels shot ----------------------------------------------------
held_keys['control'] = 1
h.press('h'); h.press('i'); h.press('b')
held_keys['control'] = 0
h.step(3)
shot('all_panels_collapsed')
held_keys['control'] = 1
h.press('h'); h.press('i'); h.press('b')
held_keys['control'] = 0
h.step(3)

if editor.enemies:
    editor._deselect_all()
    editor._select(editor.enemies[0])
h.step(2)
shot('final_state')

REPORT['clean'] = not h.has_crashed()
if h.has_crashed():
    REPORT['tail'] = h.output_so_far().splitlines()[-40:]
    finding("crash patterns in editor probe output")
with open(os.path.join(SHOTS_DIR, 'editor_probe_report.json'), 'w') as f:
    json.dump(REPORT, f, indent=2)
log(f"done. {len(REPORT['findings'])} findings")
print("EDITOR_PROBE_COMPLETE", flush=True)
