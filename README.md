# DSDPlus Scanner Recorder

Standalone PySide6 scanner-recorder app for DSDPlus scan log monitoring and audio-triggered recording.

## Features
- Audio-triggered recording with MP3 conversion
- Scanner metadata pulled from `fmp24_scan.log`
- Combined `recordings_log.json` metadata log
- Persistent options for audio device, auto-start, and tray minimize
- PyInstaller build script for a windowed Windows `.exe`

## Requirements
- Python 3.11+
- FFmpeg binaries in a local `ffmpeg/` folder next to `scanner_gui_recorder.py`
- DSDPlus scan log available at `C:\DSDPlusFastlane\startup\fmp24_scan.log`

Install Python dependencies:

```powershell
pip install -r requirements.txt
```

## Run

```powershell
python scanner_gui_recorder.py
```

## Build EXE

```powershell
build_exe.bat
```

Or run manually:

```powershell
python -m PyInstaller --noconfirm --clean --windowed --onedir --name DSDPlusScannerRecorder --icon app.ico scanner_gui_recorder.py
```

## Notes
- The `ffmpeg/` folder is intentionally ignored in Git because it contains third-party binaries.
- Generated recordings and local settings are also ignored.
