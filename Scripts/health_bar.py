from ursina import *
from Scripts.ui_theme import HEALTH_FULL, HEALTH_MID, HEALTH_LOW, HEALTH_BG, TEXT_PRIMARY

# v1.5 UI redesign: palette now lives in Scripts/ui_theme.py, shared with
# PlayerHUD/PauseMenu/EndScreen. Kept as local aliases so call sites below
# don't need to change.
BAR_COLOR_FULL  = HEALTH_FULL
BAR_COLOR_MID   = HEALTH_MID
BAR_COLOR_LOW   = HEALTH_LOW
BAR_BG_COLOR    = HEALTH_BG


class HealthBar(Entity):
    """World-space (is_3d=True) or screen-space health bar with a registry for O(1) iteration."""

    _registry: list = []   # populated by __init__, cleared by on_destroy; replaces scene.entities scan

    def __init__(self, max_value=100, value=100, parent=None,
                 position=(0, 0, 0), scale=(1, .1), is_3d=False, **kwargs):
        """Create a health bar; registers itself in HealthBar._registry."""
        super().__init__(
            parent=parent,
            position=position,
            scale=scale,
            **kwargs
        )
        self.max_value = max_value
        self.value = value
        self.is_3d = is_3d
        self.always_on_top = True
        self.billboard = bool(is_3d)

        self.bg = Entity(
            parent=self,
            model='quad',
            color=BAR_BG_COLOR,
            z=0.01,
            scale=(1, 1),
            origin=(-.5, .5),
            billboard=self.is_3d
        )

        self.bar = Entity(
            parent=self,
            model='quad',
            color=BAR_COLOR_FULL,
            z=0.02,
            scale=(1, 1),
            origin=(-.5, .5),
            billboard=self.is_3d
        )

        if self.is_3d:
            self.text = Text(
                parent=self,
                text=f"{int(self.value)}",
                position=(0, 0.5, 0),
                scale=10 / self.scale_x,
                color=TEXT_PRIMARY,
                billboard=True,
            )
        else:
            text_pos   = kwargs.get('text_position', (0, 0))
            text_scale = kwargs.get('text_scale', 2)
            # Screen-space text is parented to camera.ui so it stays fixed on screen.
            # camera.ui children are NOT destroyed by parent cascade — on_destroy() handles it.
            self.text = Text(
                parent=camera.ui,
                text=f"{int(self.value)}/{int(self.max_value)}",
                position=(
                    self.position.x + text_pos[0],
                    self.position.y + text_pos[1],
                ),
                scale=text_scale,
                color=TEXT_PRIMARY,
                z=-1,
            )

        HealthBar._registry.append(self)

    def update(self):
        """Resize bar and recolor by health ratio each frame."""
        health_ratio = self.value / self.max_value
        if self.is_3d:
            self.bar.scale_x = max(0, min(1, health_ratio))
            self.bar.scale_y = 1
            self.bg.scale_y  = 1
        else:
            self.bar.scale_x = max(0, min(1, health_ratio))
            self.bg.scale_y  = self.scale_y

        if health_ratio > 0.6:
            self.bar.color = BAR_COLOR_FULL
        elif health_ratio > 0.3:
            self.bar.color = BAR_COLOR_MID
        else:   
            self.bar.color = BAR_COLOR_LOW

        if self.is_3d:
            self.text.text = f"{int(self.value)}"
        else:
            self.text.text = f"{int(self.value)}/{int(self.max_value)}"

    def on_destroy(self):
        """Remove from registry and explicitly destroy children — Ursina's destroy()
        never cascades to parent= children (only loose_children), so bg/bar (parent=
        self for both is_3d and screen-space bars) and text (parent=self for is_3d,
        parent=camera.ui for screen-space) would otherwise leak as orphaned NodePaths."""
        if self in HealthBar._registry:
            HealthBar._registry.remove(self)
        if hasattr(self, 'bg') and self.bg:
            destroy(self.bg)
        if hasattr(self, 'bar') and self.bar:
            destroy(self.bar)
        if hasattr(self, 'text') and self.text:
            destroy(self.text)
