export async function api(path, data) {
  if (!path.startsWith('/api/')) throw new Error('Only local API paths are allowed');
  const res = await fetch(path, { method: data === undefined ? 'GET' : 'POST', headers: data === undefined ? {} : { 'Content-Type': 'application/json' }, ...(data === undefined ? {} : { body: JSON.stringify(data) }) });
  const result = await res.json();
  if (!res.ok) { const error = new Error(result.error?.message ?? `HTTP ${res.status}`); error.code = result.error?.code; throw error; }
  return result;
}
// All transports share a contract. Physical I/O is intentionally absent in U1.
export class ProtectedTransport {
  constructor(kind) { this.kind = kind; }
  async connect() { return api(this.kind === 'serial' ? '/api/serial/open' : '/api/connect/network', {}); }
  async saveProfile(profile) { return api('/api/profiles', { ...profile, kind: this.kind }); }
}
export function download(filename, text, type = 'application/json') {
  const href = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement('a'); a.href = href; a.download = filename; a.hidden = true; document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(href), 1000);
}
export function escape(value) { return String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
