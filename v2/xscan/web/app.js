import { api, ApiError } from './api.js';
import { Player } from './player.js';

const el = id => document.getElementById(id);
const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const formatDate = value => value ? new Date(value).toLocaleString([], { dateStyle: 'short', timeStyle: 'medium' }) : '—';
const formatDuration = value => value == null ? '—' : `${Number(value).toFixed(1)}s`;
const state = { status: null, calls: [], route: 'dashboard', config: null, settings: null, devices: null, eventSource: null, authenticated: false, installPrompt: null, androidPairing: null, player: null };
let selectedCallId = '';
let toastTimer;

function toast(message, error = false) {
  const node = el('toast'); node.textContent = message; node.className = `toast show${error ? ' error' : ''}`;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => node.className = 'toast', 3500);
}

const player = new Player(el('audioElement'), info => {
  state.player = info;
  document.querySelectorAll('.player-status-mode').forEach(node => node.textContent = info.mode === 'replay' ? 'RECORDED CALL' : 'LIVE AUDIO');
  document.querySelectorAll('.player-status-label').forEach(node => node.textContent = info.label);
  document.querySelectorAll('.player-toggle-icon').forEach(node => node.textContent = info.playing ? 'Ⅱ' : '▶');
  const primaryLabel = info.state === 'connecting' ? 'Connecting…' : info.state === 'unavailable' ? 'Try Live Again' : info.mode === 'idle' ? 'Listen Live' : info.playing ? 'Pause' : info.mode === 'live' ? 'Resume' : 'Play';
  document.querySelectorAll('.player-primary-label').forEach(node => node.textContent = primaryLabel);
  document.querySelectorAll('[data-action="player-primary"]').forEach(node => { node.disabled = info.state === 'connecting'; });
  document.querySelectorAll('.player-return-live').forEach(node => { node.hidden = info.mode !== 'replay'; });
  document.querySelectorAll('.player-live-dot').forEach(node => node.classList.toggle('ok', ['playing','connected'].includes(info.state)));
  document.querySelectorAll('.mobile-listen-panel').forEach(node => { node.dataset.state=info.state; node.classList.toggle('is-playing',info.playing); });
  document.body.classList.toggle('audio-active', info.mode !== 'idle');
  el('playerSeek').hidden = info.mode !== 'replay';
  if (info.mode === 'replay' && info.duration) el('playerSeek').value = Math.round(info.currentTime / info.duration * 1000);
});
el('playerVolume').addEventListener('input', event => player.volume(event.target.value));
el('playerSeek').addEventListener('input', event => player.seek(Number(event.target.value) / 1000));

async function authenticate() {
  const auth = await api.authState();
  if (auth.authenticated) return showApp();
  showAuth(auth);
}

function showAuth(auth) {
  state.authenticated = false;
  state.eventSource?.close(); state.eventSource = null;
  el('appShell').hidden = true; el('authGate').hidden = false;
  const setup = !auth.configured;
  el('authTitle').textContent = setup ? 'Create administrator' : 'Welcome back';
  el('authCopy').textContent = setup
    ? auth.setup_allowed ? 'Set the owner password for this scanner. Use at least 12 characters, mixed case, and a number.' : 'Initial setup must be completed from the scanner PC.'
    : 'Sign in to manage and listen to this scanner.';
  el('passwordLabel').hidden = !auth.configured && !auth.setup_allowed;
  el('confirmLabel').hidden = !setup || !auth.setup_allowed;
  el('authSubmit').hidden = !auth.configured && !auth.setup_allowed;
  el('passwordInput').autocomplete = setup ? 'new-password' : 'current-password';
  el('passwordInput').value = ''; el('confirmInput').value = '';
  el('authForm').dataset.mode = setup ? 'setup' : 'login';
}

el('authForm').addEventListener('submit', async event => {
  event.preventDefault(); el('authError').textContent = '';
  const password = el('passwordInput').value;
  if (event.currentTarget.dataset.mode === 'setup' && password !== el('confirmInput').value) {
    el('authError').textContent = 'Passwords do not match.'; return;
  }
  try {
    event.currentTarget.dataset.mode === 'setup' ? await api.setup(password) : await api.login(password);
    await showApp();
  } catch (error) { el('authError').textContent = error.message; }
});

async function showApp() {
  state.authenticated = true;
  el('authGate').hidden = true; el('appShell').hidden = false;
  if (!await refreshStatus() || !state.authenticated) return;
  connectEvents(); route();
  ensureAndroidPairing().catch(error => toast(`Android live audio setup: ${error.message}`, true));
}

async function ensureAndroidPairing(force = false) {
  if (!window.XScanAndroid?.getPublicKey || (!force && window.XScanAndroid.isPaired?.())) return;
  if (state.androidPairing) return state.androidPairing;
  state.androidPairing = (async () => {
    let public_key;
    if(window.XScanAndroid.getPublicKeyResult){
      const result=JSON.parse(window.XScanAndroid.getPublicKeyResult());
      if(!result.ok)throw new Error(result.error||'Android device key generation failed');
      public_key=result.public_key;
    }else public_key=window.XScanAndroid.getPublicKey();
    const [device,bootstrap]=await Promise.all([api.registerMobileDevice({name:'XScan Android phone',public_key}),api.mobileBootstrap()]);
    window.XScanAndroid.setDeviceRegistration(JSON.stringify({...device,...bootstrap}));
  })();
  try { await state.androidPairing; } finally { state.androidPairing=null; }
}

async function refreshStatus() {
  if (!state.authenticated) return false;
  try {
    state.status = await api.status();
    const running = state.status.running;
    el('hostDot').classList.toggle('ok', !!state.status.components?.host?.healthy);
    el('hostSummary').textContent = running ? 'System running' : state.status.hardware_control_enabled ? 'System stopped' : 'Side-by-side safe mode';
    const networkBadge=el('networkBadge');
    networkBadge.hidden=false;
    networkBadge.textContent=location.protocol === 'https:' ? 'PUBLIC HTTPS' : 'LOCAL BACKEND';
    networkBadge.className=`badge ${location.protocol === 'https:' ? 'good' : 'warning'}`;
    if (state.route === 'dashboard') updateDashboard();
    return true;
  } catch (error) {
    if (error.status === 401) {
      state.authenticated = false;
      await authenticate();
    }
    return false;
  }
}

function connectEvents() {
  if (!state.authenticated) return;
  state.eventSource?.close();
  const source = new EventSource('/api/v1/events'); state.eventSource = source;
  let scheduled;
  const update = () => { clearTimeout(scheduled); scheduled = setTimeout(refreshStatus, 120); };
  ['snapshot','component','now-playing','recording','system','stream'].forEach(name => source.addEventListener(name, update));
  source.addEventListener('audio-level', event => {
    try { updateAudioMeter(JSON.parse(event.data).data?.level ?? 0); } catch { /* next sample will recover */ }
  });
  source.addEventListener('call-completed', () => { update(); if (state.route === 'calls') { const scroll=el('view').scrollTop; renderCalls().then(()=>{el('view').scrollTop=scroll;}); } });
  source.onerror = () => setTimeout(() => { if (state.authenticated && state.eventSource === source) connectEvents(); }, 3000);
}

function audioMeterValues(level) {
  const raw = Math.max(0, Number(level) || 0);
  const trigger = Math.max(0.000001, Number(state.status?.audio_trigger_level) || 0.0021);
  return { raw, trigger, percent: Math.min(100, raw / trigger * 65), active: raw >= trigger };
}

function updateAudioMeter(level) {
  const meter = el('dashboardAudioMeter');
  if (!meter) return;
  const values = audioMeterValues(level);
  const running = !!state.status?.components?.audio?.healthy;
  meter.querySelector('.meter-fill').style.width = `${values.percent}%`;
  meter.querySelector('.signal-light').classList.toggle('active', running && values.active);
  meter.querySelector('.signal-state').textContent = !running ? 'Audio capture stopped' : values.active ? 'Audio detected' : 'Listening — quiet';
  meter.querySelector('.signal-value').textContent = `RMS ${values.raw.toFixed(5)}`;
  meter.classList.toggle('signal-active', running && values.active);
}

function route() {
  const requested = location.hash.replace('#','') || 'dashboard';
  const allowed = ['dashboard','calls','scanlists','dsdplus','devices','diagnostics','settings','more'];
  state.route = allowed.includes(requested) ? requested : 'dashboard';
  document.querySelectorAll('[data-route]').forEach(node => node.classList.toggle('active', node.dataset.route === state.route));
  const names = { dashboard:'Dashboard', calls:'Call Library', scanlists:'Scanlists', dsdplus:'DSDPlus Data', devices:'Audio Devices', diagnostics:'Diagnostics', settings:'Settings', more:'More' };
  el('pageTitle').textContent = names[state.route];
  document.body.classList.remove('drawer-open');
  ({ dashboard: renderDashboard, calls: renderCalls, scanlists: renderScanlist, dsdplus: renderDsdplus, devices: renderDevices, diagnostics: renderDiagnostics, settings: renderSettings, more: renderMore })[state.route]();
}
window.addEventListener('hashchange', route);

function statusBadge(component) {
  const good = component?.healthy; const cls = good ? 'good' : ['fault','error'].includes(component?.state) ? 'bad' : 'warning';
  return `<span class="badge ${cls}">${escapeHtml(component?.state || 'unknown')}</span>`;
}

function renderDashboard() {
  if (!el('dashboardStable')) {
    el('view').innerHTML = `
      <div id="dashboardStable" class="dashboard-stable">
        <div id="dashboardNotice" class="status-strip"><strong id="dashboardNoticeTitle">XScan connected</strong><span id="dashboardNoticeCopy">Scanner status is current.</span></div>
        <div class="grid two dashboard-grid">
          <section class="card receiver-card"><div class="card-head"><div><p class="eyebrow">NOW PLAYING</p><h2>Receiver</h2></div><span id="receiverBadge" class="badge warning">connecting</span></div>
            <div id="receiverLcd" class="lcd stopped"><div class="lcd-chips"><span id="lcdMode" class="lcd-chip">Mode —</span><span id="lcdFreq" class="lcd-chip">Freq —</span><span id="lcdRecord" class="lcd-chip">REC OFF</span><span id="lcdState" class="lcd-chip">STOPPED</span></div><div id="lcdChannel" class="lcd-channel">Connecting to scanner</div><div class="lcd-foot"><span id="lcdDevice">Audio device</span><span id="lcdClock">--:--:--</span></div></div>
            <div id="dashboardAudioMeter" class="audio-monitor"><div class="audio-monitor-head"><div class="signal-summary"><span class="signal-light"></span><span><strong class="signal-state">Checking audio capture</strong><small id="signalDevice">Decoded scanner audio</small></span></div><span class="signal-value">RMS 0.00000</span></div><div class="meter meter-large"><span class="meter-fill"></span><i class="trigger-marker"></i></div><div class="meter-scale"><span>Silence</span><span id="triggerLabel">Recording trigger</span><span>Strong</span></div></div>
            <section class="mobile-listen-panel" aria-label="Live scanner audio"><div class="player-meta"><span class="live-dot player-live-dot"></span><span><small class="player-status-mode">LIVE AUDIO</small><strong class="player-status-label">Press Listen Live to hear the scanner</strong></span></div><div class="player-controls"><button class="button primary" data-action="player-primary"><span class="player-primary-label">Listen Live</span></button><button class="button subtle player-return-live" data-action="player-live" hidden>Live</button></div></section>
            <div class="control-block"><div class="control-copy"><strong>Scanner power</strong><span>Start or stop the receiver and recorder together.</span></div><div class="button-row scanner-buttons"><button id="startScanner" class="button primary" data-action="system-start">Start</button><button id="stopScanner" class="button danger" data-action="system-stop">Stop</button><button id="restartScanner" class="button" data-action="system-restart">Restart</button></div></div>
            <details class="native-window-controls"><summary>DSDPlus and FMP24 windows</summary><p>These only show or hide the original program windows.</p><div class="button-row"><button class="button subtle" data-action="window-show">Show native windows</button><button class="button subtle" data-action="window-hide">Hide native windows</button></div></details>
          </section>
          <section class="card health-card"><div class="card-head"><div><p class="eyebrow">COMPONENT HEALTH</p><h2>Actual runtime state</h2></div><a class="button subtle" href="#diagnostics">Diagnostics</a></div><div id="healthGrid" class="grid health-grid">${['dsdplus','fmp24','audio','recorder','ffmpeg','mediamtx'].map(name=>`<div class="health"><div class="health-top"><strong>${name}</strong><span id="health-${name}-badge" class="badge warning">unknown</span></div><p id="health-${name}-message">Waiting for status</p></div>`).join('')}</div><div class="grid three health-stats"><div class="stat"><span>Disk free</span><strong id="diskFree">— GB</strong></div><div class="stat"><span>Recorder</span><strong id="recorderState">Ready</strong></div><div class="stat"><span>Restarts</span><strong id="restartCount">0</strong></div></div></section>
        </div>
      </div>`;
  }
  updateDashboard();
}

function updateBadge(node, component) {
  if (!node) return;
  const good=component?.healthy, bad=['fault','error'].includes(component?.state);
  node.className=`badge ${good?'good':bad?'bad':'warning'}`;
  node.textContent=component?.state||'unknown';
}

function updateDashboard() {
  const s = state.status;
  if (!s || !el('dashboardStable')) return;
  const now = s.now_playing || {}, components = s.components || {};
  const stopped = !s.running;
  const faulted = s.desired_running && stopped && ['fault','error'].some(value => Object.values(components).some(component => component?.state === value));
  const notice=el('dashboardNotice');
  if(!s.hardware_control_enabled){notice.className='status-strip warning';el('dashboardNoticeTitle').textContent='Side-by-side safety lock';el('dashboardNoticeCopy').textContent='Hardware control is disabled.';}
  else if(s.storage?.warning){notice.className='status-strip bad';el('dashboardNoticeTitle').textContent='Recording storage is low';el('dashboardNoticeCopy').textContent=`${s.storage.free_gb} GB remains.`;}
  else{notice.className=`status-strip ${s.running?'good':'warning'}`;el('dashboardNoticeTitle').textContent=s.running?'Scanner system running':'Scanner system stopped';el('dashboardNoticeCopy').textContent=s.running?'Recording and live streaming are supervised.':'Press Start scanner to begin.';}
  updateBadge(el('receiverBadge'),components.fmp24);
  const lcd=el('receiverLcd'); lcd.className=`lcd ${stopped?'stopped':''} ${faulted?'fault':''} ${s.recording?'recording':''}`;
  el('lcdMode').textContent=`Mode ${now.mode||'—'}`; el('lcdFreq').textContent=`Freq ${now.frequency||'—'}`; el('lcdRecord').textContent=s.recording?'REC ON':'REC OFF'; el('lcdState').textContent=faulted?'FAULT':s.running?'SCANNING':'STOPPED';
  el('lcdChannel').textContent=stopped?(faulted?'Scanner fault':'Scanner stopped'):(now.display||'Waiting for scanner'); el('lcdDevice').textContent=s.audio_device_name||components.audio?.message||'Audio device'; el('lcdClock').textContent=new Date().toLocaleTimeString();
  el('signalDevice').textContent=`Decoded audio from ${s.audio_device_name||'selected input'}`; el('triggerLabel').textContent=`Recording trigger ${audioMeterValues(s.audio_level).trigger.toFixed(5)}`; updateAudioMeter(s.audio_level);
  el('startScanner').disabled=!s.hardware_control_enabled||s.running; el('stopScanner').disabled=!s.desired_running&&!s.running; el('restartScanner').disabled=!s.hardware_control_enabled;
  ['dsdplus','fmp24','audio','recorder','ffmpeg','mediamtx'].forEach(name=>{updateBadge(el(`health-${name}-badge`),components[name]);el(`health-${name}-message`).textContent=components[name]?.message||'No status';});
  el('diskFree').textContent=`${s.storage?.free_gb??'—'} GB`; el('recorderState').textContent=s.recording?'Active':'Ready'; el('restartCount').textContent=Object.values(components).reduce((n,c)=>n+(c.restarts||0),0);
}

function renderMore() {
  el('view').innerHTML=`<section class="card more-menu"><p class="eyebrow">ADMINISTRATION</p><h2>More XScan tools</h2><div class="more-links"><a href="#devices"><strong>Audio Devices</strong><span>Input, meter, and calibration</span></a><a href="#dsdplus"><strong>DSDPlus Data</strong><span>Aliases and advanced configuration</span></a><a href="#diagnostics"><strong>Diagnostics</strong><span>Processes, ports, and logs</span></a><a href="#settings"><strong>Settings & Install</strong><span>Runtime, PWA, and Android app</span></a></div></section>`;
}

async function renderCalls() {
  el('view').innerHTML = '<section class="card"><div class="empty">Loading call history…</div></section>';
  try {
    const result = await api.calls({ limit: 200, state: 'active' }); state.calls = result.items;
    const rows = result.items.map(call => `<div class="call-card" data-call-id="${call.id}"><input class="call-select" type="checkbox" value="${call.id}" aria-label="Select ${escapeHtml(call.label||call.frequency)}"><button class="call-play" data-action="play-call" data-id="${call.id}">▶</button><div><div class="call-title">${escapeHtml(call.label || call.frequency)}</div><div class="call-sub">${escapeHtml(call.frequency)} · ${escapeHtml(call.mode)} ${call.radio_alias ? '· '+escapeHtml(call.radio_alias):''} · ${formatDate(call.started_at)} · ${formatDuration(call.duration_seconds)}</div></div><div class="button-row"><button class="button subtle" data-action="edit-call" data-id="${call.id}">Details</button><button class="button subtle" data-action="favorite-call" data-id="${call.id}">${call.favorite?'★':'☆'}</button><button class="button subtle" data-action="trash-call" data-id="${call.id}">Trash</button></div></div>`).join('');
    el('view').innerHTML = `<section class="card"><div class="split-toolbar"><div><p class="eyebrow">RECORDING LIBRARY</p><h2>${result.total} saved calls</h2></div><div class="filters"><input id="callSearch" placeholder="Search label, RID, alias…"><select id="callState"><option value="active">Saved</option><option value="trashed">Trash</option></select><button class="button" data-action="search-calls">Search</button><button class="button subtle" data-action="select-all-calls">Select all</button><button id="bulkCallAction" class="button danger" data-action="bulk-trash-calls">Trash selected</button><button id="bulkPurgeAction" class="button danger" data-action="bulk-purge-calls" hidden>Purge selected</button></div></div><div id="callList" class="call-list">${rows || '<div class="empty">No calls have been recorded yet.</div>'}</div></section>`;
  } catch (error) { showViewError(error); }
}

async function searchCalls() {
  const result = await api.calls({ limit: 200, search: el('callSearch')?.value || '', state: el('callState')?.value || 'active' });
  state.calls = result.items; await renderCallsFromState(result);
}
async function renderCallsFromState(result) {
  const trashed = (el('callState')?.value || 'active') === 'trashed';
  const bulk=el('bulkCallAction'); bulk.dataset.action=trashed?'bulk-restore-calls':'bulk-trash-calls'; bulk.textContent=trashed?'Restore selected':'Trash selected'; el('bulkPurgeAction').hidden=!trashed;
  el('callList').innerHTML = result.items.map(call => `<div class="call-card"><input class="call-select" type="checkbox" value="${call.id}" aria-label="Select ${escapeHtml(call.label||call.frequency)}"><button class="call-play" data-action="play-call" data-id="${call.id}">▶</button><div><div class="call-title">${escapeHtml(call.label || call.frequency)}</div><div class="call-sub">${escapeHtml(call.frequency)} · ${formatDate(call.started_at)} · ${formatDuration(call.duration_seconds)}</div></div><div class="button-row">${trashed?`<button class="button" data-action="restore-call" data-id="${call.id}">Restore</button><button class="button danger" data-action="purge-call" data-id="${call.id}">Purge</button>`:`<button class="button subtle" data-action="trash-call" data-id="${call.id}">Trash</button>`}</div></div>`).join('') || '<div class="empty">No matching calls.</div>';
}

function selectedCallIds() { return [...document.querySelectorAll('.call-select:checked')].map(input=>input.value); }

async function renderScanlist() {
  el('view').innerHTML = '<section class="card"><div class="empty">Reading FMP24.ScanList…</div></section>';
  try {
    const [doc, settings, backups] = await Promise.all([api.config('scanlist'), api.settings(), api.backups('scanlist')]); state.config = doc; state.settings = settings;
    const issues = renderIssues(doc.issues);
    const mobile=matchMedia('(max-width:780px)').matches;
    let group=''; const rows = doc.entries.map(entry => { const original=escapeHtml(JSON.stringify(entry)); const changed=entry.group!==group; group=entry.group; const heading=changed?(mobile?`<h3 class="scan-group">${escapeHtml(entry.group)}</h3>`:`<tr class="group-row"><th colspan="8">${escapeHtml(entry.group)}</th></tr>`):''; const override=settings.audio.per_channel?.[entry.frequency]?.trigger_level ?? ''; if(mobile)return `${heading}<article class="scan-entry scan-card" data-line="${entry.line_number}" data-group="${escapeHtml(entry.group)}" data-original="${original}"><div class="scan-card-head"><label class="toggle-line"><input class="sl-enabled" type="checkbox" ${entry.enabled?'checked':''}> Scan this channel</label><span class="row-actions"><button class="button" data-action="scan-up">↑</button><button class="button" data-action="scan-down">↓</button></span></div><label>Frequency<input class="sl-frequency" value="${escapeHtml(entry.frequency)}"></label><div class="form-grid"><label>Mode<input class="sl-mode" value="${escapeHtml(entry.mode)}"></label><label>Trigger override<input class="sl-trigger" type="number" step="0.0001" placeholder="Global" value="${escapeHtml(override)}"></label></div><label>Channel name<input class="sl-label" value="${escapeHtml(entry.label)}"></label><label>Options<input class="sl-options" value="${escapeHtml(entry.options.join(' '))}"></label></article>`; return `${heading}<tr class="scan-entry" data-line="${entry.line_number}" data-group="${escapeHtml(entry.group)}" data-original="${original}"><td><input class="sl-enabled" type="checkbox" ${entry.enabled?'checked':''}></td><td><input class="sl-frequency" value="${escapeHtml(entry.frequency)}"></td><td><input class="sl-mode" value="${escapeHtml(entry.mode)}"></td><td><input class="sl-options" value="${escapeHtml(entry.options.join(' '))}"></td><td><input class="sl-label" value="${escapeHtml(entry.label)}"></td><td><input class="sl-trigger" type="number" step="0.0001" placeholder="Global" value="${escapeHtml(override)}"></td><td class="muted">${entry.line_number}</td><td class="row-actions"><button class="button" data-action="scan-up">↑</button><button class="button" data-action="scan-down">↓</button></td></tr>`; }).join('');
    const backupOptions=backups.items.map(item=>`<option value="${escapeHtml(item.name)}">${escapeHtml(item.name)}</option>`).join('');
    el('view').innerHTML = `<section class="card"><div class="split-toolbar"><div><p class="eyebrow">SOURCE OF TRUTH</p><h2>FMP24.ScanList</h2><p class="muted small">Untouched comments and blank lines remain exactly where they are.</p></div><div class="button-row"><button class="button" data-action="add-scan-channel">Add channel</button><button class="button primary" data-action="save-scanlist">Save changes</button><button class="button" data-action="apply-scanlist">Apply and restart</button></div></div>${issues}${mobile?`<div id="scanEntries" class="scan-cards">${rows}</div>`:`<div class="table-wrap"><table><thead><tr><th>On</th><th>Frequency</th><th>Mode</th><th>Options</th><th>Label</th><th>Trigger</th><th>Line</th><th>Order</th></tr></thead><tbody id="scanEntries">${rows}</tbody></table></div>`}<div class="button-row" style="margin-top:14px"><select id="scanBackup" style="width:auto"><option value="">Restore a backup…</option>${backupOptions}</select><button class="button" data-action="restore-config" data-key="scanlist">Restore selected</button></div><details style="margin-top:16px"><summary>Advanced raw preview</summary><textarea readonly>${escapeHtml(doc.text)}</textarea></details></section>`;
  } catch (error) { showViewError(error); }
}

async function saveScanlist(apply) {
  const rows=[...document.querySelectorAll('.scan-entry[data-line]')], slots=state.config.entries.map(item=>item.line_number).sort((a,b)=>a-b), lineCount=state.config.text?state.config.text.split(/\r?\n/).length-(state.config.text.endsWith('\n')?1:0):0, firstNew=lineCount+1, perChannel={};
  const patches=[]; rows.forEach((row,index)=>{ const original=JSON.parse(row.dataset.original||'{}'), current={line_number:slots[index]??firstNew+(index-slots.length),enabled:row.querySelector('.sl-enabled').checked,frequency:row.querySelector('.sl-frequency').value,mode:row.querySelector('.sl-mode').value,options:row.querySelector('.sl-options').value.split(/\s+/).filter(Boolean),label:row.querySelector('.sl-label').value}; const changed=current.line_number!==original.line_number||current.enabled!==original.enabled||current.frequency!==original.frequency||current.mode!==original.mode||current.label!==original.label||current.options.join(' ')!==(original.options||[]).join(' '); if(changed) patches.push(current); const threshold=row.querySelector('.sl-trigger').value; if(threshold) perChannel[current.frequency]={trigger_level:Number(threshold)}; });
  if(patches.length) state.config = await api.saveConfig('scanlist', { revision: state.config.revision, patches });
  await api.saveSettings({audio:{per_channel:perChannel}}); toast(patches.length?`Scanlist saved; backup ${state.config.backup.split(/[\\/]/).pop()}`:'Per-channel detector settings saved');
  if (apply) { await api.system('restart'); toast('Scanlist applied and receiver restarted'); }
  renderScanlist();
}

async function renderDsdplus() {
  el('view').innerHTML = '<section class="card"><div class="empty">Loading editable DSDPlus data…</div></section>';
  try {
    const index = await api.configIndex();
    const selected = sessionStorage.getItem('xscan-config-key') || 'radios';
    const [doc,backups] = await Promise.all([api.config(selected),api.backups(selected)]); state.config = doc;
    const options = index.items.filter(item=>item.key!=='scanlist').map(item=>`<option value="${item.key}" ${item.key===selected?'selected':''}>${escapeHtml(item.name)}</option>`).join('');
    const records = doc.records.slice(0,500).map(record => `<tr data-line="${record.line_number}" data-original="${escapeHtml(JSON.stringify(record.fields))}">${doc.schema.map(field=>`<td><input data-field="${field}" value="${escapeHtml(record.fields[field] ?? '')}"></td>`).join('')}<td>${record.line_number}</td></tr>`).join('');
    const backupOptions=backups.items.map(item=>`<option value="${escapeHtml(item.name)}">${escapeHtml(item.name)}</option>`).join('');
    el('view').innerHTML = `<section class="card"><div class="split-toolbar"><div><p class="eyebrow">VALIDATED FILE EDITOR</p><h2>${escapeHtml(doc.name)}</h2></div><div class="button-row"><select id="configSelector">${options}</select><button class="button" data-action="save-dsd-table">Save changed rows</button><button class="button primary" data-action="save-dsd-raw">Save raw text</button></div></div>${renderIssues(doc.issues)}<div class="table-wrap" style="max-height:390px"><table><thead><tr>${doc.schema.map(field=>`<th>${escapeHtml(field.replaceAll('_',' '))}</th>`).join('')}<th>Line</th></tr></thead><tbody>${records}</tbody></table></div><div class="button-row" style="margin-top:14px"><select id="dsdBackup" style="width:auto"><option value="">Restore a backup…</option>${backupOptions}</select><button class="button" data-action="restore-config" data-key="${selected}">Restore selected</button></div><details open style="margin-top:16px"><summary>Advanced raw editor</summary><textarea id="rawConfig">${escapeHtml(doc.text)}</textarea></details></section>`;
    el('configSelector').addEventListener('change', event => { sessionStorage.setItem('xscan-config-key', event.target.value); renderDsdplus(); });
  } catch (error) { showViewError(error); }
}

async function saveDsdTable() {
  const patches = [...document.querySelectorAll('tbody tr[data-line]')].flatMap(row => { const original=JSON.parse(row.dataset.original||'{}'), fields={...original}; row.querySelectorAll('[data-field]').forEach(input=>fields[input.dataset.field]=input.value); return JSON.stringify(fields)===JSON.stringify(original)?[]:[{line_number:Number(row.dataset.line),fields}]; });
  if(!patches.length){toast('No structured rows changed');return;} state.config = await api.saveConfig(state.config.key, { revision:state.config.revision, patches }); toast('DSDPlus data saved with backup'); renderDsdplus();
}
async function saveDsdRaw() { state.config = await api.saveConfig(state.config.key, { revision:state.config.revision, text:el('rawConfig').value }); toast('Configuration saved with backup'); renderDsdplus(); }

async function renderDevices() {
  el('view').innerHTML = '<section class="card"><div class="empty">Querying Windows audio devices…</div></section>';
  try {
    const [devices, settings] = await Promise.all([api.devices(),api.settings()]); state.devices=devices; state.settings=settings;
    el('view').innerHTML = `<div class="grid two"><section class="card"><p class="eyebrow">CAPTURE ENDPOINT</p><h2>Audio device</h2><p class="muted">XScan matches the full device and host-API names, so Windows index changes do not break capture.</p><label>Selected input<select id="audioDevice">${devices.items.map(device=>`<option value="${escapeHtml(JSON.stringify({name:device.name,host_api:device.host_api}))}" ${device.name===devices.selected_name&&device.host_api===devices.selected_host_api?'selected':''}>${escapeHtml(device.name)} · ${escapeHtml(device.host_api)} · ${device.default_sample_rate} Hz</option>`).join('')}</select></label><div class="meter" style="margin:20px 0"><span style="width:${Math.min(100,devices.level*2400)}%"></span></div><button class="button primary" data-action="save-device">Save device</button></section><section class="card"><p class="eyebrow">TRIGGER CALIBRATION</p><h2>Recording detector</h2><div class="form-grid"><label>Trigger level<input id="triggerLevel" type="number" step="0.0001" value="${settings.audio.trigger_level}"></label><label>Pre-roll seconds<input id="preRoll" type="number" step="0.1" value="${settings.audio.pre_roll_seconds}"></label><label>Silence hang<input id="silenceHang" type="number" step="0.1" value="${settings.audio.silence_hang_seconds}"></label><label>Minimum call<input id="minimumCall" type="number" step="0.1" value="${settings.audio.minimum_seconds}"></label></div><div class="button-row" style="margin-top:16px"><button class="button" data-action="calibrate-audio">Measure noise floor</button><button class="button primary" data-action="save-audio-settings">Save detector settings</button></div></section></div>`;
  } catch (error) { showViewError(error); }
}

async function renderDiagnostics() {
  el('view').innerHTML = '<section class="card"><div class="empty">Gathering diagnostics…</div></section>';
  try {
    const [diag,logs] = await Promise.all([api.diagnostics(),api.logs()]);
    const rows = Object.entries(diag.status.components).map(([name,c])=>`<tr><td>${name}</td><td>${statusBadge(c)}</td><td>${c.pid??'—'}</td><td>${escapeHtml(c.message)}</td><td>${c.heartbeat_age_seconds??'—'}s</td><td>${c.restarts}</td></tr>`).join('');
    el('view').innerHTML = `<section class="card"><div class="split-toolbar"><div><p class="eyebrow">SUPPORT AND HEALTH</p><h2>Runtime diagnostics</h2></div><div class="button-row"><button class="button" data-action="window-show">Show native windows</button><a class="button primary" href="/api/v1/diagnostics/bundle">Download support bundle</a></div></div><div class="table-wrap"><table><thead><tr><th>Component</th><th>State</th><th>PID</th><th>Message</th><th>Heartbeat</th><th>Restarts</th></tr></thead><tbody>${rows}</tbody></table></div><h3 style="margin-top:20px">Recent host log</h3><pre class="log">${escapeHtml(logs.items.join('\n'))}</pre></section>`;
  } catch (error) { showViewError(error); }
}

async function renderSettings() {
  el('view').innerHTML = '<section class="card"><div class="empty">Loading settings…</div></section>';
  try {
    const [settings,backups,mobileDevices,release] = await Promise.all([api.settings(),api.settingsBackups(),api.mobileDevices(),api.mobileRelease()]); state.settings=settings;
    const backupOptions=backups.items.map(item=>`<option value="${escapeHtml(item.name)}">${escapeHtml(item.name)}</option>`).join('');
    const phones=mobileDevices.items.map(device=>`<div class="health"><div class="health-top"><strong>${escapeHtml(device.name)}</strong><button class="button danger" data-action="revoke-mobile" data-id="${device.id}">Revoke</button></div><p>Paired ${formatDate(device.created_at)}${device.last_seen_at?` · Used ${formatDate(device.last_seen_at)}`:''}</p></div>`).join('')||'<div class="empty">No Android phones paired yet.</div>';
    el('view').innerHTML = `<div class="grid two"><section class="card"><p class="eyebrow">RUNTIME</p><h2>Host behavior</h2><div class="form-grid"><label>Backend port<input value="${settings.server.port}" disabled></label><label>Session hours<input id="sessionHours" type="number" value="${settings.server.session_hours}"></label><label><span>Live WebRTC / HLS</span><input id="streamEnabled" type="checkbox" ${settings.streaming.enabled?'checked':''}></label><label><span>Auto-restart receiver</span><input id="autoRestart" type="checkbox" ${settings.runtime.auto_restart?'checked':''}></label><label><span>Hide native windows</span><input id="hideWindows" type="checkbox" ${settings.runtime.hide_native_windows?'checked':''}></label><label>Disk warning (GB free)<input id="diskWarning" type="number" value="${settings.storage.warning_free_gb}"></label></div><div class="button-row" style="margin-top:16px"><button class="button primary" data-action="save-settings">Save settings</button><select id="settingsBackup" style="width:auto"><option value="">Restore settings…</option>${backupOptions}</select><button class="button" data-action="restore-settings">Restore</button></div></section><section class="card"><p class="eyebrow">MOBILE APPS</p><h2>Install XScan</h2><p class="muted">The web app works like an installed app. The Android APK adds persistent lock-screen and background audio.</p><div class="button-row"><button id="installPwaButton" class="button primary" data-action="install-pwa" ${state.installPrompt?'':'disabled'}>Install web app</button>${release.available?`<a class="button" href="${release.download_url}" download>Download Android app</a>`:'<button class="button" disabled>Android build unavailable</button>'}${window.XScanAndroid?'<button class="button primary" data-action="pair-android">Pair this Android phone</button>':''}</div><p class="small muted">Android ${release.version_name} ${release.available?`· SHA-256 ${escapeHtml(release.sha256.slice(0,16))}…`:'is being prepared'}</p><h3>Paired Android phones</h3><div class="grid">${phones}</div></section><section class="card"><p class="eyebrow">NETWORK SECURITY</p><h2>Public access</h2><p><span class="badge good">Let's Encrypt HTTPS</span></p><p><a href="${escapeHtml(settings.server.public_url)}">${escapeHtml(settings.server.public_url)}</a></p><p class="muted">Caddy terminates public TLS and automatically renews the certificate. XScan itself listens only on localhost.</p><hr style="border:0;border-top:1px solid var(--line);margin:20px 0"><p class="small muted">Hardware control: <strong>${settings.runtime.hardware_control_enabled?'enabled':'side-by-side locked'}</strong>.</p></section></div>`;
  } catch (error) { showViewError(error); }
}

function renderIssues(issues=[]) { return issues.length ? `<ul class="issues">${issues.map(issue=>`<li class="${issue.level}">Line ${issue.line}: ${escapeHtml(issue.message)}</li>`).join('')}</ul>` : ''; }
function showViewError(error) { el('view').innerHTML=`<section class="card"><div class="empty"><strong>Could not load this view</strong><p>${escapeHtml(error.message)}</p></div></section>`; }

function showCallDetails(call) {
  selectedCallId = call.id;
  el('dialogCallTitle').textContent = call.label || call.frequency || 'Recorded call';
  const details = [['Started',formatDate(call.started_at)],['Duration',formatDuration(call.duration_seconds)],['Frequency',call.frequency||'—'],['Mode',call.mode||'—'],['Radio',call.radio_alias||call.radio_id||'—'],['RAN / NAC',call.ran_nac||'—']];
  el('dialogCallMeta').innerHTML = details.map(([name,value])=>`<div class="stat"><span class="muted small">${escapeHtml(name)}</span><strong>${escapeHtml(value)}</strong></div>`).join('');
  el('dialogCallTags').value = (call.tags||[]).join(', ');
  el('dialogCallNote').value = call.note || '';
  el('dialogCallDownload').href = `/api/v1/calls/${call.id}/audio?download=true`;
  el('callDialog').showModal();
}

function moveScanRow(button, direction) {
  const row=button.closest('.scan-entry[data-line]'), group=row.dataset.group, rows=[...row.parentElement.querySelectorAll(`.scan-entry[data-line][data-group="${CSS.escape(group)}"]`)];
  const index=rows.indexOf(row), target=rows[index+direction]; if(!target)return;
  if(direction<0) row.parentElement.insertBefore(row,target); else row.parentElement.insertBefore(target,row);
}

function addScanChannel() {
  const body=el('scanEntries'), rows=[...body.querySelectorAll('.scan-entry[data-line]')], last=rows.at(-1), group=last?.dataset.group||'NEW CHANNELS';
  const original={line_number:0,group,enabled:true,frequency:'',mode:'FM',options:[],label:'',raw:''};
  const mobile=matchMedia('(max-width:780px)').matches, row=document.createElement(mobile?'article':'tr'); row.className=mobile?'scan-entry scan-card':'scan-entry'; row.dataset.line='0'; row.dataset.group=group; row.dataset.original=JSON.stringify(original);
  row.innerHTML=mobile?'<div class="scan-card-head"><label class="toggle-line"><input class="sl-enabled" type="checkbox" checked> Scan this channel</label><span class="row-actions"><button class="button" data-action="scan-up">↑</button><button class="button" data-action="scan-down">↓</button></span></div><label>Frequency<input class="sl-frequency" placeholder="155.0000"></label><div class="form-grid"><label>Mode<input class="sl-mode" value="FM"></label><label>Trigger override<input class="sl-trigger" type="number" step="0.0001" placeholder="Global"></label></div><label>Channel name<input class="sl-label" placeholder="Channel name"></label><label>Options<input class="sl-options" value="BW=11.0 DELAY=2"></label>':'<td><input class="sl-enabled" type="checkbox" checked></td><td><input class="sl-frequency" placeholder="155.0000"></td><td><input class="sl-mode" value="FM"></td><td><input class="sl-options" value="BW=11.0 DELAY=2"></td><td><input class="sl-label" placeholder="Channel name"></td><td><input class="sl-trigger" type="number" step="0.0001" placeholder="Global"></td><td class="muted">new</td><td class="row-actions"><button class="button" data-action="scan-up">↑</button><button class="button" data-action="scan-down">↓</button></td>';
  body.appendChild(row); row.querySelector('.sl-frequency').focus();
}

document.addEventListener('click', async event => {
  const target = event.target.closest('[data-action]'); if (!target) return;
  const action = target.dataset.action;
  try {
    if (action==='refresh') { await refreshStatus(); route(); }
    else if (action==='mobile-menu') document.body.classList.add('drawer-open');
    else if (action==='mobile-menu-close') document.body.classList.remove('drawer-open');
    else if (action==='logout') { await player.close(); state.eventSource?.close(); await api.logout(); location.reload(); }
    else if (action.startsWith('system-')) { await api.system(action.slice(7)); await refreshStatus(); toast(`System ${action.slice(7)} requested`); }
    else if (action==='window-show'||action==='window-hide') { const mode=action.endsWith('show')?'show':'hide'; const result=await api.windows(mode); toast(`${result.windows} native windows ${mode==='show'?'shown':'hidden'}`); }
    else if (action==='player-toggle'||action==='player-primary') { await ensureAndroidPairing(); await player.primary(); }
    else if (action==='player-live') { await ensureAndroidPairing(); await player.live(true); }
    else if (action==='play-call') { const call=state.calls.find(item=>item.id===target.dataset.id); if(call) await player.replay(call); }
    else if (action==='edit-call') { const call=state.calls.find(item=>item.id===target.dataset.id); if(call) showCallDetails(call); }
    else if (action==='save-call-details') { const call=await api.updateCall(selectedCallId,{tags:el('dialogCallTags').value.split(',').map(tag=>tag.trim()).filter(Boolean),note:el('dialogCallNote').value}); const index=state.calls.findIndex(item=>item.id===call.id); if(index>=0)state.calls[index]=call; el('callDialog').close(); toast('Call details saved'); }
    else if (action==='favorite-call') { const call=state.calls.find(item=>item.id===target.dataset.id); await api.updateCall(call.id,{favorite:!call.favorite}); await renderCalls(); }
    else if (action==='trash-call') { await api.callAction('trash',[target.dataset.id]); toast('Call moved to XScan trash'); await renderCalls(); }
    else if (action==='restore-call') { await api.callAction('restore',[target.dataset.id]); toast('Call restored'); await searchCalls(); }
    else if (action==='purge-call') { if(confirm('Permanently delete this trashed recording and database record?')) { await api.callAction('purge',[target.dataset.id],{confirm:'PURGE'}); await searchCalls(); } }
    else if (action==='search-calls') await searchCalls();
    else if (action==='select-all-calls') { const boxes=[...document.querySelectorAll('.call-select')], checked=boxes.some(box=>!box.checked); boxes.forEach(box=>box.checked=checked); }
    else if (action==='bulk-trash-calls') { const ids=selectedCallIds(); if(!ids.length)throw new Error('Select at least one call'); await api.callAction('trash',ids); toast(`${ids.length} calls moved to XScan trash`); await searchCalls(); }
    else if (action==='bulk-restore-calls') { const ids=selectedCallIds(); if(!ids.length)throw new Error('Select at least one call'); await api.callAction('restore',ids); toast(`${ids.length} calls restored`); await searchCalls(); }
    else if (action==='bulk-purge-calls') { const ids=selectedCallIds(); if(!ids.length)throw new Error('Select at least one call'); if(confirm(`Permanently purge ${ids.length} selected recordings?`)){ await api.callAction('purge',ids,{confirm:'PURGE'}); toast(`${ids.length} calls permanently purged`); await searchCalls(); } }
    else if (action==='save-scanlist') await saveScanlist(false);
    else if (action==='apply-scanlist') await saveScanlist(true);
    else if (action==='scan-up') moveScanRow(target,-1);
    else if (action==='scan-down') moveScanRow(target,1);
    else if (action==='add-scan-channel') addScanChannel();
    else if (action==='save-dsd-table') await saveDsdTable();
    else if (action==='save-dsd-raw') await saveDsdRaw();
    else if (action==='save-device') { const selected=JSON.parse(el('audioDevice').value); await api.selectDevice(selected.name,selected.host_api); toast('Audio device saved; restart to apply'); }
    else if (action==='calibrate-audio') { toast('Measuring three seconds of background audio…'); const result=await api.calibrate(); el('triggerLevel').value=result.recommended_trigger; toast(`Noise floor ${result.noise_floor_p95.toFixed(6)}; recommended trigger loaded`); }
    else if (action==='save-audio-settings') { await api.saveSettings({audio:{trigger_level:Number(el('triggerLevel').value),pre_roll_seconds:Number(el('preRoll').value),silence_hang_seconds:Number(el('silenceHang').value),minimum_seconds:Number(el('minimumCall').value)}}); toast('Detector settings saved; restart to apply'); }
    else if (action==='save-settings') { await api.saveSettings({server:{session_hours:Number(el('sessionHours').value)},streaming:{enabled:el('streamEnabled').checked},runtime:{auto_restart:el('autoRestart').checked,hide_native_windows:el('hideWindows').checked},storage:{warning_free_gb:Number(el('diskWarning').value)}}); toast('Settings saved'); }
    else if (action==='restore-settings') { const backup=el('settingsBackup').value; if(!backup)throw new Error('Choose a settings backup first'); if(confirm(`Restore ${backup}? Hardware control and the active web port will remain unchanged.`)){ await api.restoreSettings(backup); toast('Settings backup restored'); renderSettings(); } }
    else if (action==='restore-config') { const key=target.dataset.key, select=key==='scanlist'?el('scanBackup'):el('dsdBackup'); if(!select?.value)throw new Error('Choose a backup first'); if(confirm(`Restore ${select.value}? The current file will also be backed up.`)){ await api.restore(key,select.value,state.config.revision); toast('Backup restored'); key==='scanlist'?renderScanlist():renderDsdplus(); } }
    else if (action==='install-pwa') { if(!state.installPrompt) throw new Error('PWA installation requires secure HTTPS and a supported browser'); state.installPrompt.prompt(); await state.installPrompt.userChoice; state.installPrompt=null; renderSettings(); }
    else if (action==='revoke-mobile') { if(confirm('Revoke this phone and stop future mobile access?')) { await api.revokeMobileDevice(target.dataset.id); toast('Phone access revoked'); renderSettings(); } }
    else if (action==='pair-android') { await ensureAndroidPairing(true); toast('This Android phone is paired for background playback'); renderSettings(); }
    else if (action==='activate-update') { const registration=await navigator.serviceWorker.getRegistration(); registration?.waiting?.postMessage({type:'SKIP_WAITING'}); location.reload(); }
  } catch (error) { toast(error.message, true); }
});

setInterval(() => { if (state.authenticated) refreshStatus(); }, 5000);
window.addEventListener('beforeinstallprompt', event => { event.preventDefault(); state.installPrompt=event; if(el('installPwaButton')) el('installPwaButton').disabled=false; });
window.addEventListener('appinstalled', () => { state.installPrompt=null; toast('XScan web app installed'); });
window.addEventListener('offline', () => { const banner=el('connectionBanner'); banner.hidden=false; banner.textContent='Offline — controls and live audio will reconnect automatically.'; });
window.addEventListener('online', () => { const banner=el('connectionBanner'); banner.hidden=true; refreshStatus(); });
if ('serviceWorker' in navigator && location.protocol === 'https:') navigator.serviceWorker.register('/sw.js').then(registration => { registration.update(); if(registration.waiting){const banner=el('connectionBanner');banner.hidden=false;banner.innerHTML='XScan update ready. <button class="button" data-action="activate-update">Reload now</button>';}}).catch(()=>{});
authenticate().catch(error => { el('authCopy').textContent='XScan could not start the web session.'; el('authError').textContent=error.message; });
