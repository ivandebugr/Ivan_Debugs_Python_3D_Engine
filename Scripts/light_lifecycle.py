"""
light_lifecycle.py — correct teardown for Ursina lights.

Why this exists
---------------
`destroy(light)` LEAKS. Ursina's DirectionalLight.__init__ does:

    node_path = self.attachNewNode(self._light)   # local — never stored
    render.setLight(node_path)

`render.setLight()` registers that NodePath on render's LightAttrib, which holds
its own reference. Ursina keeps no handle to the NodePath, so nothing ever calls
the matching `clear_light()`. destroy() empties the *Entity's* NodePath, which
detaches the light from the scene graph but leaves the LightAttrib entry intact —
an orphan that renders as "-PandaNode/directional_light" (the "-" meaning its
parent is gone) and still counts as lit.

Measured on this Mac (GL 2.1 Metal), one DirectionalLight per menu cycle:

    cycle 0: after create=1 after destroy=1
    cycle 1: after create=2 after destroy=2
    cycle 2: after create=3 after destroy=3   <- monotonic, never released

Every leaked shadow-casting light also holds its 1024x1024 depth FBO forever.
This is the blocker tools/shadow_fbo_probe.py identified as the real risk to
v1.7 shadows (the GL 2.1 driver itself turned out to be fine).

The teardown contract
---------------------
Three steps, in this order:

  1. `render.clear_light(np)` — MUST be passed the light's NodePath. Two traps:
     - `clear_light()` with NO argument clears the whole LightAttrib (every
       light, including ones you did not create). Not a targeted release.
     - The NodePath must be the one Panda has, not a rewrapped `self._light`.
       `render.get_attrib(LightAttrib).get_on_light(i).node() is light._light`
       is **False** — Panda returns a fresh Python wrapper around the same C++
       node, so identity comparison against `_light` silently matches nothing.
       Recovering the NodePath by traversing the Entity's children sidesteps
       this entirely.
  2. `np.remove_node()` — drops the now-unlit node from the graph.
  3. `set_shadow_caster(False)` — releases the depth FBO. Not implied by
     clear_light(); a still-caster light keeps its buffer.

Then the normal `destroy()` on the Entity.

Verified: on_lights returns to 0 and stays flat across repeated cycles, and the
release is order-independent — destroying the FIRST of two live lights leaves
exactly the second one lit (the naive "clear the last attrib entry" shortcut
gets this wrong).
"""

from ursina import destroy

# Matches any Panda light node parented under the Entity. Ursina's light classes
# all attach exactly one, via attachNewNode(self._light).
#
# MUST be `+Light`, not `+LightNode`. Panda splits lights across two node
# hierarchies and LightNode is only ONE of them:
#     AmbientLight    <- LightNode     <- Light
#     DirectionalLight <- LightLensNode <- Light      (also PointLight, Spotlight)
# `+LightNode` therefore matches ambient lights ONLY and silently misses the sun —
# i.e. it would leak precisely the light this module exists to release. `Light` is
# the common base; verified to match exactly 1 node on all four Ursina light types.
_LIGHT_NODE_PATTERN = '**/+Light'


def destroy_light(light_entity):
    """Fully release an Ursina light: LightAttrib entry, node, shadow FBO, Entity.

    Use this instead of `destroy()` for ANY light — destroy() alone leaks the
    LightAttrib entry and the shadow buffer (see module docstring).

    Safe to call on an already-destroyed or light-less entity: a light whose
    NodePath is gone yields no matches and is simply destroy()ed.
    """
    # find_all_matches on the ENTITY (not on render) gives us the exact NodePath
    # objects Panda holds, and only this light's — so interleaved lights release
    # independently of creation order.
    for np in light_entity.find_all_matches(_LIGHT_NODE_PATTERN):
        render.clear_light(np)   # noqa: F821 — `render` is a Panda3D builtin, injected by ShowBase
        np.remove_node()

    # Release the shadow depth buffer. clear_light() does not do this, and Ursina's
    # DirectionalLight turns shadows ON in __init__, so every one of them has a
    # buffer to release. Guarded: AmbientLight has no set_shadow_caster().
    light = getattr(light_entity, '_light', None)
    if light is not None and hasattr(light, 'set_shadow_caster'):
        light.set_shadow_caster(False)

    destroy(light_entity)


def is_light(entity) -> bool:
    """True if `entity` is an Ursina light and must be torn down via destroy_light().

    Duck-typed on `_light` rather than isinstance(Light) so it covers every
    Ursina light subclass without importing them all — and so a scene sweep can
    route lights correctly without knowing which kind it found.
    """
    return getattr(entity, '_light', None) is not None
