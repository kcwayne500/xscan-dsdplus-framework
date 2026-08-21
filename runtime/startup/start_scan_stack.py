import subprocess
import time
import json
import re
import msvcrt
from datetime import datetime, timezone
from pathlib import Path

# ---------------- CONFIG ----------------
STARTUP_DIR = Path(__file__).resolve().parent
WORKDIR = str(STARTUP_DIR.parent)
CONFIG_PATH = STARTUP_DIR / "stack_config.json"

config = {
    "link_id": 20001,
    "rtl_index": 2,
    "dsd_audio_output": "2M",
    "dsd_filename_modifier": 6,
}
if CONFIG_PATH.exists():
    try:
        loaded_config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(loaded_config, dict):
            config.update(loaded_config)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Warning: could not read {CONFIG_PATH}: {exc}")

DSDPLUS_EXE = str(Path(WORKDIR) / "DSDPlus.exe")
FMP24_EXE = str(Path(WORKDIR) / "FMP24.exe")

LINK_ID = int(config["link_id"])
RTL_INDEX = int(config["rtl_index"])

# DSD+ numbers playback devices independently from Windows/PortAudio.  On this
# machine device #1 is "CABLE In 16ch" and device #2 is the regular
# "CABLE Input" endpoint paired with the recorder's "CABLE Output" input.
# Keep this explicit: relying on DSD+'s -o1 default silently routed synthesized
# NXDN speech to the wrong virtual endpoint.
DSD_AUDIO_OUTPUT = str(config["dsd_audio_output"])
DSD_FILENAME_MODIFIER = int(config["dsd_filename_modifier"])

# Append-only JSON Lines avoids repeatedly rewriting the legacy 16 MB JSON
# array, which can be held open by Windows and previously crashed the controller.
LOG_PATH = STARTUP_DIR / "scan_events_live.jsonl"

# Use a dedicated live handoff file.  The legacy fmp24_scan.log can remain
# locked by an older process/reader on Windows, which previously killed the
# controller or left recorder metadata permanently stale.
SCAN_LOG_PATH = STARTUP_DIR / "fmp24_scan_live.log"
DSD_LOG_PATH = STARTUP_DIR / "dsdplus_runtime.log"
STACK_LOCK_PATH = STARTUP_DIR / "scan_stack.lock"
SCAN_LOG_MAX_LINES = 200

HOLD_THRESHOLD_SEC = 1.25
# ----------------------------------------

tuning_re = re.compile(
    r"^Tuning to\s+([\d.]+)\s+(\w+)\s+BW=.*?\s+DELAY=\d+\s+(.*)$"
)

def utcnow_iso():
    return datetime.now(timezone.utc).isoformat()

def ensure_log_file():
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not LOG_PATH.exists():
        with open(LOG_PATH, "a", encoding="utf-8"):
            pass


def acquire_instance_lock():
    """Hold a one-byte Windows lock for the lifetime of the controller."""
    STACK_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    handle = open(STACK_LOCK_PATH, "a+b")
    handle.seek(0, 2)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return None
    return handle


def stop_child(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()


def stop_stale_radio_processes():
    """Remove orphaned radio children after a prior controller was closed."""
    found = False
    for image_name in ("FMP24.exe", "DSDPlus.exe"):
        result = subprocess.run(
            ["taskkill", "/IM", image_name, "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        found = found or result.returncode == 0
    if found:
        print("Stopped orphaned DSD+/FMP24 process(es) from the previous run.")
        time.sleep(0.5)

def append_event(event: dict):
    serialized = json.dumps(event, separators=(",", ":")) + "\n"
    for _ in range(25):
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(serialized)
            return True
        except PermissionError:
            time.sleep(0.01)
    print(f"Warning: event log remained locked; dropped event for {event.get('frequency', 'unknown')}")
    return False

def append_scan_line(line: str):
    SCAN_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    # The recorder polls this file frequently.  On Windows its short-lived read
    # handle can overlap this rewrite and raise PermissionError.  A missed line
    # is harmless; crashing the scan controller is not, so retry briefly and
    # keep the FMP/DSD link alive if contention persists.
    for _ in range(25):
        try:
            lines = []
            if SCAN_LOG_PATH.exists():
                with open(SCAN_LOG_PATH, "r", encoding="utf-8") as f:
                    lines = f.readlines()

            lines.append(line.rstrip() + "\n")
            lines = lines[-SCAN_LOG_MAX_LINES:]

            with open(SCAN_LOG_PATH, "w", encoding="utf-8") as f:
                f.writelines(lines)
            return True
        except PermissionError:
            time.sleep(0.01)
    print(f"Warning: scan log remained locked; dropped line: {line.rstrip()}")
    return False

def launch_dsdplus():
    cmd = [
        DSDPLUS_EXE,
        "-r1",
        f"-i{LINK_ID}",
        f"-o{DSD_AUDIO_OUTPUT}",
        f"-F{DSD_FILENAME_MODIFIER}",
        "-O",
        "NUL",
    ]
    DSD_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_handle = open(DSD_LOG_PATH, "a", encoding="utf-8", buffering=1)
    try:
        return subprocess.Popen(
            cmd,
            cwd=WORKDIR,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    finally:
        log_handle.close()

def launch_fmp24():
    cmd = [
        FMP24_EXE,
        "-s1",
        f"-i{RTL_INDEX}",
        f"-o{LINK_ID}"
    ]
    return subprocess.Popen(
        cmd,
        cwd=WORKDIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )

def main():
    instance_lock = acquire_instance_lock()
    if instance_lock is None:
        print("Scanner stack is already running; no second DSD+/FMP24 instance was started.")
        return

    stop_stale_radio_processes()
    print("Starting DSD+ + FMP24 + scan/hold logging...")
    ensure_log_file()
    dsd_proc = None
    fmp_proc = None
    try:
        dsd_proc = launch_dsdplus()
        time.sleep(1.0)
        fmp_proc = launch_fmp24()

        last_tune_time = None
        last_channel = None

        while True:
            line = fmp_proc.stdout.readline()
            if line == "" and fmp_proc.poll() is not None:
                break

            if not line:
                time.sleep(0.02)
                continue

            print(line, end="")
            append_scan_line(line)

            line_stripped = line.strip()
            m = tuning_re.match(line_stripped)
            if not m:
                continue

            now = time.time()
            freq_s, mode, label = m.groups()

            if last_tune_time and last_channel:
                gap = now - last_tune_time
                if gap >= HOLD_THRESHOLD_SEC:
                    event = {
                        "timestamp_start": last_channel["tune_time_iso"],
                        "timestamp_end": utcnow_iso(),
                        "hold_seconds": round(gap, 2),
                        "frequency": last_channel["frequency"],
                        "mode": last_channel["mode"],
                        "label": last_channel["label"],
                        "raw": last_channel["raw"]
                    }
                    append_event(event)

            last_tune_time = now
            last_channel = {
                "tune_time_iso": utcnow_iso(),
                "frequency": float(freq_s),
                "mode": mode,
                "label": label.strip(),
                "raw": line_stripped
            }
    except KeyboardInterrupt:
        print("Stopping scanner stack...")
    finally:
        stop_child(fmp_proc)
        stop_child(dsd_proc)
        instance_lock.close()

if __name__ == "__main__":
    main()
