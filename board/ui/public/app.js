import { api, ProtectedTransport, download, escape as e } from './api-client.js';
import { boardPrediction, boardProgress, boardLayer, boardBenchmark } from './live-view.js';
import {overview} from './presentation.js';
import {routeMap} from './charts.js';
import {finalOverview,finalPerformance,finalArchitecture,bindFinalPerformance} from './final-lab.js';

const paths = {
  image: '<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8" cy="8" r="1.5"/><path d="m3 16 5-5 4 4 3-3 6 6"/>',
  layers: '<path d="m12 3 10 5-10 5L2 8Z"/><path d="m2 12 10 5 10-5M2 16l10 5 10-5"/>',
  chart: '<path d="M4 3v18h17M8 16l4-5 4 2 5-7"/>', history: '<path d="M3 11a9 9 0 1 1 2.7 7M3 4v7h7M12 7v5l3 2"/>',
  plug: '<path d="m13 4 7 7-4 4a5 5 0 0 1-7-7ZM17 2l-4 4m9 1-4 4M9 15l-3 3H2"/>', upload: '<path d="M12 16V3m-5 5 5-5 5 5M3 15v5h18v-5"/>',
  play: '<path d="m8 4 12 8-12 8Z"/>', stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>', download: '<path d="M12 3v12m-5-5 5 5 5-5M3 16v5h18v-5"/>',
  arrow: '<path d="M4 12h16m-6-6 6 6-6 6"/>', shield: '<path d="m12 3 8 3v6q0 6-8 9-8-3-8-9V6ZM9 12l2 2 4-4"/>',
  check: '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>', clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  box: '<rect x="4" y="4" width="16" height="16" rx="4"/><path d="M8 1v3m4-3v3m4-3v3M8 20v3m4-3v3m4-3v3M1 8h3m-3 4h3m-3 4h3m16-8h3m-3 4h3m-3 4h3"/>',
  file: '<path d="M14 2H5v20h14V7Zm0 0v5h5M8 12h8m-8 4h8"/>', x: '<path d="m6 6 12 12M6 18 18 6"/>', refresh: '<path d="M20 7a8 8 0 0 0-14-2L3 8m0-5v5h5m-4 9a8 8 0 0 0 14 2l3-3m0 5v-5h-5"/>',
  terminal: '<path d="m4 6 5 5-5 5m8 1h8"/>', network: '<rect x="8" y="2" width="8" height="6" rx="1"/><path d="M12 8v5M4 13h16M4 13v3m8-3v3m8-3v3"/><rect x="1" y="16" width="6" height="5" rx="1"/><rect x="9" y="16" width="6" height="5" rx="1"/><rect x="17" y="16" width="6" height="5" rx="1"/>',
};
const icon = (name) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] ?? paths.box}</svg>`;
const pages = [['overview', '成果总览', 'chart'], ['performance', '性能与耗时', 'clock'], ['architecture', '系统架构', 'box'], ['inference', '推理工作台', 'image'], ['layer', '线性层实验', 'layers'], ['benchmark', '测评演示', 'chart'], ['history', '运行记录', 'history'], ['archive', '历史实测', 'history'], ['connection', '连接与诊断', 'plug']];
const samples = [['bridge', '建筑图卡'], ['dog', '动物图卡'], ['pattern', '结构图卡']];
const statusNames = { VALIDATING: '校验输入', PREPROCESSING: '预处理', RUNNING: '执行中', POSTPROCESSING: '组织结果', SUCCEEDED: '已完成', FAILED: '执行失败', CANCELLING: '正在停止', CANCELLED: '已停止', IMPORTED: '已导入' };
const backendNames = { fp32: 'FP32 参考演示', w8a8: 'W8A8 参考演示', pl49: '49 层 PL 演示' };
const typeNames = { inference: '整网推理', layer: '线性层实验', benchmark: '测评对照', report: '导入报告' };
const terminal = new Set(['SUCCEEDED', 'FAILED', 'CANCELLED', 'IMPORTED']);
const state = { page: 'inference', catalog: null, jobs: [], imports: [], active: null, selected: {}, input: { name: '建筑图卡.svg', kind: 'builtin', sample: 'bridge', url: '/assets/bridge.svg', dimensions: '1000 × 680', size: null }, backend: 'pl49', scenario: 'normal', layerIndex: 0, layerCase: 'real', runs: 12, warmup: 3, connectionTab: 'network', endpoint: 'http://kv260:8080', port: 'COM11', baud: 115200, logs: [], logLevel: 'ALL', search: '', historyStatus: 'ALL', submitting: false, error: null, polling: false, dialogFocus: null };
const plLabel = () => state.boardHealth?.model_protocol==='h576_v5'?'H576 · 36PL / 13CPU':'49层PL';
state.motionReduced = localStorage.getItem('kv260-ui-reduced-motion') === 'true';
state.source = 'DEMO'; state.boardHealth = null; state.boardConfigured = false; state.inputFile = null;
document.documentElement.dataset.reducedMotion = String(state.motionReduced);
try { const saved = JSON.parse(localStorage.getItem('kv260-ui-profiles') ?? '{}'); Object.assign(state, { endpoint: saved.endpoint ?? state.endpoint, port: saved.port ?? state.port, baud: saved.baud ?? state.baud }); } catch { /* corrupt preferences are not executable */ }
const screen = document.querySelector('#screen');
const isBusy = () => Boolean(state.active || state.submitting);
const currentJob = (type) => state.jobs.find(x => x.id === state.selected[type]);
const allRecords = () => [...state.imports, ...state.jobs];
const fmt = (x, digits = 1) => Number.isFinite(x) ? x.toFixed(digits) : '—';
const time = x => x && Number.isFinite(Date.parse(x)) ? new Date(x).toLocaleTimeString('zh-CN', { hour12: false }) : '未报告';
const badge = (status) => `<span class="badge ${status === 'SUCCEEDED' ? 'badge-success' : status === 'FAILED' ? 'badge-failed' : status === 'IMPORTED' ? 'badge-imported' : terminal.has(status) ? '' : 'badge-running'}">${e(statusNames[status] ?? status)}</span>`;
const btn = (action, label, glyph, extra = '') => `<button class="btn ${extra}" data-action="${action}" aria-label="${e(label)}">${icon(glyph)}<span>${label}</span></button>`;
const demoStrip = () => state.source === 'BOARD' ? `<div class="demo-strip">${icon('network')}<span>${state.boardHealth ? (state.boardHealth.pl_ready?`KV260已连接 / ${plLabel()} owner已核验`:'KV260已连接 / ARM后端可用') : '板端未连接，不能开始真实任务'}。结果与实际图片、模型及窗口绑定。</span></div>` : `<div class="demo-strip">${icon('shield')}<span>界面演示 · 不运行模型、不分析图片，分类与计时均为固定样例。上传任意图片不会改变该样例。</span></div>`;
function pageHead(eyebrow, title, subtitle, actions = '') { return `<div class="page-head"><div><div class="eyebrow">${eyebrow}</div><h1>${title}</h1><p>${subtitle}</p></div><div class="page-actions">${isBusy() ? btn('cancel', state.source==='BOARD'?'请求停止任务':'停止演示', 'stop', 'btn-danger') : ''}${actions}</div></div>`; }
function stat(glyph, label, value, unit) { return `<div class="stat-card"><div class="stat-icon">${icon(glyph)}</div><div><label>${label}</label><div class="stat-value">${value}<small>${unit}</small></div></div></div>`; }
function scenarioSelect() { if (state.source==='BOARD') { const backend=state.page==='layer'?state.layerBackend:state.backend; return `<div class="scenario-field"><label>执行位置</label><span class="badge">${backend==='pl49' ? (state.boardHealth?.pl_ready?`KV260 PS/PL · ${plLabel()}`:'KV260 PS/PL · 当前PL未就绪') : 'KV260 ARM · 实际计算'}</span></div>`; } return `<div class="scenario-field"><label for="scenario">演示情景</label><select id="scenario" data-field="scenario" data-focus="scenario" ${isBusy() ? 'disabled' : ''}>${[['normal', '正常流程'], ['invalid_input', '输入校验失败'], ['busy', '设备占用'], ['disconnect', '中途断连']].map(([v, t]) => `<option value="${v}" ${state.scenario === v ? 'selected' : ''}>${t}</option>`).join('')}</select></div>`; }
function runButton(type) { const live=state.source==='BOARD'; return `<button class="btn btn-primary" data-action="start-${type}" data-focus="start" ${isBusy() || (live&&!state.boardHealth) ? 'disabled' : ''}>${icon('play')}<span>${state.submitting ? '正在提交' : live ? ({inference:'开始板端推理',layer:'运行板端参考',benchmark:'开始板端测评'})[type] : ({inference:'播放分类样例',layer:'运行演示用例',benchmark:'开始演示测评'})[type]}</span></button>`; }
function progress(job) {
  if (!job) return '';
  if (job.source==='BOARD') return boardProgress(job);
  const phases = ['VALIDATING', 'PREPROCESSING', 'RUNNING', 'POSTPROCESSING', 'SUCCEEDED'];
  const idx = phases.indexOf(job.status);
  return `<div class="card progress-card"><div class="progress-top"><div class="progress-label">${!terminal.has(job.status) ? '<i class="pulse"></i>' : icon(job.status === 'SUCCEEDED' ? 'check' : 'clock')}${e(statusNames[job.status])}</div><span class="mono">${job.layerCompleted} / ${job.layerTotal} 模拟步骤 · ${job.progress}%</span></div><p class="progress-sub">${e(job.id)} · 本机流程 ${fmt(job.elapsedMs / 1000)}s · 实际 PL starts 0</p><div class="progress-track"><i style="width:${job.progress}%"></i></div>${job.type === 'inference' ? `<div class="layer-grid" aria-label="49个模拟层的完成进度">${Array.from({ length: 49 }, (_, i) => `<i class="${i < job.layerCompleted ? 'done' : i === job.layerCompleted && !terminal.has(job.status) ? 'current' : ''}" title="${e(state.catalog.layers[i].id)}"></i>`).join('')}</div>` : ''}<div class="stage-line">${['输入', '预处理', '计算', '后处理', '结果'].map((t, i) => `<span class="${i <= idx ? 'done' : ''}">${t}</span>`).join('')}</div>${job.error ? `<div class="alert"><strong>${e(job.error.code)}</strong>${e(job.error.message)}</div>` : ''}</div>`;
}
function emptyResult(title = '等待本次运行', text = '选择输入与模式，启动本机演示后查看结果。') { return `<div class="result-empty"><div class="empty-glyph">${icon('chart')}</div><strong>${title}</strong><p>${text}</p><div class="empty-bars"><i></i><i></i><i></i><i></i><i></i></div></div>`; }
function failedResult(job) { return `<div class="error-state">${icon(job.status === 'CANCELLED' ? 'stop' : 'shield')}<strong>${e(statusNames[job.status])}</strong><p>${e(job.error?.message ?? '该任务没有生成完成结果。可以选择正常情景后重新运行。')}</p></div>`; }
function prediction(job) {
  if (state.source==='BOARD') return boardPrediction(job);
  if (!job) return emptyResult();
  if (['FAILED', 'CANCELLED'].includes(job.status)) return failedResult(job);
  if (job.status !== 'SUCCEEDED') return emptyResult(statusNames[job.status], `正在演示 ${job.layerCompleted} / 49 层。真实设备调用为 0。`);
  const top = job.result.top5;
  return `<div class="success-enter"><div class="winner"><div class="tiny-label">TOP 1 · 合成演示</div><strong>${e(top[0].label)}</strong><div class="english-label">${e(top[0].english)} · #${top[0].index}</div><div class="winner-score">${fmt(top[0].probability * 100)}<small>%</small></div></div><div class="top5-title">TOP 5 / 1000 类 · 合成概率</div><div>${top.map((x, i) => `<div class="prediction"><div class="prediction-label"><span>0${i + 1}</span><strong>${e(x.label)}</strong><em>${fmt(x.probability * 100)}%</em></div><div class="prediction-bar"><i style="width:${x.probability * 100}%"></i></div></div>`).join('')}</div><div class="result-meta"><div><label>本机流程耗时</label><strong>${fmt(job.elapsedMs / 1000)} s</strong></div><div><label>模拟层 / 实际 PL</label><strong>49 / 0</strong></div><div><label>演示后端</label><strong>${e(job.options.backend.toUpperCase())}</strong></div><div><label>对应输入</label><strong>${e(job.options.input.name)}</strong></div></div><div class="result-note">DEMO · 此结果不是模型预测或精度测量。模拟完成不表示 FPGA 覆盖验收通过。</div><div class="result-actions"><button class="btn btn-sm" data-action="export" data-id="${job.id}">${icon('download')}导出 JSON</button><button class="btn btn-sm btn-quiet" data-action="details" data-id="${job.id}">运行详情 ${icon('arrow')}</button></div></div>`;
}
function inferencePage() {
  const job = currentJob('inference');
  return pageHead('INFERENCE / 01', '推理工作台', '从一张图片开始，观察计算流程并核对返回结果。', btn('import', '导入报告', 'upload')) + demoStrip() + `<div class="stats-grid">${stat('box',state.boardHealth?.model_protocol==='h576_v5'?'MLP隐藏维':'模型参数',state.boardHealth?.model_protocol==='h576_v5'?'576':'5.72',state.boardHealth?.model_protocol==='h576_v5'?'通道':'M')}${stat('image', '输入规格', '224 × 224', 'RGB')}${stat('layers', '目标 Linear', '49', '12 blocks')}${stat('shield', '本任务 PL 启动', String(job?.source === 'BOARD' ? (job.realPLStarts ?? 0) : 0), job?.source === 'BOARD' ? '实际计数' : '尚无实板任务')}</div><div class="inference-grid"><section class="card input-panel"><div class="card-head"><span class="card-title"><span class="section-number">01</span>输入图像</span><span class="badge">${state.input.kind === 'builtin' ? '本机图卡' : '本机上传'}</span></div><div class="card-body"><div class="input-preview" id="drop-zone" role="button" tabindex="0" aria-label="选择或拖入本机图片" data-action="upload" data-focus="upload"><img src="${e(state.input.url)}" alt="${e(state.input.name)}"><button class="btn btn-sm upload-overlay" data-action="upload">${icon('upload')}选择图片</button><div class="image-overlay"><span>${e(state.input.dimensions)}</span><span>LOCAL INPUT</span></div></div><div class="image-info"><strong>${e(state.input.name)}</strong><span>${state.input.size ? fmt(state.input.size / 1024, 0) + ' KB' : '演示插画 · 非数据集图片'}</span></div><div class="sample-row">${samples.map(([id, name]) => `<button class="sample-card ${state.input.sample === id ? 'selected' : ''}" data-action="sample" data-sample="${id}" ${isBusy() ? 'disabled' : ''}><img src="/assets/${id}.svg" alt=""><span>${name}</span></button>`).join('')}</div><p class="input-hint">拖入 PNG / JPEG / WebP，最大 10 MiB。图片只在本机预览。</p><label class="field-label">运行模式</label><div class="segmented" role="group" aria-label="演示后端">${[['fp32', 'FP32 参考', 'ARM / DEMO'], ['w8a8', 'W8A8 参考', 'INT8 / DEMO'], ['pl49', '49 层 PL', 'FPGA / DEMO']].map(([id, name, sub]) => `<button class="segment ${state.backend === id ? 'selected' : ''}" data-action="backend" data-backend="${id}" aria-pressed="${state.backend === id}" ${isBusy() ? 'disabled' : ''}>${name}<small>${state.source === 'BOARD' ? (id === 'pl49' ? (state.boardHealth?.pl_ready ? 'PL 可用' : 'PL 未就绪') : 'ARM 实测') : sub}</small></button>`).join('')}</div><div class="run-controls">${scenarioSelect()}${runButton('inference')}</div></div></section><section class="card result-panel"><div class="card-head"><span class="card-title"><span class="section-number">02</span>预测结果</span>${job ? badge(job.status) : '<span class="badge">等待运行</span>'}</div><div class="card-body">${prediction(job)}</div></section></div>${progress(job)}`;
}
function layerPage() {
  const layer = state.catalog.layers[state.layerIndex]; const job = currentJob('layer'); const r = job?.result;
  return pageHead('COMPUTE / 02', '线性层实验', '选择真实模型层规格，演练完整请求与整数结果核对。') + demoStrip() + `<div class="task-grid"><section class="card"><div class="card-head"><span class="card-title">实验配置</span><span class="badge badge-demo">DEMO</span></div><div class="card-body"><div class="field"><label for="layer-select">模型层</label><select id="layer-select" data-field="layerIndex" data-focus="layerIndex" ${isBusy() ? 'disabled' : ''}>${state.catalog.layers.map(x => `<option value="${x.index}" ${x.index === state.layerIndex ? 'selected' : ''}>${e(x.id)}</option>`).join('')}</select></div><div class="dimensions">${['M', 'K', 'N'].map(k => `<div class="dimension"><small>${k}</small><strong>${layer[k]}</strong></div>`).join('')}</div><div class="field"><label for="case-select">测试用例</label><select id="case-select" data-field="layerCase" ${isBusy() ? 'disabled' : ''}>${[['real', '真实激活规格 · 演示'], ['zero', '全零输入 · 演示'], ['extreme', '正负极值 · 演示']].map(([id, name]) => `<option value="${id}" ${id === state.layerCase ? 'selected' : ''}>${name}</option>`).join('')}</select></div><div class="field"><label for="mode-select">数值模式</label><select id="mode-select" disabled><option>Dense W8A8</option><option disabled>2:4 sparse · UNSUPPORTED</option><option disabled>W4A8 · UNSUPPORTED</option></select><small>INT8 × INT8 → INT32，row major</small></div>${scenarioSelect()}<div style="margin-top:18px">${runButton('layer')}</div></div></section><section class="card"><div class="card-head"><span class="card-title">整数核对与返回值</span><span class="badge">真实验真未运行</span></div><div class="card-body">${state.source==='BOARD' ? boardLayer(job) : !job ? emptyResult('等待层实验', '此处展示合成用例回执。B 的真实数据包与 C 后端接入后再做实际验真。') : job.status !== 'SUCCEEDED' ? terminal.has(job.status) ? failedResult(job) : emptyResult(statusNames[job.status]) : `<div class="check-summary">${icon('check')}<div><strong>演示差异数 0</strong><small>SIMULATED · 非真实 INT64 核对结论</small></div></div><table class="numeric-table"><tbody>${[['层名', r.layer.id], ['有效输出元素', r.outputs.toLocaleString()], ['模拟 start / 实际 start', '1 / 0'], ['样例 API 时间', r.apiMs + ' ms · SYNTHETIC'], ['样例参考时间', r.referenceMs + ' ms · SYNTHETIC'], ['kernel cycles', 'UNAVAILABLE'], ['数值模式', 'dense_w8a8']].map(([k, v]) => `<tr><td>${k}</td><td>${e(v)}</td></tr>`).join('')}</tbody></table><div class="result-note">回执演示全部来自本机 fixture。原始 X/W/Y 和 FPGA 整数验真尚未执行。</div><div class="result-actions"><button class="btn" data-action="export" data-id="${job.id}">${icon('download')}导出回执</button><button class="btn btn-quiet" data-action="details" data-id="${job.id}">原始详情 ${icon('arrow')}</button></div>`}</div></section></div>${progress(job)}`;
}
function lineChart(samples) {
  const max = 80, x = i => 45 + i / Math.max(1, samples.length - 1) * 535, y = v => 195 - v / max * 165;
  return `<svg class="chart" viewBox="0 0 610 230" role="img" aria-label="合成样例耗时曲线"><g stroke="#e7ecdf" stroke-width="1">${[0,20,40,60,80].map(v => `<path d="M45 ${y(v)}H580"/><text x="12" y="${y(v) + 3}" stroke="none">${v}</text>`).join('')}</g><text x="10" y="12">ms</text><polyline fill="none" stroke="#91a782" stroke-width="2.5" points="${samples.map((s, i) => `${x(i)},${y(s.armMs)}`).join(' ')}"/><polyline fill="none" stroke="#cfa174" stroke-width="2.5" points="${samples.map((s, i) => `${x(i)},${y(s.demoMs)}`).join(' ')}"/>${samples.map((s, i) => `<circle cx="${x(i)}" cy="${y(s.demoMs)}" r="3" fill="#cfa174"/><text x="${x(i) - 4}" y="216">${s.index}</text>`).join('')}</svg>`;
}
function benchmarkPage() {
  const job = currentJob('benchmark'), r = job?.result;
  if (state.source==='BOARD') return pageHead('EVALUATION / 03','板端测评对照',state.boardHealth?.model_protocol==='h576_v5'?'同一H576模型的ARM与PS/PL完整调用时间。':'同一图片的实际ARM FP32与量化语义时间。')+demoStrip()+boardBenchmark(state,job,{busy:isBusy(),run:runButton('benchmark')})+progress(job);
  return pageHead('EVALUATION / 03', '测评对照', '组织同样本对照，保留统计范围与每次运行记录。') + demoStrip() + `<div class="task-grid"><section class="card"><div class="card-head"><span class="card-title">测评配置</span></div><div class="card-body"><div class="field"><label>对照模式</label><div class="lock-panel">${icon('chart')}<div><strong>ARM 参考 / PL 演示</strong>仅演练测评流程，不测设备、不计算真实加速比。</div></div></div><div class="form-grid"><div class="field"><label for="warmup">预热次数</label><input id="warmup" type="number" min="0" max="5" value="${state.warmup}" data-field="warmup" ${isBusy() ? 'disabled' : ''}></div><div class="field"><label for="runs">正式次数</label><input id="runs" type="number" min="3" max="30" value="${state.runs}" data-field="runs" ${isBusy() ? 'disabled' : ''}></div></div><div class="field"><label>数据范围</label><span class="badge">SYNTHETIC FIXTURE</span><small>真实准确率、温度与 kernel cycles 未测。</small></div>${scenarioSelect()}<div style="margin-top:18px">${runButton('benchmark')}</div></div></section><section class="card"><div class="card-head"><span class="card-title">耗时分布</span><span class="badge badge-demo">合成统计</span></div><div class="card-body">${!job ? emptyResult('等待测评', '运行演示后显示合成曲线、p50/p95和可导出的逐次样本。') : job.status !== 'SUCCEEDED' ? terminal.has(job.status) ? failedResult(job) : emptyResult(statusNames[job.status]) : `<div class="metrics-row">${[['演示 p50', fmt(r.p50Ms), 'ms'], ['演示 p95', fmt(r.p95Ms), 'ms'], ['正式样例', r.samples.length, '次']].map(([k,v,u]) => `<div class="mini-metric"><label>${k}</label><strong>${v}<small> ${u}</small></strong></div>`).join('')}</div><div class="chart-legend"><span><i></i>ARM · 合成样例</span><span><i class="orange"></i>PL演示 · 合成样例</span></div>${lineChart(r.samples)}<div class="result-actions"><button class="btn" data-action="csv" data-id="${job.id}">${icon('download')}导出 CSV</button><button class="btn btn-quiet" data-action="export" data-id="${job.id}">JSON ${icon('arrow')}</button></div><div class="result-note">这些数字来自样例序列，不代表 ARM 或 FPGA 的实际性能。实际设备 starts=0。</div>`}</div></section></div>${progress(job)}${r ? `<section class="card" style="margin-top:20px"><div class="card-head"><span class="card-title">逐次样例</span><span class="subtle">未包含模拟预热 · 实际测量 NOT RUN</span></div><div class="table-wrap"><table class="data-table"><thead><tr><th>样本</th><th>ARM 合成 ms</th><th>PL 演示合成 ms</th><th>来源</th></tr></thead><tbody>${r.samples.map(s => `<tr><td class="mono">${String(s.index).padStart(2,'0')}</td><td class="mono">${s.armMs}</td><td class="mono">${s.demoMs}</td><td><span class="badge badge-demo">${s.source}</span></td></tr>`).join('')}</tbody></table></div></section>` : ''}`;
}
function historyPage() {
  const rows = allRecords().filter(x => (state.historyStatus === 'ALL' || x.status === state.historyStatus) && `${x.id} ${typeNames[x.type]} ${x.options?.input?.name ?? ''}`.toLowerCase().includes(state.search.toLowerCase()));
  return pageHead('RUNS / 04', '运行记录', '保留每次输入、来源和结果；导入报告只作为文件查看。', btn('import', '导入报告', 'upload')) + demoStrip() + `<div class="history-toolbar"><input id="history-search" placeholder="搜索运行 ID、类型或图片名称" value="${e(state.search)}" data-field="search" data-focus="search" aria-label="搜索运行记录"><select data-field="historyStatus" aria-label="按状态筛选">${[['ALL','全部状态'],['SUCCEEDED','已完成'],['FAILED','执行失败'],['CANCELLED','已停止'],['IMPORTED','已导入']].map(([v,t]) => `<option value="${v}" ${v === state.historyStatus ? 'selected' : ''}>${t}</option>`).join('')}</select>${btn('refresh-history','刷新','refresh')}</div><section class="card"><div class="table-wrap"><table class="data-table"><thead><tr><th>运行 ID</th><th>任务 / 输入</th><th>来源</th><th>状态</th><th>来源时间</th><th>操作</th></tr></thead><tbody>${rows.map(x => `<tr><td class="mono">${e(x.id)}</td><td>${e(typeNames[x.type] ?? x.type)}<br><span class="subtle">${e(x.options?.input?.name ?? x.options?.case ?? x.filename ?? '—')}</span></td><td><span class="badge ${x.source === 'IMPORTED' ? 'badge-imported' : 'badge-demo'}">${e(x.source)}</span></td><td>${badge(x.status)}</td><td class="mono">${time(x.startedAt)}</td><td><button class="btn btn-sm" data-action="details" data-id="${e(x.id)}">查看</button></td></tr>`).join('')}</tbody></table>${!rows.length ? '<div class="table-empty">暂无符合条件的记录。先执行任务，或导入结果 JSON。</div>' : ''}</div></section><div class="wide-note">每个服务最多保留50条内存记录；板端原始文件另行保存。板端日历未同步，来源时间不能替代客户端UTC；导入报告只在当前浏览器内存中显示。导入不表示设备在线或报告已验收。</div>`;
}
function logsMarkup() {
  const logs = state.logs.filter(x => state.logLevel === 'ALL' || x.level === state.logLevel);
  return logs.length ? logs.map(x => `<div class="log-line ${x.level.toLowerCase()}"><time>${time(x.at)}</time><span class="log-level">${e(x.level)}</span><span>${e(x.message)}</span></div>`).join('') : '<div class="terminal-empty">本机日志等待操作。可读取演示快照，板卡不会收到任何命令。</div>';
}
function boardConnectionPage() {
  const h=state.boardHealth;
  return pageHead('DEVICE / 05','连接与诊断','实际网口SSH通道与板端冻结模型身份。')+demoStrip()+`<div class="connection-grid"><section class="card"><div class="card-head"><span class="card-title">真实网口连接</span><span class="badge">${h?'CONNECTED':'DISCONNECTED'}</span></div><div class="card-body"><div class="lock-panel">${icon('network')}<div><strong>SSH kv260 → loopback任务代理</strong>图片与结果通过当前SSH网口通道传输；两端均只监听loopback，不开放板端LAN服务。</div></div><table class="numeric-table"><tbody>${[['执行主机',h?.execution_host??'未连接'],['架构',h?.architecture??'未报告'],['boot',h?.boot_id??'未报告'],['模型清单',h?.bundle_identity?.manifest_sha256??'未核验'],['模型payload',h?.payload_verified?'全部核验通过':'未核验'],['实际active任务',h?.active_job??'无UI active任务'],['PL状态',h?.pl_ready?`READY · ${plLabel()}`:h?`NOT_READY · ${h.pl_reason??'当前窗口没有可用的PL owner'}`:'NOT_QUERIED · 未连接'],['串口','未连接 · 不打开COM']].map(([k,v])=>`<tr><td>${k}</td><td class="mono">${e(v)}</td></tr>`).join('')}</tbody></table><div class="button-row">${btn('connect-board',h?'刷新真实连接':'连接 KV260','network','btn-primary')}</div><p class="input-hint">连接刷新不加载PL、不改驱动、不重启。板端日历未同步，报告保留boot与单调耗时。</p></div></section><section class="card"><div class="card-head"><span class="card-title">实际板端任务日志</span><span class="badge">BOARD</span></div><div class="card-body">${btn('refresh-board','刷新状态与日志','refresh','btn-sm')}<div class="terminal" id="terminal" role="log" aria-label="实际板端日志">${logsMarkup()}</div><div class="result-note">${h?.pl_ready?`当前可用：完整ARM FP32、W8A8量化语义、${plLabel()}。`:'当前可用：完整ARM FP32、W8A8量化语义、CPU custom参考。'}PL不就绪时严格拒绝，不自动fallback。</div></div></section></div>`;
}
function connectionPage() {
  if (state.source==='BOARD') return boardConnectionPage();
  const serial = state.connectionTab === 'serial';
  return pageHead('DEVICE / 05', '连接与诊断', '配置下一次连接，查看本机状态与受控诊断记录。') + demoStrip() + `<div class="connection-grid"><section class="card"><div class="card-head"><span class="card-title">连接配置</span><span class="badge badge-demo">HARDWARE LOCKED</span></div><div class="tabs" role="tablist" aria-label="连接方式"><button class="tab ${!serial ? 'active' : ''}" data-action="tab-network" role="tab" aria-selected="${!serial}">网口连接</button><button class="tab ${serial ? 'active' : ''}" data-action="tab-serial" role="tab" aria-selected="${serial}">USB 串口</button></div><div class="card-body"><div class="lock-panel">${icon('shield')}<div><strong>真实连接暂未启用</strong>B 会话正在使用板卡。此页只保存配置，不执行 DNS、SSH、网口探测或串口打开。</div></div>${serial ? `<div class="field"><label for="serial-port">串口名称</label><input id="serial-port" value="${e(state.port)}" placeholder="COM11" data-field="port"><small>手动配置；未枚举或占用任何COM口。</small></div><div class="form-grid"><div class="field"><label for="baud">波特率</label><select id="baud" data-field="baud">${[9600,57600,115200,230400].map(x => `<option ${Number(state.baud) === x ? 'selected' : ''}>${x}</option>`).join('')}</select></div><div class="field"><label>帧格式</label><input value="8N1 · 无流控" disabled></div></div><div class="field"><label>应用协议</label><input value="NDJSON · 待板端代理验证" disabled><small>Linux UART控制台不是模型API；后续需要独立代理。</small></div>` : `<div class="field"><label for="endpoint">板端服务地址</label><input id="endpoint" value="${e(state.endpoint)}" placeholder="http://kv260:8080" data-field="endpoint"><small>HTTP应用服务地址。保存不访问该主机。</small></div><div class="field"><label>设备状态</label><input value="NOT_CONNECTED / NOT_QUERIED" disabled></div><div class="field"><label>传输用途</label><span class="subtle">图片、任务与结果的主通道；C 后端就绪后再接入。</span></div>`}<div class="button-row">${btn('save-profile','保存配置','check','btn-primary')}${btn('protected-connect','验证保护锁','shield')}</div><p class="input-hint">不保存密码。界面操作不能解除本轮设备保护。</p></div></section><section class="card"><div class="card-head"><span class="card-title">诊断日志</span><span class="badge">LOCAL ONLY</span></div><div class="card-body"><div class="diagnostic-actions">${btn('diag-snapshot','本机快照','refresh','btn-sm')}${btn('diag-model_identity','模型身份示例','box','btn-sm')}${btn('diag-owner_status','Owner状态示例','shield','btn-sm')}</div><div class="terminal-toolbar"><select data-field="logLevel" aria-label="日志级别">${['ALL','INFO','WARN','ERROR'].map(v => `<option ${v === state.logLevel ? 'selected' : ''}>${v}</option>`).join('')}</select><div class="button-row">${btn('clear-logs','清空视图','x','btn-sm btn-quiet')}${btn('export-logs','导出','download','btn-sm')}</div></div><div class="terminal" id="terminal" role="log" aria-label="本机演示日志">${logsMarkup()}</div><div class="diagnostic-summary"><div>板卡连接<strong>NOT_CONNECTED</strong></div><div>Owner真实查询<strong>NOT_QUERIED</strong></div><div>设备访问<strong>0</strong></div></div></div></section></div>`;
}
function render() {
  if(state.boardHealth?.model_protocol==='h576_v5'&&state.catalog?.layers[state.layerIndex]?.backend==='CPU')state.layerBackend='cpu';
  if (!state.catalog) return;
  const focused = screen.contains(document.activeElement) ? document.activeElement.dataset.focus : null;
  const caret = focused && document.activeElement.tagName === 'INPUT' ? document.activeElement.selectionStart : null;
  document.querySelector('#page-name').textContent = pages.find(x => x[0] === state.page)[1];
  document.querySelector('#navigation').innerHTML = pages.map(([id,name,glyph]) => `<a href="#${id}" aria-label="${name}" title="${name}" class="nav-link ${state.page === id ? 'active' : ''}" ${state.page === id ? 'aria-current="page"' : ''}>${icon(glyph)}<span>${name}</span>${id === 'history' && allRecords().length ? `<span class="nav-count">${allRecords().length}</span>` : ''}</a>`).join('');
  screen.innerHTML = ({overview:()=>finalOverview(state.finalData), performance:()=>finalPerformance(state.finalData), architecture:()=>finalArchitecture(state.finalData), archive:()=>'<div class="lab-history-note">历史 H576 Dense 实测 · 本页的 3925 张、5000 次调用及三点曲线属于历史记录。当前交付结果见“成果总览”和“性能与耗时”。</div>'+overview(state.presentation,state), inference: inferencePage, layer: layerPage, benchmark: benchmarkPage, history: historyPage, connection: connectionPage })[state.page]();
  if(state.page==='performance' && state.finalData)bindFinalPerformance(screen,state.finalData,state);
  if(!['overview','performance','architecture','archive'].includes(state.page))screen.insertAdjacentHTML('afterbegin', `<div class="mode-bar"><strong>运行来源</strong><button class="btn btn-sm ${state.source==='BOARD'?'btn-primary':''}" data-action="source-board" ${isBusy()||!state.boardConfigured?'disabled':''}>真实板端</button><button class="btn btn-sm ${state.source==='DEMO'?'btn-primary':''}" data-action="source-demo" ${isBusy()?'disabled':''}>界面演示 · 固定样例</button><span>${state.source==='BOARD'?(state.boardHealth?.pl_ready?'BOARD / ARM+PL':'BOARD / ARM'):'DEMO / 不运行模型'}</span></div>`);
  document.querySelector('.source-badge').innerHTML = `<i></i> ${state.source==='BOARD'?'BOARD':'DEMO'}`;
  document.querySelector('.device-caption').textContent = state.boardHealth ? (state.boardHealth.pl_ready?'KV260 已连接 / ARM+PL':'KV260 已连接 / ARM') : 'KV260 未连接';
  document.querySelector('.side-bottom strong').textContent = state.boardHealth ? '网口SSH通道已连接' : '板端通道未连接';
  document.querySelector('.side-bottom span').textContent = state.source==='BOARD'?(state.boardHealth?.pl_ready?`真实ARM / ${plLabel()}就绪`:'真实ARM / 当前PL未就绪'):'仅界面样例 · 无模型';
  document.querySelector('.app-footer span:last-child').textContent = `${state.source==='BOARD'?(currentJob(state.page)?.realPLStarts>0?'KV260 PS/PL实际计算':'KV260 ARM实际计算'):'固定样例 · 不运行模型'} / PL starts ${currentJob(state.page)?.realPLStarts??0}`;
  if(['overview','performance','architecture','archive'].includes(state.page)) {
    document.querySelector('.source-badge').innerHTML='<i></i> 实测归档';
    document.querySelector('.device-caption').textContent='只读展示 · 不访问板卡';
    document.querySelector('.app-footer span:last-child').textContent='已保存的实板回执 / 本页不启动PL';
    document.querySelector('.side-bottom strong').textContent=state.page==='archive'?'H576 历史实测':'OPT50 最终实测归档';
    document.querySelector('.side-bottom span').textContent='只读展示 · 不发送板端命令';
  }
  if(state.page==='inference'&&currentJob('inference')?.result?.route_manifest)screen.insertAdjacentHTML('beforeend',routeMap(currentJob('inference').result.route_manifest));
  if (state.source==='BOARD' && state.page==='inference') {
    screen.querySelector('[aria-label="演示后端"]').setAttribute('aria-label','实际板端后端');
    screen.querySelector('.stats-grid .stat-card:last-child label').textContent='真实推理回执';
    screen.querySelector('.stats-grid .stat-card:last-child .stat-value').innerHTML=`${state.jobs.filter(x=>x.source==='BOARD'&&x.type==='inference'&&x.status==='SUCCEEDED').length}<small>BOARD</small>`;
    for (const segment of screen.querySelectorAll('[data-action="backend"]')) {
      const id=segment.dataset.backend;
      segment.innerHTML=id==='fp32'?'FP32 实际<small>KV260 ARM</small>':id==='w8a8'?'W8A8 实际<small>ARM 量化语义</small>':`${plLabel()}<small>${state.boardHealth?.pl_ready?'当前owner已核验':'当前PL未就绪'}</small>`;
      if (id==='pl49') { segment.disabled=isBusy()||!state.boardHealth?.pl_ready; segment.title=state.boardHealth?.pl_ready?plLabel():'当前窗口没有可用的PL owner'; }
    }
    screen.querySelector('.field-label').textContent='实际运行后端';
    screen.querySelector('.input-hint').textContent='PNG / JPEG / WebP，最大10MiB；开始后图片字节经SSH网口发送到KV260。';
  }
  if (state.source==='BOARD' && state.page==='layer') {
    const panels=screen.querySelectorAll('.task-grid>.card');
    panels[1].querySelector('.card-body').innerHTML=boardLayer(currentJob('layer'));
    panels[1].querySelector('.badge').textContent=currentJob('layer')?.result?.execution_kind==='FPGA_LAYER'?'实际PL / INT64核对':'实际ARM INT64参考';
    panels[0].querySelector('.badge').textContent='BOARD';
    const layer=state.catalog.layers[state.layerIndex];
    panels[0].querySelector('.card-body').insertAdjacentHTML('afterbegin',`<div class="wide-note">固定模型路由：${e(layer.id)} → ${e(layer.backend??'未报告')}。这是图中分工，实际层实验后端由下方选择。</div><label class="field-label">实际层后端</label><select data-field="layerBackend"><option value="cpu" ${state.layerBackend!=='pl49'?'selected':''}>ARM INT64参考</option><option value="pl49" ${state.layerBackend==='pl49'?'selected':''} ${state.boardHealth?.pl_ready&&layer.backend!=='CPU'?'':'disabled'}>真实PL / 独立INT64核对</option></select>`);
    for(const option of screen.querySelectorAll('#case-select option')) option.textContent=option.textContent.replace(' · 演示','');
  }
  if (state.page === 'connection') screen.insertAdjacentHTML('beforeend', `<div class="wide-note"><label class="motion-option"><input type="checkbox" data-field="motionReduced" ${state.motionReduced ? 'checked' : ''}>减弱界面动画</label><span>仅调整本机工作台的过渡与运行指示；系统减弱动画偏好也会自动生效。</span></div>`);
  if (state.error) screen.insertAdjacentHTML('beforeend', `<div class="alert"><strong>${e(state.error.code ?? '请求失败')}</strong>${e(state.error.message)}</div>`);
  if (focused) { const target = screen.querySelector(`[data-focus="${CSS.escape(focused)}"]`); if (target && !target.disabled) { target.focus({ preventScroll: true }); if (caret !== null && ['text','search'].includes(target.type)) target.setSelectionRange(caret, caret); } }
}
function toast(message, type = '') { const el = document.createElement('div'); el.className = `toast ${type}`; el.textContent = message; document.querySelector('#toast-stack').append(el); setTimeout(() => el.remove(), 4200); }
function changePage() { const candidate = location.hash.slice(1); state.page = pages.some(x => x[0] === candidate) ? candidate : 'overview'; state.error = null; render(); window.scrollTo(0,0); screen.classList.remove('screen-enter'); requestAnimationFrame(() => screen.classList.add('screen-enter')); if (state.page === 'connection') refreshLogs(); }
async function refreshLogs() { try { state.logs = (await api(state.source==='BOARD'?'/api/board/logs':'/api/logs')).logs; if (state.page === 'connection') { const term = document.querySelector('#terminal'); if (term) { term.innerHTML = logsMarkup(); term.scrollTop = term.scrollHeight; } } } catch (err) { toast(err.message, 'error'); } }
async function connectBoard() {
  try { state.boardHealth=await new ProtectedTransport('network').connect(); state.error=null; state.jobs=(await api('/api/jobs')).jobs; const active=state.jobs.find(x=>!terminal.has(x.status)); if(active) {state.active=active.id;state.selected[active.type]=active.id;poll();} state.logs=(await api('/api/board/logs')).logs; toast(state.boardHealth.pl_ready ? `KV260已连接，模型身份核验通过。ARM参考与${plLabel()}可用。` : 'KV260已连接，模型身份核验通过。当前提供ARM参考。'); }
  catch(err) {state.boardHealth=null;state.error={code:err.code,message:err.message};toast(err.message,'error');} render();
}
async function encodedInput() {
  let file=state.inputFile;
  if(state.input.kind==='builtin') {
    const im=new Image(); im.src=state.input.url; await im.decode();
    const canvas=document.createElement('canvas');canvas.width=im.naturalWidth;canvas.height=im.naturalHeight;canvas.getContext('2d').drawImage(im,0,0);
    file=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));
  }
  if(!file) throw new Error('请重新选择实际图片。页面刷新后不会保存用户图片字节。');
  const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));
  return {mime:file.type,base64:btoa(binary)};
}
async function start(type) {
  if (isBusy()) return;
  state.error = null; state.submitting = true; render();
  try {
    const options = { scenario: state.scenario, ...(type === 'inference' ? { backend: state.backend, sample: state.input.sample ?? 'dog', input: { name: state.input.name, kind: state.input.kind, dimensions: state.input.dimensions, size: state.input.size } } : type === 'layer' ? { layerIndex: state.layerIndex, case: state.layerCase,backend:state.layerBackend==='pl49'?'pl49':'fp32' } : { runs: Number(state.runs), warmup: Number(state.warmup) }) };
    if (state.source==='BOARD') { if (!state.boardHealth) throw new Error('请先连接KV260。'); options.scenario='normal'; if(type==='inference')options.backend=state.backend; }
    const image=state.source==='BOARD'&&type!=='layer'?await encodedInput():undefined;
    const job = await api('/api/jobs', { source: state.source, type, options, ...(image?{image}:{}) }); state.jobs.unshift(job); state.active = job.id; state.selected[type] = job.id; toast(state.source==='BOARD'?'真实任务已提交到KV260，等待模型回执。':'开始播放固定样例，没有模型或图片分析。');
  } catch (err) { state.error = { code: err.code, message: err.message }; if(state.source==='BOARD'&&['BOARD_UNREACHABLE','BAD_BOARD_IDENTITY'].includes(err.code))state.boardHealth=null; toast(err.message, 'error'); }
  finally { state.submitting = false; render(); poll(); }
}
async function poll() {
  if (state.polling || !state.active) return;
  state.polling = true;
  try {
    while (state.active) {
      const id = state.active; const job = await api(`/api/jobs/${id}`);
      if (state.active !== id) break;
      const index = state.jobs.findIndex(x => x.id === id); if (index >= 0) state.jobs[index] = job;
      if (terminal.has(job.status)) { state.active = null; toast(job.status === 'SUCCEEDED' ? (job.source==='BOARD'?'实际板端计算完成，已收到模型回执。':'固定样例播放完成。') : job.status === 'CANCELLED' ? '停止已确认，未生成完成结果。' : job.error?.message ?? '任务失败', job.status === 'FAILED' ? 'error' : ''); }
      if (['inference','layer','benchmark','history'].includes(state.page)) render();
      if (state.active) await new Promise(resolve => setTimeout(resolve, 320));
    }
  } catch (err) { state.error = { code: err.code ?? 'LOCAL_SERVICE_UNREACHABLE', message: '服务连接中断，实际完成状态未知。未重发或回退；恢复连接后刷新记录。' }; if (state.source==='BOARD') state.boardHealth=null; state.active = null; toast(state.error.message, 'error'); render(); }
  finally { state.polling = false; }
}
async function cancel() {
  if (!state.active) return;
  try { const job = await api(`/api/jobs/${state.active}/cancel`, {}); const i = state.jobs.findIndex(x => x.id === job.id); if (i >= 0) state.jobs[i] = job; render(); } catch (err) { toast(err.message, 'error'); }
}
function record(id) { return allRecords().find(x => x.id === id); }
function serverDownload(id, extension) { const a = document.createElement('a'); a.href = `/api/jobs/${encodeURIComponent(id)}/export.${extension}`; a.download = `${id}.${extension}`; a.hidden = true; document.body.append(a); a.click(); a.remove(); }
function exportRecord(id) { const job = record(id); if (['DEMO','BOARD'].includes(job?.source)) serverDownload(id, 'json'); else if (job) download(`${job.id}.json`, JSON.stringify(job, null, 2)); }
function exportCsv(id) {
  const job = record(id); if (!job?.result?.samples&&!job?.result?.layers&&!job?.result?.route_manifest) return;
  serverDownload(id, 'csv');
}
function showDetails(id) {
  const job = record(id); if (!job) return;
  state.dialogFocus = document.activeElement;
  if (job.source==='BOARD') {
    document.querySelector('#dialog-content').innerHTML=`<div class="dialog-head"><strong>实际运行详情 · ${e(job.id)}</strong><button class="icon-button" data-action="close-dialog" aria-label="关闭详情">${icon('x')}</button></div><div class="dialog-body"><div class="lock-panel"><div><strong>BOARD · KV260实际回执</strong>实际PL starts=${job.realPLStarts}；模型/输入/boot和原始logits见JSON。</div></div><pre>${e(JSON.stringify(job,null,2))}</pre><button class="btn" data-action="export" data-id="${job.id}">导出完整JSON</button>${job.result?.layers||job.result?.route_manifest?`<button class="btn" data-action="csv" data-id="${job.id}">导出49层路由CSV</button>`:''}</div>`;
    document.querySelector('#details-dialog').showModal();return;
  }
  document.querySelector('#dialog-content').innerHTML = `<div class="dialog-head"><strong>运行详情 · ${e(job.id)}</strong><button class="icon-button" data-action="close-dialog" aria-label="关闭详情">${icon('x')}</button></div><div class="dialog-body"><div class="lock-panel">${icon('file')}<div><strong>${job.source === 'IMPORTED' ? 'IMPORTED · 用户本机文件' : 'DEMO · 合成演示记录'}</strong>${job.source === 'IMPORTED' ? '仅查看报告，未核验签署、设备状态或现场运行。' : '不代表模型推理、设备验收或实际性能。'}</div></div><table class="numeric-table"><tbody><tr><td>任务</td><td>${e(typeNames[job.type] ?? job.type)}</td></tr><tr><td>状态</td><td>${e(statusNames[job.status])}</td></tr><tr><td>来源</td><td>${e(job.source)}</td></tr><tr><td>实际PL starts</td><td>${job.source === 'IMPORTED' ? '依原报告，未核验' : 0}</td></tr></tbody></table><pre>${e(JSON.stringify(job.source === 'IMPORTED' ? job.report : { ...job, result: job.result?.logits ? { ...job.result, logits: `[1000 values; present in exported JSON]` } : job.result }, null, 2))}</pre><button class="btn btn-primary" data-action="export" data-id="${e(job.id)}">${icon('download')}导出完整 JSON</button></div>`;
  document.querySelector('#details-dialog').showModal();
}
async function loadImage(file) {
  if (!file) return;
  if (isBusy()) { toast('请先结束当前演示，再更换图片。', 'warn'); return; }
  if (!['image/png','image/jpeg','image/webp'].includes(file.type) || file.size > 10 * 1024 * 1024 || file.size === 0) { toast('仅支持有效 PNG/JPEG/WebP，最大 10 MiB。', 'error'); return; }
  const url = URL.createObjectURL(file);
  try {
    const image = new Image(); image.src = url; await image.decode();
    if (!image.naturalWidth || !image.naturalHeight || image.naturalWidth * image.naturalHeight > 50_000_000) throw new Error('尺寸超出支持范围');
    if (state.input.url.startsWith('blob:')) URL.revokeObjectURL(state.input.url);
    state.inputFile=file; state.input = { name: file.name.slice(0,180), kind: 'upload', sample: null, url, dimensions: `${image.naturalWidth} × ${image.naturalHeight}`, size: file.size }; state.selected.inference = null; state.error = null; render(); toast('图片已载入；按开始才发送真实图片。');
  } catch { URL.revokeObjectURL(url); toast('图片无法解码或尺寸过大，请选择其他文件。', 'error'); }
}
async function importReport(file) {
  if (!file) return;
  if (file.size > 2 * 1024 * 1024) { toast('报告最大 2 MiB，请选择摘要或单次结果。', 'error'); return; }
  try {
    const report = JSON.parse(await file.text());
    if (!report || typeof report !== 'object' || Array.isArray(report) || (!report.schema && !report.run_id && !report.id && !report.status && !Array.isArray(report.top5))) throw new Error('不是支持的结果对象');
    const job = { id: 'import_' + crypto.randomUUID().slice(0,8), source: 'IMPORTED', status: 'IMPORTED', type: 'report', filename: file.name.slice(0,180), startedAt: new Date().toISOString(), report };
    state.imports.unshift(job); state.imports = state.imports.slice(0,20); location.hash = 'history'; render(); toast('报告已导入本机视图，真实性未核验。');
  } catch { toast('报告不是有效的结果 JSON。文件内容未执行。', 'error'); }
}
async function saveProfile() {
  const kind = state.connectionTab;
  try { const response = await new ProtectedTransport(kind).saveProfile(kind === 'network' ? { endpoint: state.endpoint } : { port: state.port, baud: Number(state.baud) }); const p = response.profile; if (kind === 'network') state.endpoint = p.endpoint; else { state.port = p.port; state.baud = p.baud; } localStorage.setItem('kv260-ui-profiles', JSON.stringify({ endpoint: state.endpoint, port: state.port, baud: state.baud })); toast('配置已保存。没有连接、探测或访问设备。'); }
  catch (err) { toast(err.message, 'error'); }
}
document.addEventListener('click', async event => {
  const el = event.target.closest('[data-action]'); if (!el || el.disabled) return;
  const action = el.dataset.action;
  if(action==='benchmark-reference') { if(['fp32','w8'].includes(el.dataset.reference))state.benchmarkReference=el.dataset.reference; render(); return; }
  if(action==='saved-export') { const ext=el.dataset.kind==='csv'?'csv':'json';const a=document.createElement('a');a.href=`/api/presentation/benchmark.${ext}`;a.download=`saved_benchmark.${ext}`;a.hidden=true;document.body.append(a);a.click();a.remove();return; }
  if (action==='source-board'||action==='source-demo') { if (isBusy()) return; state.source=action==='source-board'?'BOARD':'DEMO'; state.backend=state.source==='BOARD'?'fp32':'pl49'; state.scenario='normal'; state.selected={}; state.error=null; render(); return; }
  if (action==='connect-board'||action==='refresh-board') return connectBoard();
  if (action.startsWith('start-')) return start(action.slice(6));
  if (action === 'upload') { if (isBusy()) return toast('请先结束当前演示。', 'warn'); document.querySelector('#image-picker').click(); }
  if (action === 'sample') { if (isBusy()) return; if (state.input.url.startsWith('blob:')) URL.revokeObjectURL(state.input.url); state.inputFile=null; state.input = { name: samples.find(x => x[0] === el.dataset.sample)[1] + '.svg', kind: 'builtin', sample: el.dataset.sample, url: `/assets/${el.dataset.sample}.svg`, dimensions: '1000 × 680', size: null }; state.selected.inference = null; state.error = null; render(); }
  if (action === 'backend') { state.backend = el.dataset.backend; state.selected.inference = null; render(); }
  if (action === 'cancel') return cancel();
  if (action === 'export') return exportRecord(el.dataset.id);
  if (action === 'csv') return exportCsv(el.dataset.id);
  if (action === 'details') return showDetails(el.dataset.id);
  if (action === 'close-dialog') document.querySelector('#details-dialog').close();
  if (action === 'import') document.querySelector('#report-picker').click();
  if (action === 'connect') location.hash = 'connection';
  if (action.startsWith('tab-')) { state.connectionTab = action.slice(4); render(); }
  if (action === 'save-profile') return saveProfile();
  if (action === 'protected-connect') { try { await new ProtectedTransport(state.connectionTab).connect(); } catch (err) { toast(`${err.code}：${err.message}`, 'warn'); } }
  if (action.startsWith('diag-')) { try { const response = await api('/api/diagnostics', { action: action.slice(5) }); state.logs = response.logs; render(); } catch (err) { toast(err.message, 'error'); } }
  if (action === 'clear-logs') { state.logs = []; render(); }
  if (action === 'export-logs') download('kv260-local-demo-logs.json', JSON.stringify({ source: 'DEMO', boardContacted: false, logs: state.logs }, null, 2));
  if (action === 'refresh-history') { try { state.jobs = (await api('/api/jobs')).jobs; const active = state.jobs.find(x => !terminal.has(x.status)); if (active) { state.active = active.id; state.selected[active.type] = active.id; poll(); } render(); } catch (err) { toast(err.message, 'error'); } }
});
document.addEventListener('change', event => {
  const el = event.target, key = el.dataset.field; if (!key) return;
  if (key === 'motionReduced') { state.motionReduced = el.checked; document.documentElement.dataset.reducedMotion = String(el.checked); localStorage.setItem('kv260-ui-reduced-motion', String(el.checked)); return; }
  state[key] = ['layerIndex','runs','warmup','baud'].includes(key) ? Number(el.value) : el.value;
  if (['layerIndex','layerCase','layerBackend'].includes(key)) state.selected.layer = null;
  if (key==='layerIndex'&&state.catalog.layers[state.layerIndex]?.backend==='CPU') state.layerBackend='cpu';
  if (['runs','warmup'].includes(key)) state.selected.benchmark = null;
  if (['layerIndex','layerCase','layerBackend','scenario','historyStatus','logLevel'].includes(key)) { state.error = null; render(); }
});
document.addEventListener('input', event => { const key = event.target.dataset.field; if (['runs','warmup'].includes(key)) { state[key] = Number(event.target.value); state.selected.benchmark = null; } if (['endpoint','port','search'].includes(key)) { state[key] = event.target.value; if (key === 'search') render(); } });
document.addEventListener('keydown', event => { if (['Enter',' '].includes(event.key) && event.target.id === 'drop-zone') { event.preventDefault(); event.target.click(); } });
document.addEventListener('dragover', event => { if (event.target.closest('#drop-zone')) { event.preventDefault(); document.querySelector('#drop-zone').classList.add('dragging'); } });
document.addEventListener('dragleave', event => event.target.closest('#drop-zone')?.classList.remove('dragging'));
document.addEventListener('drop', event => { const zone = event.target.closest('#drop-zone'); if (!zone) return; event.preventDefault(); zone.classList.remove('dragging'); loadImage(event.dataTransfer.files[0]); });
document.querySelector('#image-picker').addEventListener('change', event => { loadImage(event.target.files[0]); event.target.value = ''; });
document.querySelector('#report-picker').addEventListener('change', event => { importReport(event.target.files[0]); event.target.value = ''; });
document.querySelector('#details-dialog').addEventListener('close', () => { if (state.dialogFocus?.isConnected) state.dialogFocus.focus(); });
window.addEventListener('hashchange', changePage);
document.querySelector('#settings-button').innerHTML = icon('plug');
try {
  const [policy, catalog, records, presentation, final] = await Promise.all([api('/api/policy'), api('/api/catalog'), api('/api/jobs'),api('/api/presentation').catch(()=>null),api('/api/final').catch(()=>null)]);
  state.presentation=presentation;
  state.finalData=final;
  if (!['BOARD','DEMO'].includes(policy.source)) throw new Error('未知服务来源。');
  state.boardConfigured=policy.source==='BOARD'; state.source=policy.source; state.backend=state.boardConfigured?'fp32':'pl49';
  state.catalog = catalog; state.jobs = records.jobs;
  if (state.boardConfigured) { try { state.boardHealth=await api('/api/board/health'); } catch(err) { state.error={code:err.code,message:err.message}; } }
  const active = state.jobs.find(x => !terminal.has(x.status));
  if (active) { state.active = active.id; state.selected[active.type] = active.id; if (active.type === 'inference') { const sample = samples.find(x => x[0] === active.options.sample); state.input = { ...active.options.input, sample: sample?.[0], url: sample ? `/assets/${sample[0]}.svg` : '/assets/pattern.svg' }; state.backend = active.options.backend; } }
  changePage(); poll();
} catch (err) { screen.innerHTML = `<div class="alert"><strong>本机服务未就绪</strong>${e(err.message)}<p>请启动 ui/server.mjs，再重新加载页面。没有设备访问。</p></div>`; }
