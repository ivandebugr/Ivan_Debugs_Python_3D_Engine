class Game:
    MAIN_MENU         = 'main_menu'
    PLAYING           = 'playing'
    PAUSED            = 'paused'
    RETURNING_TO_MENU = 'returning_to_menu'
    WIN               = 'win'

    def __init__(self):
        self.state      = Game.MAIN_MENU
        self.player     = None
        self.enemies    = []
        self.pause_menu = None

    def start(self):
        self.state = Game.PLAYING

    def pause(self):
        self.state = Game.PAUSED

    def resume(self):
        self.state = Game.PLAYING

    def return_to_menu(self):
        self.state = Game.RETURNING_TO_MENU
        try:
            from main import _clear_gameplay_entities
            _clear_gameplay_entities()
        finally:
            self.state = Game.MAIN_MENU


game = Game()
