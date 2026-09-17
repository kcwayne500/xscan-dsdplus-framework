from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import sqlite3

import numpy as np
import pytest
from fastapi.testclient import TestClient

from xscan.api import create_app
from xscan.audio import AudioEngine
from xscan.database import Database
from xscan.multimedia import PcmMixer
from xscan.settings import SettingsStore


def login(client):
    response = client.post('/api/v1/auth/setup', json={'password': 'TestScannerPassword123'})
    assert response.status_code == 200
    return {'X-CSRF-Token': client.cookies['xscan_csrf']}


def assignments(manager):
    for number, feed in enumerate(manager.feeds.values(), 1):
        feed.settings.update({'runtime': {'fmp24_args': [f'-i"TEST000{number}"', f'-o{20000+number}', '-s1'],
            'dsdplus_args': ['-r1', f'-i{20000+number}', f'-o{number}M']},
            'audio': {'device_name': f'Test cable {number}'}})


def test_database_upgrade_preserves_legacy_records(app_paths):
    with sqlite3.connect(app_paths.database) as db:
        from xscan.database import SCHEMA
        db.executescript(SCHEMA)
        db.execute("INSERT INTO calls(id,started_at,created_at,audio_file,note) VALUES ('old','2020','2020','old.wav','keep')")
    first = Database(app_paths)
    second = Database(app_paths.for_feed('feed2'), 'feed2')
    assert first.get_call('old')['feed_id'] == 'feed1'
    assert first.get_call('old')['note'] == 'keep'
    assert second.get_call('old') is None
    assert Database(app_paths).list_calls()['total'] == 1
    backups = list((app_paths.backups / 'schema').glob('*.db'))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup:
        assert 'feed_id' not in [row[1] for row in backup.execute('PRAGMA table_info(calls)')]
        assert backup.execute('SELECT note FROM calls').fetchone()[0] == 'keep'


def test_settings_cannot_bypass_feed_enable_and_restore_lock(app_paths):
    import json
    app = create_app(app_paths)
    with TestClient(app) as client:
        headers = login(client)
        assert client.put('/api/v1/settings', headers=headers,
            json={'feeds':{'feed2':{'enabled':True}}}).status_code == 422
        feed = app.state.context.feeds.get('feed1')
        data = feed.settings.snapshot()
        data['runtime']['hardware_control_enabled'] = True
        data['feeds']['feed2']['enabled'] = True
        folder = app_paths.backups / 'settings'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'old.json').write_text(json.dumps(data))
        response = client.post('/api/v1/settings/restore', headers=headers, json={'backup':'old.json'})
        assert response.status_code == 200
        assert response.json()['runtime']['hardware_control_enabled'] is False
        assert response.json()['feeds']['feed2']['enabled'] is False


def test_whep_sessions_are_source_scoped(app_paths, monkeypatch):
    import httpx
    calls = []
    class Upstream:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            calls.append(url)
            return httpx.Response(201, headers={'Location':url + '/session-1'}, content=b'answer')
        async def request(self, method, url, **kwargs):
            calls.append(url)
            return httpx.Response(204)
    monkeypatch.setattr('xscan.api.httpx.AsyncClient', Upstream)
    app = create_app(app_paths)
    with TestClient(app) as client:
        app.state.context.root_settings.update({'feeds':{'feed2':{'enabled':True}}})
        second = client.post('/api/m2/feeds/feed2/whep', content=b'offer')
        assert second.status_code == 201
        location = second.headers['Location']
        token = location.rsplit('/',1)[1]
        before = len(calls)
        assert client.patch('/api/m2/whep/' + token).status_code == 404
        assert client.patch('/api/m2/mix/whep/' + token).status_code == 404
        assert len(calls) == before
        assert client.delete(location).status_code == 204
        mixed = client.post('/api/m2/mix/whep', content=b'offer')
        token = mixed.headers['Location'].rsplit('/',1)[1]
        assert client.patch('/api/m2/whep/' + token).status_code == 404
        assert client.delete(mixed.headers['Location']).status_code == 204
        assert any('/scanner-feed2/' in url for url in calls)
        assert any('/scanner-both/' in url for url in calls)


def test_simultaneous_recordings_keep_their_original_pcm(app_paths):
    import time
    import wave
    from xscan.audio import TriggerEvent
    app = create_app(app_paths)
    manager = app.state.context.feeds
    try:
        def record(item):
            key, feed = item
            feed.audio.ffmpeg = None  # Exercise actual WAV writer/finalizer, without a codec dependency.
            value = 1000 if key == 'feed1' else -2000
            pcm = np.full(48000, value, dtype='<i2').tobytes()
            feed.audio._handle_trigger(TriggerEvent('start', pcm), 48000, key, 1.0)
            feed.audio._handle_trigger(TriggerEvent('stop'), 48000, key, 2.0)
            return value
        with ThreadPoolExecutor(2) as pool:
            list(pool.map(record, manager.feeds.items()))
        deadline = time.monotonic() + 5
        while any(feed.database.list_calls()['total'] != 1 for feed in manager.feeds.values()) and time.monotonic() < deadline:
            time.sleep(.02)
        for key, feed in manager.feeds.items():
            call = feed.database.list_calls()['items'][0]
            assert call['feed_id'] == key
            with wave.open(str(feed.paths.recordings / call['audio_file']), 'rb') as wav:
                pcm = np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2')
            assert np.all(pcm == (1000 if key == 'feed1' else -2000))
    finally:
        app.state.context.close()


def test_optional_mixer_failure_never_interrupts_capture(app_paths):
    from xscan.streaming import StreamingManager
    from xscan.events import EventBus
    from xscan.state import RuntimeState
    import logging
    def fail(*args): raise RuntimeError('simulated mixer failure')
    events = EventBus()
    publisher = StreamingManager(app_paths, SettingsStore(app_paths), RuntimeState(app_paths, events),
        events, logging.getLogger('test'), on_pcm=fail)
    publisher.write(b'\x00\x00' * 960)
    publisher.start(24000)
    assert publisher.sample_rate == 24000


def test_instance_lock_prevents_second_owner_and_releases(app_paths):
    from xscan.instance import InstanceLock
    with InstanceLock(app_paths.state):
        with pytest.raises(RuntimeError, match='already owns'):
            InstanceLock(app_paths.state)
    with InstanceLock(app_paths.state):
        pass


def test_independent_history_files_and_bulk_actions(app_paths):
    first, second = Database(app_paths), Database(app_paths.for_feed('feed2'), 'feed2')
    for db, value in ((first, b'one'), (second, b'two')):
        db.paths.ensure()
        (db.paths.recordings / 'same.wav').write_bytes(value)
        db.add_call({'id': db.feed_id, 'audio_file': 'same.wav', 'source_ref': 'same-import'})
    assert first.list_calls()['total'] == second.list_calls()['total'] == 1
    assert second.update_call('feed1', {'note':'wrong'}) is None
    assert second.trash_calls(['feed1']) == 0
    assert first.trash_calls(['feed1','feed2']) == 1
    assert (second.paths.recordings / 'same.wav').read_bytes() == b'two'
    assert second.purge_calls(['feed1']) == 0
    assert first.restore_calls(['feed1']) == 1
    assert (first.paths.recordings / 'same.wav').read_bytes() == b'one'


def test_scoped_api_auth_history_and_parallel_requests(app_paths):
    app = create_app(app_paths)
    with TestClient(app) as client:
        assert client.get('/api/v1/feeds/feed2/settings').status_code == 428
        headers = login(client)
        for key, feed in app.state.context.feeds.feeds.items():
            (feed.paths.recordings / 'same.wav').write_bytes(key.encode())
            feed.database.add_call({'id': key, 'audio_file':'same.wav', 'label':key})
        assert client.get('/api/m2/calls').json()['items'][0]['id'] == 'feed1'
        def read(key):
            for _ in range(10):
                payload = client.get(f'/api/m2/feeds/{key}/calls').json()
                assert [item['id'] for item in payload['items']] == [key]
        with ThreadPoolExecutor(2) as pool:
            list(pool.map(read, ['feed1','feed2']))
        assert client.get('/api/m2/feeds/feed2/calls/feed1/audio').status_code == 404
        assert client.get('/api/m2/feeds/feed2/calls/feed2/audio').content == b'feed2'
        assert client.post('/api/v1/feeds/feed2/system/stop').status_code == 403
        assert client.post('/api/v1/feeds/feed2/system/stop', headers=headers).status_code == 200
        assert client.get('/api/m2/feeds/missing/status').status_code == 404
        # Old endpoints continue to resolve Feed 1 after scoped calls complete.
        assert client.get('/api/v1/status').status_code == 200
        assert app.state.context.database.feed_id == 'feed1'


def test_second_feed_disabled_and_conflicts_rejected(app_paths):
    app = create_app(app_paths)
    manager = app.state.context.feeds
    try:
        assert manager.settings.section('feeds')['feed2']['enabled'] is False
        with pytest.raises(ValueError, match='disabled'):
            manager.get('feed2').runtime.start()
        assignments(manager)
        data = {key: feed.settings.snapshot() for key, feed in manager.feeds.items()}
        manager.validate_pair(data)
        bad = deepcopy(data)
        bad['feed2']['audio']['device_name'] = bad['feed1']['audio']['device_name']
        with pytest.raises(ValueError, match='audio cable'):
            manager.validate_pair(bad)
        bad = deepcopy(data)
        bad['feed2']['runtime']['fmp24_args'][0] = bad['feed1']['runtime']['fmp24_args'][0]
        with pytest.raises(ValueError, match='serial'):
            manager.validate_pair(bad)
    finally:
        app.state.context.close()


def test_stop_one_feed_does_not_stop_other_or_server(app_paths, monkeypatch):
    app = create_app(app_paths)
    manager = app.state.context.feeds
    calls = []
    try:
        monkeypatch.setattr(manager.get('feed1').audio, 'stop', lambda: calls.append('one'))
        monkeypatch.setattr(manager.get('feed2').audio, 'stop', lambda: calls.append('two'))
        monkeypatch.setattr(manager.server, 'close', lambda: calls.append('server'))
        manager.get('feed2').runtime.stop()
        assert calls == ['two']
    finally:
        app.state.context.close()


def test_mixer_two_signals_no_clipping_missing_feed_and_bounds(app_paths):
    mixer = PcmMixer(SettingsStore(app_paths), None)
    mixer.put('feed1', np.full(960, 30000, dtype='<i2').tobytes(), 48000)
    mixer.put('feed2', np.full(960, 10000, dtype='<i2').tobytes(), 48000)
    assert np.all(np.frombuffer(mixer.render(), dtype='<i2') == 20000)
    mixer.put('feed1', np.full(480, 20000, dtype='<i2').tobytes(), 24000)
    assert np.all(np.frombuffer(mixer.render(), dtype='<i2') == 10000)
    assert not np.frombuffer(mixer.render(), dtype='<i2').any()
    for _ in range(100):
        mixer.put('feed1', b'\x01\x00' * 960, 48000)
    assert mixer.queues['feed1'].qsize() == 8
    assert mixer.drops['feed1'] > 0
    mixer.clear('feed1')
    assert not np.frombuffer(mixer.render(), dtype='<i2').any()


def test_strict_audio_never_matches_other_cable_or_host(app_paths, monkeypatch):
    audio = object.__new__(AudioEngine)
    audio.settings = SettingsStore(app_paths)
    audio.settings.update({'audio': {'strict_device':True, 'device_name':'CABLE-A Output'}})
    audio.devices = lambda: [{'index':1,'name':'CABLE-B Output','host_api':'Windows WASAPI'},
                            {'index':2,'name':'CABLE-A Output','host_api':'MME'}]
    assert audio.resolve_device() is None


def test_provision_does_not_copy_recordings_or_scanlist(app_paths):
    app = create_app(app_paths)
    try:
        for name in ('DSDPlus.exe','FMP24.exe','example.dll'):
            (app_paths.dsdplus / name).write_bytes(b'fixture')
        (app_paths.dsdplus / 'FMP24.ScanList').write_text('private frequencies')
        app.state.context.feeds.provision()
        second = app.state.context.feeds.get('feed2')
        assert (second.paths.dsdplus / 'FMP24.exe').exists()
        assert 'private' not in (second.paths.dsdplus / 'FMP24.ScanList').read_text()
        for suffix in ('networks','sites','frequencies','groups','radios','P25data','siteLoader'):
            assert (second.paths.dsdplus / f'DSDPlus.{suffix}').is_file()
        assert second.database.list_calls()['total'] == 0
    finally:
        app.state.context.close()
