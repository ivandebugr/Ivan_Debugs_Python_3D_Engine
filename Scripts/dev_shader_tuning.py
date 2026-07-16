"""
dev_shader_tuning.py — TEMPORARY dev-only live-tuning for lit_shader.py uniforms.

NOT MEANT TO SHIP. This exists to nudge rim/spec/warm/cool values while the
game is running so numbers can be read off the console and copied back into
lit_shader.py's default_input by hand. Delete this file (and its main.py
wiring) once tuning is done.

Set ENABLED = False (or just delete the import in main.py) to turn it off
without touching lit_shader.py itself — no defaults in lit_shader.py are
changed by this module; it only pushes overrides onto the live shader inputs.
"""

from ursina import held_keys

from Scripts.lit_shader import lit_shader

ENABLED = True

# key: (uniform_name, is_vec4, step). Shift held = nudge down instead of up.
# control+<letter>, deliberately NOT any bare function key. Ursina's own
# window/editor chrome (development_mode=True, which this project runs with)
# reserves the WHOLE F-row: HotReloader (ursina/prefabs/hot_reloader.py) owns
# F5 (reload code)/F6 (textures)/F7 (models)/F8 (shaders)/F9 (toggle
# hotreload); window.make_editor_gui() (ursina/window.py) separately owns
# F10 (render mode)/F11 (fullscreen)/F12 (editor UI toggle). Confirmed live:
# binding this tool to F5 triggered Ursina's code-reload mid-frame, tearing
# down scene.entities while this module's shader-input update was iterating
# them and crashing the collision grid the next frame. control+letter avoids
# every reserved single key in both Ursina's chrome and this project's own
# gameplay/editor bindings (checked against main.py, player_controller.py,
# editor_core.py — n/m/,/. are unclaimed everywhere).
_KEYBINDS = {
    'control+n': ('rim_strength', False, 0.05),
    'control+m': ('spec_strength', False, 0.05),
    'control+,': ('warm_tint', True, 0.02),
    'control+.': ('cool_tint', True, 0.02),
}

_current = dict(lit_shader.default_input)


def _print_value(name):
    print(f'[dev_shader_tuning] {name} = {_current[name]}')


def handle_input(key):
    """Call from main.py's global input(key). No-op if ENABLED is False."""
    if not ENABLED or not held_keys['control']:
        return
    binding = _KEYBINDS.get('control+' + key)
    if binding is None:
        return
    name, is_vec4, step = binding
    if held_keys['shift']:
        step = -step
    if is_vec4:
        r, g, b, a = _current[name]
        _current[name] = (r + step, g + step, b + step, a)
    else:
        _current[name] = _current[name] + step
    # Shader.__setattr__ special-cases keys already in default_input: it
    # pushes the new value to every live entity using this shader.
    setattr(lit_shader, name, _current[name])
    _print_value(name)
