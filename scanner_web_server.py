import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE_HTML = """<!doctype html>
<html lang=\"en\">
<head>
<meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
<title>Scanner Live</title>
<style>
:root{--bg:#11161c;--panel:#1a212b;--panel2:#202936;--line:#334155;--muted:#94a3b8;--text:#e5edf5;--lcd:#cdd9a7;--lcdfg:#1f2916;--lcdb:#95a46c;--ok:#22c55e;--bad:#ef4444;--accent:#93c5fd}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at top,#1b2430 0%,var(--bg) 60%);color:var(--text);font-family:"Segoe UI",Tahoma,sans-serif}
.shell{max-width:980px;margin:0 auto;padding:18px}.topbar,.meta{display:grid;gap:12px}.topbar{grid-template-columns:1.2fr 1fr 1fr;margin-bottom:12px}.meta{grid-template-columns:repeat(4,minmax(0,1fr));margin-bottom:12px}
.card{background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:16px;padding:14px 16px;box-shadow:0 14px 34px rgba(0,0,0,.28)}
.label{font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);margin-bottom:8px}.value{font-size:20px;font-weight:700}
.lcd{background:linear-gradient(180deg,#d9e5b6,var(--lcd));color:var(--lcdfg);border:3px solid var(--lcdb);border-radius:18px;padding:18px 20px;font-family:Consolas,"Lucida Console",monospace;font-size:clamp(26px,4vw,42px);font-weight:700;min-height:112px;display:flex;align-items:center;text-shadow:0 1px 0 rgba(255,255,255,.5);margin-bottom:12px;word-break:break-word}.lcd.recording{background:linear-gradient(180deg,#e7b2b2,#d9a3a3);border-color:#b57171;color:#4c1717}
.deck{margin-top:8px;padding:14px;border-radius:18px;border:1px solid var(--line);background:radial-gradient(circle at top left,rgba(147,197,253,.08),transparent 35%),linear-gradient(180deg,#10161f,#0c1118)}
.decktop,.metarow,.footer{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}.decktop{align-items:center;margin-bottom:12px}.metarow{align-items:center;margin-top:12px;color:var(--muted);font-size:13px}.footer{margin-top:10px;color:var(--muted);font-size:13px}
.decktitle{font-size:16px;font-weight:700;letter-spacing:.04em}.badge{display:inline-flex;align-items:center;gap:8px;padding:7px 11px;border-radius:999px;border:1px solid var(--line);background:rgba(15,23,32,.9);color:var(--muted);font-size:13px;font-weight:600}
.signal{display:flex;align-items:flex-end;gap:4px;height:22px}.signal span{width:5px;border-radius:99px;background:#324151;opacity:.55}.signal span:nth-child(1){height:7px}.signal span:nth-child(2){height:11px}.signal span:nth-child(3){height:15px}.signal span:nth-child(4){height:19px}.signal.live span{background:linear-gradient(180deg,#b8f08e,var(--ok));opacity:1}
.controls{display:grid;grid-template-columns:auto auto minmax(140px,1fr) auto;gap:10px;align-items:center}.btn{appearance:none;border:1px solid var(--line);background:linear-gradient(180deg,#243244,#18222f);color:var(--text);border-radius:12px;padding:10px 14px;min-width:86px;font-size:14px;font-weight:700;cursor:pointer}.btn.secondary{min-width:62px}.btn:disabled{opacity:.55;cursor:not-allowed}
.vol{display:flex;align-items:center;gap:10px;min-width:0;padding:10px 12px;border-radius:12px;border:1px solid #263243;background:rgba(16,22,30,.78)}.vol input{width:100%;accent-color:#93c5fd}.hidden{width:0;height:0;opacity:0;position:absolute;pointer-events:none}a{color:var(--accent)}
@media (max-width:760px){.topbar,.meta{grid-template-columns:1fr}.controls{grid-template-columns:1fr 1fr}}
</style>
</head>
<body>
<div class=\"shell\">
<div class=\"topbar\">
<div class=\"card\"><div class=\"label\">System Status</div><div class=\"value\" id=\"statusValue\">STOPPED</div></div>
<div class=\"card\"><div class=\"label\">Streaming</div><div class=\"value\" id=\"streamValue\">OFF</div></div>
<div class=\"card\"><div class=\"label\">Recording</div><div class=\"value\" id=\"recordValue\">OFF</div></div>
</div>
<div class=\"card\">
<div class=\"label\">Active Channel</div><div class=\"lcd\" id=\"channelDisplay\">---</div>
<div class=\"meta\">
<div class=\"card\"><div class=\"label\">Frequency</div><div class=\"value\" id=\"freqValue\">---</div></div>
<div class=\"card\"><div class=\"label\">Mode</div><div class=\"value\" id=\"modeValue\">---</div></div>
<div class=\"card\"><div class=\"label\">Audio Device</div><div class=\"value\" id=\"deviceValue\">---</div></div>
<div class=\"card\"><div class=\"label\">Updated</div><div class=\"value\" id=\"updatedValue\">---</div></div>
</div>
<div class=\"card\" style=\"margin-bottom:12px\"><div class=\"label\">Raw Log Line</div><div id=\"rawValue\" style=\"color:var(--muted);font-size:15px;word-break:break-word\">---</div></div>
<div class=\"label\">Live Audio</div>
<div class=\"deck\">
<div class=\"decktop\"><div class=\"decktitle\">Scanner Audio Deck</div><div class=\"badge\"><div class=\"signal\" id=\"signalBars\"><span></span><span></span><span></span><span></span></div><span id=\"playerStatus\">Waiting for live stream...</span></div></div>
<div class=\"controls\">
<button class=\"btn\" id=\"playButton\" type=\"button\">Listen</button>
<button class=\"btn secondary\" id=\"muteButton\" type=\"button\">Mute</button>
<div class=\"vol\"><span>VOL</span><input id=\"volumeSlider\" type=\"range\" min=\"0\" max=\"100\" value=\"85\"><span id=\"volumeValue\">85%</span></div>
<div class=\"badge\">WebRTC direct</div>
</div>
<div class=\"metarow\"><div id=\"streamHint\">Streaming disabled</div><div><a id=\"rawLink\" href=\"#\" target=\"_blank\" rel=\"noopener\">Open raw stream page</a></div></div>
<audio id=\"scannerAudio\" class=\"hidden\" autoplay playsinline></audio>
</div>
<div class=\"footer\"><div>The custom player uses MediaMTX WebRTC under the hood.</div><div id=\"sessionState\">Idle</div></div>
</div>
</div>
<script>
const audioEl=document.getElementById('scannerAudio'),playButton=document.getElementById('playButton'),muteButton=document.getElementById('muteButton'),volumeSlider=document.getElementById('volumeSlider'),volumeValue=document.getElementById('volumeValue'),signalBars=document.getElementById('signalBars'),playerStatus=document.getElementById('playerStatus'),sessionState=document.getElementById('sessionState');
let latestStatus=null,peerConnection=null,playerStarting=false,lastStreamUrl='',userPaused=false;
function setPlayerStatus(text,live){playerStatus.textContent=text;signalBars.classList.toggle('live',!!live)}
function setSessionState(text){sessionState.textContent=text}
function updateButtons(){playButton.textContent=audioEl.paused?'Listen':'Pause';muteButton.textContent=audioEl.muted?'Unmute':'Mute'}
function setVolume(value){const n=Math.max(0,Math.min(100,value));audioEl.volume=n/100;volumeSlider.value=n;volumeValue.textContent=n+'%'}
function closePlayer(){if(peerConnection){try{peerConnection.ontrack=null}catch(err){}try{peerConnection.close()}catch(err){}peerConnection=null}audioEl.srcObject=null;playerStarting=false;updateButtons()}
function waitForIceComplete(pc,timeoutMs){return new Promise((resolve)=>{if(pc.iceGatheringState==='complete'){resolve();return}const timeout=setTimeout(done,timeoutMs);function onChange(){if(pc.iceGatheringState==='complete')done()}function done(){clearTimeout(timeout);pc.removeEventListener('icegatheringstatechange',onChange);resolve()}pc.addEventListener('icegatheringstatechange',onChange)})}async function startPlayer(autoStart){if(playerStarting)return;if(!latestStatus||!latestStatus.streaming_enabled||!latestStatus.raw_stream_url){setPlayerStatus('Streaming disabled',false);setSessionState('No stream available');return}playerStarting=true;playButton.disabled=true;setPlayerStatus('Connecting...',false);setSessionState('Negotiating WebRTC session');closePlayer();try{const pc=new RTCPeerConnection({iceServers:[]});peerConnection=pc;pc.addTransceiver('audio',{direction:'recvonly'});pc.ontrack=async(event)=>{const stream=event.streams[0];if(stream){audioEl.srcObject=stream;if(!userPaused||autoStart){try{await audioEl.play()}catch(err){setSessionState('Press Listen to start audio')}}updateButtons()}};pc.onconnectionstatechange=()=>{const state=pc.connectionState;if(state==='connected'){setPlayerStatus('Live audio connected',true);setSessionState('Media path is direct WebRTC')}else if(state==='connecting'){setPlayerStatus('Connecting...',false);setSessionState('Establishing peer connection')}else if(state==='failed'||state==='disconnected'||state==='closed'){setPlayerStatus('Connection lost',false);setSessionState('Reconnect by pressing Listen')}};const offer=await pc.createOffer();await pc.setLocalDescription(offer);await waitForIceComplete(pc,1500);const res=await fetch('/api/whep',{method:'POST',headers:{'Content-Type':'application/sdp'},body:pc.localDescription.sdp,cache:'no-store'});if(!res.ok)throw new Error('WHEP request failed with status '+res.status);const answer=await res.text();await pc.setRemoteDescription({type:'answer',sdp:answer});setPlayerStatus('Starting audio...',false);setSessionState('Waiting for first audio packets')}catch(err){closePlayer();setPlayerStatus('Player unavailable',false);setSessionState(err&&err.message?err.message:'Failed to start player')}finally{playerStarting=false;playButton.disabled=false;updateButtons()}}
async function refresh(){try{const res=await fetch('/api/status',{cache:'no-store'});const data=await res.json();latestStatus=data;document.getElementById('statusValue').textContent=(data.status||'STOPPED').toUpperCase();document.getElementById('streamValue').textContent=(data.stream_status||'OFF').toUpperCase();document.getElementById('recordValue').textContent=data.recording?'ON':'OFF';document.getElementById('channelDisplay').textContent=data.display||'---';document.getElementById('channelDisplay').classList.toggle('recording',!!data.recording);document.getElementById('freqValue').textContent=data.frequency||'---';document.getElementById('modeValue').textContent=data.mode||'---';document.getElementById('deviceValue').textContent=data.audio_device||'---';document.getElementById('updatedValue').textContent=data.updated_at||'---';document.getElementById('rawValue').textContent=data.raw_log_line||'---';const rawUrl=data.raw_stream_url||'';document.getElementById('rawLink').href=rawUrl?rawUrl+'/':'#';document.getElementById('streamHint').textContent=data.streaming_enabled?('Stream source: '+rawUrl):'Streaming disabled';const ready=data.streaming_enabled&&rawUrl&&['LIVE','READY'].includes(String(data.stream_status||'').toUpperCase());if(!ready){closePlayer();setPlayerStatus(data.streaming_enabled?'Waiting for live stream...':'Streaming disabled',false);setSessionState(data.streaming_enabled?'No active WebRTC session':'Enable streaming in the app');lastStreamUrl=rawUrl}else if((!peerConnection||peerConnection.connectionState==='closed'||peerConnection.connectionState==='failed')&&!playerStarting){lastStreamUrl=rawUrl;startPlayer(true)}else if(rawUrl!==lastStreamUrl&&!playerStarting){lastStreamUrl=rawUrl;startPlayer(true)}}catch(err){setPlayerStatus('Page update failed',false);setSessionState('Metadata request failed')}}
playButton.addEventListener('click',async()=>{if(!peerConnection||peerConnection.connectionState==='closed'||peerConnection.connectionState==='failed'){userPaused=false;await startPlayer(false);return}if(audioEl.paused){userPaused=false;try{await audioEl.play();setSessionState('Audio resumed')}catch(err){setSessionState('Browser blocked autoplay, press Listen again')}}else{userPaused=true;audioEl.pause();setSessionState('Audio paused locally')}updateButtons()});
muteButton.addEventListener('click',()=>{audioEl.muted=!audioEl.muted;updateButtons()});volumeSlider.addEventListener('input',(event)=>setVolume(Number(event.target.value)));audioEl.addEventListener('play',updateButtons);audioEl.addEventListener('pause',updateButtons);setVolume(85);updateButtons();refresh();setInterval(refresh,500);
</script>
</body>
</html>
"""


class _Handler(BaseHTTPRequestHandler):
    server_version = "ScannerMetadataServer/1.0"

    def _send_bytes(self, status, content_type, data):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        return self.rfile.read(length) if length > 0 else b""

    def _get_whep_target(self):
        payload = self.server.payload_func()
        raw_stream_url = str(payload.get("raw_stream_url") or "").strip()
        if not raw_stream_url or raw_stream_url.lower() == "streaming disabled":
            return None
        return raw_stream_url.rstrip("/") + "/whep"
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send_bytes(200, "text/html; charset=utf-8", PAGE_HTML.encode("utf-8"))
            return
        if self.path == "/api/status":
            payload = self.server.payload_func()
            data = json.dumps(payload).encode("utf-8")
            self._send_bytes(200, "application/json; charset=utf-8", data)
            return
        self._send_bytes(404, "text/plain; charset=utf-8", b"not found")

    def do_POST(self):
        if self.path != "/api/whep":
            self._send_bytes(404, "text/plain; charset=utf-8", b"not found")
            return
        target = self._get_whep_target()
        if not target:
            self._send_bytes(503, "text/plain; charset=utf-8", b"stream not available")
            return
        body = self._read_body()
        request = urllib.request.Request(
            target,
            data=body,
            headers={
                "Content-Type": self.headers.get("Content-Type", "application/sdp"),
                "Accept": "application/sdp",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                data = response.read()
                self._send_bytes(
                    getattr(response, "status", 200),
                    response.headers.get("Content-Type", "application/sdp"),
                    data,
                )
        except urllib.error.HTTPError as exc:
            data = exc.read() if hasattr(exc, "read") else str(exc).encode("utf-8")
            self._send_bytes(exc.code, exc.headers.get("Content-Type", "text/plain; charset=utf-8"), data)
        except OSError as exc:
            self._send_bytes(502, "text/plain; charset=utf-8", str(exc).encode("utf-8"))

    def log_message(self, fmt, *args):
        return


class MetadataWebServer:
    def __init__(self, host, port, payload_func):
        self.host = host
        self.port = port
        self.payload_func = payload_func
        self.httpd = None
        self.thread = None

    def start(self):
        if self.httpd is not None:
            return
        self.httpd = ThreadingHTTPServer((self.host, self.port), _Handler)
        self.httpd.payload_func = self.payload_func
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        if self.httpd is None:
            return
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        finally:
            self.httpd = None
            self.thread = None