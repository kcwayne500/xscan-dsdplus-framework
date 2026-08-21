import json
import sys
from pathlib import Path

import sounddevice as sd


def main():
    if len(sys.argv) != 2:
        print("usage: configure_audio.py <settings.json>")
        return 1

    settings_path = Path(sys.argv[1])
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        settings = {}

    candidates = []
    for index, device in enumerate(sd.query_devices()):
        name = str(device.get("name", ""))
        if device.get("max_input_channels", 0) > 0 and "CABLE Output" in name:
            candidates.append((index, name))

    if not candidates:
        print("VB-CABLE recording endpoint was not found.")
        return 2

    index, name = candidates[0]
    settings.update(
        {
            "auto_start_on_open": True,
            "minimize_to_tray": True,
            "audio_device_name": name,
            "audio_device_index": index,
            "streaming_enabled": True,
        }
    )
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    print(f"Configured scanner input {index}: {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
