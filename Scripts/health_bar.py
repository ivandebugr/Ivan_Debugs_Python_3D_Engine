from ursina import *

class HealthBar(Entity):
    def __init__(self, max_value=100, value=100, parent=None, position=(0,0,0), scale=(1,.1), is_3d=False, **kwargs):
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
        self.billboard = True if is_3d else False
        self.eternal = True

        self.bg = Entity(
            parent=self,
            model='quad',
            color=color.dark_gray,
            z=0.01,
            scale=(1, 1),
            origin=(-.5,.5),
            eternal=True,
            billboard=self.is_3d
        )
            
        self.bar = Entity(
            parent=self,
            model='quad',
            color=color.green,
            z=0.02,
            scale=(1, 1),
            origin=(-.5,.5),
            eternal=True,
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
                eternal=True
            )
        else:
            text_pos = kwargs.get('text_position', (0, 0))
            text_scale = kwargs.get('text_scale', 2)
            # Parented to camera.ui so text position is in screen space.
            # Callers must explicitly destroy(health_bar.text) before destroy(health_bar)
            # because Ursina's destroy() does not cascade to camera.ui children.
            self.text = Text(
                parent=camera.ui,
                text=f"{int(self.value)}/{int(self.max_value)}",
                position=(
                    self.position.x + text_pos[0],
                    self.position.y + text_pos[1]
                ),
                scale=text_scale,
                color=color.white,
                eternal=True,
                z=-1
            )

    def update(self):
        health_ratio = self.value / self.max_value
        if self.is_3d:
            self.bar.scale_x = max(0, min(1, health_ratio)) * self.scale_x
            self.bar.scale_y = self.scale_y
            self.bg.scale_y = self.scale_y
        else:
            self.bar.scale_x = max(0, min(1, health_ratio))
            self.bg.scale_y = self.scale_y
        
        if health_ratio > 0.6:
            self.bar.color = color.green
        elif health_ratio > 0.3:
            self.bar.color = color.orange
        else:
            self.bar.color = color.red

        if self.is_3d:
            self.text.text = f"{int(self.value)}"
        else:
            self.text.text = f"{int(self.value)}/{int(self.max_value)}"
            
