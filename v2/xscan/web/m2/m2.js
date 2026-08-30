const $ = id => document.getElementById(id);
const state = {
  status: null, calls: [], selectedIndex: -1, mode: 'live', peer: null, whepSession: '',
  reconnectTimer: 0, reconnectAttempt: 0, userPaused: true, connecting: false,
  installPrompt: null, eventSource: null, audioLevel: 0, triggerLevel: 0.0003,
  playIntent: false, recoveryPending: false, lastRecoveryAt: 0,
  connectionGeneration: 0, activeConnect: 0, connectStartedAt: 0
};
const audio = $('audio');
const meter = $('audioMeter');
const meterSegments = Array.from({length: 24}, (_, index) => {
  const segment = document.createElement('i');
  segment.className = 'meter-segment';
  segment.style.height = `${18 + (index / 23) * 72}%`;
  meter.append(segment);
  return segment;
});

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
}
function durationClass(seconds) {
  const value = Number(seconds) || 0;
  return value > 20 ? 'very-long' : value > 8 ? 'long' : value > 3 ? 'medium' : 'short';
}
function formatDuration(seconds) {
  const value = Number(seconds) || 0;
  return value >= 60 ? `${Math.floor(value / 60)}m ${Math.round(value % 60)}s` : `${value.toFixed(value < 10 ? 1 : 0)}s`;
}
function formatTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? '--' : date.toLocaleTimeString([], {hour:'numeric', minute:'2-digit'});
}
function callLabel(call) { return call?.label || call?.talkgroup_alias || call?.frequency || 'Scanner call'; }
function selectedCall() { return state.selectedIndex >= 0 ? state.calls[state.selectedIndex] : null; }
function hostPlaybackBlocked() { return Boolean(state.status?.local_playback_blocked); }

function setTab(name) {
  document.querySelectorAll('.tab').forEach(button => button.classList.toggle('active', button.dataset.tab === name));
  for (const tab of ['live', 'calls']) {
    const panel = $(`${tab}Panel`);
    panel.hidden = tab !== name;
    panel.classList.toggle('active', tab === name);
  }
  const url = new URL(location.href);
  name === 'calls' ? url.searchParams.set('tab', 'calls') : url.searchParams.delete('tab');
  history.replaceState(null, '', url);
}

function setConnection(kind, label, message) {
  const pill = $('connectionPill');
  pill.className = `status-pill ${kind}`;
  pill.querySelector('span').textContent = label;
  if (message) $('sessionMessage').textContent = message;
}

function updateMetadata() {
  if (!('mediaSession' in navigator)) return;
  const current = selectedCall();
  const now = state.status?.now_playing || {};
  const title = state.mode === 'replay' && current ? callLabel(current) : (now.display || now.label || 'XScan Live');
  const detail = state.mode === 'replay' && current
    ? `${current.frequency || 'Scanner'} · ${formatTime(current.started_at)}`
    : `${now.frequency || 'Scanner'} · ${now.mode || 'Live'}`;
  navigator.mediaSession.metadata = new MediaMetadata({
    title, artist: detail, album: state.mode === 'live' ? 'XScan M2 · Live WebRTC' : 'XScan M2 · Call Replay',
    artwork: [{src:'/icons/xscan-192.png', sizes:'192x192', type:'image/png'}, {src:'/icons/xscan-512.png', sizes:'512x512', type:'image/png'}]
  });
  navigator.mediaSession.playbackState = audio.paused ? 'paused' : 'playing';
  try {
    if (state.mode === 'replay' && Number.isFinite(audio.duration) && audio.duration > 0) {
      navigator.mediaSession.setPositionState({duration: audio.duration, playbackRate: audio.playbackRate, position: Math.min(audio.currentTime, audio.duration)});
    } else navigator.mediaSession.setPositionState();
  } catch {}
}

function renderLcd() {
  const now = state.status?.now_playing || {};
  const current = selectedCall();
  const source = state.mode === 'replay' && current ? current : now;
  $('lcdMode').textContent = String(source.mode || 'FM').toUpperCase();
  $('lcdFrequency').textContent = source.frequency || '---.----';
  $('lcdChannel').textContent = state.mode === 'replay' && current ? callLabel(current) : (now.display || now.label || 'WAITING FOR SCANNER');
  $('lcdState').textContent = state.mode === 'replay' ? 'REPLAY' : !audio.paused ? 'MONITOR' : state.connecting ? 'LINKING' : 'STANDBY';
  $('lcdDetail').textContent = state.mode === 'replay' && current
    ? `${formatTime(current.started_at)} · ${formatDuration(current.duration_seconds)}`
    : !audio.paused ? 'WEBRTC / OPUS LIVE AUDIO' : 'TAP LISTEN TO START';
  $('lcdRecord').textContent = state.status?.recording ? 'REC ON' : 'REC OFF';
  const replayLoaded = state.mode === 'replay' && Boolean(current);
  document.body.classList.toggle('history-loaded', replayLoaded);
  $('lcd').className = `lcd-panel ${replayLoaded ? 'replay' : ''} ${state.status?.recording ? 'recording' : ''} ${state.status?.stream_ready ? '' : 'offline'}`;
  updateMetadata();
}

function renderMeter(level = state.audioLevel) {
  state.audioLevel = Math.max(0, Number(level) || 0);
  const db = state.audioLevel > 0 ? Math.max(-80, 20 * Math.log10(state.audioLevel)) : -80;
  const percent = Math.max(0, Math.min(100, (db + 80) / 80 * 100));
  const activeCount = Math.round(percent / 100 * meterSegments.length);
  const triggerDb = Math.max(-80, 20 * Math.log10(Math.max(state.triggerLevel, 0.000001)));
  const triggerIndex = Math.max(0, Math.min(23, Math.round((triggerDb + 80) / 80 * 23)));
  meterSegments.forEach((segment, index) => {
    segment.className = 'meter-segment';
    if (index < activeCount) segment.classList.add('on');
    if (index >= 17 && index < activeCount) segment.classList.add('hot');
    if (index >= 21 && index < activeCount) segment.classList.add('clip');
    if (index === triggerIndex) segment.classList.add('trigger');
  });
  $('meterReadout').textContent = state.audioLevel > 0 ? `${db.toFixed(1)} dB` : '−∞ dB';
  $('triggerReadout').textContent = `TRIGGER ${triggerDb.toFixed(0)} dB`;
  meter.setAttribute('aria-valuenow', String(Math.round(db)));
}

function updateControls() {
  const playing = !audio.paused && !audio.ended;
  const live = state.mode === 'live';
  const glyph = playing ? 'Ⅱ' : '▶';
  $('listenButton').classList.toggle('active', playing);
  $('listenButton').querySelector('.listen-icon').textContent = glyph;
  $('listenButton').querySelector('strong').textContent = playing ? 'PAUSE AUDIO' : live ? 'LISTEN LIVE' : 'PLAY REPLAY';
  $('playerReadout').textContent = state.connecting ? 'CONNECTING' : playing ? (live ? 'LIVE' : 'REPLAY') : 'PAUSED';
  $('listenButton').disabled = hostPlaybackBlocked();
  $('jumpLiveButton').disabled = hostPlaybackBlocked();
  $('replayButton').disabled = hostPlaybackBlocked() || !selectedCall()?.playable;
  renderLcd();
}

function applyStatus(status) {
  state.status = status;
  state.audioLevel = Number(status.audio_level) || state.audioLevel;
  state.triggerLevel = Number(status.audio_trigger_level) || state.triggerLevel;
  $('streamReadout').textContent = status.stream_ready ? 'READY' : status.streaming_enabled ? 'STARTING' : 'DISABLED';
  $('deviceReadout').textContent = status.audio_device_name || 'Scanner input';
  if (status.local_playback_blocked) {
    if (!audio.paused) audio.pause();
    if (state.peer) closePeer();
    state.userPaused = true;
    setConnection('offline', 'HOST SAFE', 'Playback is blocked on the scanner PC to prevent VB-CABLE feedback. Use M2 on your phone.');
  } else if (state.mode === 'replay' && selectedCall()) {
    setConnection('replay', 'REPLAY');
  } else if (!state.connecting && audio.paused) {
    setConnection('offline', status.stream_ready ? 'READY' : 'OFFLINE');
  }
  renderMeter();
  renderLcd();
}

async function loadStatus() {
  try {
    const response = await fetch('/api/m2/status', {cache:'no-store'});
    if (!response.ok) throw new Error(`status ${response.status}`);
    applyStatus(await response.json());
  } catch {
    setConnection('offline', 'OFFLINE', 'Scanner status is unavailable. Retrying…');
  }
}

function renderCallTape() {
  const tape = $('callTape');
  const ordered = [...state.calls].reverse();
  $('callCount').textContent = state.calls.length;
  if (!ordered.length) {
    tape.innerHTML = '<div class="tape-empty">Waiting for completed calls</div>';
    $('recentCalls').innerHTML = '';
    return;
  }
  const selectedPosition = ordered.findIndex(call => call.id === selectedCall()?.id);
  const notchCount = 28;
  let first = Math.max(0, ordered.length - notchCount);
  if (selectedPosition >= 0 && selectedPosition < first) first = Math.max(0, selectedPosition - Math.floor(notchCount / 2));
  const shown = ordered.slice(first, first + notchCount);
  $('tapeStart').textContent = first > 0 ? `OLDER · ${first} MORE` : 'OLDER';
  tape.innerHTML = shown.map(call => {
    const duration = Math.max(.2, Number(call.duration_seconds) || .2);
    const height = Math.min(68, 18 + Math.sqrt(duration) * 8);
    const selected = selectedCall()?.id === call.id ? ' selected' : '';
    return `<button class="call-notch ${durationClass(duration)}${selected}" style="height:${height}px" data-call-id="${escapeHtml(call.id)}" aria-label="Replay ${escapeHtml(callLabel(call))}, ${formatDuration(duration)}" title="${escapeHtml(callLabel(call))} · ${formatDuration(duration)}"></button>`;
  }).join('');
  $('recentCalls').innerHTML = state.calls.slice(0, 12).map((call, index) => `<button class="recent-call ${durationClass(call.duration_seconds)} ${selectedCall()?.id === call.id ? 'selected' : ''}" type="button" data-call-index="${index}"><i class="color"></i><span><strong>${escapeHtml(callLabel(call))}</strong><small>${escapeHtml(call.frequency || '—')} · ${escapeHtml(call.mode || '—')} · ${formatDuration(call.duration_seconds)}</small></span><time>${formatTime(call.started_at)}</time></button>`).join('');
}

function renderSelectedCall() {
  const call = selectedCall();
  $('selectedLabel').textContent = call ? callLabel(call) : 'Live stream';
  $('selectedMeta').textContent = call ? `${call.frequency || '—'} · ${call.mode || '—'} · ${formatTime(call.started_at)} · ${formatDuration(call.duration_seconds)}` : 'No replay selected';
  $('sliderCaption').textContent = call ? `Loaded ${callLabel(call)}. Tap another notch or Return Live.` : 'Tap a notch to load that call. Use Return Live when you are done.';
  renderCallTape();
  updateControls();
}

async function loadCalls() {
  try {
    const response = await fetch('/api/m2/calls?limit=40', {cache:'no-store'});
    if (!response.ok) throw new Error();
    const currentId = selectedCall()?.id;
    state.calls = (await response.json()).items || [];
    if (currentId) state.selectedIndex = state.calls.findIndex(call => call.id === currentId);
    renderSelectedCall();
  } catch {
    $('recentCalls').innerHTML = '<p class="session-message">Recent calls are unavailable.</p>';
  }
}

function waitForIce(peer, timeout = 2500) {
  if (peer.iceGatheringState === 'complete') return Promise.resolve();
  return new Promise(resolve => {
    const timer = setTimeout(done, timeout);
    function done() { clearTimeout(timer); peer.removeEventListener('icegatheringstatechange', change); resolve(); }
    function change() { if (peer.iceGatheringState === 'complete') done(); }
    peer.addEventListener('icegatheringstatechange', change);
  });
}

async function closePeer(notifyServer = true) {
  state.connectionGeneration += 1;
  clearTimeout(state.reconnectTimer);
  state.reconnectTimer = 0;
  const peer = state.peer;
  const session = state.whepSession;
  state.peer = null;
  state.whepSession = '';
  if (peer) {
    peer.ontrack = null;
    peer.onconnectionstatechange = null;
    peer.oniceconnectionstatechange = null;
    peer.close();
  }
  if (notifyServer && session) fetch(session, {method:'DELETE', keepalive:true}).catch(() => {});
}

function canReconnect() { return state.mode === 'live' && state.playIntent && navigator.onLine; }
function liveTrackReady() {
  const stream = audio.srcObject;
  return stream instanceof MediaStream && stream.getAudioTracks().some(track => track.readyState === 'live' && !track.muted);
}
function peerState() { return state.peer?.connectionState || state.peer?.iceConnectionState || 'closed'; }
function scheduleReconnect(message = 'Connection interrupted. Reconnecting…') {
  if (!canReconnect() || state.reconnectTimer) return;
  const delay = Math.min(10_000, 800 * 2 ** Math.min(state.reconnectAttempt++, 4));
  setConnection('connecting', 'RETRYING', message);
  state.reconnectTimer = setTimeout(() => { state.reconnectTimer = 0; startLive(false); }, delay);
}

async function startLive(fromUser = true) {
  if (hostPlaybackBlocked()) {
    state.playIntent = false;
    setConnection('offline', 'HOST SAFE', 'Playback is blocked on the scanner PC to prevent VB-CABLE feedback. Use M2 on your phone.');
    updateControls();
    return;
  }
  if (fromUser) state.playIntent = true;
  if (!state.playIntent) return;
  if (state.connecting) return;
  state.mode = 'live';
  state.selectedIndex = -1;
  state.userPaused = false;
  state.connecting = true;
  state.connectStartedAt = Date.now();
  if (fromUser) state.reconnectAttempt = 0;
  audio.pause();
  audio.removeAttribute('src');
  audio.srcObject = null;
  await closePeer();
  const generation = state.connectionGeneration;
  state.activeConnect = generation;
  setConnection('connecting', 'LINKING', 'Negotiating low-latency WebRTC audio…');
  updateControls();
  try {
    const peer = new RTCPeerConnection({iceServers:[]});
    state.peer = peer;
    peer.addTransceiver('audio', {direction:'recvonly'});
    peer.ontrack = async event => {
      if (state.peer !== peer) return;
      audio.srcObject = event.streams[0] || new MediaStream([event.track]);
      event.track.addEventListener('ended', () => { if (state.peer === peer) recoverLivePlayback('Live track ended. Reconnecting…', true); });
      event.track.addEventListener('mute', () => setTimeout(() => { if (state.peer === peer && event.track.muted) recoverLivePlayback('Live packets stopped. Reconnecting…', true); }, 1200));
      try {
        await audio.play();
        state.reconnectAttempt = 0;
        setConnection('live', 'LIVE', 'Live scanner audio is playing. You can leave this page in the background.');
      } catch {
        state.userPaused = true;
        setConnection('offline', 'READY', 'Android blocked automatic resume. Tap Listen once.');
      }
      updateControls();
    };
    const connectionChanged = () => {
      if (state.peer !== peer) return;
      const value = peer.connectionState || peer.iceConnectionState;
      if (value === 'connected') setConnection('live', 'LIVE');
      else if (value === 'failed') { closePeer(); scheduleReconnect(); }
      else if (value === 'disconnected') scheduleReconnect('Network changed. Reconnecting live audio…');
    };
    peer.onconnectionstatechange = connectionChanged;
    peer.oniceconnectionstatechange = connectionChanged;
    const offer = await peer.createOffer();
    await peer.setLocalDescription(offer);
    await waitForIce(peer);
    if (generation !== state.connectionGeneration) return;
    const response = await fetch('/api/m2/whep', {method:'POST', headers:{'Content-Type':'application/sdp'}, body:peer.localDescription.sdp, cache:'no-store'});
    if (generation !== state.connectionGeneration) return;
    if (!response.ok) throw new Error(`WebRTC handshake failed (${response.status})`);
    const locationHeader = response.headers.get('Location');
    if (locationHeader) state.whepSession = new URL(locationHeader, location.href).toString();
    await peer.setRemoteDescription({type:'answer', sdp:await response.text()});
    setConnection('connecting', 'LINKING', 'Waiting for live Opus audio packets…');
  } catch (error) {
    if (generation !== state.connectionGeneration) return;
    await closePeer();
    setConnection('offline', 'OFFLINE', error?.message || 'Live audio could not connect.');
    scheduleReconnect();
  } finally {
    if (state.activeConnect === generation) {
      state.connecting = false;
      state.activeConnect = 0;
      state.connectStartedAt = 0;
      renderSelectedCall();
    }
  }
}

async function recoverLivePlayback(message = 'Resuming live audio…', force = false) {
  if (!canReconnect() || document.hidden || state.recoveryPending) return;
  const now = Date.now();
  if (!force && now - state.lastRecoveryAt < 1500) return;
  state.lastRecoveryAt = now;
  state.recoveryPending = true;
  clearTimeout(state.reconnectTimer);
  state.reconnectTimer = 0;
  try {
    if (state.connecting) {
      if (!force && Date.now() - state.connectStartedAt < 8000) return;
      state.connecting = false;
      state.activeConnect = 0;
      await closePeer();
    }
    const connection = peerState();
    const stale = !state.peer || ['failed', 'disconnected', 'closed'].includes(connection) || !liveTrackReady();
    if (stale) {
      setConnection('connecting', 'RESUMING', message);
      await startLive(false);
      return;
    }
    if (audio.paused) {
      try {
        await audio.play();
        state.userPaused = false;
        setConnection('live', 'LIVE', 'Live scanner audio resumed.');
      } catch {
        state.userPaused = true;
        setConnection('offline', 'TAP TO RESUME', 'Android requires one tap to restore audio after suspending the browser.');
      }
    }
  } finally {
    state.recoveryPending = false;
    updateControls();
  }
}

async function playCall(index, autoplay = true) {
  const call = state.calls[index];
  if (!call?.playable) return;
  if (hostPlaybackBlocked()) {
    state.mode = 'replay';
    state.selectedIndex = index;
    state.userPaused = true;
    setConnection('offline', 'HOST SAFE', 'Historical call loaded. Audio stays blocked on the scanner PC to prevent VB-CABLE feedback.');
    renderSelectedCall();
    return;
  }
  state.mode = 'replay';
  state.selectedIndex = index;
  state.userPaused = !autoplay;
  await closePeer();
  audio.pause();
  audio.srcObject = null;
  audio.src = call.audio_url;
  audio.load();
  if (autoplay) {
    try { await audio.play(); setConnection('replay', 'REPLAY', `Replaying ${callLabel(call)}.`); }
    catch { state.userPaused = true; setConnection('offline', 'READY', 'Tap Replay to start this call.'); }
  }
  renderSelectedCall();
}

function pausePlayback() {
  state.userPaused = true;
  if (state.mode === 'live') state.playIntent = false;
  audio.pause();
  setConnection(state.mode === 'live' ? 'offline' : 'replay', state.mode === 'live' ? 'PAUSED' : 'REPLAY', state.mode === 'live' ? 'Live audio paused. Tap Listen to resume.' : 'Historical call loaded and paused.');
  updateControls();
}

async function togglePlayback() {
  if (hostPlaybackBlocked()) {
    setConnection('offline', 'HOST SAFE', 'Playback is blocked on the scanner PC to prevent VB-CABLE feedback. Use M2 on your phone.');
    return;
  }
  if (!audio.paused) { pausePlayback(); return; }
  state.userPaused = false;
  if (state.mode === 'live') {
    state.playIntent = true;
    if (state.peer && audio.srcObject) {
      try { await audio.play(); setConnection('live', 'LIVE', 'Live scanner audio resumed.'); }
      catch { await startLive(true); }
    } else await startLive(true);
  } else if (selectedCall()) {
    try { await audio.play(); setConnection('replay', 'REPLAY', `Replaying ${callLabel(selectedCall())}.`); }
    catch { await playCall(state.selectedIndex, true); }
  }
  updateControls();
}

async function previousCall() {
  if (!state.calls.length) return;
  await playCall(state.mode === 'live' ? 0 : Math.min(state.calls.length - 1, state.selectedIndex + 1), true);
}
async function nextCall() {
  if (state.mode !== 'replay') return;
  if (state.selectedIndex > 0) await playCall(state.selectedIndex - 1, true);
  else await startLive(true);
}

function connectEvents() {
  state.eventSource?.close();
  const source = new EventSource('/api/m2/events');
  state.eventSource = source;
  source.addEventListener('snapshot', event => { try { applyStatus(JSON.parse(event.data)); } catch {} });
  source.addEventListener('audio-level', event => { try { renderMeter(JSON.parse(event.data).level); } catch {} });
  source.addEventListener('now-playing', event => {
    try { state.status = {...(state.status || {}), now_playing:JSON.parse(event.data)}; renderLcd(); } catch {}
  });
  source.addEventListener('recording', event => {
    try { state.status = {...(state.status || {}), recording:Boolean(JSON.parse(event.data).active)}; renderLcd(); } catch {}
  });
  source.addEventListener('call-completed', loadCalls);
  source.onerror = () => setTimeout(() => { if (state.eventSource === source && source.readyState === EventSource.CLOSED) connectEvents(); }, 2000);
}

document.querySelectorAll('.tab').forEach(button => button.addEventListener('click', () => setTab(button.dataset.tab)));
$('listenButton').addEventListener('click', togglePlayback);
$('jumpLiveButton').addEventListener('click', () => startLive(true));
$('replayButton').addEventListener('click', () => selectedCall() && playCall(state.selectedIndex, true));
$('callTape').addEventListener('click', event => {
  const button = event.target.closest('[data-call-id]');
  if (!button) return;
  const index = state.calls.findIndex(call => call.id === button.dataset.callId);
  if (index >= 0) playCall(index, true);
});
$('recentCalls').addEventListener('click', event => {
  const button = event.target.closest('[data-call-index]');
  if (button) playCall(Number(button.dataset.callIndex), true);
});
$('volume').addEventListener('input', event => {
  audio.volume = Number(event.target.value) / 100;
  $('volumeValue').value = `${event.target.value}%`;
  localStorage.setItem('xscan-m2-volume', event.target.value);
});
$('installButton').addEventListener('click', async () => {
  if (!state.installPrompt) return;
  state.installPrompt.prompt();
  await state.installPrompt.userChoice.catch(() => {});
  state.installPrompt = null;
  $('installButton').hidden = true;
});

audio.addEventListener('play', () => { state.userPaused = false; updateControls(); });
audio.addEventListener('pause', updateControls);
audio.addEventListener('timeupdate', updateMetadata);
audio.addEventListener('durationchange', updateMetadata);
audio.addEventListener('ended', () => {
  if (state.mode === 'live' && state.playIntent) { scheduleReconnect('Live audio ended. Reconnecting…'); return; }
  state.userPaused = true;
  setConnection(state.mode === 'replay' ? 'replay' : 'offline', state.mode === 'replay' ? 'REPLAY' : 'ENDED', state.mode === 'replay' ? 'Historical call remains loaded. Tap Replay or return Live.' : 'Playback ended.');
  updateControls();
});
audio.addEventListener('error', () => { if (state.mode === 'live') scheduleReconnect('Audio playback failed. Reconnecting…'); else setConnection('offline', 'ERROR', 'This call could not be played.'); });
window.addEventListener('online', () => { if (canReconnect()) scheduleReconnect('Network restored. Reconnecting…'); });
window.addEventListener('offline', () => setConnection('offline', 'OFFLINE', 'Phone network is offline. Live audio will reconnect automatically.'));
window.addEventListener('beforeinstallprompt', event => { event.preventDefault(); state.installPrompt = event; $('installButton').hidden = false; });
window.addEventListener('appinstalled', () => { state.installPrompt = null; $('installButton').hidden = true; });
window.addEventListener('beforeunload', () => closePeer());
function resumeFromBrowserLifecycle() {
  if (!document.hidden && canReconnect()) recoverLivePlayback('Restoring live audio after Android suspended the page…', true);
  updateMetadata();
}
document.addEventListener('visibilitychange', resumeFromBrowserLifecycle);
document.addEventListener('resume', resumeFromBrowserLifecycle);
window.addEventListener('pageshow', resumeFromBrowserLifecycle);
window.addEventListener('focus', resumeFromBrowserLifecycle);

if ('mediaSession' in navigator) {
  for (const [action, handler] of [['play', togglePlayback], ['pause', pausePlayback], ['stop', () => { pausePlayback(); closePeer(); }], ['previoustrack', previousCall], ['nexttrack', nextCall]]) {
    try { navigator.mediaSession.setActionHandler(action, handler); } catch {}
  }
}
if ('serviceWorker' in navigator) window.addEventListener('load', () => navigator.serviceWorker.register('/m2/sw.js', {scope:'/m2/'}).catch(() => {}));

setInterval(() => { $('lcdClock').textContent = new Date().toLocaleTimeString([], {hour12:false}); }, 1000);
setInterval(() => {
  if (!document.hidden && canReconnect() && (audio.paused || !liveTrackReady() || ['failed', 'disconnected', 'closed'].includes(peerState()))) {
    recoverLivePlayback('Live audio stalled. Reconnecting…');
  }
}, 4000);
setInterval(loadStatus, 8000);
setInterval(loadCalls, 10_000);
const savedVolume = Math.max(0, Math.min(100, Number(localStorage.getItem('xscan-m2-volume') ?? 90)));
$('volume').value = String(savedVolume);
$('volumeValue').value = `${savedVolume}%`;
audio.volume = savedVolume / 100;
setTab(new URL(location.href).searchParams.get('tab') === 'calls' ? 'calls' : 'live');
renderMeter(0);
updateControls();
connectEvents();
Promise.all([loadStatus(), loadCalls()]);
