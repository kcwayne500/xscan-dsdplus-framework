@echo off
python -m PyInstaller --noconfirm --clean --windowed --onedir --name DSDPlusScannerRecorder --icon app.ico scanner_gui_recorder.py
pause
