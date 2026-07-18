"""playtest_probe3.py — tower route + decisive shotgun-pellet test (2026-07-17).

Companion to tools/playtest_probe.py (the full-route driver). This one clears
the roaming enemies first (die(), the same API win_then_r uses) so the idle
player doesn't get hunted mid-phase, leaves one distant patroller alive so a
win must come from the trigger, and drives: shotgun pellet-count test ->
checkpoint -> lava kill-plane -> 5-step hop chain -> z=12 wall jump ->
aggressive cadence -> win trigger -> R teardown.

Key measurements from the 2026-07-17 run (see vault v1.7-playtest-findings):
  - shotgun spawns 5 pellets same-frame, only 1 alive 2 frames later in an
    OPEN FIELD -> pellet fratricide confirmed
  - player rests at y=0.5 at spawn (half-buried, ~0.4 u/s crawl) and at
    y~1.59 after a jump (floating ~0.6 above the floor)
  - aggressive preset fired ~2 shots/3s (preset says 0.4s cadence; clamped by
    Enemy.shoot's own 1.0s can_shoot gate)

Run from the repo root:  python3 tools/playtest_probe3.py
"""

import json
import math
import os
import sys
import time as _wall
import traceback

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

SHOTS_DIR = os.environ.get(
    'PLAYTEST_SHOTS', os.path.join(PROJECT_ROOT, 'tools', 'playtest_shots3'))
os.makedirs(SHOTS_DIR, exist_ok=True)
TRACE_PATH = os.path.join(SHOTS_DIR, 'rtm_trace.txt')

from Scripts import audio_workaround  # noqa: F401
from tests.smoke_test_harness import GameTestHarness

REPORT = {'findings': [], 'measurements': {}}
_shot_n = 0


def log(msg):
    print(f"[probe3] {msg}", flush=True)


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


h.launch_main()
h.step(3)
h.press('space')
h.step(5)

from ursina import held_keys, camera, scene, Vec3
from Scripts.game import game, Game
from Scripts.collision_system import collision_manager, Layers

# Trace any teardown to a FILE — the harness redirects stdout inside step(), so
# a stdout trace would be swallowed (learned the hard way in probe 2).
_orig_rtm = game.return_to_menu
def _traced_rtm():
    with open(TRACE_PATH, 'a') as f:
        f.write("return_to_menu called:\n")
        traceback.print_stack(file=f)
        f.write("\n---\n")
    _orig_rtm()
game.return_to_menu = _traced_rtm

h.start_real_game()
h.step(5)


def player():
    return game.player


def aim_at(target, eye_h=1.5):
    p = player()
    t = Vec3(*target)
    d = t - (p.position + Vec3(0, eye_h, 0))
    p.rotation_y = math.degrees(math.atan2(d.x, d.z))
    horiz = math.sqrt(d.x * d.x + d.z * d.z)
    camera.rotation_x = -math.degrees(math.atan2(d.y, max(horiz, 1e-6)))


def fire_once():
    h.press('left mouse down')
    h.press('left mouse up')


def state():
    return game.state


measure('y_at_spawn', round(player().y, 3))
h.press('space')
for i in range(90):
    h.step(1)
    if i > 10 and player().grounded:
        break
measure('y_after_first_jump_landing', round(player().y, 3))
p0 = Vec3(player().position)
player().rotation_y = 180
held_keys['w'] = 1
t0 = _wall.perf_counter()
h.step(40)
held_keys['w'] = 0
measure('speed_after_first_jump',
        round((Vec3(player().position) - p0).length() / (_wall.perf_counter() - t0), 2))

# clear roaming enemies: cautious + flee + ONE patroller; keep aggressive + 1 patroller
cleared = 0
keep_patroller = None
for e in list(game.enemies):
    if not e.alive:
        continue
    if e.z < 10:
        continue                    # aggressive stays for the chamber fight
    if e.z > 40 and keep_patroller is None:
        keep_patroller = e          # one patroller survives (win must come from trigger)
        continue
    e.die()
    cleared += 1
h.step(3)
measure('cleared_roamers', cleared)
measure('alive_now', sum(1 for e in game.enemies if e.alive))

# --- decisive shotgun pellet test --------------------------------------------
player().position = Vec3(-14, 1.01, 18)   # shotgun weapon pickup
h.step(10)
inv = player().inventory
h.press('2')
h.step(40)
player().position = Vec3(-20, 1.01, -20)  # open field
player().rotation_y = 180
camera.rotation_x = 0
h.step(3)
before = {id(b) for b in collision_manager.query_layer(Layers.PLAYER_BULLET)}
fire_once()   # shoot() runs synchronously inside the input dispatch
now0 = {id(b) for b in collision_manager.query_layer(Layers.PLAYER_BULLET)}
spawned_same_frame = len(now0 - before)
h.step(2)
now2 = [b for b in collision_manager.query_layer(Layers.PLAYER_BULLET) if id(b) not in before]
measure('shotgun_pellets_spawned_same_frame', spawned_same_frame)
measure('shotgun_pellets_alive_after_2_frames', len(now2))
w = inv.active_weapon
measure('shotgun_ammo_after_test', getattr(w, 'ammo', None))
if spawned_same_frame >= 5 and len(now2) < spawned_same_frame:
    finding(f"CONFIRMED pellet fratricide: {spawned_same_frame} pellets spawn co-located, only "
            f"{len(now2)} alive 2 frames later in an open field — pellets raycast-hit each other "
            f"(PlayerBullet has collider='box'; bullet rays ignore only [self, player])")
elif spawned_same_frame < 5:
    finding(f"shotgun spawned only {spawned_same_frame}/5 pellets in the same frame — "
            f"pool or shoot-path issue, not fratricide")
h.step(30)

# --- tower route -------------------------------------------------------------
h.press('1')
h.step(15)
player().position = Vec3(42, 1.01, 30)
h.step(3)
p = player()
p.rotation_y = 180
camera.rotation_x = 0
held_keys['w'] = 1
for i in range(240):
    h.step(1)
    if game.respawn_point is not None:
        break
held_keys['w'] = 0
measure('checkpoint_respawn_point',
        tuple(round(v, 1) for v in game.respawn_point) if game.respawn_point else None)
shot('checkpoint_area')

# NOTE 2026-07-17: walking straight down the tower centre line never reaches the
# kill-plane volume (z 13.5-24) — the first step block at (42,0.9,25) blocks the
# path at ground level. Walk BESIDE the steps (x offset) to actually fall in.
hp0 = player().health
player().position = Vec3(40.2, 1.01, 26)   # left of the step column
h.step(3)
player().rotation_y = 180
held_keys['w'] = 1
snapped_back = False
for i in range(300):
    h.step(1)
    if state() != Game.PLAYING:
        break
    if i > 20 and player().z > 25.5 and abs(player().x - 42) < 4 and player().health < hp0:
        snapped_back = True
        break
held_keys['w'] = 0
h.step(5)
measure('lava_respawn_worked', snapped_back)
measure('lava_hp_cost', hp0 - player().health if player() else None)
measure('post_lava_pos', tuple(round(v, 1) for v in player().position) if player() else None)
shot('post_lava')
if not snapped_back:
    finding(f"lava kill-plane respawn not observed (state={state()}, "
            f"pos={tuple(round(v,1) for v in player().position) if player() else None})")


def hop_to(x, z, timeout_s=8):
    t = Vec3(x, 0, z)
    for i in range(int(timeout_s * 60)):
        p = player()
        if p is None or state() != Game.PLAYING:
            return False
        d = Vec3(t.x - p.x, 0, t.z - p.z)
        if d.length() < 0.9 and p.grounded:
            held_keys['w'] = 0
            return True
        p.rotation_y = math.degrees(math.atan2(d.x, d.z))
        held_keys['w'] = 1
        if p.grounded and d.length() < 3.4:
            h.press('space')
        h.step(1)
    held_keys['w'] = 0
    return False


steps_ok = 0
for (sx, sz) in [(42, 25), (42, 22), (42, 19), (42, 16.5), (42, 14)]:
    if not hop_to(sx, sz):
        break
    steps_ok += 1
measure('hop_steps_reached', steps_ok)
measure('pos_after_hops', tuple(round(v, 1) for v in player().position) if player() else None)
shot('parkour_progress')
if steps_ok < 5:
    finding(f"hop chain reached {steps_ok}/5 steps with the automated jumper — measured jump "
            f"apex is ~1.8u (double-driven jump), design assumed 3.6u; hands-on check needed. "
            f"Continuing from top step.")
    player().position = Vec3(42, 3.75, 14)
    h.step(5)

p = player()
p.rotation_y = 180
held_keys['w'] = 1
h.press('space')
cleared_wall = False
for i in range(90):
    h.step(1)
    if player() and player().z < 11.6:
        cleared_wall = True
        break
held_keys['w'] = 0
measure('wall_jump_cleared', cleared_wall)
if not cleared_wall:
    finding("z=12 wall (top y=4.5) NOT cleared from the top step — verify by hand; teleporting past")
    player().position = Vec3(42, 1.5, 9)
    h.step(5)
h.step(20)   # settle the landing before engaging
shot('final_chamber')

aggro = next((e for e in game.enemies if e.alive and e.z < 10), None)
if aggro:
    aim_at(aggro.position + Vec3(0, 1.5, 0))
    h.step(2)
    shot('aggressive_engaged')
    seen = {id(b) for b in collision_manager.query_layer(Layers.ENEMY_BULLET)}
    shots_n = 0
    hp0 = player().health
    for i in range(int(3 * 60)):
        h.step(1)
        if state() != Game.PLAYING:
            break
        now = {id(b) for b in collision_manager.query_layer(Layers.ENEMY_BULLET)}
        shots_n += len(now - seen)
        seen = now
    measure('aggressive_shots_in_3s', shots_n)
    measure('hp_lost_to_aggressive_3s', hp0 - player().health if player() else None)
    if state() == Game.PLAYING:
        player().health = min(player().health + 75, 100)   # instrumentation top-up
        kills = 0
        for i in range(10):
            if not aggro.alive:
                break
            aim_at(aggro.position + Vec3(0, 1.5, 0))
            fire_once()
            h.step(12)
            kills = i + 1
        measure('pistol_shots_to_kill_aggressive',
                kills if not aggro.alive else f'alive hp={aggro.health}')
else:
    finding("aggressive enemy not alive when reaching the final chamber")

measure('alive_before_win', sum(1 for e in game.enemies if e.alive))
if state() == Game.PLAYING:
    p = player()
    for i in range(int(8 * 60)):
        if state() != Game.PLAYING:
            break
        d = Vec3(42 - p.x, 0, 3.5 - p.z)
        if d.length() > 0.5:
            p.rotation_y = math.degrees(math.atan2(d.x, d.z))
            held_keys['w'] = 1
        h.step(1)
    held_keys['w'] = 0
h.step(5)
measure('state_at_end', state())
shot('win_screen')
if state() != Game.WIN:
    finding(f"win trigger path did not end in WIN (state={state()})")

h.press('r')
h.step(10)
measure('back_to_menu', state())
measure('rtm_trace_exists', os.path.exists(TRACE_PATH))

REPORT['clean'] = not h.has_crashed()
if h.has_crashed():
    REPORT['tail'] = h.output_so_far().splitlines()[-40:]
    finding("crash patterns in output tail")
with open(os.path.join(SHOTS_DIR, 'probe3_report.json'), 'w') as f:
    json.dump(REPORT, f, indent=2)
log(f"done. {len(REPORT['findings'])} findings")
print("PROBE3_COMPLETE", flush=True)
