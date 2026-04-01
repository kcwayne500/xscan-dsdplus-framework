@echo off
setlocal
python -m PyInstaller --noconfirm --clean --windowed --onefile --name DSDPlusScannerRecorder --icon app.ico --add-data "app.ico;." --add-data "webui;webui" --add-data "ffmpeg;ffmpeg" scanner_gui_recorder.py
pause
