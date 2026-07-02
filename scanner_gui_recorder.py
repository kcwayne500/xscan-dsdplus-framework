import os
import subprocess
import sys
import time
from datetime import datetime

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QProgressBar,
    QPushButton,
    QSystemTrayIcon,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from scanner_core import (
    AUDIO_TRIGGER_LEVEL,
    SILENCE_HANG_SEC,
    AudioDeviceCatalog,
    AudioMonitor,
    MetadataReader,
    MonitorCallbacks,
    RecordingCatalog,
    ScannerMetadata,
    SettingsStore,
)
from scanner_web_server import MetadataWebServer
from streaming_support import StreamingManager


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
LOG_FILE = r"C:\DSDPlusFastlane\startup\fmp24_scan.log"
OUTPUT_DIR = r"C:\DSDPlusFastlane\recordings"
LOCAL_FFMPEG_DIR = os.path.join(SCRIPT_DIR, "ffmpeg")
LOCAL_FFMPEG_EXE = os.path.join(LOCAL_FFMPEG_DIR, "ffmpeg.exe")
RECORDINGS_LOG_FILE = os.path.join(OUTPUT_DIR, "recordings_log.json")
SETTINGS_FILE = os.path.join(SCRIPT_DIR, "scanner_gui_recorder_settings.json")
ICON_FILE = os.path.join(SCRIPT_DIR, "app.ico")
WEB_UI_PORT = 8890


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


def get_listening_pids(port):
    try:
        result = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True, timeout=5)
    except Exception:
        return []
    pids = []
    needle = f":{port}"
    for line in result.stdout.splitlines():
        if needle not in line or "LISTENING" not in line.upper():
            continue
        parts = line.split()
        if not parts:
            continue
        try:
            pid = int(parts[-1])
        except (TypeError, ValueError):
            continue
        if pid not in pids:
            pids.append(pid)
    return pids


def get_process_name(pid):
    try:
        result = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=5)
    except Exception:
        return ""
    lines = (result.stdout or "").strip().splitlines()
    if not lines:
        return ""
    first = lines[0].strip()
    if not first or first.startswith("INFO:"):
        return ""
    return first.split('","', 1)[0].strip('"').lower()


def release_app_port(port, log_func):
    allowed = {"python.exe", "pythonw.exe", "dsdplusscannerrecorder.exe"}
    current_pid = os.getpid()
    released = False
    for pid in get_listening_pids(port):
        if pid == current_pid:
            continue
        name = get_process_name(pid)
        if name not in allowed:
            continue
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, text=True, timeout=5)
            released = True
            log_func(f"Released stale listener on port {port} from PID {pid} ({name})")
        except Exception:
            pass
    if released:
        time.sleep(0.6)


class RecorderGUI(QWidget):
    log_message = Signal(str)
    metadata_update = Signal(object)
    status_update = Signal(str)
    recording_update = Signal(bool)
    meter_update = Signal(int)
    monitor_mode_update = Signal(bool)
    stream_status_update = Signal(str)
    stream_url_update = Signal(str)
    web_url_update = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("DSDPlus Scanner Recorder")
        self.setStyleSheet(APP_STYLE)

        self.app_icon = self.load_app_icon()
        if not self.app_icon.isNull():
            self.setWindowIcon(self.app_icon)

        self.allow_close = False
        self.current_status_value = "STOPPED"
        self.current_recording_value = "OFF"
        self.current_stream_status_value = "OFF"
        self.current_metadata = ScannerMetadata()

        self.settings = SettingsStore(SETTINGS_FILE)
        self.device_catalog = AudioDeviceCatalog()
        self.metadata_reader = MetadataReader(LOG_FILE)
        self.streaming_manager = StreamingManager(
            SCRIPT_DIR,
            LOCAL_FFMPEG_DIR,
            self.log_message.emit,
            self.stream_status_update.emit,
            self.stream_url_update.emit,
        )
        self.recording_catalog = RecordingCatalog(OUTPUT_DIR, LOCAL_FFMPEG_DIR, RECORDINGS_LOG_FILE)
        self.monitor = AudioMonitor(
            self.device_catalog,
            self.metadata_reader,
            self.recording_catalog,
            self.streaming_manager,
            MonitorCallbacks(
                log=self.log_message.emit,
                metadata=self.metadata_update.emit,
                status=self.status_update.emit,
                recording=self.recording_update.emit,
                meter=self.meter_update.emit,
            ),
            self.settings.snapshot,
        )
        self.web_server = MetadataWebServer("0.0.0.0", WEB_UI_PORT, self.build_web_payload)

        self.build_layout()
        self.connect_signals()
        self.create_options_menu()
        self.create_tray()

        self.set_recording_state(False)
        self.set_monitor_button_mode(False)
        self.update_device_label()
        self.refresh_stream_labels()
        self.start_web_server()

        if self.settings.get("auto_start_on_open"):
            QTimer.singleShot(0, self.start_monitor)

    def build_layout(self):
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

        self.channel = self.add_value_block(middle_layout, "Scanner Metadata", "---")
        self.channel.setStyleSheet(LCD_IDLE_STYLE)

        meter_title = QLabel("Audio Level")
        meter_title.setObjectName("sectionLabel")
        middle_layout.addWidget(meter_title)
        self.audio_meter = QProgressBar()
        self.audio_meter.setRange(0, 100)
        self.audio_meter.setFormat("%p%")
        middle_layout.addWidget(self.audio_meter)

        self.device_value = self.add_value_block(middle_layout, "Input Device", "Not selected")
        self.stream_status = self.add_value_block(middle_layout, "Live Stream", "OFF")
        self.stream_url_value = self.add_value_block(middle_layout, "WebRTC URL", "Streaming disabled")
        self.stream_url_value.setWordWrap(True)
        self.web_url_value = self.add_value_block(middle_layout, "Web Player URL", "Starting web UI...")
        self.web_url_value.setWordWrap(True)
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

    def add_value_block(self, layout, title, value):
        label = QLabel(title)
        label.setObjectName("sectionLabel")
        layout.addWidget(label)
        value_label = QLabel(value)
        value_label.setObjectName("statusValue")
        layout.addWidget(value_label)
        return value_label

    def connect_signals(self):
        self.monitor_btn.clicked.connect(self.toggle_monitor)
        self.log_message.connect(self.log)
        self.metadata_update.connect(self.set_metadata)
        self.status_update.connect(self.set_status_text)
        self.recording_update.connect(self.set_recording_state)
        self.meter_update.connect(self.audio_meter.setValue)
        self.monitor_mode_update.connect(self.set_monitor_button_mode)
        self.stream_status_update.connect(self.set_stream_status_text)
        self.stream_url_update.connect(self.set_stream_url_text)
        self.web_url_update.connect(self.set_web_url_text)

    def create_options_menu(self):
        self.options_menu = QMenu(self)
        self.options_menu.aboutToShow.connect(self.rebuild_options_menu)
        self.options_btn.setMenu(self.options_menu)

    def create_tray(self):
        self.tray = QSystemTrayIcon(self.app_icon, self)
        menu = QMenu()
        show_action = QAction("Show", self)
        quit_action = QAction("Quit", self)
        show_action.triggered.connect(self.show_normal)
        quit_action.triggered.connect(self.quit_app)
        menu.addAction(show_action)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.show()

    def load_app_icon(self):
        return QIcon(ICON_FILE) if os.path.exists(ICON_FILE) else QIcon()

    def get_web_player_url(self):
        host = self.streaming_manager.get_webrtc_url().split("//", 1)[1].split(":", 1)[0]
        return f"http://{host}:{WEB_UI_PORT}/"

    def build_web_payload(self):
        metadata = self.current_metadata
        return {
            "status": self.current_status_value,
            "recording": self.current_recording_value == "ON",
            "stream_status": self.current_stream_status_value,
            "streaming_enabled": bool(self.settings.get("streaming_enabled")),
            "display": metadata.display,
            "frequency": metadata.frequency,
            "mode": metadata.mode,
            "label": metadata.label,
            "raw_log_line": metadata.raw,
            "audio_device": self.device_value.text(),
            "raw_stream_url": self.streaming_manager.get_webrtc_url(),
            "web_player_url": self.get_web_player_url(),
            "updated_at": datetime.now().strftime("%H:%M:%S"),
        }

    def start_web_server(self):
        try:
            release_app_port(WEB_UI_PORT, self.log_message.emit)
            self.web_server.start()
            self.web_url_update.emit(self.get_web_player_url())
            self.log_message.emit(f"Scanner web UI -> {self.get_web_player_url()}")
        except OSError as exc:
            self.web_url_update.emit(f"Web UI failed: {exc}")
            self.log_message.emit(f"Web UI failed: {exc}")

    def refresh_stream_labels(self):
        self.web_url_update.emit(self.get_web_player_url())
        if self.settings.get("streaming_enabled"):
            self.stream_status_update.emit("READY")
            self.stream_url_update.emit(self.streaming_manager.get_webrtc_url())
        else:
            self.stream_status_update.emit("OFF")
            self.stream_url_update.emit("Streaming disabled")

    def get_selected_device(self):
        return self.device_catalog.select(self.settings.snapshot())

    def update_device_label(self):
        index, name = self.get_selected_device()
        self.device_value.setText(f"{index}: {name}" if name else "No input device available")

    def rebuild_options_menu(self):
        self.options_menu.clear()

        self.add_checked_option("Start Monitoring On Open", "auto_start_on_open")
        self.add_checked_option("Minimize To Tray", "minimize_to_tray")
        self.add_checked_option("Enable Live Streaming", "streaming_enabled")
        self.options_menu.addSeparator()

        refresh_action = QAction("Refresh Audio Devices", self)
        refresh_action.triggered.connect(self.refresh_devices)
        self.options_menu.addAction(refresh_action)

        device_menu = self.options_menu.addMenu("Input Device")
        device_group = QActionGroup(self)
        device_group.setExclusive(True)
        selected_index, _ = self.get_selected_device()

        for index, name in self.device_catalog.list_inputs():
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

    def add_checked_option(self, text, key):
        action = QAction(text, self)
        action.setCheckable(True)
        action.setChecked(bool(self.settings.get(key)))
        action.triggered.connect(lambda checked, setting_key=key: self.update_setting(setting_key, checked))
        self.options_menu.addAction(action)

    def update_setting(self, key, value):
        try:
            self.settings.set(key, value)
        except OSError as exc:
            self.log_message.emit(f"Settings save failed: {exc}")
            return
        if key == "streaming_enabled":
            if not value:
                self.streaming_manager.stop(log_message=True)
            self.refresh_stream_labels()
            self.log_message.emit(
                f"Live streaming enabled -> {self.streaming_manager.get_webrtc_url()}"
                if value
                else "Live streaming disabled"
            )

    def set_selected_device(self, index, name):
        self.settings.set("audio_device_index", index)
        self.settings.set("audio_device_name", name)
        self.update_device_label()
        self.log_message.emit(f"Audio device set to {name}")

    def refresh_devices(self):
        self.update_device_label()
        self.log_message.emit("Audio device list refreshed")

    def show_normal(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def quit_app(self):
        self.allow_close = True
        self.stop_monitor()
        self.web_server.stop()
        QApplication.instance().quit()

    def closeEvent(self, event):
        if not self.allow_close and self.settings.get("minimize_to_tray", True):
            event.ignore()
            self.hide()
            return
        self.stop_monitor()
        self.web_server.stop()
        event.accept()

    def log(self, text):
        self.log_view.append(text)
        scrollbar = self.log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def set_metadata(self, metadata):
        self.current_metadata = metadata
        self.channel.setText(metadata.display or "---")

    def set_status_text(self, text):
        self.current_status_value = text.replace("Status:", "").strip().upper()
        self.status.setText(self.current_status_value)
        self.monitor_mode_update.emit(self.current_status_value in {"RUNNING", "RECORDING"})

    def set_stream_status_text(self, text):
        self.current_stream_status_value = text.strip().upper()
        self.stream_status.setText(self.current_stream_status_value)

    def set_stream_url_text(self, text):
        self.stream_url_value.setText(text)

    def set_web_url_text(self, text):
        self.web_url_value.setText(text)

    def set_recording_state(self, recording):
        self.current_recording_value = "ON" if recording else "OFF"
        self.recording_light.setStyleSheet(LIGHT_RECORD_STYLE if recording else LIGHT_IDLE_STYLE)
        self.channel.setStyleSheet(LCD_RECORD_STYLE if recording else LCD_IDLE_STYLE)

    def set_monitor_button_mode(self, monitoring):
        self.monitor_btn.setText("Stop Monitoring" if monitoring else "Start Monitoring")
        self.monitor_btn.setStyleSheet(STOP_BUTTON_STYLE if monitoring else START_BUTTON_STYLE)

    def toggle_monitor(self):
        if self.monitor.is_running():
            self.stop_monitor()
        else:
            self.start_monitor()

    def start_monitor(self):
        device_index, _ = self.get_selected_device()
        if device_index is None:
            self.log_message.emit("Select an input device in Options first")
            return
        self.update_device_label()
        self.refresh_stream_labels()
        self.log_message.emit(f"Watching {LOG_FILE}")
        self.log_message.emit(f"Audio trigger level: {AUDIO_TRIGGER_LEVEL:.4f}, silence hang: {SILENCE_HANG_SEC:.2f}s")
        if os.path.exists(LOCAL_FFMPEG_EXE):
            self.log_message.emit(f"Using local ffmpeg for MP3 conversion: {LOCAL_FFMPEG_EXE}")
        else:
            self.log_message.emit("ffmpeg not found; recordings will stay as WAV files")
        if self.monitor.start():
            self.monitor_mode_update.emit(True)

    def stop_monitor(self):
        was_running = self.monitor.is_running()
        self.monitor.stop()
        self.status_update.emit("STOPPED")
        self.recording_update.emit(False)
        self.meter_update.emit(0)
        self.monitor_mode_update.emit(False)
        self.refresh_stream_labels()
        if was_running:
            self.log_message.emit("Monitoring stopped")


def main():
    app = QApplication(sys.argv)
    window = RecorderGUI()
    window.resize(620, 660)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
