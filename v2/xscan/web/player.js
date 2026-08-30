import { cookie } from './api.js';

function csrfHeaders(extra = {}) { return { ...extra, 'X-CSRF-Token': cookie('xscan_csrf') }; }

async function waitForIce(pc, timeout = 2500) {
  if (pc.iceGatheringState === 'complete') return;
  await new Promise(resolve => {
    const timer = setTimeout(done, timeout);
    function done() { clearTimeout(timer); pc.removeEventListener('icegatheringstatechange', changed); resolve(); }
    function changed() { if (pc.iceGatheringState === 'complete') done(); }
    pc.addEventListener('icegatheringstatechange', changed);
  });
}

export class Player {
  constructor(audio, onState) {
    this.audio = audio;
    this.onState = onState;
    this.pc = null;
    this.hls = null;
    this.sessionUrl = '';
    this.mode = 'idle';
    this.label = 'Waiting for scanner';
    this.nativeTimer = null;
    this.nativePlaying = false;
    audio.volume = .85;
    audio.addEventListener('play', () => this.emit('playing'));
    audio.addEventListener('pause', () => this.emit('paused'));
    audio.addEventListener('ended', () => this.emit('ended'));
    audio.addEventListener('error', () => this.emit('error'));
    audio.addEventListener('timeupdate', () => this.emit('progress'));
    audio.addEventListener('durationchange', () => this.emit('progress'));
  }
  emit(state, message = '') { this.onState?.({ state, mode: this.mode, label: message || this.label, playing: this.nativeTimer ? this.nativePlaying : !this.audio.paused, currentTime: this.audio.currentTime || 0, duration: Number.isFinite(this.audio.duration) ? this.audio.duration : 0 }); }
  setMediaSession() {
    if (!('mediaSession' in navigator)) return;
    navigator.mediaSession.metadata = new MediaMetadata({ title: this.label, artist: 'XScan Radio Console', album: this.mode === 'live' ? 'Live scanner audio' : 'Recorded call', artwork: [{src:'/icons/xscan-192.png',sizes:'192x192',type:'image/png'},{src:'/icons/xscan-512.png',sizes:'512x512',type:'image/png'}] });
    navigator.mediaSession.setActionHandler('play', () => this.audio.play());
    navigator.mediaSession.setActionHandler('pause', () => this.audio.pause());
    navigator.mediaSession.setActionHandler('stop', () => this.close());
  }
  async live(autoplay = true) {
    await this.close();
    this.mode = 'live'; this.label = 'Live scanner audio'; this.emit('connecting');
    this.setMediaSession();
    if (window.XScanAndroid?.listenLive) {
      const result = window.XScanAndroid.listenLive();
      if (result === 'pair_required') throw new Error('Pairing the Android player. Tap Listen Live again in a moment.');
      this.watchNativePlayback();
      this.emit('connecting', 'Connecting to live scanner audio…');
      return;
    }
    const mobile = matchMedia('(max-width: 780px)').matches || /Android|iPhone|iPad/i.test(navigator.userAgent);
    const transports = mobile
      ? [['HLS', () => this.liveHls(autoplay)], ['WebRTC', () => this.liveWebRtc(autoplay)]]
      : [['WebRTC', () => this.liveWebRtc(autoplay)], ['HLS', () => this.liveHls(autoplay)]];
    for (const [name, connect] of transports) {
      try { await connect(); return; }
      catch (error) {
        console.warn(`${name} live audio unavailable; trying fallback`, error);
        this.resetTransport();
      }
    }
    this.mode = 'idle';
    this.label = 'Live audio is reconnecting — try again';
    this.emit('unavailable', this.label);
    throw new Error('Live audio is reconnecting. Please try again in a few seconds.');
  }
  resetTransport() {
    this.audio.pause(); this.audio.srcObject = null; this.audio.removeAttribute('src');
    if (this.hls) { this.hls.destroy(); this.hls=null; }
    if (this.pc) { this.pc.close(); this.pc=null; }
    if (this.sessionUrl) {
      fetch(this.sessionUrl, { method: 'DELETE', credentials: 'same-origin', headers: csrfHeaders() }).catch(() => {});
      this.sessionUrl = '';
    }
  }
  watchNativePlayback() {
    clearInterval(this.nativeTimer);
    const read = () => {
      try {
        const status = JSON.parse(window.XScanAndroid.getPlaybackStatus());
        this.nativePlaying = status.state === 'playing';
        this.emit(status.state || 'connecting', status.message || this.label);
      } catch { this.emit('connecting', 'Starting Android background audio…'); }
    };
    read(); this.nativeTimer = setInterval(read, 800);
  }
  async liveHls(autoplay = true) {
    const path='/api/v1/stream/hls/scanner/index.m3u8';
    this.audio.srcObject=null;
    if (window.Hls?.isSupported()) {
      const hls=new window.Hls({lowLatencyMode:true,liveSyncDurationCount:2,maxLiveSyncPlaybackRate:1.5,backBufferLength:0,xhrSetup:xhr=>{xhr.withCredentials=true;}}); this.hls=hls;
      await new Promise((resolve,reject)=>{ const timer=setTimeout(()=>reject(new Error('HLS startup timed out')),8000); hls.once(window.Hls.Events.MANIFEST_PARSED,()=>{clearTimeout(timer);resolve();}); hls.once(window.Hls.Events.ERROR,(_,data)=>{if(data.fatal){clearTimeout(timer);reject(new Error(data.details||'HLS failed'));}}); hls.loadSource(path); hls.attachMedia(this.audio); });
    } else if (this.audio.canPlayType('application/vnd.apple.mpegurl')) {
      this.audio.src=path; this.audio.load();
    } else throw new Error('HLS is not supported');
    if (autoplay) try { await this.audio.play(); } catch { this.emit('ready'); }
  }
  async liveWebRtc(autoplay = true) {
    const pc = new RTCPeerConnection(); this.pc = pc;
    pc.addTransceiver('audio', { direction: 'recvonly' });
    pc.ontrack = async event => {
      this.audio.removeAttribute('src'); this.audio.srcObject = event.streams[0];
      if (autoplay) try { await this.audio.play(); } catch { this.emit('ready'); }
    };
    pc.onconnectionstatechange = () => this.emit(pc.connectionState);
    await pc.setLocalDescription(await pc.createOffer());
    await waitForIce(pc);
    const response = await fetch('/api/v1/stream/whep', {
      method: 'POST', credentials: 'same-origin', headers: csrfHeaders({ 'Content-Type': 'application/sdp' }), body: pc.localDescription.sdp,
    });
    if (!response.ok) throw new Error(await response.text() || 'Live stream unavailable');
    this.sessionUrl = response.headers.get('location') || '';
    await pc.setRemoteDescription({ type: 'answer', sdp: await response.text() });
    this.emit('ready');
  }
  async replay(call) {
    await this.close();
    this.mode = 'replay'; this.label = call.label || call.frequency || 'Recorded call';
    this.setMediaSession();
    this.audio.srcObject = null; this.audio.src = `/api/v1/calls/${call.id}/audio`; this.audio.load();
    try { await this.audio.play(); } catch { this.emit('ready'); }
  }
  async toggle() {
    if (window.XScanAndroid?.togglePlayback && this.mode === 'live') {
      const result=window.XScanAndroid.togglePlayback();
      if(result==='pair_required') throw new Error('Android background player is not paired yet');
      this.watchNativePlayback(); return;
    }
    if (this.mode === 'idle') return this.live(true);
    if (this.audio.paused) return this.audio.play();
    this.audio.pause();
  }
  async primary() {
    if (this.mode === 'idle') return this.live(true);
    return this.toggle();
  }
  async close() {
    clearInterval(this.nativeTimer); this.nativeTimer=null; this.nativePlaying=false;
    if (window.XScanAndroid?.stopPlayback && this.mode === 'live') window.XScanAndroid.stopPlayback();
    this.resetTransport();
  }
  volume(value) { this.audio.volume = Math.max(0, Math.min(1, Number(value) / 100)); }
  seek(ratio) { if (this.mode === 'replay' && Number.isFinite(this.audio.duration)) this.audio.currentTime = this.audio.duration * Math.max(0, Math.min(1, Number(ratio))); }
}
