"""playtest_probe.py — full automated playthrough of the shipped level (v1.7).

Extends tests/smoke_test_harness.GameTestHarness (real input dispatch, real frame
loop) into one CONTINUOUS play session across the whole curated level: movement
feel measurements, all four enemy presets, weapons/pickups/door/checkpoint/kill
plane/win, pause UI, and screenshot capture in motion so the v1.7 visual stack
(lit shader L1 + PCF shadows L3 + bloom B2 + blob shadows) is evaluated
composited, not in isolated probes.

Written for the 2026-07-17 automated playtest (see the vault:
work/active/v1.7-playtest-findings). Findings are SOFT — a failed expectation
logs [FINDING] and the run continues. Output: numbered screenshots +
playtest_report.json in SHOTS_DIR.

NOTE: needs a tree where the game boots — the 2026-07-17 uncommitted B3
glow-map WIP crashes at first render (glow_amount shader-input assertion), so
run this at HEAD (e.g. a worktree) or after that fix lands.

KNOWN HARNESS LIMITS (hit during the 2026-07-17 run — see the report):
- an idle player gets hunted and killed by chasing enemies during long
  "observe" phases; clear roamers first if a later phase needs the player alive
  (tools/playtest_probe3.py does this for the tower route)
- perf numbers here are vsync-bound (sync-video not disabled) — they prove
  "no dropped frames at 60Hz", not headroom (see brain/Gotchas sync-video entry)

Run from the repo root:  python3 tools/playtest_probe.py
"""

import json
import math
import os
import sys
import time as _wall

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

SHOTS_DIR = os.environ.get(
    'PLAYTEST_SHOTS', os.path.join(PROJECT_ROOT, 'tools', 'playtest_shots'))
os.makedirs(SHOTS_DIR, exist_ok=True)

from Scripts import audio_workaround  # noqa: F401  — before any ursina import
from tests.smoke_test_harness import GameTestHarness

REPORT = {'findings': [], 'measurements': {}, 'shots': []}
_shot_n = 0


def log(msg):
    print(f"[probe] {msg}", flush=True)


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
    path = os.path.join(SHOTS_DIR, fname)
    application.base.win.saveScreenshot(Filename.from_os_specific(path))
    REPORT['shots'].append(fname)
    log(f"shot -> {fname}")


def crash_check(tag):
    if h.has_crashed():
        tail = "\n".join(h.output_so_far().splitlines()[-15:])
        finding(f"CRASH after {tag}: {tail}")
        return True
    return False


# ---------------------------------------------------------------- boot
log("launching main.py ...")
h.launch_main()
h.step(3)
shot('intro_screen')
h.press('space')
h.step(2)

from ursina import held_keys, camera, scene, application, time as utime, Vec3, mouse
from Scripts.game import game, Game
from Scripts.collision_system import collision_manager, Layers

h.step(30)
shot('main_menu')
h.step(45)
shot('main_menu_orbit2')
measure('menu_entity_count', len(scene.entities))
crash_check('boot')

h.step(5)
measure('dt_after_step', round(utime.dt, 4))

# ---------------------------------------------------------------- helpers
def player():
    return game.player


def aim_at(target, eye_h=1.5):
    p = player()
    t = Vec3(*target)
    d = t - (p.position + Vec3(0, eye_h, 0))
    p.rotation_y = math.degrees(math.atan2(d.x, d.z))
    horiz = math.sqrt(d.x * d.x + d.z * d.z)
    camera.rotation_x = -math.degrees(math.atan2(d.y, max(horiz, 1e-6)))


def walk_to(target, timeout_s=15.0, arrive=1.2, label=''):
    t = Vec3(*target)
    frames = int(timeout_s * 60)
    for i in range(frames):
        p = player()
        if p is None:
            finding(f"walk_to({label}): player vanished")
            return False
        d = t - p.position
        d = Vec3(d.x, 0, d.z)
        if d.length() < arrive:
            held_keys['w'] = 0
            return True
        p.rotation_y = math.degrees(math.atan2(d.x, d.z))
        held_keys['w'] = 1
        h.step(1)
    held_keys['w'] = 0
    finding(f"walk_to({label}): timeout {timeout_s}s, at "
            f"{tuple(round(v,1) for v in player().position)} target {tuple(t)}")
    return False


def fire_once():
    h.press('left mouse down')
    h.press('left mouse up')


def live_enemy_bullets():
    return {id(b) for b in collision_manager.query_layer(Layers.ENEMY_BULLET)}


def count_enemy_shots(seconds):
    seen = live_enemy_bullets()
    shots_n = 0
    for _ in range(int(seconds * 60)):
        h.step(1)
        now = live_enemy_bullets()
        shots_n += len(now - seen)
        seen = now
    return shots_n


def nearest_enemy():
    best, bd = None, 1e9
    for e in game.enemies:
        if getattr(e, 'alive', False):
            d = (e.position - player().position).length()
            if d < bd:
                best, bd = e, d
    return best


# ---------------------------------------------------------------- start game
log("starting real game ...")
h.start_real_game()
h.step(5)
shot('spawn_view')
measure('spawn_pos', tuple(round(v, 2) for v in player().position))
measure('enemies_alive', sum(1 for e in game.enemies if e.alive))
measure('hud_ammo_text', game.hud.ammo_text.text if game.hud and game.hud.ammo_text else None)
measure('play_entity_count', len(scene.entities))
crash_check('start_game')

# ---------------------------------------------------------------- movement
log("phase: movement")
player().rotation_y = 180
camera.rotation_x = 0
p0 = Vec3(player().position); t0 = _wall.perf_counter()
held_keys['w'] = 1
h.step(60)
held_keys['w'] = 0
elapsed = _wall.perf_counter() - t0
measure('ground_speed_ups', round((Vec3(player().position) - p0).length() / max(elapsed, 1e-6), 2))

player().rotation_y = 0
ys = []
held_keys['w'] = 1
for i in range(90):
    h.step(1)
    ys.append(camera.y)
    if i == 30:
        shot('walk_bob_frame_a')
    if i == 37:
        shot('walk_bob_frame_b')
held_keys['w'] = 0
measure('headbob_camera_y_min', round(min(ys), 3))
measure('headbob_camera_y_max', round(max(ys), 3))

p_y = player().y
h.press('space')
apex, apex_t, landed_t = p_y, 0, None
t0 = _wall.perf_counter()
for i in range(120):
    h.step(1)
    if player().y > apex:
        apex, apex_t = player().y, _wall.perf_counter() - t0
    if i > 10 and player().grounded:
        landed_t = _wall.perf_counter() - t0
        break
measure('jump_apex_height', round(apex - p_y, 2))
measure('jump_apex_time_s', round(apex_t, 2))
measure('jump_total_air_s', round(landed_t, 2) if landed_t else 'never landed')
crash_check('movement')

# ---------------------------------------------------------------- cautious enemy combat
log("phase: cautious enemy")
cautious = None
for e in game.enemies:
    if e.alive and abs(e.x - 3) < 1 and abs(e.z - 16) < 1:
        cautious = e
        break
if cautious is None:
    finding("cautious enemy not found where level.json places it (3,0.5,16)")
else:
    walk_to((3, 0, 10.5), timeout_s=10, label='approach cautious')
    aim_at(cautious.position + Vec3(0, 1.5, 0))
    h.step(2)
    shot('cautious_before_fight')
    incoming = count_enemy_shots(7.0)
    measure('cautious_shots_in_7s', incoming)
    measure('player_hp_after_cautious_stand', player().health)
    h.press('escape')
    h.step(2)
    shot('pause_menu_midfight')
    measure('pause_engaged', game.state == Game.PAUSED and application.time_scale == 0)
    h.press('escape')
    h.real_step(14)
    measure('resume_state', game.state)
    kills = 0
    for i in range(8):
        if not cautious.alive:
            break
        aim_at(cautious.position + Vec3(0, 1.5, 0))
        fire_once()
        if i == 0:
            h.step(1)
            shot('pistol_muzzle_flash')
        h.step(14)
        kills = i + 1
    measure('pistol_shots_to_kill_cautious',
            kills if not cautious.alive else f'still alive hp={cautious.health}')
    h.step(5)
    shot('after_cautious_kill')
crash_check('cautious')

# ---------------------------------------------------------------- armory
log("phase: armory (door, flee_when_low, shotgun)")
flee = None
for e in game.enemies:
    if e.alive and abs(e.x + 12) < 1.5 and abs(e.z - 21.5) < 1.5:
        flee = e
        break

door = next((e for e in scene.entities
             if getattr(e, 'door_name', '') == 'armory_door'), None)
if door is None:
    finding("armory_door block not found")
else:
    door_y0 = door.y
    walk_to((-5, 0, 16), timeout_s=15, label='to plate approach')
    aim_at((-7.75, 2, 20))
    shot('door_closed')
    walk_to((-5, 0, 20), timeout_s=8, arrive=0.8, label='onto plate')
    h.step(20)
    shot('door_opening')
    h.step(30)
    measure('door_dy_after_open', round(door.y - door_y0, 2))
    shot('door_open')
    if door.y - door_y0 < 2.5:
        finding(f"armory door did not slide fully (dy={door.y - door_y0:.2f}, expected ~3)")

if flee is not None and flee.alive:
    walk_to((-6.5, 0, 20), timeout_s=6, arrive=0.8, label='through door')
    walk_to((-9.5, 0, 20), timeout_s=6, arrive=0.8, label='inside armory')
    for i in range(3):
        if not flee.alive:
            break
        aim_at(flee.position + Vec3(0, 1.5, 0))
        fire_once()
        h.step(14)
    d0 = (flee.position - player().position).length() if flee.alive else None
    h.step(90)
    if flee.alive:
        d1 = (flee.position - player().position).length()
        measure('flee_distance_delta', round(d1 - d0, 2))
        shot('flee_running_away')
        if d1 - d0 < 1.0:
            finding(f"flee_when_low at hp={flee.health} did not open distance")
        for i in range(6):
            if not flee.alive:
                break
            aim_at(flee.position + Vec3(0, 1.5, 0))
            fire_once()
            h.step(14)
    measure('flee_killed', not (flee and flee.alive))
else:
    finding("flee_when_low enemy not found/alive in armory")

inv = player().inventory
walk_to((-14, 0, 18), timeout_s=8, arrive=0.7, label='shotgun pickup')
h.step(5)
slot1 = inv.slots[1] if len(inv.slots) > 1 else None
measure('shotgun_granted', type(slot1).__name__ if slot1 else None)
h.press('2')
h.step(20)
shot('shotgun_viewmodel')
measure('hud_ammo_shotgun', game.hud.ammo_text.text if game.hud and game.hud.ammo_text else None)
before = {id(b) for b in collision_manager.query_layer(Layers.PLAYER_BULLET)}
aim_at((-12, 2.5, 24))
fire_once()
h.step(2)
pellets = len({id(b) for b in collision_manager.query_layer(Layers.PLAYER_BULLET)} - before)
measure('shotgun_pellets_in_flight_after_2f', pellets)
shot('shotgun_pellets_in_flight')
h.step(30)
walk_to((-10, 0, 18), timeout_s=8, arrive=0.7, label='shotgun ammo pickup')
h.step(5)
measure('hud_ammo_after_pickup', game.hud.ammo_text.text if game.hud and game.hud.ammo_text else None)
crash_check('armory')

# ---------------------------------------------------------------- patrol yard + rifle
log("phase: patrol yard (NE corner)")
patrols = [e for e in game.enemies if e.alive and e.z > 40]
measure('patrollers_alive_in_yard', len(patrols))
if patrols:
    traces = {id(e): [] for e in patrols}
    for i in range(300):
        h.step(1)
        for e in patrols:
            if e.alive:
                traces[id(e)].append((round(e.x, 1), round(e.z, 1)))
    for n, (eid, tr) in enumerate(traces.items()):
        if len(tr) > 2:
            span = max(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a in tr for b in [tr[0]])
            measure(f'patrol_{n}_route_span_u', round(span, 1))

walk_to((20, 0, 30), timeout_s=15, label='cross map 1')
walk_to((40, 0, 38), timeout_s=15, label='cross map 2')
shot('patrol_yard_approach')
walk_to((45, 0, 41), timeout_s=8, arrive=0.7, label='rifle pickup')
h.step(5)
slot2 = inv.slots[2] if len(inv.slots) > 2 else None
measure('rifle_granted', type(slot2).__name__ if slot2 else None)
h.press('3')
h.step(20)
shot('rifle_viewmodel')

tgt = nearest_enemy()
if tgt is not None:
    aim_at(tgt.position + Vec3(0, 1.5, 0))
    h.press('left mouse down')
    for i in range(50):
        if tgt.alive:
            aim_at(tgt.position + Vec3(0, 1.5, 0))
        h.step(1)
        if i == 12:
            shot('rifle_autofire')
    h.press('left mouse up')
    measure('rifle_autofire_target_dead', not tgt.alive)
    measure('hud_ammo_after_autofire',
            game.hud.ammo_text.text if game.hud and game.hud.ammo_text else None)
h.press('r')
h.step(5)
measure('hud_ammo_during_reload',
        game.hud.ammo_text.text if game.hud and game.hud.ammo_text else None)
h.step(90)
measure('hud_ammo_after_reload',
        game.hud.ammo_text.text if game.hud and game.hud.ammo_text else None)
crash_check('patrol yard')

# ---------------------------------------------------------------- frame timing (see header note)
log("phase: frame timing (vsync-bound — proves no dropped frames, not headroom)")
samples = []
for i in range(150):
    t0 = _wall.perf_counter()
    h.step(1)
    samples.append((_wall.perf_counter() - t0) * 1000)
samples.sort()
measure('frame_ms_median', round(samples[len(samples)//2], 2))
measure('frame_ms_p95', round(samples[int(len(samples)*0.95)], 2))

aim_at((42, 3, 20))
h.step(2)
shot('bloom_on_view')
if game.bloom:
    game.bloom.set_enabled(False)
    h.step(2)
    shot('bloom_off_view')
    game.bloom.set_enabled(True)
    h.step(2)

# ---------------------------------------------------------------- wrap
# The tower route (checkpoint/lava/parkour/aggressive/win) lives in
# tools/playtest_probe3.py, which clears the roaming enemies first so the idle
# player doesn't get hunted mid-phase — see that file.
final_crash = h.has_crashed()
REPORT['clean_run'] = not final_crash
if final_crash:
    finding("crash patterns present in captured output — see raw tail")
    REPORT['raw_tail'] = h.output_so_far().splitlines()[-60:]

with open(os.path.join(SHOTS_DIR, 'playtest_report.json'), 'w') as f:
    json.dump(REPORT, f, indent=2)
log(f"done. {len(REPORT['findings'])} findings, {len(REPORT['shots'])} shots -> {SHOTS_DIR}")
print("PLAYTEST_COMPLETE", flush=True)
