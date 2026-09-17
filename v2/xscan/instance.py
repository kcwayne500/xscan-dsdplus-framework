"""An OS-released lock; a second host cannot migrate or control the same state."""
import os
from pathlib import Path


class InstanceLock:
    def __init__(self, state: Path):
        state.mkdir(parents=True, exist_ok=True)
        self.handle = (state / 'host.lock').open('a+b')
        self.handle.seek(0, 2)
        if self.handle.tell() == 0:
            self.handle.write(b'0')
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            raise RuntimeError('Another XScan host already owns this state directory') from exc

    def close(self):
        self.handle.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
