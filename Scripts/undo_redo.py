from collections import deque


class Command:
    def execute(self): raise NotImplementedError
    def undo(self):    raise NotImplementedError


class PlaceEntityCommand(Command):
    def __init__(self, editor, entity):
        self.editor = editor
        self.entity = entity

    def execute(self):
        pass  # entity already placed before command is recorded

    def undo(self):
        if self.entity in self.editor.blocks:
            self.editor.blocks.remove(self.entity)
        elif self.entity in self.editor.enemies:
            self.editor.enemies.remove(self.entity)
        from ursina import destroy
        destroy(self.entity)
        self.editor._refresh_hierarchy()


class DeleteEntityCommand(Command):
    def __init__(self, editor, entity, entity_data):
        self.editor      = editor
        self.entity_data = entity_data
        self.entity      = entity
        self._restored   = None

    def execute(self):
        if self.entity in self.editor.blocks:
            self.editor.blocks.remove(self.entity)
        if self.entity in self.editor.enemies:
            self.editor.enemies.remove(self.entity)
        from ursina import destroy
        destroy(self.entity)
        self.editor._refresh_hierarchy()

    def undo(self):
        from ursina import Entity, color, destroy
        e = Entity(
            model='cube',
            texture=self.entity_data.get('texture', 'white_cube'),
            position=self.entity_data['position'],
            color=self.entity_data.get('color', color.white),
            rotation=self.entity_data.get('rotation', (0, 0, 0)),
            collider='box'
        )
        e.enemy_hp   = self.entity_data.get('enemy_hp', 100)
        e.enemy_type = self.entity_data.get('enemy_type', 'default')
        e._original_color = self.entity_data.get('color', color.white)
        target_list = self.editor.enemies if self.entity_data.get('is_enemy') else self.editor.blocks
        target_list.append(e)
        self._restored = e
        self.editor._refresh_hierarchy()


class MoveEntityCommand(Command):
    def __init__(self, entity, old_pos, new_pos):
        self.entity  = entity
        self.old_pos = old_pos
        self.new_pos = new_pos

    def execute(self): self.entity.position = self.new_pos
    def undo(self):    self.entity.position = self.old_pos


class ChangeTextureCommand(Command):
    def __init__(self, entities, old_textures, new_texture):
        self.entities     = entities
        self.old_textures = old_textures
        self.new_texture  = new_texture

    def execute(self):
        for e in self.entities:
            e.texture = self.new_texture

    def undo(self):
        for e, t in zip(self.entities, self.old_textures):
            e.texture = t


class ChangeColourCommand(Command):
    def __init__(self, entities, old_colours, new_colour):
        self.entities    = entities
        self.old_colours = old_colours
        self.new_colour  = new_colour

    def execute(self):
        for e in self.entities:
            e.color = self.new_colour

    def undo(self):
        for e, c in zip(self.entities, self.old_colours):
            e.color = c


class ChangePropertyCommand(Command):
    def __init__(self, entity, prop, old_val, new_val):
        self.entity  = entity
        self.prop    = prop
        self.old_val = old_val
        self.new_val = new_val

    def execute(self): setattr(self.entity, self.prop, self.new_val)
    def undo(self):    setattr(self.entity, self.prop, self.old_val)


class UndoRedoStack:
    def __init__(self, max_depth=50):
        self._undo = deque(maxlen=max_depth)
        self._redo = deque(maxlen=max_depth)

    def push(self, command):
        self._undo.append(command)
        self._redo.clear()

    def undo(self):
        if not self._undo:
            return
        cmd = self._undo.pop()
        cmd.undo()
        self._redo.append(cmd)

    def redo(self):
        if not self._redo:
            return
        cmd = self._redo.pop()
        cmd.execute()
        self._undo.append(cmd)

    def clear(self):
        self._undo.clear()
        self._redo.clear()
