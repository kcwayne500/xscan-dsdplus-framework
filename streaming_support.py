import os
import shutil
import socket
import subprocess
import threading
import time

STREAM_NAME = "scanner"
STREAM_RTSP_PORT = 8554
STREAM_WEBRTC_PORT = 8889
STREAM_WEBRTC_UDP_PORT = 8189
STREAM_AUDIO_BITRATE = "48k"
PUBLIC_WEBRTC_HOST = os.environ.get("XSCAN_PUBLIC_HOST", "xscan.cc-group.org")


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


def release_stale_listener(port, log_func):
    allowed = {"python.exe", "pythonw.exe", "dsdplusscannerrecorder.exe", "mediamtx.exe", "ffmpeg.exe"}
    released = False
    for pid in get_listening_pids(port):
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


def get_preferred_lan_ip():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
        finally:
            sock.close()
    except OSError:
        pass
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    return "127.0.0.1"


def build_mediamtx_config():
    hosts = []
    for host in ("127.0.0.1", get_preferred_lan_ip(), PUBLIC_WEBRTC_HOST):
        if host and host not in hosts:
            hosts.append(host)
    lines = [
        "logLevel: warn",
        f"rtspAddress: :{STREAM_RTSP_PORT}",
        f"webrtcAddress: :{STREAM_WEBRTC_PORT}",
        f"webrtcLocalUDPAddress: :{STREAM_WEBRTC_UDP_PORT}",
        "hls: no",
        "rtmp: no",
        "api: no",
        "metrics: no",
        "pprof: no",
        "webrtcAdditionalHosts:",
    ]
    for host in hosts:
        lines.append(f"  - {host}")
    lines.extend(["paths:", f"  {STREAM_NAME}: {{}}", ""])
    return "\n".join(lines)


class StreamingManager:
    def __init__(self, base_dir, ffmpeg_dir, log_func, status_func, url_func):
        self.base_dir = base_dir
        self.ffmpeg_dir = ffmpeg_dir
        self.log_func = log_func
        self.status_func = status_func
        self.url_func = url_func
        self.mediamtx_dir = self._resolve_mediamtx_dir(base_dir)
        self.mediamtx_exe = os.path.join(self.mediamtx_dir, "mediamtx.exe")
        self.mediamtx_config = os.path.join(self.mediamtx_dir, "mediamtx.generated.yml")
        self.ffmpeg_exe = os.path.join(ffmpeg_dir, "ffmpeg.exe")
        self.mediamtx_process = None
        self.publish_process = None
        self.lock = threading.Lock()
        self.threads = []

    def _resolve_mediamtx_dir(self, base_dir):
        candidates = [
            os.path.join(base_dir, "mediamtx"),
            os.path.join(os.path.dirname(base_dir), "mediamtx"),
        ]
        for candidate in candidates:
            if os.path.exists(os.path.join(candidate, "mediamtx.exe")):
                return candidate
        return candidates[0]

    def get_webrtc_url(self):
        return f"http://{get_preferred_lan_ip()}:{STREAM_WEBRTC_PORT}/{STREAM_NAME}"

    def get_rtsp_url(self):
        return f"rtsp://127.0.0.1:{STREAM_RTSP_PORT}/{STREAM_NAME}"

    def is_available(self):
        return os.path.exists(self.mediamtx_exe)

    def _pump(self, process, prefix):
        def worker():
            stream = process.stderr
            if stream is None:
                return
            try:
                for raw_line in iter(stream.readline, b""):
                    if not raw_line:
                        break
                    line = raw_line.decode("utf-8", errors="ignore").strip()
                    if line:
                        self.log_func(f"{prefix}: {line}")
            except Exception:
                return
        thread = threading.Thread(target=worker, daemon=True)
        self.threads.append(thread)
        thread.start()

    def _stop_process(self, process):
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
        except Exception:
            pass
        try:
            process.terminate()
            process.wait(timeout=2)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def start(self, samplerate):
        ffmpeg_exe = self.ffmpeg_exe if os.path.exists(self.ffmpeg_exe) else shutil.which("ffmpeg")
        if not os.path.exists(self.mediamtx_exe):
            self.status_func("UNAVAILABLE")
            self.log_func(f"MediaMTX not found at {self.mediamtx_exe}")
            return False
        if not ffmpeg_exe:
            self.status_func("ERROR")
            self.log_func("Live streaming unavailable because ffmpeg was not found")
            return False
        self.stop(log_message=False)
        release_stale_listener(STREAM_RTSP_PORT, self.log_func)
        release_stale_listener(STREAM_WEBRTC_PORT, self.log_func)
        with self.lock:
            os.makedirs(self.mediamtx_dir, exist_ok=True)
            with open(self.mediamtx_config, "w", encoding="utf-8") as f:
                f.write(build_mediamtx_config())
            self.status_func("STARTING")
            self.url_func(self.get_webrtc_url())
            self.mediamtx_process = subprocess.Popen(
                [self.mediamtx_exe, self.mediamtx_config],
                cwd=self.mediamtx_dir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
            self._pump(self.mediamtx_process, "MediaMTX")
        time.sleep(0.5)
        if self.mediamtx_process is None or self.mediamtx_process.poll() is not None:
            self.status_func("ERROR")
            self.log_func("MediaMTX failed to start")
            return False
        env = os.environ.copy()
        if os.path.isdir(self.ffmpeg_dir):
            env["PATH"] = self.ffmpeg_dir + os.pathsep + env.get("PATH", "")
        cmd = [
            ffmpeg_exe,
            "-hide_banner",
            "-loglevel", "error",
            "-fflags", "nobuffer",
            "-f", "s16le",
            "-ar", str(samplerate),
            "-ac", "1",
            "-i", "pipe:0",
            "-map", "0:a:0",
            "-c:a", "libopus",
            "-application", "lowdelay",
            "-frame_duration", "20",
            "-b:a", STREAM_AUDIO_BITRATE,
            "-f", "rtsp",
            "-rtsp_transport", "tcp",
            self.get_rtsp_url(),
        ]
        with self.lock:
            self.publish_process = subprocess.Popen(
                cmd,
                cwd=self.ffmpeg_dir if os.path.isdir(self.ffmpeg_dir) else self.base_dir,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
            self._pump(self.publish_process, "Stream publish")
        time.sleep(0.5)
        if self.publish_process is None or self.publish_process.poll() is not None:
            self.status_func("ERROR")
            self.log_func("Streaming publisher failed to start")
            self.stop(log_message=False)
            return False
        self.status_func("LIVE")
        self.url_func(self.get_webrtc_url())
        self.log_func(f"Live stream ready -> {self.get_webrtc_url()}")
        return True

    def write(self, data):
        with self.lock:
            proc = self.publish_process
        if proc is None or proc.stdin is None:
            return
        if proc.poll() is not None:
            self.status_func("ERROR")
            return
        try:
            proc.stdin.write(data)
        except (BrokenPipeError, OSError, ValueError) as exc:
            self.status_func("ERROR")
            self.log_func(f"Stream write failed: {exc}")
            self.stop(log_message=False)

    def stop(self, log_message=True):
        with self.lock:
            publish_proc = self.publish_process
            mediamtx_proc = self.mediamtx_process
            self.publish_process = None
            self.mediamtx_process = None
        if publish_proc is not None:
            self._stop_process(publish_proc)
        if mediamtx_proc is not None:
            self._stop_process(mediamtx_proc)
        if log_message and (publish_proc is not None or mediamtx_proc is not None):
            self.log_func("Live streaming stopped")
