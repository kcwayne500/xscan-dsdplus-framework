from __future__ import annotations

import multiprocessing
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path


def _write_fatal_log() -> None:
    state_root = Path(
        os.environ.get("XSCAN_STATE_DIR")
        or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "XScan"
    )
    try:
        log_dir = state_root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat()
        with (log_dir / "windowed-fatal.log").open("a", encoding="utf-8") as handle:
            handle.write(f"\n[{stamp}] XScan V2 terminated unexpectedly\n")
            traceback.print_exc(file=handle)
    except OSError:
        # There is nowhere safer to report an error in a windowed executable.
        pass


if __name__ == "__main__":
    multiprocessing.freeze_support()
    try:
        from xscan.__main__ import main

        main()
    except BaseException:
        _write_fatal_log()
        raise
