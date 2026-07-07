"""game_settings.py — persisted player-facing settings (resolution, volume).

Separate from editor_prefs.json (that file is level-editor-only state: camera
bookmarks, grid snap, layout presets). Mirrors its load/save shape: a flat
dict at the project root, loaded on launch and written on change.
"""

import json
import os

SETTINGS_PATH = 'game_settings.json'

RESOLUTIONS = [(1280, 720), (1600, 900), (1920, 1080)]

DEFAULTS = {
    'resolution_index': 0,
    'sfx_volume': 1.0,
    'music_volume': 1.0,
}


def load_settings():
    settings = dict(DEFAULTS)
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH) as f:
                saved = json.load(f)
            for key in DEFAULTS:
                if key in saved:
                    settings[key] = saved[key]
        except Exception:
            pass
    return settings


def save_settings(settings):
    try:
        with open(SETTINGS_PATH, 'w') as f:
            json.dump(settings, f, indent=4)
    except Exception:
        pass


def apply_audio_settings(settings):
    """Push sfx_volume onto Ursina's global Audio.volume_multiplier.

    Audio.volume_multiplier is Ursina's built-in master-volume knob (applied
    to every Audio() instance's group_volume_multiplier at construction and
    on .volume writes) — no per-instance wiring needed. music_volume is
    persisted but not yet applied: the game has no music/ambient system to
    scale (same forward-declared-knob pattern as Layers.PICKUP in
    collision_system.py).
    """
    from ursina import Audio
    Audio.volume_multiplier = settings['sfx_volume']
