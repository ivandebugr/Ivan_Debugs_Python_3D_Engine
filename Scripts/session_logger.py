import atexit
import os
import time as _time
from datetime import datetime
from pathlib import Path


class SessionLogger:
    _LEVELS = {'INFO', 'WARN', 'ERROR'}

    def __init__(self, log_dir: str = 'logs'):
        self._lines: list[str] = []
        self._log_dir = log_dir
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        self._filename = os.path.join(log_dir, f'session_{stamp}.log')
        atexit.register(self.flush)
        self.log('INFO', 'Editor opened')

    def log(self, level: str, message: str) -> None:
        if level not in self._LEVELS:
            level = 'INFO'
        now = datetime.now()
        ts = now.strftime('%H:%M:%S.') + f'{now.microsecond // 1000:03d}'
        self._lines.append(f'[{ts}] [{level}] {message}')

    def flush(self) -> None:
        if not self._lines:
            return
        self.log('INFO', 'Editor closed')
        try:
            Path(self._log_dir).mkdir(parents=True, exist_ok=True)
            with open(self._filename, 'w', encoding='utf-8') as f:
                f.write('\n'.join(self._lines) + '\n')
            print(f'Session log saved: {self._filename}')
        except Exception as e:
            print(f'SessionLogger.flush failed: {e}')
