class Game:
    """Singleton state machine for the game session.

    Import with: from Scripts.game import game, Game
    """

    MAIN_MENU         = 'main_menu'
    PLAYING           = 'playing'
    PAUSED            = 'paused'
    RETURNING_TO_MENU = 'returning_to_menu'
    WIN               = 'win'
    GAME_OVER         = 'game_over'

    def __init__(self):
        self.state            = Game.MAIN_MENU
        self.player           = None
        self.enemies          = []
        self.pause_menu       = None
        self.hud              = None
        self.win_screen       = None
        self.game_over_screen = None
        # v1.5: written by the trigger `checkpoint` action (player.position snapshot).
        # Forward declaration — no consumer yet, like Layers.PICKUP. A respawn-on-death
        # mechanic would read this, but the death path is terminal (trigger_game_over),
        # so nothing reads it this version. Reset in return_to_menu() so it never leaks.
        self.respawn_point    = None

    def __repr__(self):
        return f"Game(state={self.state!r})"

    def start(self):
        """Transition to PLAYING."""
        self.state = Game.PLAYING

    def pause(self):
        """Transition to PAUSED."""
        self.state = Game.PAUSED

    def resume(self):
        """Transition back to PLAYING from PAUSED."""
        self.state = Game.PLAYING

    def trigger_win(self):
        """Show WIN overlay, freeze time, surface cursor. Idempotent — only fires from PLAYING."""
        if self.state != Game.PLAYING:
            return
        self.state = Game.WIN
        self._show_end_screen('YOU WIN', is_win=True)

    def trigger_game_over(self):
        """Show GAME OVER overlay, freeze time, surface cursor. Idempotent — only fires from PLAYING."""
        if self.state != Game.PLAYING:
            return
        self.state = Game.GAME_OVER
        self._show_end_screen('GAME OVER', is_win=False)

    def _show_end_screen(self, title: str, is_win: bool):
        from ursina import application, mouse
        from main import EndScreen
        application.time_scale = 0
        mouse.visible = True
        mouse.locked = False
        if self.hud:
            self.hud.hide()
        screen = EndScreen(title, is_win=is_win)
        if is_win:
            self.win_screen = screen
        else:
            self.game_over_screen = screen

    def return_to_menu(self):
        """Tear down gameplay entities and land on MAIN_MENU regardless of errors."""
        self.state = Game.RETURNING_TO_MENU
        try:
            from main import _clear_gameplay_entities
            _clear_gameplay_entities()
        finally:
            self.enemies          = []
            self.player           = None
            self.pause_menu       = None
            self.hud              = None
            self.win_screen       = None
            self.game_over_screen = None
            self.respawn_point    = None
            # Reset time_scale here (not in _clear_gameplay_entities) so it always
            # resets even when teardown raises mid-way (e.g. stale NodePath crash).
            try:
                from ursina import application
                application.time_scale = 1
            except Exception:
                pass
            self.state            = Game.MAIN_MENU


game = Game()
