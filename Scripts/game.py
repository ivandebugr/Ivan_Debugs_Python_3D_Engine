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
            # Reset time_scale here (not in _clear_gameplay_entities) so it always
            # resets even when teardown raises mid-way (e.g. stale NodePath crash).
            try:
                from ursina import application
                application.time_scale = 1
            except Exception:
                pass
            self.state            = Game.MAIN_MENU


game = Game()
