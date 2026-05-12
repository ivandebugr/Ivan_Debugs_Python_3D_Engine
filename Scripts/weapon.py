from ursina import *
from ursina.prefabs.health_bar import HealthBar

class Weapon(Entity):
    def __init__(self, player, model='cube', texture='white_cube', scale=(0.3, 0.2, 1), position=(0.5, -0.5, 1), **kwargs):
        super().__init__(
            parent=camera,
            model=model,
            texture=texture,
            scale=scale,
            position=position,
            rotation=(0, 0, 0),
            **kwargs
        )
        self.player = player
        self.original_pos = Vec3(position)
        self.cooldown = 0.15 
        self.last_shot = 0
        self.damage = 25

        self.crosshair = Entity(
            parent=camera.ui,
            model='quad',
            texture='circle',
            color=color.red,
            scale=(0.02, 0.02),
            z=-1
        )

    def shoot(self):
        if time.time() - self.last_shot < self.cooldown:
            return

        PlayerBullet(
            position=self.world_position + camera.forward,
            direction=camera.forward,
            speed=50
        )

        self.animate_position(self.original_pos + Vec3(0, 0, -0.2), duration=0.05)
        self.animate_position(self.original_pos, delay=0.05, duration=0.15, curve=curve.out_quad)

        self.last_shot = time.time()

class PlayerBullet(Entity):
    def __init__(self, position, direction, speed=50):
        super().__init__(
            model='cube',
            color=color.cyan,
            scale=(0.1, 0.1, 0.3),
            position=position,
            collider='box'
        )
        self.speed = speed
        self.direction = direction.normalized()
        self.lifetime = 2
        self.rotation_z = math.degrees(math.atan2(-self.direction.y, self.direction.length()))
        self.spawn_time = time.time()
        
    def update(self):
        self.position += self.direction * self.speed * time.dt
        self.lifetime -= time.dt
        if self.lifetime <= 0:
            destroy(self)

        self.look_at(self.position + self.direction)
        
        if time.time() - self.spawn_time > self.lifetime:
            destroy(self)
            
        hit = self.intersects()
        if hit and hit.entity.name == 'enemy':
            hit.entity.health -= 25
            if hit.entity.health <= 0:
                destroy(hit.entity)
            destroy(self)