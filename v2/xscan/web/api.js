export function cookie(name) {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = document.cookie.match(new RegExp(`(?:^|; )${escaped}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : '';
}

export class ApiError extends Error {
  constructor(message, status, payload) { super(message); this.status = status; this.payload = payload; }
}

export async function request(path, options = {}) {
  const method = (options.method || 'GET').toUpperCase();
  const headers = new Headers(options.headers || {});
  let body = options.body;
  if (body && typeof body !== 'string' && !(body instanceof Blob) && !(body instanceof ArrayBuffer)) {
    headers.set('Content-Type', 'application/json');
    body = JSON.stringify(body);
  }
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) headers.set('X-CSRF-Token', cookie('xscan_csrf'));
  const response = await fetch(path, { ...options, method, headers, body, credentials: 'same-origin', cache: 'no-store' });
  const contentType = response.headers.get('content-type') || '';
  const payload = contentType.includes('json') ? await response.json() : await response.text();
  if (!response.ok) throw new ApiError(payload?.detail || payload || `Request failed (${response.status})`, response.status, payload);
  return payload;
}

export const api = {
  authState: () => request('/api/v1/auth/state'),
  setup: password => request('/api/v1/auth/setup', { method: 'POST', body: { password } }),
  login: password => request('/api/v1/auth/login', { method: 'POST', body: { password } }),
  logout: () => request('/api/v1/auth/logout', { method: 'POST' }),
  status: () => request('/api/v1/status'),
  system: action => request(`/api/v1/system/${action}`, { method: 'POST' }),
  windows: action => request(`/api/v1/system/windows/${action}`, { method: 'POST' }),
  calls: query => request(`/api/v1/calls?${new URLSearchParams(query)}`),
  updateCall: (id, patch) => request(`/api/v1/calls/${id}`, { method: 'PATCH', body: patch }),
  callAction: (action, ids, extra = {}) => request(`/api/v1/calls/${action}`, { method: 'POST', body: { ids, ...extra } }),
  devices: () => request('/api/v1/devices'),
  selectDevice: (name, host_api) => request('/api/v1/devices/selected', { method: 'PUT', body: { name, host_api } }),
  calibrate: () => request('/api/v1/devices/calibrate?seconds=3', { method: 'POST' }),
  settings: () => request('/api/v1/settings'),
  saveSettings: patch => request('/api/v1/settings', { method: 'PUT', body: patch }),
  settingsBackups: () => request('/api/v1/settings/backups'),
  restoreSettings: backup => request('/api/v1/settings/restore', { method: 'POST', body: { backup } }),
  configIndex: () => request('/api/v1/config'),
  config: key => request(`/api/v1/config/${key}`),
  saveConfig: (key, payload) => request(`/api/v1/config/${key}`, { method: 'PUT', body: payload }),
  backups: key => request(`/api/v1/config/${key}/backups`),
  restore: (key, backup, revision) => request(`/api/v1/config/${key}/restore`, { method: 'POST', body: { backup, revision } }),
  diagnostics: () => request('/api/v1/diagnostics'),
  logs: () => request('/api/v1/diagnostics/logs?limit=500'),
  mobileDevices: () => request('/api/v1/mobile/devices'),
  registerMobileDevice: payload => request('/api/v1/mobile/devices', { method: 'POST', body: payload }),
  revokeMobileDevice: id => request(`/api/v1/mobile/devices/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  mobileRelease: () => request('/api/v1/mobile/release'),
  mobileBootstrap: () => request('/api/v1/mobile/bootstrap'),
};
