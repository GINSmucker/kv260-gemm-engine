// Current board protocol adapter. No synthetic results and no automatic POST retry.
export async function read(path) {
  if (!path.startsWith('/api/')) throw Error('Only local API paths are allowed');
  const response = await fetch(path, {cache:'no-store'});
  const value = await response.json();
  if (!response.ok || value.ok === false) throw Error(value.error?.message || value.error || `HTTP ${response.status}`);
  return value.value ?? value;
}
export function newRequestId() {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), b=>b.toString(16).padStart(2,'0')).join('');
}
export async function submit(id, file) {
  if (!/^[0-9a-f]{32}$/.test(id)) throw Error('Invalid request ID');
  if (file.type !== 'image/jpeg' || !file.size || file.size > 8*1024*1024) throw Error('请选择 1..8 MiB 的 JPEG 图片');
  const response = await fetch(`/api/infer/${id}`, {method:'POST', headers:{'Content-Type':'image/jpeg'}, body:file});
  const value = await response.json();
  if (!response.ok || value.ok === false) throw Error(value.error?.message || value.error || `HTTP ${response.status}`);
  return value.value ?? value;
}
export const terminal = stage => ['PASS','REJECTED_IMAGE_NO_GRAPH','RETAINED_NO_REPLAY',
  'INTERRUPTED_OLD_BOOT_NO_REPLAY','INCOMPLETE_ADMISSION_NO_REPLAY'].includes(stage);
