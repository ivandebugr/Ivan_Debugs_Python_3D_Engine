import atexit
import os
import time as _time
from datetime import datetime
from pathlib import Path


class SessionLogger:
    _LEVELS = {'INFO', 'WARN', 'ERROR'}

    def __init__(self, log_dir: str = 'logs', open_message: str = 'Editor opened'):
        self._lines: list[str] = []
        self._log_dir = log_dir
        self._open_message = open_message
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        self._filename = os.path.join(log_dir, f'session_{stamp}.log')
        atexit.register(self.flush)
        self.log('INFO', open_message)

    def log(self, level: str, message: str) -> None:
        if level not in self._LEVELS:
            level = 'INFO'
        now = datetime.now()
        ts = now.strftime('%H:%M:%S.') + f'{now.microsecond // 1000:03d}'
        self._lines.append(f'[{ts}] [{level}] {message}')

    def flush(self) -> None:
        if not self._lines:
            return
        self.log('INFO', self._open_message.replace('opened', 'closed'))
        try:
            Path(self._log_dir).mkdir(parents=True, exist_ok=True)
            with open(self._filename, 'w', encoding='utf-8') as f:
                f.write('\n'.join(self._lines) + '\n')
            print(f'Session log saved: {self._filename}')
        except Exception as e:
            print(f'SessionLogger.flush failed: {e}')


# Shared game-session logger. main.py is loaded twice as a module object (once as
# __main__ when you run `python main.py`, and again as `main` via game.py's
# `from main import ...`). A module-level `SessionLogger()` in main.py would therefore
# create two instances and write two log files. Routing through this single cached
# instance keeps the whole game session in one log. The editor keeps its own instance.
_game_logger: 'SessionLogger | None' = None


def get_game_logger() -> 'SessionLogger':
    """Return the process-wide game-session logger (one log file per run)."""
    global _game_logger
    if _game_logger is None:
        _game_logger = SessionLogger(open_message='Game session opened')
    return _game_logger


# Editor-session logger, same cached pattern. The v1.6 editor split puts editor
# code in several Scripts/editor_* modules; a module-level SessionLogger() in
# each would write one log file per module. All editor modules route through
# this single cached instance instead.
_editor_logger: 'SessionLogger | None' = None


def get_editor_logger() -> 'SessionLogger':
    """Return the process-wide editor-session logger (one log file per run)."""
    global _editor_logger
    if _editor_logger is None:
        _editor_logger = SessionLogger()
    return _editor_logger
