from ursina import *

# TUNE: named color constants — swap these to experiment with bar appearance
BAR_COLOR_FULL  = color.rgb(50, 200, 50)
BAR_COLOR_MID   = color.rgb(220, 150, 0)
BAR_COLOR_LOW   = color.rgb(200, 50, 50)
BAR_BG_COLOR    = color.dark_gray


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
                color=color.white,
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
                color=color.white,
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
        """Remove from registry and explicitly destroy camera.ui text (won't cascade)."""
        if self in HealthBar._registry:
            HealthBar._registry.remove(self)
        if not self.is_3d and hasattr(self, 'text') and self.text:
            destroy(self.text)
