import sys
import os
import re
import time
import json
import shutil
import threading
import subprocess
import wave
from datetime import datetime

import sounddevice as sd
import numpy as np

from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QPushButton,
    QTextEdit, QLabel, QProgressBar,
    QSystemTrayIcon, QMenu, QHBoxLayout, QFrame
)
from PySide6.QtGui import QAction, QActionGroup, QIcon
from PySide6.QtCore import Signal, QTimer

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = r"C:\DSDPlusFastlane\startup\fmp24_scan.log"
OUTPUT_DIR = r"C:\DSDPlusFastlane\recordings"
LOCAL_FFMPEG_DIR = os.path.join(SCRIPT_DIR, "ffmpeg")
LOCAL_FFMPEG_EXE = os.path.join(LOCAL_FFMPEG_DIR, "ffmpeg.exe")
RECORDINGS_LOG_FILE = os.path.join(OUTPUT_DIR, "recordings_log.json")
SETTINGS_FILE = os.path.join(SCRIPT_DIR, "scanner_gui_recorder_settings.json")

POLL_INTERVAL_SEC = 0.02
LOG_POLL_SEC = 0.05
AUDIO_CHANNELS = 1
AUDIO_DTYPE = "int16"
AUDIO_BLOCKSIZE = 2048
AUDIO_TRIGGER_LEVEL = 0.0021
SILENCE_HANG_SEC = 1.00
MIN_RECORD_SEC = 0.50

tuning_regex = re.compile(r"Tuning to\s+([\d\.]+)\s+(\w+)\s+BW=.*?DELAY=\d+\s+(.*)")

running = False

APP_STYLE = """
QWidget {
    background-color: #181c22;
    color: #e5e7eb;
    font-family: "Segoe UI", "Tahoma", sans-serif;
    font-size: 10pt;
}
QFrame#card {
    background-color: #242a33;
    border: 1px solid #3a4350;
    border-radius: 10px;
}
QLabel#sectionLabel {
    color: #aab4c2;
    font-size: 8.5pt;
    font-weight: 600;
    text-transform: uppercase;
}
QLabel#statusValue {
    color: #f3f4f6;
    font-size: 12pt;
    font-weight: 700;
}
QLabel#lcdDisplay {
    background-color: #c9d7a1;
    color: #1c2412;
    border: 2px solid #93a16c;
    border-radius: 10px;
    padding: 16px 18px;
    font-family: "Consolas", "Lucida Console", monospace;
    font-size: 18pt;
    font-weight: 700;
}
QPushButton {
    background-color: #343c48;
    color: #f3f4f6;
    border: 1px solid #505c6d;
    border-radius: 8px;
    padding: 8px 12px;
    font-weight: 600;
}
QPushButton:hover {
    background-color: #3d4755;
}
QPushButton:pressed {
    background-color: #2d343f;
}
QTextEdit {
    background-color: #161b21;
    border: 1px solid #3f4956;
    border-radius: 8px;
    color: #e5e7eb;
}
QProgressBar {
    background-color: #1b2128;
    border: 1px solid #404a57;
    border-radius: 7px;
    text-align: center;
    color: #e5e7eb;
}
QProgressBar::chunk {
    background-color: #22c55e;
    border-radius: 6px;
}
QMenu {
    background-color: #242a33;
    color: #f3f4f6;
    border: 1px solid #3a4350;
}
QMenu::item:selected {
    background-color: #36404d;
}
"""

LCD_IDLE_STYLE = "background-color: #c9d7a1; color: #1c2412; border: 2px solid #93a16c; border-radius: 10px; padding: 16px 18px; font-family: Consolas, 'Lucida Console', monospace; font-size: 18pt; font-weight: 700;"
LCD_RECORD_STYLE = "background-color: #d9a3a3; color: #451616; border: 2px solid #b57171; border-radius: 10px; padding: 16px 18px; font-family: Consolas, 'Lucida Console', monospace; font-size: 18pt; font-weight: 700;"
LIGHT_IDLE_STYLE = "background-color: #9ca3af; border-radius: 8px; border: 1px solid #6b7280;"
LIGHT_RECORD_STYLE = "background-color: #ef4444; border-radius: 8px; border: 1px solid #b91c1c;"
START_BUTTON_STYLE = "background-color: #1f4f38; color: #ecfdf5; border: 1px solid #3d8b65; border-radius: 8px; padding: 8px 12px; font-weight: 700;"
STOP_BUTTON_STYLE = "background-color: #5a2323; color: #fef2f2; border: 1px solid #b26a6a; border-radius: 8px; padding: 8px 12px; font-weight: 700;"
OPTIONS_BUTTON_STYLE = "background-color: #343c48; color: #f3f4f6; border: 1px solid #505c6d; border-radius: 8px; padding: 8px 12px; font-weight: 600;"

DEFAULT_SETTINGS = {
    "auto_start_on_open": False,
    "minimize_to_tray": True,
    "audio_device_name": "",
    "audio_device_index": None,
}


class RecorderGUI(QWidget):
    log_message = Signal(str)
    channel_update = Signal(str)
    status_update = Signal(str)
    recording_update = Signal(str)
    meter_update = Signal(int)
    button_mode_update = Signal(bool)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("DSDPlus Scanner Recorder")
        self.setStyleSheet(APP_STYLE)

        self.monitor_thread = None
        self.current_metadata = None
        self.current_log_line = None
        self.finalizer_threads = []
        self.metadata_lock = threading.Lock()
        self.allow_close = False
        self.settings = self.load_settings()

        layout = QVBoxLayout()
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        top_card = QFrame()
        top_card.setObjectName("card")
        top_layout = QHBoxLayout(top_card)
        top_layout.setContentsMargins(14, 12, 14, 12)
        top_layout.setSpacing(10)

        status_col = QVBoxLayout()
        status_title = QLabel("Status")
        status_title.setObjectName("sectionLabel")
        status_col.addWidget(status_title)
        self.status = QLabel("STOPPED")
        self.status.setObjectName("statusValue")
        status_col.addWidget(self.status)
        top_layout.addLayout(status_col, 1)

        self.recording_light = QLabel()
        self.recording_light.setFixedSize(16, 16)
        top_layout.addWidget(self.recording_light)

        self.options_btn = QPushButton("Options")
        self.options_btn.setStyleSheet(OPTIONS_BUTTON_STYLE)
        top_layout.addWidget(self.options_btn)

        self.monitor_btn = QPushButton("Start Monitoring")
        top_layout.addWidget(self.monitor_btn)
        layout.addWidget(top_card)

        middle_card = QFrame()
        middle_card.setObjectName("card")
        middle_layout = QVBoxLayout(middle_card)
        middle_layout.setContentsMargins(14, 14, 14, 14)
        middle_layout.setSpacing(10)

        meta_title = QLabel("Scanner Metadata")
        meta_title.setObjectName("sectionLabel")
        middle_layout.addWidget(meta_title)

        self.channel = QLabel("---")
        self.channel.setObjectName("lcdDisplay")
        self.channel.setStyleSheet(LCD_IDLE_STYLE)
        middle_layout.addWidget(self.channel)

        meter_title = QLabel("Audio Level")
        meter_title.setObjectName("sectionLabel")
        middle_layout.addWidget(meter_title)

        self.audio_meter = QProgressBar()
        self.audio_meter.setRange(0, 100)
        self.audio_meter.setFormat("%p%")
        middle_layout.addWidget(self.audio_meter)

        device_title = QLabel("Input Device")
        device_title.setObjectName("sectionLabel")
        middle_layout.addWidget(device_title)

        self.device_value = QLabel("Not selected")
        self.device_value.setObjectName("statusValue")
        middle_layout.addWidget(self.device_value)
        layout.addWidget(middle_card)

        log_card = QFrame()
        log_card.setObjectName("card")
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(14, 14, 14, 14)
        log_layout.setSpacing(10)

        log_title = QLabel("Event Log")
        log_title.setObjectName("sectionLabel")
        log_layout.addWidget(log_title)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        log_layout.addWidget(self.log_view)
        layout.addWidget(log_card, 1)
        self.setLayout(layout)

        self.monitor_btn.clicked.connect(self.toggle_monitor)
        self.log_message.connect(self.log)
        self.channel_update.connect(self.set_channel_display)
        self.status_update.connect(self.set_status_text)
        self.recording_update.connect(self.set_recording_state)
        self.meter_update.connect(self.audio_meter.setValue)
        self.button_mode_update.connect(self.set_monitor_button_mode)

        self.options_menu = QMenu(self)
        self.options_menu.aboutToShow.connect(self.rebuild_options_menu)
        self.options_btn.setMenu(self.options_menu)

        self.create_tray()
        self.set_recording_state("Recording: OFF")
        self.set_monitor_button_mode(False)
        self.update_device_label()

        if self.settings.get("auto_start_on_open"):
            QTimer.singleShot(0, self.start_monitor)

    def load_settings(self):
        settings = dict(DEFAULT_SETTINGS)
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    settings.update(loaded)
            except (OSError, json.JSONDecodeError):
                pass
        return settings

    def save_settings(self):
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=2)
        except OSError as e:
            self.log_message.emit(f"Settings save failed: {e}")

    def get_input_devices(self):
        devices = []
        for i, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] > 0:
                devices.append((i, dev["name"]))
        return devices

    def get_selected_device(self):
        devices = self.get_input_devices()
        preferred_name = self.settings.get("audio_device_name", "")
        preferred_index = self.settings.get("audio_device_index")
        for index, name in devices:
            if preferred_name and name == preferred_name:
                return index, name
        for index, name in devices:
            if preferred_index is not None and index == preferred_index:
                return index, name
        if devices:
            return devices[0]
        return None, None

    def set_selected_device(self, index, name):
        self.settings["audio_device_index"] = index
        self.settings["audio_device_name"] = name
        self.save_settings()
        self.update_device_label()
        self.log_message.emit(f"Audio device set to {name}")

    def update_device_label(self):
        index, name = self.get_selected_device()
        self.device_value.setText(f"{index}: {name}" if name else "No input device available")

    def rebuild_options_menu(self):
        self.options_menu.clear()

        auto_start_action = QAction("Start Monitoring On Open", self)
        auto_start_action.setCheckable(True)
        auto_start_action.setChecked(bool(self.settings.get("auto_start_on_open")))
        auto_start_action.triggered.connect(lambda checked: self.update_setting("auto_start_on_open", checked))
        self.options_menu.addAction(auto_start_action)

        tray_action = QAction("Minimize To Tray", self)
        tray_action.setCheckable(True)
        tray_action.setChecked(bool(self.settings.get("minimize_to_tray", True)))
        tray_action.triggered.connect(lambda checked: self.update_setting("minimize_to_tray", checked))
        self.options_menu.addAction(tray_action)

        self.options_menu.addSeparator()

        refresh_action = QAction("Refresh Audio Devices", self)
        refresh_action.triggered.connect(self.refresh_devices)
        self.options_menu.addAction(refresh_action)

        device_menu = self.options_menu.addMenu("Input Device")
        device_group = QActionGroup(self)
        device_group.setExclusive(True)
        selected_index, _ = self.get_selected_device()

        for index, name in self.get_input_devices():
            action = QAction(f"{index}: {name}", self)
            action.setCheckable(True)
            action.setChecked(index == selected_index)
            action.triggered.connect(lambda checked=False, i=index, n=name: self.set_selected_device(i, n))
            device_group.addAction(action)
            device_menu.addAction(action)

        if not device_menu.actions():
            action = QAction("No input devices found", self)
            action.setEnabled(False)
            device_menu.addAction(action)

    def update_setting(self, key, value):
        self.settings[key] = value
        self.save_settings()

    def refresh_devices(self):
        self.update_device_label()
        self.log_message.emit("Audio device list refreshed")

    def append_recording_log(self, entry):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        data = []
        if os.path.exists(RECORDINGS_LOG_FILE):
            try:
                with open(RECORDINGS_LOG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, list):
                    data = []
            except (OSError, json.JSONDecodeError):
                data = []
        data.append(entry)
        with open(RECORDINGS_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def create_tray(self):
        self.tray = QSystemTrayIcon(self)
        menu = QMenu()
        show_action = QAction("Show", self)
        quit_action = QAction("Quit", self)
        show_action.triggered.connect(self.show_normal)
        quit_action.triggered.connect(self.quit_app)
        menu.addAction(show_action)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.show()

    def show_normal(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def quit_app(self):
        self.allow_close = True
        self.stop_monitor()
        QApplication.instance().quit()

    def closeEvent(self, event):
        if not self.allow_close and self.settings.get("minimize_to_tray", True):
            event.ignore()
            self.hide()
            return
        event.accept()

    def log(self, text):
        self.log_view.append(text)
        scrollbar = self.log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def set_status_text(self, text):
        self.status.setText(text.replace("Status:", "").strip().upper())

    def set_channel_display(self, text):
        value = text.replace("Channel:", "").strip()
        self.channel.setText(value or "---")

    def set_recording_state(self, text):
        is_recording = text.replace("Recording:", "").strip().upper() == "ON"
        self.recording_light.setStyleSheet(LIGHT_RECORD_STYLE if is_recording else LIGHT_IDLE_STYLE)
        self.channel.setStyleSheet(LCD_RECORD_STYLE if is_recording else LCD_IDLE_STYLE)

    def set_monitor_button_mode(self, monitoring):
        self.monitor_btn.setText("Stop Monitoring" if monitoring else "Start Monitoring")
        self.monitor_btn.setStyleSheet(STOP_BUTTON_STYLE if monitoring else START_BUTTON_STYLE)

    def toggle_monitor(self):
        global running
        if running:
            self.stop_monitor()
        else:
            self.start_monitor()

    def start_monitor(self):
        global running
        if running:
            return
        device_index, device_name = self.get_selected_device()
        if device_index is None:
            self.log_message.emit("Select an input device in Options first")
            return
        running = True
        self.status_update.emit("Status: RUNNING")
        self.recording_update.emit("Recording: OFF")
        self.button_mode_update.emit(True)
        self.update_device_label()
        self.log_message.emit(f"Watching {LOG_FILE}")
        self.log_message.emit(f"Audio trigger level: {AUDIO_TRIGGER_LEVEL:.4f}, silence hang: {SILENCE_HANG_SEC:.2f}s")
        if os.path.exists(LOCAL_FFMPEG_EXE):
            self.log_message.emit(f"Using local ffmpeg for MP3 conversion: {LOCAL_FFMPEG_EXE}")
        else:
            self.log_message.emit("ffmpeg not found; recordings will stay as WAV files")
        self.monitor_thread = threading.Thread(target=self.monitor_audio, daemon=True)
        self.monitor_thread.start()

    def stop_monitor(self):
        global running
        was_running = running
        running = False
        self.status_update.emit("Status: STOPPED")
        self.recording_update.emit("Recording: OFF")
        self.channel_update.emit("Channel: ---")
        self.meter_update.emit(0)
        self.button_mode_update.emit(False)
        if was_running:
            self.log_message.emit("Monitoring stopped")

    def read_last_log_line(self):
        if not os.path.exists(LOG_FILE):
            return None
        with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
            lines = [line.strip() for line in f.readlines() if line.strip()]
        return lines[-1] if lines else None

    def parse_metadata(self, line):
        if not line:
            return None
        match = tuning_regex.search(line)
        if not match:
            return {"raw": line, "frequency": "unknown", "mode": "unknown", "label": "Unknown_Channel", "display": line}
        freq, mode, label = match.groups()
        label = label.strip()
        return {"raw": line, "frequency": freq, "mode": mode, "label": label, "display": f"{freq} {label}"}

    def update_metadata_from_log(self):
        line = self.read_last_log_line()
        if line == self.current_log_line:
            return
        self.current_log_line = line
        metadata = self.parse_metadata(line)
        with self.metadata_lock:
            self.current_metadata = metadata
        self.channel_update.emit(metadata["display"] if metadata else "Channel: ---")

    def get_current_metadata(self):
        with self.metadata_lock:
            if self.current_metadata is None:
                return {"raw": "", "frequency": "unknown", "mode": "unknown", "label": "Unknown_Channel", "display": "Unknown Channel"}
            return dict(self.current_metadata)

    def build_recording_paths(self, metadata):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_label = re.sub(r"[^a-zA-Z0-9]", "_", metadata["label"]).strip("_") or "Unknown_Channel"
        base_name = f"{timestamp}_{metadata['frequency']}_{safe_label}"
        return (
            base_name,
            os.path.join(OUTPUT_DIR, base_name + ".wav"),
            os.path.join(OUTPUT_DIR, base_name + ".mp3"),
        )

    def convert_wav_to_mp3(self, session):
        ffmpeg_exe = LOCAL_FFMPEG_EXE if os.path.exists(LOCAL_FFMPEG_EXE) else shutil.which("ffmpeg")
        if not ffmpeg_exe:
            return False, "ffmpeg not available"
        env = os.environ.copy()
        if os.path.isdir(LOCAL_FFMPEG_DIR):
            env["PATH"] = LOCAL_FFMPEG_DIR + os.pathsep + env.get("PATH", "")
        metadata = session["metadata"]
        cmd = [ffmpeg_exe, "-hide_banner", "-loglevel", "error", "-y", "-i", session["wav_path"], "-metadata", f"title={metadata['label']}", "-metadata", f"artist={metadata['frequency']}", "-metadata", f"album={metadata['mode']}", "-metadata", f"comment={metadata['raw']}", "-acodec", "libmp3lame", "-ab", "96k", session["mp3_path"]]
        proc = subprocess.run(cmd, cwd=LOCAL_FFMPEG_DIR if os.path.isdir(LOCAL_FFMPEG_DIR) else SCRIPT_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=60)
        if proc.returncode != 0:
            return False, proc.stderr.strip().splitlines()[-1] if proc.stderr else f"ffmpeg exit {proc.returncode}"
        return True, None

    def finalize_session(self, session, reason):
        duration = max(0.0, session["ended_at"] - session["started_at_monotonic"])
        metadata = session["metadata"]
        log_entry = {
            "started_at": session["started_at_iso"],
            "ended_at": session["ended_at_iso"],
            "duration_seconds": round(duration, 3),
            "frequency": metadata["frequency"],
            "mode": metadata["mode"],
            "label": metadata["label"],
            "raw_log_line": metadata["raw"],
            "audio_device": session["device_name"],
            "trigger_level": AUDIO_TRIGGER_LEVEL,
            "silence_hang_sec": SILENCE_HANG_SEC,
            "stop_reason": reason,
            "wav_file": os.path.basename(session["wav_path"]),
            "mp3_file": os.path.basename(session["mp3_path"]),
            "audio_file": os.path.basename(session["wav_path"]),
        }
        converted, error_text = self.convert_wav_to_mp3(session)
        if converted:
            try:
                os.remove(session["wav_path"])
            except OSError:
                pass
            log_entry["audio_file"] = os.path.basename(session["mp3_path"])
            self.log_message.emit(f"Saved MP3 -> {os.path.basename(session['mp3_path'])}")
        else:
            self.log_message.emit(f"Saved WAV -> {os.path.basename(session['wav_path'])}")
            self.log_message.emit(f"MP3 conversion skipped: {error_text}")
        self.append_recording_log(log_entry)

    def finalize_session_async(self, session, reason):
        worker = threading.Thread(target=self.finalize_session, args=(session, reason), daemon=True)
        self.finalizer_threads.append(worker)
        worker.start()

    def start_audio_session(self, metadata, samplerate, device_name):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        _, wav_path, mp3_path = self.build_recording_paths(metadata)
        wave_file = wave.open(wav_path, "wb")
        wave_file.setnchannels(AUDIO_CHANNELS)
        wave_file.setsampwidth(2)
        wave_file.setframerate(samplerate)
        session = {
            "wave_file": wave_file,
            "wav_path": wav_path,
            "mp3_path": mp3_path,
            "metadata": metadata,
            "device_name": device_name,
            "started_at_iso": datetime.now().isoformat(),
            "started_at_monotonic": time.monotonic(),
            "last_audio_at": time.monotonic(),
        }
        self.recording_update.emit("Recording: ON")
        self.status_update.emit("Status: RECORDING")
        self.log_message.emit(f"Audio detected -> {metadata['display']}")
        self.log_message.emit(f"Recording WAV -> {os.path.basename(wav_path)}")
        return session

    def stop_audio_session(self, session, reason):
        session["ended_at"] = time.monotonic()
        session["ended_at_iso"] = datetime.now().isoformat()
        try:
            session["wave_file"].close()
        except Exception:
            pass
        duration = session["ended_at"] - session["started_at_monotonic"]
        self.recording_update.emit("Recording: OFF")
        self.status_update.emit("Status: RUNNING")
        if duration < MIN_RECORD_SEC:
            for path in (session["wav_path"], session["mp3_path"]):
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            self.log_message.emit(f"Clip discarded (< {MIN_RECORD_SEC:.2f}s)")
            return
        self.finalize_session_async(session, reason)

    def monitor_audio(self):
        global running
        device_index, device_name = self.get_selected_device()
        if device_index is None:
            running = False
            self.log_message.emit("No audio device selected")
            self.status_update.emit("Status: ERROR")
            self.button_mode_update.emit(False)
            return
        device_info = sd.query_devices(device_index)
        samplerate = int(device_info.get("default_samplerate") or 48000)
        last_log_poll = 0.0
        active_session = None
        try:
            stream = sd.RawInputStream(samplerate=samplerate, blocksize=AUDIO_BLOCKSIZE, device=device_index, channels=AUDIO_CHANNELS, dtype=AUDIO_DTYPE)
            stream.start()
        except Exception as e:
            running = False
            self.log_message.emit(f"Audio monitor failed: {e}")
            self.status_update.emit("Status: ERROR")
            self.button_mode_update.emit(False)
            return
        self.log_message.emit(f"Monitoring audio on {device_name}")
        while running:
            now = time.monotonic()
            if now - last_log_poll >= LOG_POLL_SEC:
                try:
                    self.update_metadata_from_log()
                except Exception as e:
                    self.log_message.emit(f"Log read error: {e}")
                last_log_poll = now
            try:
                data, overflowed = stream.read(AUDIO_BLOCKSIZE)
            except Exception as e:
                self.log_message.emit(f"Audio read error: {e}")
                break
            samples = np.frombuffer(data, dtype=np.int16)
            level = 0.0 if samples.size == 0 else float(np.sqrt(np.mean(np.square(samples.astype(np.float32))))) / 32768.0
            if overflowed:
                self.log_message.emit("Audio overflow detected")
            self.meter_update.emit(int(min(level * 2400, 100)))
            if active_session:
                active_session["wave_file"].writeframes(data)
                if level >= AUDIO_TRIGGER_LEVEL:
                    active_session["last_audio_at"] = now
                elif now - active_session["last_audio_at"] >= SILENCE_HANG_SEC:
                    self.stop_audio_session(active_session, "Silence detected")
                    active_session = None
            elif level >= AUDIO_TRIGGER_LEVEL:
                metadata = self.get_current_metadata()
                try:
                    active_session = self.start_audio_session(metadata, samplerate, device_name)
                    active_session["wave_file"].writeframes(data)
                except Exception as e:
                    self.log_message.emit(f"Recording start failed: {e}")
                    active_session = None
                    time.sleep(0.2)
            time.sleep(POLL_INTERVAL_SEC)
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass
        if active_session:
            self.stop_audio_session(active_session, "Monitoring stopped")
        self.meter_update.emit(0)
        self.recording_update.emit("Recording: OFF")
        self.button_mode_update.emit(False)
        if not running:
            self.status_update.emit("Status: STOPPED")

app = QApplication(sys.argv)
window = RecorderGUI()
window.resize(620, 660)
window.show()
sys.exit(app.exec())



