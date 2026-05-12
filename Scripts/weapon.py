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
            speed=50,
            player=self.player  # FIXED: bug-1 - pass player for ignore list
        )

        self.animate_position(self.original_pos + Vec3(0, 0, -0.2), duration=0.05)
        self.animate_position(self.original_pos, delay=0.05, duration=0.15, curve=curve.out_quad)

        self.last_shot = time.time()

class PlayerBullet(Entity):
    MAX_LIFETIME = 2.0  # FIXED: bug-1 - class constant replaces double lifetime check

    def __init__(self, position, direction, speed=50, player=None):  # FIXED: bug-1 - accept player for ignore list
        super().__init__(
            model='cube',
            color=color.cyan,
            scale=(0.1, 0.1, 0.3),
            position=position,
            collider='box'
        )
        self.speed = speed
        self.direction = direction.normalized()
        self.player = player  # FIXED: bug-1 - store player for raycast ignore
        self.rotation_z = math.degrees(math.atan2(-self.direction.y, self.direction.length()))
        self.spawn_time = time.time()

    def update(self):
        from Scripts.enemy import Enemy  # FIXED: bug-1 - lazy import to break circular import
        prev = Vec3(self.position)
        dist = (self.direction * self.speed * time.dt).length() + 0.2
        ignore = [self, self.player] if self.player else [self]
        hit = raycast(prev, self.direction, distance=dist, ignore=ignore, debug=False)  # FIXED: bug-1 - swept raycast before move
        if hit.hit:
            if isinstance(hit.entity, Enemy):  # FIXED: bug-1 - isinstance instead of name check
                hit.entity.health -= 25
            self._destroy()
            return
        self.position += self.direction * self.speed * time.dt
        self.look_at(self.position + self.direction)
        if time.time() - self.spawn_time > self.MAX_LIFETIME:  # FIXED: bug-1 - single lifetime check
            self._destroy()
            return

    def _destroy(self):  # FIXED: bug-1 - safe destroy helper
        try:
            from Scripts.collision_system import remove_from_collision_layer
            remove_from_collision_layer(self)
        except Exception:
            pass
        destroy(self)