from ursina import *
from ursina.prefabs.editor_camera import EditorCamera
from panda3d.core import loadPrcFileData, AntialiasAttrib
import json
import os

loadPrcFileData('', 'model-cache-dir')

class LevelEditor(Entity):
    def __init__(self):
        super().__init__()
        self.blocks = []
        self.enemies = []
        self.filename = 'level.json'
        self.grid_snap = True
        self.current_texture = 'white_cube'
        self.current_mode = 'block'
        
        self.texture_button = Button(
            parent=camera.ui,
            text='Texture: White',
            scale=(.2, .05),
            position=(0, .4),
            on_click=self.toggle_texture,
            color=color.dark_gray,
            text_scale=1.2,
            z=-1
        )
        
        self.enemy_button = Button(
            parent=camera.ui,
            text='Mode: Block',
            scale=(.2, .05),
            position=(0, .35),
            on_click=self.toggle_mode,
            color=color.dark_gray,
            text_scale=1.2,
            z=-1
        )
        
        self.model_preview = Entity(
            model='cube',
            color=color.white33,
            texture=self.current_texture,
            visible=False,
            scale=(1,1,1))
        
        self.load_existing_level()

    def toggle_mode(self):
        self.current_mode = 'enemy' if self.current_mode == 'block' else 'block'
        self.enemy_button.text = f'Mode: {self.current_mode.capitalize()}'
        
        self.model_preview.scale = (1.5, 3, 1.5) if self.current_mode == 'enemy' else (1,1,1)
        self.model_preview.texture = self.current_texture if self.current_mode == 'block' else ''

    def toggle_texture(self):
        if self.current_mode == 'block':
            if self.current_texture == 'white_cube':
                self.current_texture = 'grass'
                self.texture_button.text = 'Texture: Grass'
            else:
                self.current_texture = 'white_cube'
                self.texture_button.text = 'Texture: White'
            self.model_preview.texture = self.current_texture

    def update_model_preview(self):
        if mouse.hovered_entity and mouse.hovered_entity.collider:
            preview_position = mouse.hovered_entity.position + mouse.normal
            if self.grid_snap:
                preview_position = [round(p) for p in preview_position]
            
            if self.current_mode == 'enemy':
                preview_position[1] += 1
                
            self.model_preview.position = preview_position
            self.model_preview.visible = True
        else:
            self.model_preview.visible = False

    def update(self):
        self.update_model_preview()

    def input(self, key):
        if key == 'g':
            self.grid_snap = not self.grid_snap
            
        if key == 's' and held_keys['control']:
            self.save_level()

        if key == 'left mouse down':
            if held_keys['shift']:
                if mouse.hovered_entity in self.blocks:
                    destroy(mouse.hovered_entity)
                    self.blocks.remove(mouse.hovered_entity)
                elif mouse.hovered_entity in self.enemies:
                    destroy(mouse.hovered_entity)
                    self.enemies.remove(mouse.hovered_entity)
            else:
                if mouse.hovered_entity:
                    position = mouse.hovered_entity.position + mouse.normal
                    if self.grid_snap:
                        position = [round(p) for p in position]
                        
                    if self.current_mode == 'enemy':
                        if not self.position_valid(position):
                            return
                            
                        new_entity = Entity(
                            model='cube',
                            color=color.red,
                            texture='white_cube',
                            scale=(1.5, 3, 1.5),
                            position=position,
                            collider='box',
                            origin_y=-0.5
                        )
                        self.enemies.append(new_entity)
                    else:
                        new_entity = Entity(
                            model='cube',
                            texture=self.current_texture,
                            collider='box',
                            position=position
                        )
                        self.blocks.append(new_entity)

    def position_valid(self, position):
        for y_offset in [0, 1]:
            check_pos = (position[0], position[1] + y_offset, position[2])
            if any(e.position == check_pos for e in self.blocks + self.enemies):
                return False
        return True

    def save_level(self):
        data = []
        for block in self.blocks:
            data.append({
                'type': 'block',
                'position': [block.x, block.y, block.z],
                'texture': block.texture.name
            })
        for enemy in self.enemies:
            data.append({
                'type': 'enemy',
                'position': [enemy.x, enemy.y, enemy.z]
            })

        seen = set()
        deduped = []
        for item in data:
            key = (item['type'], tuple(item['position']))
            if key not in seen:
                seen.add(key)
                deduped.append(item)
        data = deduped

        with open(self.filename, 'w') as f:
            json.dump(data, f, indent=4)
        print(f'Saved level to {self.filename} ({len(data)} entries)')

    def load_existing_level(self):
        for e in self.blocks + self.enemies:
            destroy(e)
        self.blocks.clear()
        self.enemies.clear()

        try:
            with open(self.filename, 'r') as f:
                entities = json.load(f)
                for entity_data in entities:
                    if entity_data.get('type') == 'enemy':
                        new_entity = Entity(
                            model='cube',
                            color=color.red,
                            scale=(1.5, 3, 1.5),
                            position=entity_data['position'],
                            collider='box',
                            origin_y=-0.5
                        )
                        self.enemies.append(new_entity)
                    else:
                        new_entity = Entity(
                            model='cube',
                            texture=entity_data.get('texture', 'white_cube'),
                            position=entity_data['position'],
                            collider='box'
                        )
                        self.blocks.append(new_entity)
        except FileNotFoundError:
            print("No level file found")

if __name__ == '__main__':
    loadPrcFileData('', 'framebuffer-multisample 1\nmultisamples 4')
    app = Ursina(title="Level Editor")
    def _patch_shaders_to_glsl120():
        from ursina.shaders.unlit_shader import unlit_shader as _us
        from ursina.shaders.unlit_with_fog_shader import unlit_with_fog_shader as _ufs
        _us.vertex = (
            '#version 120\n'
            'uniform mat4 p3d_ModelViewProjectionMatrix;\n'
            'uniform mat4 p3d_ModelViewMatrix;\n'
            'uniform mat4 p3d_ModelMatrix;\n'
            'attribute vec4 p3d_Vertex;\n'
            'attribute vec2 p3d_MultiTexCoord0;\n'
            'varying vec2 uvs;\n'
            'uniform vec2 texture_scale;\n'
            'uniform vec2 texture_offset;\n'
            'attribute vec4 p3d_Color;\n'
            'varying vec4 vertex_color;\n'
            'void main() {\n'
            '    gl_Position = p3d_ModelViewProjectionMatrix * p3d_Vertex;\n'
            '    uvs = (p3d_MultiTexCoord0 * texture_scale) + texture_offset;\n'
            '    vertex_color = p3d_Color;\n'
            '}\n'
        )
        _us.fragment = (
            '#version 120\n'
            'uniform sampler2D p3d_Texture0;\n'
            'uniform vec4 p3d_ColorScale;\n'
            'varying vec2 uvs;\n'
            'varying vec4 vertex_color;\n'
            'void main() {\n'
            '    gl_FragColor = texture2D(p3d_Texture0, uvs) * p3d_ColorScale * vertex_color;\n'
            '}\n'
        )
        _us.compiled = False
        _ufs.vertex = (
            '#version 120\n'
            'uniform mat4 p3d_ModelViewProjectionMatrix;\n'
            'uniform mat4 p3d_ModelViewMatrix;\n'
            'uniform mat4 p3d_ModelMatrix;\n'
            'attribute vec4 p3d_Vertex;\n'
            'attribute vec2 p3d_MultiTexCoord0;\n'
            'varying vec2 uvs;\n'
            'uniform vec2 texture_scale;\n'
            'uniform vec2 texture_offset;\n'
            'attribute vec4 p3d_Color;\n'
            'varying vec4 vertex_color;\n'
            'varying vec3 vertex_world_position;\n'
            'void main() {\n'
            '    gl_Position = p3d_ModelViewProjectionMatrix * p3d_Vertex;\n'
            '    uvs = (p3d_MultiTexCoord0 * texture_scale) + texture_offset;\n'
            '    vertex_color = p3d_Color;\n'
            '    vertex_world_position = (p3d_ModelMatrix * p3d_Vertex).xyz;\n'
            '}\n'
        )
        _ufs.fragment = (
            '#version 120\n'
            'uniform sampler2D p3d_Texture0;\n'
            'uniform vec4 p3d_ColorScale;\n'
            'varying vec2 uvs;\n'
            'varying vec4 vertex_color;\n'
            'varying vec3 vertex_world_position;\n'
            'uniform vec3 camera_world_position;\n'
            'uniform vec4 fog_color;\n'
            'uniform float fog_start;\n'
            'uniform float fog_end;\n'
            'void main() {\n'
            '    vec4 fragColor = texture2D(p3d_Texture0, uvs) * p3d_ColorScale * vertex_color;\n'
            '    float distance_to_camera = length(vertex_world_position.xyz - camera_world_position);\n'
            '    float fog_length = fog_end - fog_start;\n'
            '    float t = clamp(distance_to_camera / fog_length, 0.0, 1.0);\n'
            '    fragColor.rgb = mix(fragColor.rgb, fog_color.rgb, t * fog_color.a);\n'
            '    gl_FragColor = fragColor;\n'
            '}\n'
        )
        _ufs.compiled = False
    _patch_shaders_to_glsl120()
    window.color = color.rgb(50, 50, 60)
    render.setAntialias(AntialiasAttrib.MAuto)
    render2d.setAntialias(AntialiasAttrib.MAuto)
    window.title = 'Level Editor'
    window.exit_button.visible = True
    window.fps_counter.enabled = True
    window.borderless = False
    window.size = (1280, 720)
    
    ground = Entity(
        model='plane',
        collider='box',
        y=-0.5,
        scale=(100,1,100),
        texture='grass'
    )
    
    editor = LevelEditor()
    EditorCamera()
    
    Text(text="Controls:\nLeft Click: Place\nShift+Left Click: Remove\nCtrl+S: Save\nG: Toggle Grid Snap",
        parent=camera.ui,
        position=(-0.8, 0.33),
        origin=(-.5, -.5),
        scale=0.9,
        z=-1)
    
    app.run()