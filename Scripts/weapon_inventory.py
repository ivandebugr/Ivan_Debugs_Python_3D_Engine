from ursina import Vec3

SWITCH_ANIM_DURATION = 0.2
_HIDDEN_OFFSET = Vec3(0, -0.3, 0)   # slide-down amount for the hide/show animation


class WeaponInventory:
    """Owns a player's weapon slots, active-weapon switching, and slide animation.

    Replaces `self.weapon = Weapon(self)` in Player.__init__ (v1.5 Step 7);
    Player now gives it a Pistol instance (Step 8) as the slot-0 occupant.
    switch_to() hides the outgoing weapon (visible=False) and slides the incoming
    one in — weapons are only destroyed via destroy_all(), called from
    _clear_gameplay_entities() during full scene teardown.
    """

    def __init__(self, player, max_slots=3):
        self.player = player
        self.slots = [None] * max_slots
        self.active_slot = 0

    @property
    def active_weapon(self):
        return self.slots[self.active_slot]

    def give(self, weapon, slot):
        """Assign `weapon` to `slot`, hidden until switched to."""
        self.slots[slot] = weapon
        weapon.visible = False

    def switch_to(self, slot):
        """Make `slot` the active weapon; no-op if empty or already active."""
        if self.slots[slot] is None:
            return
        if slot == self.active_slot and self.active_weapon.visible:
            return
        if self.active_weapon:
            self.active_weapon.visible = False
        self.active_slot = slot
        weapon = self.active_weapon
        weapon.visible = True
        self._play_switch_animation(weapon)

    def next_weapon(self):
        """Advance to the next occupied slot, wrapping around."""
        self._step_weapon(1)

    def prev_weapon(self):
        """Retreat to the previous occupied slot, wrapping around."""
        self._step_weapon(-1)

    def _step_weapon(self, direction):
        occupied = [i for i, w in enumerate(self.slots) if w is not None]
        if len(occupied) <= 1:
            return
        pos = occupied.index(self.active_slot)
        next_slot = occupied[(pos + direction) % len(occupied)]
        self.switch_to(next_slot)

    def _play_switch_animation(self, weapon):
        """0.2s slide-in: weapon starts offset below its rest position and animates up."""
        weapon.position = weapon.original_pos + _HIDDEN_OFFSET
        weapon.animate_position(weapon.original_pos, duration=SWITCH_ANIM_DURATION)

    def destroy_all(self):
        """Destroy every weapon in every slot. Only called during full scene teardown."""
        from ursina import destroy
        for i, weapon in enumerate(self.slots):
            if weapon is not None:
                destroy(weapon)
                self.slots[i] = None
