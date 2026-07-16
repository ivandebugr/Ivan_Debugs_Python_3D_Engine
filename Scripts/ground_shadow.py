from ursina import Entity, Vec3, raycast, destroy
from Scripts.asset_resolve import resolve_texture as _resolve_texture

_SHADOW_RAY_OFFSET = Vec3(0, 0.1, 0)   # cast from slightly above the owner to avoid self-hits
_SHADOW_MAX_DISTANCE = 20
_SHADOW_Y_BIAS = 0.02   # lift above ground to avoid z-fighting with floor geometry


class GroundShadow:
    """A flat quad that tracks directly below `owner`, following ground height.

    Not an Entity subclass — a plain helper holding one quad Entity, updated from
    the owner's own update() loop (Player.update / Enemy.update) so it doesn't need
    its own per-frame scan of scene.entities. Hides itself when no ground is found
    within _SHADOW_MAX_DISTANCE (falling, off the level bounds).
    """

    def __init__(self, owner, scale=(1.0, 1.0), ignore=None):
        self.owner = owner
        self.ignore = ignore or [owner]
        self.quad = Entity(
            model='quad',
            texture=_resolve_texture('blob_shadow'),
            color=(0, 0, 0, 0.4),
            rotation_x=90,
            scale=scale,
            always_on_top=False,
        )

    def update(self):
        hit = raycast(
            self.owner.world_position + _SHADOW_RAY_OFFSET,
            Vec3(0, -1, 0),
            distance=_SHADOW_MAX_DISTANCE,
            ignore=self.ignore,
        )
        if not hit.hit:
            self.quad.enabled = False
            return
        self.quad.enabled = True
        self.quad.position = hit.world_point + Vec3(0, _SHADOW_Y_BIAS, 0)

    def destroy(self):
        if self.quad:
            destroy(self.quad)
            self.quad = None
