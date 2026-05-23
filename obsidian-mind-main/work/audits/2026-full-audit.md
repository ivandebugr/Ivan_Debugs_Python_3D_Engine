# Full Project Audit — 2026-05-20

Persona: solo-founder (Tracks 3–8) + startup-cto (Tracks 1–2). Read-only.
Scope: every file in `Scripts/`, `main.py`, `level.json`, `README.md`, `CLAUDE.md`.

---

## Executive Summary

The engine is **architecturally healthy** — three collision authorities are intact, AliveEntity lifecycle is followed, `Game` singleton is clean. The most expensive remaining issues are **gameplay polish** (no win condition, death = silent teleport, single weapon, no audio) and **shipping packaging** (no PyInstaller build, README still says state-machine is "Planned" while v1.2 shipped it). Three quiet code defects: HealthBar still `eternal=True` (blocks scene clear), `swept_cast()` still dead, level editor `_exit_play_mode` carries a swallow-all `except Exception` that can hide regressions. No new architectural debt added since v1.2.3 hotfixes. **The biggest leverage point is not engineering — it's shipping a playable build to 5 strangers.**

---

## RICE Top 10 (all findings ranked)

| # | Finding | R | I | C% | E (days) | Score | Track |
|---|---|---|---|---|---|---|---|
| 1 | Add win/lose state — player_death currently silently teleports | 100 | 3 | 90 | 0.5 | 540 | 4 |
| 2 | Ship PyInstaller/`.app` build + itch.io page | 100 | 3 | 70 | 2 | 105 | 5 |
| 3 | README changelog out of date — says state-machine is "Planned", omits v1.2/v1.2.1/v1.2.2/v1.2.3 | 100 | 2 | 95 | 0.25 | 760 | 5 |
| 4 | Add 1 SFX (shot + hit + death) — single highest game-feel ROI | 100 | 3 | 85 | 0.5 | 510 | 6 |
| 5 | HealthBar `eternal=True` on bg/bar/text — blocks `scene.clear()`, forces manual destroy chains | 50 | 2 | 90 | 0.5 | 180 | 1/3 |
| 6 | `_exit_play_mode` swallows all exceptions silently — masks future bugs | 30 | 2 | 95 | 0.1 | 570 | 1 |
| 7 | Delete `swept_cast()` dead code + `_pooled` dead param | 20 | 1 | 100 | 0.1 | 200 | 3 |
| 8 | Extract `_patch_shaders_to_glsl120` to `Scripts/compat.py` | 10 | 1 | 100 | 0.25 | 40 | 3 |
| 9 | Cut "Networked multiplayer substrate" from roadmap | — | — | — | 0 | — | 6 |
| 10 | Replace per-frame `for e in scene.entities: isinstance(e, HealthBar)` loop with cached list | 30 | 1 | 70 | 0.25 | 84 | 2 |

R = % of player sessions affected.  E = solo-dev days.

---

## Track 1 — Architecture & Collision Integrity  [senior-architect + code-reviewer]

| ID | Sev | File:Line | Description | Fix |
|---|---|---|---|---|
| A1 | LOW  | [Scripts/collision_system.py:66-80](Scripts/collision_system.py#L66-L80) | `swept_cast()` defined, exported, never called | Delete |
| A2 | LOW  | [Scripts/weapon.py:50](Scripts/weapon.py#L50), [Scripts/weapon.py:102](Scripts/weapon.py#L102) | `_pooled` constructor param unused after pool refactor | Delete arg from both bullet classes |
| A3 | MED  | [Scripts/health_bar.py:14-67](Scripts/health_bar.py#L14-L67) | `eternal=True` set on HealthBar root + bg + bar + text — survives `scene.clear()`. Forces every caller to do explicit destroy chains (see [main.py:60-66](main.py#L60-L66) and [main.py:128-133](main.py#L128-L133)). | Drop `eternal=True`; let normal parent destruction cascade |
| A4 | MED  | [Scripts/level_editor.py:813](Scripts/level_editor.py#L813) | `except Exception:` swallows ImportError + any teardown bug in play-mode exit; only logs nothing | Narrow to `ImportError`, re-raise on others |
| A5 | LOW  | [main.py:127-135](main.py#L127-L135) | `start_game()` duplicates `_clear_gameplay_entities()` cleanup logic for the player. Violates "single canonical teardown path" principle (CLAUDE.md). | Call `game.return_to_menu()` if `game.player` is set, then spawn fresh |
| A6 | LOW  | [main.py:71-77](main.py#L71-L77) | Bare `except Exception: pass` inside teardown loops hides legitimate destroy errors | Log to stderr instead of silent swallow |
| A7 | LOW  | [Scripts/enemy.py:18](Scripts/enemy.py#L18) | `self.origin_y = -0.5` assigned **after** `super().__init__()` and **after** `collision_manager.add()` — the cell registered uses the pre-shift position. Cosmetic — re-position next frame corrects it. | Pass `origin_y=-0.5` into `super().__init__` kwargs |
| A8 | INFO | [main.py:215](main.py#L215) | `invoke(setattr, application, 'time_scale', 1, delay=0.1)` queued from `resume_game` — works, but invocation is fire-and-forget. If user re-pauses inside 100ms it will clobber `time_scale=0`. | Drop the delayed invoke; the immediate set on line 207 is sufficient |

**Three collision authorities:** still exactly three (projectile raycast, swept player movement, ground/ceiling raycast). No fourth introduced. ✅
**AliveEntity lifecycle:** all `die()` paths idempotent; `_clear_gameplay_entities` and `main_menu()` both call `e.die()` before blanket destroy. ✅
**Key Rule violations:** none. `entity.name == 'enemy'` is absent. No classes passed to `raycast(ignore=...)`. ✅
**Scene teardown canonical path:** mostly intact — `_clear_gameplay_entities` is the single canonical path **except** for the cleanup duplicated in `start_game()` (A5).

Section health: **GREEN with two LOW housekeeping items.**

---

## Track 2 — Performance & Game Loop  [karpathy-coder + performance-profiler]

| ID | Sev | File:Line | Description | Fix |
|---|---|---|---|---|
| P1 | MED  | [main.py:435-438](main.py#L435-L438) | Global `update()` scans `scene.entities` every frame and does `isinstance(e, HealthBar)`. Cost = O(N entities) per frame even when no health bar moved. | Maintain a `_active_health_bars: list[HealthBar]` updated on create/destroy |
| P2 | MED  | [Scripts/player_controller.py:226-238](Scripts/player_controller.py#L226-L238) | `_swept_blocked` builds `ignore` list every call (up to 2× per movement frame) by concatenating `query_layer(PLAYER_BULLET) + query_layer(ENEMY_BULLET)`. Each call walks `_tracked` set. Cost scales linearly with active bullets. | Cache the ignore list once per frame in `Player.update`, pass into `_swept_blocked` |
| P3 | LOW  | [Scripts/enemy.py:58-66](Scripts/enemy.py#L58-L66) | `_is_occluded()` runs every frame per enemy — 1 raycast each. At 30 enemies = 30 raycasts/frame just for HUD visibility. | Throttle to every N frames or skip when `player_dist > 80` |
| P4 | LOW  | [Scripts/weapon.py:74-76](Scripts/weapon.py#L74-L76) | Per bullet frame: `Vec3(self.position)` copy + raycast. Bullets are short-lived; OK as-is but watch at 30 active. | None — within budget |
| P5 | INFO | [Scripts/collision_system.py:105-111](Scripts/collision_system.py#L105-L111) | `CollisionManager.update()` is O(tracked entities). Acceptable, but `list(self._tracked)` copy each frame is wasteful. | Iterate `_tracked` directly; mark dead in a deferred set |

**Top 3 frame-budget risks (ranked):**
1. **`_swept_blocked` ignore-list rebuild** — runs every player movement frame, allocates two lists. At 30 player + 60 enemy pool entries, that's 90 walks/frame even when only 1 bullet is alive. (P2)
2. **HealthBar global scan** in `main.update()` (P1) — grows with all scene entities, not just bars.
3. **Per-enemy occlusion raycast** (P3) — N raycasts/frame for HUD-only visibility.

**Worst-case @ 30 enemies + 30 active bullets:** ~120 raycasts/frame (5 swept + 30 enemy occlusion + 30 player bullets + 30 enemy bullets + ceiling/ground × ~2). At 60 FPS that's 7,200 raycasts/sec — Panda3D handles it, but margin is thin.
**Scaling break point:** ~50 enemies starts to compound (50 occlusion + 50 AI + bullet raycasts). Hard ceiling around 75 enemies on M1 at 60fps based on raycast cost alone.

Section health: **GREEN — no per-frame allocation hot paths, no scene-wide loops outside Track 2 P1.**

---

## Track 3 — Tech Debt  [tech-debt-tracker]

**Open items from CLAUDE.md, re-verified:**

| ID | Item | Status | RICE |
|---|---|---|---|
| D1 | `swept_cast()` dead code | OPEN, unchanged | R 0 I 1 C 100 E 0.1 → **score 1000** (delete-and-forget) |
| D2 | `_pooled` dead param | OPEN | R 0 I 1 C 100 E 0.1 → **1000** |
| D3 | HealthBar `eternal=True` | OPEN, **worse** — now forces manual destroy in two paths in main.py | R 50 I 2 C 90 E 0.5 → **180** |
| D4 | Crosshair visibility not restored on non-Esc pause paths | OPEN | R 5 I 1 C 80 E 0.1 → **40** |
| D5 | `_patch_shaders_to_glsl120` copy-paste in level_editor.py | OPEN | R 0 I 1 C 100 E 0.25 → **40** |
| D6 | No win/game-over state | OPEN, **promoted to gameplay blocker** | R 100 I 3 C 90 E 0.5 → **540** |

**New debt not in CLAUDE.md table:**

| ID | Item | File |
|---|---|---|
| N1 | `start_game()` duplicates player teardown logic from `_clear_gameplay_entities` | [main.py:127-135](main.py#L127-L135) |
| N2 | `_exit_play_mode` `except Exception:` swallow | [Scripts/level_editor.py:813](Scripts/level_editor.py#L813) |
| N3 | `Enemy.origin_y` assigned post-super (line 18) instead of in kwargs | [Scripts/enemy.py:18](Scripts/enemy.py#L18) |
| N4 | Per-frame `isinstance(e, HealthBar)` scan in global update | [main.py:435-438](main.py#L435-L438) |
| N5 | `invoke()` `time_scale=1` race on rapid re-pause | [main.py:215](main.py#L215) |

**Top 5 next-session backlog (by RICE):**
1. D1 — delete `swept_cast()` (10 min)
2. D2 — delete `_pooled` (5 min)
3. D6 — add win/lose screens (½ day, highest player impact)
4. D3 — remove HealthBar `eternal=True` (½ day, simplifies 2 callers)
5. N4 — cache active health bars (15 min)

Section health: **GREEN — debt is small, well-tracked, and shrinking.** No new high-severity items since v1.2.3.

---

## Track 4 — Gameplay & Design  [experiment-designer + product-manager-toolkit]

### Experiment proposals

| Variable | Current | Hypothesis | Variant | Success metric |
|---|---|---|---|---|
| Enemy HP | 100 | "100 HP at 25 dmg = 4 shots feels too long for a hipfire FPS" | 50 (2 shots) | Median engagement-to-kill time drops <2s; subjective "snappy" in playtest |
| Enemy shoot cooldown | 1.0s | "1s feels punishing with no cover" | 1.5s | Player deaths/min drops; rounds last longer |
| Player speed | 8 | "Movement feels heavy — too slow vs sprint expectation" | 11 + add `shift` sprint | Time-to-traverse test corridor drops 25% |
| Bullet pool size | 30 / 60 | "30 player bullets never exhausted in playtest" | 20 / 40 | No pool exhaustion logs across 10 playtests; save memory |
| Enemy detection_range | 100 | "100u = whole-level aggro, removes stealth/positioning" | 40 | Player reports of "I can sneak around" go up |

All experiments are **single-variable**, measurable in one playtest session.

### RICE-ranked roadmap (from CLAUDE.md "Engine features" + README)

| Item | R | I | C | E | RICE | Verdict |
|---|---|---|---|---|---|---|
| Win/lose state | 100 | 3 | 95 | 0.5 | **570** | **DO NEXT** (not on roadmap — missing!) |
| 1 SFX + 1 ambient music track | 100 | 3 | 90 | 0.5 | **540** | **ADD TO ROADMAP** |
| 2nd weapon type (shotgun) | 100 | 2 | 80 | 1 | **160** | Defer to v1.4 |
| Asset hot-reload in editor | 20 | 1 | 70 | 1 | **14** | **CUT** — solo dev edits asset rarely |
| Behaviour trees (patrol/attack/flee) | 100 | 2 | 60 | 5 | **24** | Defer; over-engineered for current scale |
| Trigger/zone system | 50 | 2 | 70 | 2 | **35** | Defer to v1.5 |
| Weapon inventory API | 100 | 2 | 70 | 3 | **47** | Pre-req for shotgun, but YAGNI until 3rd weapon |
| PyInstaller packaging | 100 | 3 | 70 | 2 | **105** | **DO BEFORE behaviour trees** |
| Networked multiplayer | 100 | 3 | 20 | 30 | **2** | **DELETE FROM ROADMAP** |
| Procedural level gen | 30 | 1 | 50 | 10 | **1.5** | **DELETE** |
| Gamepad input | 30 | 1 | 80 | 2 | **12** | Defer indefinitely |

**Missing from roadmap (high RICE):** win/lose state, audio, packaging, screenshot/trailer capture for itch.io.

Section health: **YELLOW — engine is more mature than the game on top of it.** Gameplay variables are arbitrary defaults, not tuned. Single weapon, no audio, no game-over screen.

---

## Track 5 — Shipping Readiness  [release-manager + changelog-generator]

### Current state
- CLAUDE.md says v1.2.3 (most recent). Git tag/log: latest commit `8efb0f3 v 1.2.2`. There is **no v1.2.3 commit yet** — fixes are uncommitted on `version_1.2` branch (per git status: `M main.py`, `M Scripts/enemy.py`, `M Scripts/level_editor.py`).
- README.md changelog stops at **v6 (Ursina 8.3.0)** and **`Game state machine: Planned`** — but the state machine shipped in v1.2. **README is ~4 versions stale.**

### Shipping blockers (between now and "stranger downloads and plays this")
1. **No build artifact** — no PyInstaller spec, no `.app`, no Windows `.exe`.
2. **No game-over** — player dies → silent teleport. Confusing for new players.
3. **No level beyond `level.json` debug arrangement** — 64 blocks + 1 enemy is a tech demo, not a playable level.
4. **README missing controls clarity, screenshots, install troubleshooting** for macOS GLSL pitfall.
5. **No itch.io / Hacker News page** drafted.

### v1.3 "done" criteria (proposed)
- [ ] Win condition: kill all enemies → "VICTORY" screen + return to menu
- [ ] Lose condition: HP → 0 → "GAME OVER" screen + retry/menu
- [ ] At least 1 hand-designed level (5+ enemies, layered geometry)
- [ ] 1 SFX (shot) + 1 ambient track
- [ ] PyInstaller `.app` builds on macOS
- [ ] README updated with v1.2 → v1.3 changelog + 2 screenshots

### Changelog gap analysis

README is missing: v1.2 (state machine), v1.2.1 (6 hotfixes), v1.2.2 (level editor placement, gizmo, asset tray), v1.2.3 (3 startup bugs, ghost crash, occlusion fix, etc.). CLAUDE.md has the full picture; README does not.

### Proposed v1.3 changelog entry template
```markdown
### v1.3 — Playable build

**Game state**
- Win/lose screens with retry + main-menu actions
- Curated level (5 enemies, layered geometry)

**Polish**
- 1 shot SFX, 1 ambient music loop (CC-0)
- HealthBar `eternal=True` removed; scene teardown simplified

**Distribution**
- PyInstaller `.app` builds on macOS; `.exe` on Windows
- itch.io page live

**Engine**
- (link to CLAUDE.md for engine-internal fixes)
```

Section health: **RED on shipping, GREEN on engine stability.** README staleness is the cheapest, highest-leverage fix.

---

## Track 6 — Strategic / Founder Coaching  [founder-coach]

- **You're building an engine when you should be shipping a game.** v3 collision rewrite, v1.2 state machine, asset tray, gizmo math — all good engineering. None of it puts a game in a stranger's hands. **Cut roadmap items that are infrastructure for a game that doesn't exist yet.**

- **Your real next milestone is "5 strangers played it and 1 finished it."** That requires a win condition, an `.app` bundle, and an itch.io page — and nothing else. Behaviour trees, trigger zones, weapon inventory, networking are all post-PMF features.

- **The Ursina/GLSL/AntialiasAttrib fix chain is yak-shaving disguised as productivity.** It was necessary, but the time spent rewriting shaders could have shipped a SFX pack + a single curated level. Recognize this pattern so it doesn't repeat with the next dependency bump.

- **Delete "networked multiplayer" and "procedural level gen" from the roadmap, today.** Not defer — delete. A solo dev with no public demo shipping multiplayer in Ursina is a 6-month detour that ends in burnout. They are aspiration, not roadmap.

- **The hotfix cadence is healthy.** Six fixes between v1.2 and v1.2.1, three more in v1.2.3 — you're iterating on what users (in this case, you) actually hit. Keep the audit → fix → ship loop short. Pair it with **one external playtester per week** and the engine becomes a game.

---

## Track 7 — Prompt Quality (CLAUDE.md)  [senior-prompt-engineer]

### Findings & rewrites

**1. Active Skills Stack table is broken** — claimed paths (`engineering/karpathy-coder/SKILL.md`, etc.) don't exist on disk. Only `.claude/skills/karpathy-guidelines/SKILL.md` is present. Every "Load When Relevant" skill is unreachable.

- **Before:** `| Performance profiling / FPS drops | performance-profiler | engineering/performance-profiler/SKILL.md |`
- **After:** Either install the skills, or rewrite this section to list **lenses to apply** rather than files to load. Recommended: replace "Active Skills Stack" with "Mental Modes" — Karpathy guidelines (always), performance-profiler mindset (when FPS drops), founder-coach mindset (when scoping). Drop the file-path column.

**2. Key Rules 1–10 mix HARD constraints with SOFT preferences** — Rule 7 ("`time` is Panda3D's clock, use `_time.time()` for wall clock") is a hard footgun; Rule 1 ("Never check entity.name") is also hard. Both are correctly absolute. But there is no separation between "violating this crashes" vs "violating this is just style."

- **Before:** numbered list of 10 rules at one severity
- **After:** Two sections — **"Hard Constraints (violating crashes or breaks invariants)"** for rules 1, 2, 3, 6, 7, 9, 10; **"Conventions"** for 4, 5, 8.

**3. "Common Workflows → Adding a New Enemy Type" is missing the `on_die()` step** for sub-entity cleanup, even though `Enemy.on_die` is the reference example.

- **Before:** 5 steps ending at "Add spawn handling in start_game()"
- **After:** Insert step 3.5: "Override `on_die()` to destroy sub-entities (health bar, particles, etc.) before super().on_die() destroys self."

**4. Obsidian Mind Integration assumes the vault exists and is wired up.** The Session Start protocol says "Read `brain/North Star.md`" but if the vault doesn't exist in this working directory, Claude wastes a turn discovering that. Add a "Vault location" line at the top of the section.

**5. CLAUDE.md is 350+ lines and re-loaded every session.** The Known Tech Debt table alone is 30+ rows, most FIXED. Trim to OPEN items only; move history to README changelog.

- **Single biggest reliability win:** Trim the Tech Debt table to OPEN-only rows (saves ~25 lines of "[FIXED v1.2.1]" noise per session). Move FIXED items to README v1.2.x changelog where they belong.

Section health: **YELLOW — operating manual works, but contains broken file refs and grew faster than it was pruned.**

---

## Track 8 — Synthesis & Next Actions  [capture]

### THIS SESSION (≤30 min, do now)
- [ ] Delete `swept_cast()` from collision_system.py
- [ ] Delete `_pooled` param from PlayerBullet, EnemyBullet
- [ ] Narrow `except Exception` in level_editor `_exit_play_mode` to `except ImportError`
- [ ] Trim CLAUDE.md Tech Debt table to OPEN items only

### THIS WEEK (1–3 days)
- [ ] Add Win/Lose screens — wire `game.state = WIN` when `len(game.enemies) == 0`; "GAME OVER" panel on HP ≤ 0 instead of silent teleport
- [ ] Update README.md changelog with v1.2 → v1.2.3 entries (CLAUDE.md is the source of truth)
- [ ] Cache active HealthBar list to drop the per-frame `scene.entities` isinstance scan
- [ ] Remove `eternal=True` from HealthBar; simplify destroy chains in `_clear_gameplay_entities` and `start_game`

### THIS MILESTONE (v1.3 scope)
- [ ] PyInstaller macOS `.app` build, document in README
- [ ] One curated 5-enemy level + level.json saved as `levels/v1.json`
- [ ] 1 shot SFX + 1 ambient track (CC-0 from freesound or Pixabay)
- [ ] itch.io page with 2 screenshots + 30-second clip
- [ ] DELETE from roadmap: networked multiplayer, procedural level gen

### Playability-affecting TODAY
- Death = silent teleport is the single most confusing thing for a new player. **Fix first.**

---

## New Gotchas (→ brain/Gotchas.md)

- **`scene.entities` is an iteration trap.** Even simple per-frame work in the global `update()` (e.g. `for e in scene.entities: isinstance(e, HealthBar)`) scales with *all* entities including pool bullets parked at y=-10000. Always iterate a cached collection of the specific type you care about.
- **`eternal=True` is sticky.** Setting it on an Entity makes it survive `scene.clear()` and forces every teardown path to destroy it manually. Avoid unless the entity must survive scene transitions (and even then, prefer parenting to a long-lived UI root).

## New Patterns (→ brain/Patterns.md)

- **Lazy-import to break circular deps**: `from Scripts.weapon import get_enemy_bullet_pool` *inside* `Enemy.shoot()` — used to keep `weapon.py ↔ enemy.py` separation clean. The accessor function (`get_enemy_bullet_pool()`) is the seam.
- **Position-park instead of enabled-toggle for pooled NodePaths**: `bullet.position = Vec3(0, -10000, 0)` instead of `bullet.enabled = False` — Panda3D's `unstash()` asserts on re-enable of a previously-disabled NodePath. Cheap, no per-frame cost, no allocation.

## Tech Debt Delta (new items for CLAUDE.md)

| Issue | Location | Priority | Notes |
|---|---|---|---|
| `start_game()` duplicates player teardown from `_clear_gameplay_entities` | `main.py:127-135` | Medium | Should call `game.return_to_menu()` before respawning |
| `_exit_play_mode` swallow-all exception | `Scripts/level_editor.py:813` | Medium | Narrow to ImportError |
| `Enemy.origin_y` set post-super, post-collision-register | `Scripts/enemy.py:18` | Low | Pass into super().__init__ kwargs |
| Global `update()` does per-frame `isinstance(e, HealthBar)` scan over `scene.entities` | `main.py:435-438` | Medium | Maintain a cached active-bars list |
| `invoke()` `time_scale=1` race on rapid re-pause | `main.py:215` | Low | Drop the delayed invoke |

---

*End of audit.*
