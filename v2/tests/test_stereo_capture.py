"""Split before triggering/recording/streaming; never mix then try to unmix."""
from copy import deepcopy
import time
import wave

import numpy as np
import pytest

from xscan.api import create_app
from xscan.audio import select_capture_channel
from xscan.settings import SettingsStore
from xscan.parsers import parse_fmp_line
from test_feeds import assignments


def stereo_assignments(manager):
    assignments(manager)
    for key, channel, suffix in [('feed1', 'left', 'L'), ('feed2', 'right', 'R')]:
        feed = manager.get(key)
        args = [arg for arg in feed.settings.section('runtime')['dsdplus_args'] if not arg.startswith('-o')]
        feed.settings.update({'audio': {'device_name': 'Shared cable', 'capture_channel': channel},
                              'runtime': {'dsdplus_args': args + ['-o2' + suffix]}})
    return {key: feed.settings.snapshot() for key, feed in manager.feeds.items()}


def test_demultiplex_is_bit_exact_and_never_averages():
    stereo = np.array([[-32768, 32767], [123, -456], [0, 2345]], dtype='<i2')
    for channel, index in [('left', 0), ('right', 1)]:
        assert select_capture_channel(stereo.tobytes(), channel) == stereo[:, index].tobytes()
    assert select_capture_channel(stereo.tobytes(), 'mono') == stereo.tobytes()
    with pytest.raises(ValueError):
        select_capture_channel(b'\x00\x00', 'left')


def test_shared_stereo_assignments_are_explicit_and_non_overlapping(app_paths):
    app = create_app(app_paths)
    manager = app.state.context.feeds
    try:
        data = stereo_assignments(manager)
        manager.validate_pair(data)
        for mutation in ['same_channel', 'different_output', 'different_capture', 'mono']:
            bad = deepcopy(data)
            second = bad['feed2']
            if mutation == 'same_channel':
                second['audio']['capture_channel'] = 'left'
                second['runtime']['dsdplus_args'][-1] = '-o2L'
            elif mutation == 'different_output':
                second['runtime']['dsdplus_args'][-1] = '-o3R'
            elif mutation == 'different_capture':
                second['audio']['device_name'] = 'Other cable'
            else:
                second['audio']['capture_channel'] = 'mono'
                second['runtime']['dsdplus_args'][-1] = '-o2M'
            with pytest.raises(ValueError, match='Shared audio'):
                manager.validate_pair(bad)
        manager.get('feed1').supervisor._desired = True
        with pytest.raises(ValueError, match='Stop this feed'):
            manager.get('feed1').settings.update({'audio': {'capture_channel': 'mono'}})
        manager.get('feed1').supervisor._desired = False
    finally:
        app.state.context.close()


@pytest.mark.parametrize('channel,host,output', [('wrong','Windows WASAPI','-o2L'),
    ('left','MME','-o2L'), ('left','Windows WASAPI','-o2M'), ('right','Windows WASAPI','-o2L')])
def test_invalid_single_feed_route_rejected(app_paths, channel, host, output):
    with pytest.raises(ValueError):
        SettingsStore(app_paths).update({'audio': {'capture_channel':channel, 'device_host_api':host},
                                        'runtime': {'dsdplus_args':['-i20001',output]}})


def test_split_capture_requires_exact_endpoint_even_before_feed2_enabled(app_paths):
    app = create_app(app_paths)
    try:
        manager = app.state.context.feeds
        stereo_assignments(manager)
        audio = manager.get('feed1').audio
        audio.devices = lambda: [{'name':'Shared cable renamed','host_api':'Windows WASAPI','index':1}]
        assert audio.resolve_device() is None
    finally:
        app.state.context.close()


def test_stereo_callback_through_segmenter_wav_and_stream_isolates_feeds(app_paths, monkeypatch):
    app = create_app(app_paths)
    manager = app.state.context.feeds
    streams, published = [], {'feed1': [], 'feed2': []}
    class Capture:
        def __init__(self, **kwargs):
            assert kwargs['channels'] == 2
            self.callback = kwargs['callback']
            self.active = False
            streams.append(self)
        def start(self): self.active = True
        def stop(self): self.active = False
        def close(self): self.active = False
    monkeypatch.setattr('xscan.audio.sd.RawInputStream', Capture)
    monkeypatch.setattr('xscan.audio.sd.WasapiSettings', lambda **kwargs: kwargs)
    try:
        stereo_assignments(manager)
        for key, feed in manager.feeds.items():
            feed.audio.ffmpeg = None
            feed.settings.update({'audio':{'minimum_seconds':0, 'pre_roll_seconds':0}})
            feed.audio.resolve_device = lambda: dict(name='Shared cable', index=1,
                host_api='Windows WASAPI', input_channels=2, default_sample_rate=48000)
            monkeypatch.setattr(feed.streaming, 'start', lambda rate: True)
            monkeypatch.setattr(feed.streaming, 'stop', lambda: None)
            monkeypatch.setattr(feed.streaming, 'write', published[key].append)
            feed.state.set_fmp(parse_fmp_line('Tuning to ' + ('155.100' if key == 'feed1' else '158.835') + ' FM ' + key))
            assert feed.audio.start()
        # Opposite lane must not start a recording or meter on the silent feed.
        first_only = np.column_stack((np.full(2048, 12000), np.zeros(2048))).astype('<i2').tobytes()
        for stream in streams: stream.callback(first_only, 2048, None, None)
        deadline = time.monotonic() + 3
        while manager.get('feed1').audio._session is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert manager.get('feed1').audio._session is not None
        assert manager.get('feed2').audio._session is None
        both = np.column_stack((np.full(2048, 12000), np.full(2048, -7000))).astype('<i2').tobytes()
        for _ in range(10):
            for stream in streams: stream.callback(both, 2048, None, None)
        deadline = time.monotonic() + 3
        while (manager.get('feed2').audio._session is None or any(not f.audio._queue.empty() for f in manager.feeds.values())) and time.monotonic() < deadline:
            time.sleep(.01)
        manager.get('feed1').audio.stop()
        assert streams[1].active  # independent capture lifetimes on one endpoint
        streams[1].callback(both, 2048, None, None)
        time.sleep(.05)
        manager.get('feed2').audio.stop()
        deadline = time.monotonic() + 3
        while any(f.database.list_calls()['total'] != 1 for f in manager.feeds.values()) and time.monotonic() < deadline:
            time.sleep(.01)
        for key, expected in [('feed1',12000), ('feed2',-7000)]:
            feed = manager.get(key)
            call = feed.database.list_calls()['items'][0]
            assert call['feed_id'] == key and call['label'] == key
            with wave.open(str(feed.paths.recordings / call['audio_file']), 'rb') as wav:
                assert wav.getnchannels() == 1
                samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2')
            assert set(samples) <= {0, expected} and expected in samples
            live = np.frombuffer(b''.join(published[key]), dtype='<i2')
            assert set(live) <= {0, expected} and expected in live
    finally:
        app.state.context.close()


def test_stereo_start_refuses_mono_hardware_without_fallback(app_paths):
    app = create_app(app_paths)
    try:
        manager = app.state.context.feeds
        stereo_assignments(manager)
        audio = manager.get('feed1').audio
        audio.resolve_device = lambda: dict(name='Shared cable', input_channels=1)
        assert not audio.start()
        assert audio.stream is None
    finally:
        app.state.context.close()
