"""Two independently owned scanner pipelines, with shared portal services."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import re
import shutil
import threading
from contextvars import ContextVar
from copy import deepcopy

from .audio import AudioEngine
from .config_manager import ConfigManager
from .database import Database
from .events import EventBus
from .migration import Migrator
from .multimedia import MediaServer, PcmMixer, stream_names
from .runtime import HostRuntime
from .settings import DEFAULT_SETTINGS, SettingsStore, _merge
from .state import RuntimeState
from .streaming import StreamingManager
from .supervisor import ProcessSupervisor

selected_feed = ContextVar("selected_feed", default="feed1")


class FeedSettings:
    def __init__(self, local, root, feed_id):
        self.local, self.root, self.feed_id = local, root, feed_id
        self.validator = None
        self.lock = threading.RLock()

    def snapshot(self):
        result = self.local.snapshot()
        for key in ("server", "tools", "feeds", "mix"):
            result[key] = self.root.section(key)
        result["streaming"] = {**self.root.section("streaming"),
                               "stream_name": stream_names(self.root)[self.feed_id]}
        if self.feed_id != "feed1":
            result["runtime"]["hardware_control_enabled"] = self.root.section("runtime")["hardware_control_enabled"]
        if self.root.section("feeds")["feed2"]["enabled"]:
            result["audio"]["strict_device"] = True
        return result

    def section(self, key):
        return self.snapshot()[key]

    def update(self, patch):
        with self.lock:
            return self._update(patch)

    def _update(self, patch):
        if any(key in patch for key in ("feeds", "mix")):
            raise ValueError("Use the feed controls or shared mix controls")
        if self.feed_id != "feed1" and any(key in patch for key in ("server", "tools", "streaming", "feeds", "mix")):
            raise ValueError("Host and streaming settings must be changed through Feed 1")
        if self.validator:
            self.validator(_merge(self.snapshot(), deepcopy(patch)))
        self.local.update(patch)
        return self.snapshot()

    def replace(self, data):
        with self.lock:
            current = self.snapshot()
            data = _merge(deepcopy(DEFAULT_SETTINGS), deepcopy(data))
            # Restores cannot cross the install safety boundary or change feed ownership.
            data["runtime"]["hardware_control_enabled"] = current["runtime"]["hardware_control_enabled"]
            data["server"]["port"] = current["server"]["port"]
            for key in ("feeds", "mix"):
                data[key] = current[key]
            if self.feed_id != "feed1":
                for key in ("server", "tools", "streaming"):
                    data[key] = current[key]
            if self.validator:
                self.validator(data)
            self.local.replace(data)
            return self.snapshot()

    def backups(self):
        return self.local.backups()

    def restore(self, backup):
        # Validate before modifying the saved settings.
        import json
        path = self.local.paths.backups / "settings" / str(backup)
        if path.resolve().parent != (self.local.paths.backups / "settings").resolve():
            raise ValueError("Invalid settings backup")
        return self.replace(json.loads(path.read_text(encoding="utf-8")))


class FeedRuntime(HostRuntime):
    def start(self, persist=True):
        with self._lock:
            self.validate_start()
            return super().start(persist)

    def restart(self):
        with self._lock:
            self.validate_start()
            return super().restart()


class Feed:
    def __init__(self, feed_id, paths, settings, root, server, logger, mixer):
        self.feed_id, self.paths = feed_id, paths
        paths.ensure()
        self.logger = logging.Logger(f"xscan.{feed_id}", logger.level)
        self.logger.parent = logger
        self.log_handler = None
        if feed_id == "feed2":
            self.log_handler = RotatingFileHandler(paths.logs / "xscan.log", maxBytes=10 * 1024 * 1024,
                                                   backupCount=5, encoding="utf-8")
            self.log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
            self.logger.addHandler(self.log_handler)
        self.settings = FeedSettings(settings, root, feed_id)
        self.events = EventBus(feed_id)
        self.database = Database(paths, feed_id)
        self.state = RuntimeState(paths, self.events)
        self.config = ConfigManager(paths)
        self.streaming = StreamingManager(paths, self.settings, self.state, self.events, self.logger,
            server=server, on_pcm=lambda data, rate: mixer.put(feed_id, data, rate))
        self.supervisor = ProcessSupervisor(paths, self.settings, self.state, self.events, self.logger)
        self.audio = AudioEngine(paths, self.settings, self.database, self.state, self.events, self.streaming, self.logger)
        self.runtime = FeedRuntime(self.settings, self.state, self.events, self.supervisor,
                                   self.audio, self.streaming, self.logger)
        self.migrator = Migrator(paths, self.settings, self.database, self.logger)


class FeedManager:
    def __init__(self, paths, settings, logger):
        self.paths, self.settings, self.logger = paths, settings, logger
        self.lock = threading.RLock()
        self.server = MediaServer(paths, settings, logger)
        self.mix_state = RuntimeState(paths, EventBus("both"))
        self.mix_settings = FeedSettings(settings, settings, "both")
        self.mix_stream = StreamingManager(paths, self.mix_settings, self.mix_state,
            self.mix_state.events, logger, server=self.server)
        self.mixer = PcmMixer(settings, self.mix_stream)
        self.feeds = {}
        for feed_id in ("feed1", "feed2"):
            local_paths = paths.for_feed(feed_id)
            local_paths.ensure()
            local = settings if feed_id == "feed1" else SettingsStore(local_paths)
            if feed_id == "feed2" and not local_paths.settings.exists():
                used = self.link(settings.snapshot())
                link = next(value for value in range(20002, 65536) if str(value) != used)
                local.update({"runtime": {"desired_running": False,
                    "dsdplus_args": ["-r1", "-m2", f"-i{link}", "-O", "NUL"],
                    "fmp24_args": ["-s1", "-i0", f"-o{link}"]},
                    "audio": {"device_name": "", "strict_device": True}})
            feed = Feed(feed_id, local_paths, local, settings, self.server, logger, self.mixer)
            self.feeds[feed_id] = feed
            feed.settings.lock = self.lock
            feed.runtime._lock = self.lock
            feed.runtime.validate_start = lambda key=feed_id: self.validate_start(key)
            feed.settings.validator = lambda data, key=feed_id: self.validate_edit(key, data)

    def get(self, feed_id):
        if feed_id not in self.feeds:
            raise KeyError(feed_id)
        return self.feeds[feed_id]

    @staticmethod
    def serial(data):
        arguments = [arg for arg in data["runtime"]["fmp24_args"] if arg.startswith("-i")]
        match = re.fullmatch(r'-i"([A-Za-z0-9]{1,8})"', arguments[0]) if len(arguments) == 1 else None
        return match[1] if match else ""

    @staticmethod
    def link(data):
        values = [arg[2:] for arg in data["runtime"]["dsdplus_args"] if re.fullmatch(r"-i\d+", arg)]
        return values[0] if len(values) == 1 else ""

    def validate_pair(self, candidates):
        serials, links, routes = set(), set(), []
        for key, data in candidates.items():
            SettingsStore._validate(data)
            serial = self.serial(data)
            cable = data["audio"]["device_name"].strip().casefold()
            host = data["audio"]["device_host_api"].strip().casefold()
            channel = data["audio"].get("capture_channel", "mono")
            link = self.link(data)
            if not serial or serial in serials:
                raise ValueError("Two feeds require distinct, verified RTL-SDR serial numbers")
            if not cable:
                raise ValueError("Each feed requires an explicitly selected audio cable")
            if not link or not 256 <= int(link) <= 65535 or link in links:
                raise ValueError("Each feed requires a unique direct link ID (256-65535)")
            if [arg for arg in data["runtime"]["fmp24_args"] if arg.startswith("-o")] != [f"-o{link}"]:
                raise ValueError("FMP24 output and DSDPlus input link IDs must match")
            outputs = [arg for arg in data["runtime"]["dsdplus_args"] if arg.startswith("-o")]
            output = re.fullmatch(r"-o([1-9]\d{0,2})[MLR]?", outputs[0]) if len(outputs) == 1 else None
            if not output or int(output[1]) > 255:
                raise ValueError("Select and verify the DSDPlus audio output number for each feed")
            output_id = int(output[1])
            for old_cable, old_host, old_channel, old_output in routes:
                if cable == old_cable or output_id == old_output:
                    if not (cable == old_cable and host == old_host == "windows wasapi"
                            and output_id == old_output and {channel, old_channel} == {"left", "right"}):
                        raise ValueError("Shared audio cable/output requires matching WASAPI endpoints and opposite left/right channels")
            routes.append((cable, host, channel, output_id))
            serials.add(serial); links.add(link)

    def validate_edit(self, key, candidate):
        feed = self.get(key)
        current = feed.settings.snapshot()
        if any(candidate[section] != current[section] for section in ("streaming", "tools")):
            if any(value.supervisor.desired for value in self.feeds.values()):
                raise ValueError("Stop both feeds before changing shared streaming tools or ports")
        # Hardware reassignments must not diverge from an already-running process.
        if feed.supervisor.desired:
            for section, names in (("runtime", ("fmp24_args", "dsdplus_args")),
                                   ("audio", ("device_name", "device_host_api", "capture_channel", "blocksize"))):
                if any(candidate[section].get(name) != current[section].get(name) for name in names):
                    raise ValueError("Stop this feed before changing its receiver or audio assignment")
        metadata = candidate.get("feeds", self.settings.section("feeds"))
        if metadata["feed2"]["enabled"]:
            self.validate_pair({other: candidate if other == key else value.settings.snapshot()
                                for other, value in self.feeds.items()})

    def validate_start(self, key):
        if not self.settings.section("feeds")[key]["enabled"]:
            raise ValueError("This feed is disabled")
        if self.settings.section("feeds")["feed2"]["enabled"]:
            self.validate_pair({key: feed.settings.snapshot() for key, feed in self.feeds.items()})
        if key == "feed2" and not self.get(key).config.read("scanlist")["entries"]:
            raise ValueError("Add channels to Feed 2's scan list before starting")

    def configure(self, key, patch):
        with self.lock:
            metadata = self.settings.section("feeds")
            if set(patch) - {"name", "enabled"}:
                raise ValueError("Only name and enabled can be changed here")
            candidate = {**metadata[key], **patch}
            metadata[key] = candidate
            SettingsStore._validate(_merge(self.settings.snapshot(), {"feeds": metadata}))
            if candidate["enabled"] and key == "feed2":
                self.validate_pair({key: feed.settings.snapshot() for key, feed in self.feeds.items()})
                self.get(key).paths.ensure()
                if not (self.get(key).paths.dsdplus / "FMP24.exe").exists():
                    raise ValueError("Provision Feed 2 runtime files first")
            if not candidate["enabled"]:
                self.get(key).runtime.stop()
                self.mixer.clear(key)
            self.settings.update({"feeds": metadata})
            return self.summary(key)

    def provision(self):
        """Only licensed executable dependencies are copied; never user history."""
        target = self.get("feed2").paths.dsdplus
        if self.get("feed2").supervisor.desired:
            raise ValueError("Stop Feed 2 before provisioning")
        for name in ("DSDPlus.exe", "FMP24.exe"):
            if not (self.paths.dsdplus / name).is_file():
                raise ValueError(f"Missing installed runtime file: {name}")
        files = [self.paths.dsdplus / name for name in ("DSDPlus.exe", "FMP24.exe")]
        files += list(self.paths.dsdplus.glob("*.dll"))
        for source in files:
            destination = target / source.name
            if not destination.exists():
                shutil.copy2(source, destination)
        # Preserve tuner calibration defaults, but never copy another scan list.
        config = self.paths.dsdplus / "FMP24.cfg"
        if config.exists() and not (target / config.name).exists():
            shutil.copy2(config, target / config.name)
        # DSDPlus blocks startup on modal errors if these files are absent.
        # Start with empty per-feed tables, never copy another feed's history.
        for suffix in ("networks", "sites", "frequencies", "groups", "radios", "P25data", "siteLoader"):
            table = target / f"DSDPlus.{suffix}"
            if not table.exists():
                table.write_text("; Independent Feed 2 decoder data\n", encoding="ascii")
        scanlist = target / "FMP24.ScanList"
        if not scanlist.exists():
            scanlist.write_text("; Feed 2: add channels in the portal before starting.\n", encoding="utf-8")
        return {"provisioned": True}

    def summary(self, key):
        feed = self.get(key)
        meta = self.settings.section("feeds")[key]
        return {"id": key, **meta, "status": feed.state.snapshot()}

    def bind_loop(self):
        for feed in self.feeds.values():
            feed.events.bind_loop()

    def initialise(self):
        for key, feed in self.feeds.items():
            if key == "feed1":
                feed.migrator.run()
            # A second local runtime must never discover Feed 1's legacy settings.
            feed.audio.recover_partials()
            feed.state.update_component("web", "running", message="Shared API is serving")
            if self.settings.section("feeds")[key]["enabled"]:
                feed.runtime.auto_start()
        self.mixer.start()

    def close(self):
        for feed in self.feeds.values():
            try:
                feed.runtime.close()
            except Exception:
                self.logger.exception("Could not close %s cleanly", feed.feed_id)
            finally:
                if feed.log_handler:
                    feed.log_handler.close()
                    feed.logger.removeHandler(feed.log_handler)
        try:
            self.mixer.close()
        finally:
            self.server.close()
