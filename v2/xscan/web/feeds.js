import { request, currentFeed } from './api.js?v=11';

const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let mounted = false;
let panel;
let data;

async function refresh() {
  try {
    data = await request('/api/v1/feeds');
    for (const feed of data.items) {
      const node = panel.querySelector(`[data-feed="${feed.id}"]`);
      node.querySelector('strong').textContent = feed.name;
      node.querySelector('small').textContent = !feed.enabled ? 'Disabled' :
        `${feed.status.running ? 'Scanning' : 'Stopped / unavailable'} · ${feed.status.now_playing?.display || 'No channel yet'}`;
      node.setAttribute('aria-pressed', String(feed.id === currentFeed()));
    }
  } catch { /* authentication or network recovery handled by the portal */ }
}

export function mountFeeds() {
  if (mounted) return;
  mounted = true;
  panel = document.createElement('section');
  panel.className = 'feed-panel';
  panel.innerHTML = `<div class="feed-cards">${['feed1','feed2'].map(id => `<button type="button" class="button feed-card" data-feed="${id}"><strong>${id === 'feed1' ? 'Feed 1' : 'Feed 2'}</strong><small>Loading…</small></button>`).join('')}</div>
    <details><summary>Receiver setup and shared audio mix</summary><div id="feedSetup"></div></details>`;
  document.getElementById('view').before(panel);
  panel.querySelectorAll('[data-feed]').forEach(button => button.addEventListener('click', () => {
    localStorage.setItem('xscan-admin-feed', button.dataset.feed);
    location.reload();
  }));
  panel.querySelector('details').addEventListener('toggle', event => {
    if (event.target.open) setup().catch(error => {
      panel.querySelector('#feedSetup').textContent = error.message;
    });
  });
  refresh();
  setInterval(refresh, 3000);
}

async function setup() {
  const id = currentFeed();
  const [settings, devices, feeds] = await Promise.all([
    request(`/api/v1/feeds/${id}/settings`), request(`/api/v1/feeds/${id}/devices`), request('/api/v1/feeds')]);
  const feed = feeds.items.find(item => item.id === id);
  const serial = settings.runtime.fmp24_args.map(arg => /^-i"([A-Za-z0-9]{1,8})"$/.exec(arg)).find(Boolean)?.[1] || '';
  const link = settings.runtime.dsdplus_args.find(arg => /^-i\d+$/.test(arg))?.slice(2) || '';
  const output = settings.runtime.dsdplus_args.find(arg => /^-o\d+[MLR]?$/.test(arg))?.slice(2).replace(/[MLR]$/, '') || '';
  const inputs = devices.items || devices.devices || [];
  const node = panel.querySelector('#feedSetup');
  node.innerHTML = `<form class="feed-form"><h3>${esc(feed.name)} setup</h3>
    <p>Stop this feed before changing hardware. Use the dongle's verified unique serial. The DSDPlus output number is different from the capture input number.</p>
    <div class="form-grid"><label>Feed name<input name="name" maxlength="60" required value="${esc(feed.name)}"></label>
    <label>RTL-SDR serial<input name="serial" pattern="[A-Za-z0-9]{1,8}" required value="${esc(serial)}"></label>
    <label>Decoder link ID<input name="link" type="number" min="256" max="65535" required value="${esc(link)}"></label>
    <label>DSDPlus output device number<input name="output" type="number" min="1" max="255" required value="${esc(output)}"></label>
    <label>Capture cable<select name="input" required><option value="">Select exact endpoint</option>${inputs.map((device,index) => `<option value="${index}" ${device.name === settings.audio.device_name && device.host_api === settings.audio.device_host_api ? 'selected' : ''}>${esc(device.name)} — ${esc(device.host_api)}</option>`).join('')}</select></label>
    <label>Audio routing<select name="captureChannel">${[['mono','Separate cable (mono)'],['left','Shared stereo cable: left only'],['right','Shared stereo cable: right only']].map(([value,label]) => `<option value="${value}" ${(settings.audio.capture_channel || 'mono') === value ? 'selected' : ''}>${label}</option>`).join('')}</select></label>
    <label>Feed enabled<input name="enabled" type="checkbox" ${feed.enabled ? 'checked' : ''}></label>
    <label>Start at login<input name="autostart" type="checkbox" ${settings.runtime.desired_running ? 'checked' : ''}></label></div>
    <div class="button-row">${id === 'feed2' ? '<button type="button" class="button" id="provisionFeed">Prepare Feed 2 runtime files</button>' : ''}<button class="button primary" type="submit">Save feed setup</button></div><p id="feedSetupResult" role="status"></p></form>
    <form id="mixForm"><h3>Both: shared listening balance</h3><p>These levels affect the shared mix only. Recordings retain their original audio.</p><div class="form-grid">${feeds.items.map(item => `<label>${esc(item.name)}<input name="${item.id}" type="range" min="0" max="1" step="0.05" value="${feeds.mix[item.id]}"></label>`).join('')}</div><button class="button" type="submit">Save shared balance</button><p id="mixResult" role="status"></p></form>`;
  node.querySelector('#provisionFeed')?.addEventListener('click', async () => {
    try { await request('/api/v1/feeds/feed2/provision', {method:'POST'}); node.querySelector('#feedSetupResult').textContent = 'Runtime ready. Add Feed 2 channels in Scanlists.'; }
    catch(error) { node.querySelector('#feedSetupResult').textContent = error.message; }
  });
  node.querySelector('.feed-form').addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = form.elements;
    const device = inputs[Number(fields.input.value)];
    const replace = (args, pattern, value) => [...args.filter(arg => !pattern.test(arg)), value];
    let fmp = replace(settings.runtime.fmp24_args, /^-i/, `-i"${fields.serial.value}"`);
    fmp = replace(fmp, /^-o/, `-o${fields.link.value}`);
    let dsd = replace(settings.runtime.dsdplus_args, /^-i/, `-i${fields.link.value}`);
    const captureChannel = fields.captureChannel.value;
    dsd = replace(dsd, /^-o/, `-o${fields.output.value}${{mono:'M',left:'L',right:'R'}[captureChannel]}`);
    try {
      await request(`/api/v1/feeds/${id}/settings`, {method:'PUT', body:{
        runtime:{fmp24_args:fmp, dsdplus_args:dsd, desired_running:fields.autostart.checked},
        audio:{device_name:device.name, device_host_api:device.host_api, strict_device:true, capture_channel:captureChannel}}});
      await request(`/api/v1/feeds/${id}`, {method:'PATCH', body:{name:fields.name.value.trim(),enabled:fields.enabled.checked}});
      node.querySelector('#feedSetupResult').textContent = 'Saved. Use Start on the dashboard when ready.';
      refresh();
    } catch(error) { node.querySelector('#feedSetupResult').textContent = error.message; }
  });
  node.querySelector('#mixForm').addEventListener('submit', async event => {
    event.preventDefault();
    try { await request('/api/v1/mix', {method:'PUT',body:Object.fromEntries(Array.from(new FormData(event.currentTarget), ([key,value]) => [key,Number(value)]))}); node.querySelector('#mixResult').textContent = 'Shared balance saved.'; }
    catch(error) { node.querySelector('#mixResult').textContent = error.message; }
  });
}
