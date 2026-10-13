import { randomUUID } from 'node:crypto';

export const POLICY = Object.freeze({ schema: 'kv260_ui_policy_v1', source: 'DEMO', hardwareIOEnabled: false, boardContacted: false, realPLStarts: 0, networkConnection: 'LOCKED', serialConnection: 'LOCKED', reason: 'B 会话正在使用板卡。本阶段只运行本机演示，不访问网络设备或串口。' });
export const MODEL = { name: 'DeiT Tiny', id: 'deit_tiny_patch16_224.fb_in1k', blocks: 12, tokens: 197, embedding: 192, heads: 3, classes: 1000, input: '224 × 224', parameters: 5717416, quantization: 'W8 per-channel / A8 per-row', source: 'DEMO_CONFIGURATION', identity: 'UI_DEMO_FIXTURE_V1' };
export const LAYERS = [];
for (let i = 0; i < 12; i++) for (const [family, suffix, K, N] of [['qkv', 'attn.qkv', 192, 576], ['proj', 'attn.proj', 192, 192], ['fc1', 'mlp.fc1', 192, 768], ['fc2', 'mlp.fc2', 768, 192]]) LAYERS.push({ index: LAYERS.length, id: `blocks.${i}.${suffix}`, family, M: 197, K, N });
LAYERS.push({ index: 48, id: 'head', family: 'head', M: 1, K: 192, N: 1000 });
export const TERMINAL = new Set(['SUCCEEDED', 'FAILED', 'CANCELLED']);
export class AppError extends Error {
  constructor(code, message, status = 400) { super(message); this.code = code; this.status = status; }
}
export function validateProfile(profile) {
  if (profile?.kind === 'network') {
    let url; try { url = new URL(profile.endpoint); } catch { throw new AppError('BAD_PROFILE', '请输入完整的 http:// 或 https:// 服务地址。'); }
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.hash) throw new AppError('BAD_PROFILE', '仅支持不含账号、密码或片段的 HTTP 服务地址。');
    return { kind: 'network', endpoint: url.toString(), connection: 'HARDWARE_LOCKED' };
  }
  if (profile?.kind === 'serial') {
    if (!/^COM[1-9]\d{0,3}$/i.test(profile.port ?? '')) throw new AppError('BAD_PROFILE', '串口名称应为 COM 加编号，例如 COM11。');
    if (![9600, 57600, 115200, 230400].includes(Number(profile.baud))) throw new AppError('BAD_PROFILE', '请选择支持的波特率。');
    return { kind: 'serial', port: profile.port.toUpperCase(), baud: Number(profile.baud), framing: '8N1', connection: 'HARDWARE_LOCKED' };
  }
  throw new AppError('BAD_PROFILE', '未知的连接配置类型。');
}
function percentile(sorted, p) { const index = (sorted.length - 1) * p, low = Math.floor(index), high = Math.ceil(index); return sorted[low] + (sorted[high] - sorted[low]) * (index - low); }
function fixture(job) {
  if (job.type === 'layer') {
    const layer = LAYERS[job.options.layerIndex];
    return { source: 'DEMO', layer, case: job.options.case, outputs: layer.M * layer.N, differenceCount: 0, integerCheck: 'SIMULATED_ZERO_DIFFERENCE', simulatedStarts: 1, realPLStarts: 0, apiMs: 8.4, referenceMs: 11.8, kernelCycles: null, kernelCyclesReason: 'UNAVAILABLE', timingsSource: 'SYNTHETIC_FIXTURE' };
  }
  if (job.type === 'benchmark') {
    const samples = Array.from({ length: job.options.runs }, (_, i) => ({ index: i + 1, armMs: +(35 + (i * 17 % 13) * 0.42).toFixed(2), demoMs: +(58 + (i * 7 % 11) * 0.71).toFixed(2), source: 'SYNTHETIC_FIXTURE' }));
    const xs = samples.map(x => x.demoMs).sort((a, b) => a - b);
    return { source: 'DEMO', samples, p50Ms: percentile(xs, .5), p95Ms: percentile(xs, .95), warmup: job.options.warmup, realPLStarts: 0, accuracy: null, accuracyReason: 'NOT_MEASURED', timingsSource: 'SYNTHETIC_FIXTURE' };
  }
  const labels = job.options.sample === 'bridge' ? [[497, '教堂', 'church'], [663, '修道院', 'monastery'], [449, '船屋', 'boathouse'], [698, '宫殿', 'palace'], [975, '湖畔', 'lakeside']] : job.options.sample === 'pattern' ? [[919, '路牌', 'street sign'], [688, '网格', 'net'], [854, '瓦片', 'tile roof'], [632, '火柴', 'matchstick'], [644, '火箭', 'missile']] : [[217, '英国史宾格犬', 'English springer'], [207, '金毛寻回犬', 'golden retriever'], [208, '拉布拉多犬', 'Labrador retriever'], [209, '切萨皮克猎犬', 'Chesapeake Bay retriever'], [235, '德国牧羊犬', 'German shepherd']];
  const probabilities = [.786, .097, .054, .026, .016];
  const logits = Array(1000).fill(Math.log((1 - probabilities.reduce((a, b) => a + b, 0)) / 995));
  labels.forEach((x, i) => logits[x[0]] = Math.log(probabilities[i]));
  return { source: 'DEMO', top5: labels.map((x, i) => ({ index: x[0], label: x[1], english: x[2], probability: probabilities[i] })), logits, simulatedLayers: 49, realPLStarts: 0, fallbackCount: 0, fallbackScope: 'SIMULATION_ONLY', precision: job.options.backend === 'fp32' ? 'FP32' : 'W8A8', input: job.options.input, layerTrace: LAYERS.map(x => ({ ...x, source: 'DEMO', simulatedMs: +(12 + (x.index * 11 % 19) * .55).toFixed(2), realPLStarts: 0 })), kernelCycles: null, kernelCyclesReason: 'UNAVAILABLE' };
}
export class JobStore {
  constructor(clock = Date.now) { this.clock = clock; this.jobs = new Map(); this.active = null; this.audit = []; }
  log(level, message, jobId = null) { this.audit.push({ at: new Date(this.clock()).toISOString(), level, source: 'DEMO', message, jobId }); if (this.audit.length > 400) this.audit.shift(); }
  create(body) {
    this.tickAll();
    if (body?.source !== 'DEMO') throw new AppError('HARDWARE_LOCKED', POLICY.reason, 423);
    if (!['inference', 'layer', 'benchmark'].includes(body.type)) throw new AppError('BAD_TASK', '不支持的任务类型。');
    const options = { ...body.options };
    if (!['normal', 'invalid_input', 'busy', 'disconnect'].includes(options.scenario ?? 'normal')) throw new AppError('BAD_SCENARIO', '未知演示情景。');
    if (this.active || options.scenario === 'busy') throw new AppError('DEVICE_BUSY', options.scenario === 'busy' ? '演示情景：设备正被其他任务占用。未创建新任务。' : '已有本机演示任务运行，请等待或停止后再试。', 409);
    if (body.type === 'layer' && (!Number.isInteger(options.layerIndex) || !LAYERS[options.layerIndex] || !['real', 'zero', 'extreme'].includes(options.case))) throw new AppError('BAD_LAYER', '请选择有效层与用例。');
    if (body.type === 'benchmark' && (!Number.isInteger(options.runs) || options.runs < 3 || options.runs > 30 || !Number.isInteger(options.warmup) || options.warmup < 0 || options.warmup > 5)) throw new AppError('BAD_RUNS', '正式样本3至30次，预热0至5次。');
    if (body.type === 'inference' && (!['fp32', 'w8a8', 'pl49'].includes(options.backend) || !options.input || typeof options.input.name !== 'string' || options.input.name.length > 180)) throw new AppError('BAD_INPUT', '请先选择图片和演示后端。');
    const duration = body.type === 'inference' ? 6200 : body.type === 'layer' ? 1800 : 600 + options.runs * 320;
    const job = { schema: 'kv260_ui_job_v1', id: 'demo_' + randomUUID().slice(0, 8), source: 'DEMO', type: body.type, status: 'VALIDATING', progress: 0, layerCompleted: 0, layerTotal: body.type === 'inference' ? 49 : body.type === 'layer' ? 1 : options.runs, realPLStarts: 0, options, startedAt: new Date(this.clock()).toISOString(), startedMs: this.clock(), duration, updatedAt: null, completedAt: null, elapsedMs: 0, result: null, error: null, events: [] };
    this.jobs.set(job.id, job); this.active = job.id; this.event(job, 'VALIDATING', '校验本机演示输入'); this.log('INFO', `创建${body.type}演示任务，真实PL starts=0`, job.id);
    while (this.jobs.size > 50) this.jobs.delete(this.jobs.keys().next().value);
    return structuredClone(job);
  }
  event(job, status, message) { job.status = status; job.events.push({ at: new Date(this.clock()).toISOString(), status, message, source: 'DEMO' }); }
  finish(job, status, error = null) {
    this.event(job, status, status === 'SUCCEEDED' ? '演示流程完成，不构成设备验收' : status === 'CANCELLED' ? '本机演示已停止' : error.message);
    job.completedAt = new Date(this.clock()).toISOString(); job.error = error; job.elapsedMs = this.clock() - job.startedMs;
    if (status === 'SUCCEEDED') { job.progress = 100; job.layerCompleted = job.layerTotal; job.result = fixture(job); }
    this.active = null; this.log(status === 'FAILED' ? 'ERROR' : 'INFO', `${job.id} ${status}`, job.id);
  }
  tick(job) {
    if (TERMINAL.has(job.status)) return job;
    const elapsed = this.clock() - job.startedMs; job.elapsedMs = elapsed; job.updatedAt = new Date(this.clock()).toISOString();
    if (job.status === 'CANCELLING') { if (this.clock() - job.cancelAt >= 160) this.finish(job, 'CANCELLED'); return job; }
    const pct = Math.min(100, Math.floor(elapsed / job.duration * 100));
    if (job.options.scenario === 'invalid_input' && pct >= 12) { this.finish(job, 'FAILED', { code: 'DEMO_INVALID_INPUT', message: '演示情景：输入张量校验失败。没有真实模型或设备调用。' }); return job; }
    if (job.options.scenario === 'disconnect' && pct >= 55) { this.finish(job, 'FAILED', { code: 'DEMO_CONNECTION_LOST', message: '演示情景：连接中断。任务结果无效，未重试或接触板卡。' }); return job; }
    job.progress = pct;
    const status = pct < 8 ? 'VALIDATING' : pct < 18 ? 'PREPROCESSING' : pct < 94 ? 'RUNNING' : 'POSTPROCESSING';
    if (pct >= 100) { this.finish(job, 'SUCCEEDED'); return job; }
    if (status !== job.status) this.event(job, status, { VALIDATING: '校验输入', PREPROCESSING: '模拟预处理', RUNNING: '逐步运行合成流程', POSTPROCESSING: '组织演示结果' }[status]);
    const completed = Math.min(job.layerTotal, Math.floor(Math.max(0, pct - 18) / 76 * job.layerTotal));
    while (job.layerCompleted < completed) { job.layerCompleted++; job.events.push({ at: new Date(this.clock()).toISOString(), status: 'LAYER', layer: job.layerCompleted, message: job.type === 'inference' ? LAYERS[job.layerCompleted - 1].id : `模拟步骤 ${job.layerCompleted}`, source: 'DEMO', realPLStarts: 0 }); }
    return job;
  }
  tickAll() { for (const job of this.jobs.values()) this.tick(job); }
  get(id) { const job = this.jobs.get(id); if (!job) throw new AppError('NOT_FOUND', '任务不存在。', 404); return structuredClone(this.tick(job)); }
  list() { this.tickAll(); return Array.from(this.jobs.values()).reverse().map(x => structuredClone(x)); }
  cancel(id) { const job = this.jobs.get(id); if (!job) throw new AppError('NOT_FOUND', '任务不存在。', 404); if (!TERMINAL.has(this.tick(job).status) && job.status !== 'CANCELLING') { job.cancelAt = this.clock(); this.event(job, 'CANCELLING', '等待下一个演示停止点'); this.log('WARN', '请求停止本机演示', id); } return structuredClone(job); }
  diagnostics(action) {
    if (!['snapshot', 'model_identity', 'owner_status'].includes(action)) throw new AppError('BAD_ACTION', '只允许预定义的本机诊断动作。');
    const result = { source: 'DEMO', action, hardwareContacted: false, boardConnection: 'NOT_CONNECTED', realPLStarts: 0, model: MODEL.identity, ownerPid: null, ownerState: 'NOT_QUERIED', activeDemoJob: this.active, at: new Date(this.clock()).toISOString() };
    this.log('INFO', `${action}：本机快照，板卡 NOT_CONNECTED，owner NOT_QUERIED`); return result;
  }
}
