from ursina import *
from Scripts.health_bar import HealthBar
import math

class EnemyBullet(Entity):
    def __init__(self, position, target, player, enemy, speed=10):
        super().__init__(
            model='cube',
            color=color.yellow,
            scale=(0.2, 0.2, 0.5),
            position=position,
        )
        self.speed = speed
        self.direction = (target - position).normalized()
        self.player = player
        self.enemy = enemy
        self.look_at(self.position + self.direction)
        self.travel_distance = 0
        
    def update(self):
        move_amount = self.direction * self.speed * time.dt
        self.travel_distance += self.speed * time.dt
        
        hit = raycast(
            self.world_position,
            self.direction,
            distance=1,
            ignore=[self, self.enemy],
            debug=False
        )
        
        if hit.hit:
            if hit.entity == self.player:
                self.player.health -= 25
            destroy(self)
            return
            
        self.position += move_amount
        
        if distance(self.position, (0,0,0)) > 500:
            destroy(self)

class Enemy(Entity):
    def __init__(self, spawn_position, player):
        super().__init__(
            model='cube',
            color=color.red,
            scale=(1.5, 3, 1.5),
            position=spawn_position,
            collider='box'
        )
        self.health = 100
        self.max_health = 100
        self.player = player
        self.shoot_cooldown = 1.0
        self.can_shoot = True
        self.attack_range = 30
        self.detection_range = 100
        
        self.health_bar = HealthBar(
            parent=self,
            max_value=self.max_health,
            value=self.health,
            position=(0, 2, 0), 
            scale=(2, 0.3),
            is_3d=True,
            bar_origin=(0, 0)
        )

    def update(self):
        if not self.player:
            return
        player_dist = distance(self.position, self.player.position)
        
        if player_dist <= self.detection_range:
            self.look_at(self.player.position)
            self.rotation_x = 0
            self.rotation_z = 0
            
            if player_dist <= self.attack_range and self.can_shoot:
                self.shoot()
                
        self.health_bar.world_position = self.world_position + Vec3(0, 2, 0)
        self.health_bar.value = self.health
        
        if self.health <= 0:
            destroy(self.health_bar)
            destroy(self)
            
        if hasattr(self, 'health_bar'):
            self.health_bar.enabled = player_dist < 150 and not self.is_occluded()
            self.health_bar.value = self.health
        
        if self.health <= 0:
            if hasattr(self, 'health_bar'):
                destroy(self.health_bar)
            destroy(self)
            return

    def is_occluded(self):
        hit = raycast(
            self.position + Vec3(0, 1, 0),
            (self.player.position - self.position).normalized(),
            distance=distance(self.position, self.player.position),
            ignore=[self],
            debug=False
        )
        return hit.hit and hit.entity.name != 'player'

    def shoot(self):
        if not self.can_shoot:
            return
            
        target_pos = self.player.position + Vec3(0, 1, 0)
        EnemyBullet(
            position=self.position + Vec3(0, 1.5, 0),
            target=target_pos,
            player=self.player,
            enemy=self, 
            speed=10
        )
            
        self.can_shoot = False
        invoke(setattr, self, 'can_shoot', True, delay=self.shoot_cooldown)
