from ursina import *
from ursina.prefabs.first_person_controller import FirstPersonController
from Scripts.weapon import Weapon
from Scripts.health_bar import HealthBar

class Player(FirstPersonController):
    def __init__(self, position=(0,0,0), **kwargs):
        super().__init__(
            model=None,
            color=color.azure,
            collider='box',
            position=position,
            **kwargs
        )
        self.jump_force = 18
        self.gravity = 45
        self.speed = 8
        self.vertical_speed = 0
        self.mouse_sensitivity = Vec2(4000, 4000)

        self.collider = BoxCollider(self, center=(0, 0, 0), size=(0.8, 2, 0.8))
        self.skin_width = 0.1
        self.weapon = Weapon(self)

        self.debug_lines = []
        self.create_collider_visualization()

        self.raycast_start_positions = []
        self.raycast_directions = []
        self.generate_raycast_points()

        self.draw_raycasts = True 
        self.show_colliders = False
        self.debug_rays = []
        self.draw_raycast_visuals()

        mouse.locked = True
        
        self.health = 100
        self.max_health = 100
        
        self.health_bar = HealthBar(
            parent=camera.ui,
            max_value=self.max_health,
            value=self.health,
            position=(-0.85, -0.39), 
            scale=(0.4, 0.05),
            text_position=(0, 1.5), 
            text_scale=1.5,
            is_3d=False,
            bar_origin=(0, 0)
        )

    def create_collider_visualization(self):
        corners = [
            Vec3(-0.4, 0, -0.4), Vec3(0.4, 0, -0.4),
            Vec3(0.4, 0, 0.4), Vec3(-0.4, 0, 0.4),
            Vec3(-0.4, 2, -0.4), Vec3(0.4, 2, -0.4),
            Vec3(0.4, 2, 0.4), Vec3(-0.4, 2, 0.4),
        ]
        edges = [
            (0, 1), (1, 2), (2, 3), (3, 0),
            (4, 5), (5, 6), (6, 7), (7, 4),
            (0, 4), (1, 5), (2, 6), (3, 7)
        ]
        for edge in edges:
            line = Entity(
                model=Mesh(vertices=[corners[edge[0]], corners[edge[1]]], mode='line'),
                color=color.red,
                parent=self,
                eternal=True,
                enabled=False
            )
            self.debug_lines.append(line)

    def generate_raycast_points(self):
        collider_half_width = 0.5
        collider_height = 2.0
        collider_bottom = 0
        collider_top = 2.1
        rows = 5
        columns = 5

        self.raycast_start_positions = []
        self.raycast_directions = []

        main_sides = [
            ("front", Vec3(0, 0, collider_half_width), Vec3(0, 0, 1)),
            ("back", Vec3(0, 0, -collider_half_width), Vec3(0, 0, -1)),
            ("left", Vec3(-collider_half_width, 0, 0), Vec3(-1, 0, 0)),
            ("right", Vec3(collider_half_width, 0, 0), Vec3(1, 0, 0))
        ]

        diagonal_sides = [
            ("front_right", Vec3(collider_half_width, 0, collider_half_width), Vec3(1, 0, 1).normalized()),
            ("front_left", Vec3(-collider_half_width, 0, collider_half_width), Vec3(-1, 0, 1).normalized()),
            ("back_right", Vec3(collider_half_width, 0, -collider_half_width), Vec3(1, 0, -1).normalized()),
            ("back_left", Vec3(-collider_half_width, 0, -collider_half_width), Vec3(-1, 0, -1).normalized()),
        ]

        for side_name, offset, direction in main_sides + diagonal_sides:
            for row in range(rows):
                y = collider_bottom + (collider_top - collider_bottom) * (row / (rows - 1))
                
                if "diagonal" in side_name:
                    local_pos = Vec3(offset.x, y, offset.z)
                    self.raycast_start_positions.append(local_pos)
                    self.raycast_directions.append(direction)
                else:
                    for col in range(columns):
                        if "front" in side_name or "back" in side_name:
                            x = -collider_half_width + (2 * collider_half_width) * (col / (columns - 1))
                            local_pos = Vec3(x, y, offset.z)
                        else:
                            z = -collider_half_width + (2 * collider_half_width) * (col / (columns - 1))
                            local_pos = Vec3(offset.x, y, z)
                        
                        self.raycast_start_positions.append(local_pos)
                        self.raycast_directions.append(direction)

        self.ceiling_raycast_position = Vec3(0, collider_top, 0)
        self.ceiling_raycast_direction = Vec3(0, 1, 0)

    def draw_raycast_visuals(self):
        if self.draw_raycasts:
            for local_pos, direction in zip(self.raycast_start_positions, self.raycast_directions):
                line = Entity(
                    parent=self,
                    model=Mesh(
                        vertices=[local_pos, local_pos + direction * 0.6],
                        mode='line',
                        static=False
                    ),
                    color=color.cyan,
                    eternal=True,
                    enabled=self.show_colliders
                )
                self.debug_rays.append(line)

            ceiling_line = Entity(
                parent=self,
                model=Mesh(
                    vertices=[self.ceiling_raycast_position, self.ceiling_raycast_position + self.ceiling_raycast_direction * 0.6],
                    mode='line',
                    static=False
                ),
                color=color.yellow,
                eternal=True,
                enabled=self.show_colliders
            )
            self.debug_rays.append(ceiling_line)

    def update(self):
        if mouse.locked:
            self.rotation_y += mouse.velocity[0] * self.mouse_sensitivity[0] * time.dt
            camera.rotation_x -= mouse.velocity[1] * self.mouse_sensitivity[1] * time.dt
            camera.rotation_x = clamp(camera.rotation_x, -90, 90)

        ground_hit = raycast(
            self.position + (0, -0.1, 0),
            (0, -1),
            distance=1,
            ignore=[self]
        )
        self.grounded = ground_hit.hit

        if not self.grounded:
            self.vertical_speed -= self.gravity * time.dt
        else:
            self.vertical_speed = 0
            if ground_hit.hit:
                ground_top = ground_hit.world_point.y
                player_bottom = self.y - 1
                if player_bottom < ground_top:
                    self.y += ground_top - player_bottom

        self.y += self.vertical_speed * time.dt

        ceiling_hit = raycast(
            self.position + self.ceiling_raycast_position,
            self.ceiling_raycast_direction,
            distance=0.3,
            ignore=[self],
            debug=False
        )

        if ceiling_hit.hit:
            if self.vertical_speed > 0:
                penetration = 0.3 - ceiling_hit.distance
                self.vertical_speed = 0
                self.y -= penetration 

        self.handle_horizontal_movement()  # FIXED: bug-2, bug-3 - use swept collision check
                    
        self.health_bar.value = self.health
        
        if hasattr(self, 'health_bar') and self.health_bar:
            self.health_bar.value = self.health
            if self.health <= 0:
                self.position = (0, 2, 0)
                self.health = 100

    def input(self, key):
        if key == 'left mouse down' and self.grounded:
            self.weapon.shoot()
        if key == 'space' and self.grounded:
            self.vertical_speed = self.jump_force
        if key == 'r':
            self.position = (0, 2, 0)
        if key == 'c':
            self.show_colliders = not self.show_colliders
            self.toggle_collider_visualization()
            self.toggle_raycast_visualization()
        super().input(key)
        
    def toggle_collider_visualization(self):
        for line in self.debug_lines:
            line.enabled = self.show_colliders

    def toggle_raycast_visualization(self):
        for ray in self.debug_rays:
            ray.enabled = self.show_colliders

    def _ignored_entities(self):  # FIXED: bug-2 - pass instances not classes to raycast ignore
        from Scripts.weapon import PlayerBullet
        from Scripts.enemy import EnemyBullet
        return [self] + [e for e in scene.entities if isinstance(e, (PlayerBullet, EnemyBullet))]

    def _swept_blocked(self, origin, direction, distance):  # FIXED: bug-3 - pre-move sweep at multiple heights
        ignore = self._ignored_entities()
        for offset in [Vec3(0, 0.1, 0), Vec3(0, 1, 0), Vec3(0, 1.8, 0)]:
            if raycast(origin + offset, direction, distance=distance + self.skin_width,
                       ignore=ignore, debug=False).hit:
                return True
        return False

    def handle_horizontal_movement(self):  # FIXED: bug-3 - check before move, axis separation on block
        raw = Vec3(self.forward * (held_keys['w'] - held_keys['s']) +
                   self.right * (held_keys['d'] - held_keys['a']))
        if not raw.length_squared():
            return
        d = raw.normalized()
        move = d * self.speed * time.dt
        if not self._swept_blocked(self.position, d, move.length()):
            self.position += move
            return
        x = Vec3(move.x, 0, 0)
        z = Vec3(0, 0, move.z)
        if x.length() and not self._swept_blocked(self.position, x.normalized(), abs(move.x)):
            self.position += x
        elif z.length() and not self._swept_blocked(self.position, z.normalized(), abs(move.z)):
            self.position += z