import {escape as e} from './api-client.js';

const ms=value=>value.toFixed(3)+' ms';
const parts=[['start_to_completion_ms','START → 完成证明','#287b61'],['native_prepare_ms','Native 启动准备','#c89653'],['native_verify_ms','层完成验证','#92729f'],['between_calls_ms','图内其余时间','#8b9b8d'],['runner_boundary_ms','Runner 边界','#cbd4c3']];
const unavailable=()=>'<div class="alert"><strong>最终数据暂不可用</strong>证据文件缺失或核对失败。请检查本机服务；历史数据不会替代当前结果。</div>';
const heading=(code,title,text)=>`<div class="page-head"><div><div class="eyebrow">${code}</div><h1>${title}</h1><p>${text}</p></div><span class="badge badge-success">已核验实测归档</span></div>`;
const note=text=>`<p class="lab-note">${text}</p>`;

function metricsTable(data) {
  const m=data.metrics;
  return `<div class="table-wrap"><table class="data-table lab-table"><thead><tr><th>模型 / 执行方式</th><th>Top1 / Top5</th><th>均值</th><th>中位数</th><th>P95</th><th>相对自身 CPU</th></tr></thead><tbody>${[['Dense','CPU','原始 Dense · ARM CPU'],['Dense','PS_PL','原始 Dense · ARM + FPGA'],['Sparse','CPU','Sparse24 · ARM CPU'],['Sparse','PS_PL','Sparse24 · ARM + FPGA']].map(([model,path,label])=>{
    const a=m.accuracy[model==='Sparse'?'Sparse24':'Dense'],v=m.latency[model+'_'+path+'_ms'];
    const ratio=path==='CPU'?'1×':(m.latency[model+'_CPU_ms'].mean_ms/v.mean_ms).toFixed(3)+'×';
    return `<tr${model==='Sparse'&&path==='PS_PL'?' class="lab-featured"':''}><td>${label}</td><td>${a.top1_pct.toFixed(1)}% / ${a.top5_pct.toFixed(1)}%</td><td>${ms(v.mean_ms)}</td><td>${ms(v.median_ms)}</td><td>${ms(v.p95_ms)}</td><td>${ratio}</td></tr>`;
  }).join('')}</tbody></table></div>`;
}

function identities(data) {
  const m=data.metrics;
  return `<details class="card source-details"><summary>数据来源、环境与身份<span class="mono">bd5d8cc / OPT50</span></summary><div class="card-body"><div class="source-list">${[
    ['源码基准',data.timing.source_revision],['原始证据 SHA256',data.timing.archive_sha256],
    ['Dense 模型 SHA256',m.model_sha256.Dense],['Sparse24 模型 SHA256',m.model_sha256.Sparse24],
    ['固件 UUID',m.hardware.firmware_uuid],['原受检环境',`ORT ${m.environment.ORT} / NumPy ${m.environment.NumPy} / ${m.environment.compiler} / batch=1 / ORT 4:1 / graph optimization OFF`]
  ].map(([label,value])=>`<div><b>${e(label)}</b><code>${e(value)}</code></div>`).join('')}</div></div></details>`;
}

export function finalOverview(data) {
  if(!data)return unavailable();
  const m=data.metrics,s=m.latency.Sparse_PS_PL_ms,ratio=m.latency.Sparse_CPU_ms.mean_ms/s.mean_ms;
  return `<section class="showcase-hero lab-hero"><div class="hero-copy"><div class="hero-kicker"><i></i> KV260 / OPT50 / FINAL RESULTS</div><h1>同一个阵列，<br><em>两种计算路径。</em></h1><p>H576 完整模型 · Dense / 2:4 Sparse<br>ARM 运行整网，FPGA 承接 36 个线性层。</p><div class="hero-tags"><span>512 MAC</span><span>125 MHz</span><span>128-bit AXI</span><span>W8A8</span></div><a href="#performance" class="btn hero-button">查看性能与耗时 <span>↗</span></a><small>已保存的实板证据 · 浏览此页不启动推理</small></div><div class="hero-instrument"><div class="instrument-top"><span>SPARSE24 / RUNNER.RUN</span><i>195 张同调度记录</i></div><div class="hero-number">${s.mean_ms.toFixed(3)}<span>ms</span></div><div class="hero-speed"><strong>${ratio.toFixed(3)}×</strong><span>相对自身 ARM CPU<br>W8A8 数值语义参考</span></div><div class="lab-mode-list"><div><span>Sparse24 模型</span><b>12 Dense + 24 Sparse</b></div><div><span>原始 Dense 模型</span><b>36 Dense</b></div></div><div class="instrument-bottom"><span>36 Linear → PL</span><span>13 Linear → PS</span><span>49 / 49 保留</span></div></div></section>
  <div class="showcase-stat-grid"><article><span class="metric-code">01 / LATENCY</span><strong>${m.Sparse_mean_latency_reduction_pct.toFixed(2)}<small>%</small></strong><label>Dense → Sparse24 整网均值降低</label><p>143.494 → 136.588 ms · 权重不同</p></article><article><span class="metric-code">02 / ACCURACY</span><strong>${m.accuracy.Sparse24.top1_pct.toFixed(1)}<small>%</small></strong><label>Sparse24 · 500 张 Top1</label><p>Top5 ${m.accuracy.Sparse24.top5_pct.toFixed(1)}% · 1000 类排名</p></article><article><span class="metric-code">03 / EQUIVALENCE</span><strong>0<small>差异</small></strong><label>每模型 500,000 个 FP32 logits</label><p>各自 CPU / PL 输出逐位比对</p></article><article><span class="metric-code">04 / EVIDENCE</span><strong>195<small>张</small></strong><label>同调度版本性能对照</label><p>第 306–500 张 · 每图一次记录</p></article></div>
  <div class="section-intro"><div><div class="eyebrow">MATCHED MODEL COMPARISON</div><h2>同一批图片，核对完整模型。</h2></div><a class="btn btn-sm" href="/api/final/paired.csv" download>下载逐图 CSV ↓</a></div><section class="card"><div class="card-body">${metricsTable(data)}${note('精度使用相同 500 张，耗时使用同调度的 195 张。CPU 为 ORT W8A8 数值语义参考，不代表专门优化的原生 INT8 CPU 性能上限。两种模型权重不同，整网差异不能单独归因为稀疏 MAC 加速。')}</div></section>
  <div class="lab-entry-grid"><a class="card lab-entry" href="#performance"><span class="metric-code">PERFORMANCE</span><h3>性能曲线与耗时分解 ↗</h3><p>查看全部 195 个样本、390 份事件回执分解和计时边界。</p></a><a class="card lab-entry" href="#architecture"><span class="metric-code">ARCHITECTURE</span><h3>系统结构与共享阵列 ↗</h3><p>系统、硬件、软件流程与技术锚点，支持报告 SVG 下载。</p></a></div>
  <div class="lab-limit">500 张覆盖 250 类，非完整 ImageNet 验证集。历史 1800 张 CPU 报告中 Sparse24 Top1 低于 Dense 1.28 个百分点，不据当前子集宣称总体精度提升。<a href="/evidence/verification.md">验证范围 ↗</a></div>${identities(data)}`;
}

export function pairedChart(data,series='pl',selected=306) {
  const dense=series==='pl'?'Dense_PS_PL_ms':'Dense_CPU_ms',sparse=series==='pl'?'Sparse_PS_PL_ms':'Sparse_CPU_ms';
  const values=data.samples.flatMap(row=>[row[dense],row[sparse]]);
  const low=Math.floor((Math.min(...values)-5)/10)*10,high=Math.ceil((Math.max(...values)+5)/10)*10;
  const x=i=>55+i/194*925,y=v=>245-(v-low)/(high-low)*220;
  const index=Math.max(0,Math.min(194,Number(selected)-306)),row=data.samples[index];
  let drawing='';
  for(let i=0;i<=4;i++) {const v=low+(high-low)*i/4,at=y(v);drawing+=`<line x1="55" x2="980" y1="${at}" y2="${at}" stroke="#e1e7dc"/><text x="45" y="${at+4}" text-anchor="end" fill="#74817a" font-size="12">${v.toFixed(0)}</text>`;}
  for(const n of [306,350,400,450,500])drawing+=`<text x="${x(n-306)}" y="275" text-anchor="middle" fill="#74817a" font-size="12">${n}</text>`;
  for(const [key,color] of [[dense,'#a47745'],[sparse,'#287b61']])drawing+=`<polyline fill="none" stroke="${color}" stroke-width="1.8" points="${data.samples.map((r,i)=>`${x(i)},${y(r[key])}`).join(' ')}"/>`;
  drawing+=`<line x1="${x(index)}" x2="${x(index)}" y1="20" y2="245" stroke="#536a58" stroke-dasharray="3 4"/>`;
  for(const [key,color] of [[dense,'#a47745'],[sparse,'#287b61']])drawing+=`<circle cx="${x(index)}" cy="${y(row[key])}" r="4" fill="${color}" stroke="white" stroke-width="1.5"/>`;
  return {svg:`<svg id="lab-latency-chart" viewBox="0 0 1000 290" role="img" aria-label="195 张 Dense 与 Sparse24 逐图耗时">${drawing}</svg>`,detail:`Dense ${ms(row[dense])} / Sparse24 ${ms(row[sparse])}`};
}

export function finalPerformance(data) {
  if(!data)return unavailable();
  const models=data.timing.models,maximum=models.Dense.mean_ms.runner_ms;
  return heading('PERFORMANCE / OPT50','性能与耗时','同调度第 306–500 张 · 195 对图片 · 完整 Runner.run 计时')+
  `<section class="card lab-performance"><div class="card-head"><span class="card-title">Dense / Sparse24 逐图曲线</span><div class="segmented" aria-label="计时路径"><button class="segment selected" data-final-series="pl" aria-pressed="true">ARM + FPGA</button><button class="segment" data-final-series="cpu" aria-pressed="false">CPU 参考</button></div></div><div class="card-body"><div class="lab-legend"><span class="lab-dense">原始 Dense</span><span class="lab-sparse">Sparse24</span><small>纵轴 / ms · 横轴 / 图片序号</small></div><div id="lab-chart">${pairedChart(data).svg}</div><div class="lab-sample"><label for="lab-sample">图片 <output id="lab-sample-number">306</output></label><input id="lab-sample" type="range" min="306" max="500" value="306"><span id="lab-sample-detail">${pairedChart(data).detail}</span></div>${note('曲线保留全部 195 张的单次记录，包含慢样本；不代表稳态吞吐或长期尾延迟保证。')}<div class="result-actions"><a class="btn btn-primary" href="/api/final/paired.csv" download>下载 195 张逐图 CSV</a><a class="btn btn-quiet" href="/api/final/comparison.csv" download>下载 500 张预测对照</a></div></div></section>
  <section class="card lab-section"><div class="card-head"><span class="card-title">精度与耗时汇总</span><span class="badge">500 张精度 / 195 张耗时</span></div><div class="card-body">${metricsTable(data)}</div></section>
  <div class="section-intro"><div><div class="eyebrow">RECEIPT-BASED TIMING</div><h2>整网时间，不等于计算阵列时间。</h2></div><a class="btn btn-sm" href="/api/final/timing.json" download>下载分解 JSON ↓</a></div><section class="card"><div class="card-body"><p class="lab-text">以下区间由 390 份 Native 回执和 worker 计时复算，互不重叠，相加等于 Runner.run。</p>${['Dense','Sparse24'].map(name=>`<div class="lab-breakdown"><span>${name}</span><div class="lab-stack">${parts.map(([key,label,color])=>`<i style="width:${models[name].mean_ms[key]/maximum*100}%;background:${color}" title="${label} ${ms(models[name].mean_ms[key])}"></i>`).join('')}</div><b>${ms(models[name].mean_ms.runner_ms)}</b></div>`).join('')}<div class="lab-part-legend">${parts.map(([,label,color])=>`<span><i style="background:${color}"></i>${label}</span>`).join('')}</div><div class="table-wrap"><table class="data-table lab-table"><thead><tr><th>计时内分段 / 每图均值</th><th>Dense</th><th>Sparse24</th></tr></thead><tbody>${parts.map(([key,label])=>`<tr><td>${label}</td><td>${ms(models.Dense.mean_ms[key])}</td><td>${ms(models.Sparse24.mean_ms[key])}</td></tr>`).join('')}</tbody></table></div>${note('START → 完成证明包含 FPGA 执行、轮询、MMIO 和完成／排空检查，不是纯 PL 周期。图内其余时间包含 PS 算子、量化／反量化、IPC 与缓存同步；现有回执无法将这些成本进一步独立拆分。')}</div></section>
  <div class="lab-entry-grid">${['Dense','Sparse24'].map(name=>`<section class="card"><div class="card-head"><span class="card-title">${name} · 计时外验收成本</span></div><div class="card-body lab-excluded"><div><span>CPU 参考</span><strong>${ms(models[name].mean_ms.cpu_reference_ms)}</strong></div><div><span>图后缓冲检查</span><strong>${ms(models[name].mean_ms.postrun_buffer_checks_ms)}</strong></div>${note('缓冲检查只是 INFO 内的部分区间，不是完整 INFO 耗时。输入准备、结果保存和批末关闭也在 Runner 之外，不能据此相加得出完整端到端延迟。')}</div></section>`).join('')}</div>${identities(data)}`;
}

export function bindFinalPerformance(root,data,state) {
  const slider=root.querySelector('#lab-sample');
  slider.value=String(state.finalSample??306);
  function draw() {
    const series=state.finalSeries??'pl',number=Number(slider.value),chart=pairedChart(data,series,number);
    state.finalSample=number;
    root.querySelector('#lab-chart').innerHTML=chart.svg;
    root.querySelector('#lab-sample-number').textContent=String(number);
    root.querySelector('#lab-sample-detail').textContent=chart.detail;
    root.querySelectorAll('[data-final-series]').forEach(button=>{const active=button.dataset.finalSeries===series;button.classList.toggle('selected',active);button.setAttribute('aria-pressed',String(active));});
  }
  slider.addEventListener('input',draw);
  root.querySelectorAll('[data-final-series]').forEach(button=>button.addEventListener('click',()=>{state.finalSeries=button.dataset.finalSeries;draw();}));
  root.querySelector('#lab-chart').addEventListener('pointermove',event=>{
    const svg=event.currentTarget.querySelector('svg'),bounds=svg.getBoundingClientRect();
    const index=Math.round(((event.clientX-bounds.left)/bounds.width*1000-55)/925*194);
    slider.value=String(306+Math.max(0,Math.min(194,index)));draw();
  });
  draw();
}

export function finalArchitecture(data) {
  if(!data)return unavailable();
  return heading('ARCHITECTURE / OPT50','系统架构','图片输入、ARM 完整模型、FPGA 计算和分类输出之间的关系')+
  `<div class="lab-figures">${[['system','系统整体框图','Sparse24：12 Dense + 24 Sparse；原始 Dense：36 Dense。'],['hardware','硬件模块与数据通路','依据平台 Tcl 等设计资料整理，非新生成的 Vivado 截图。'],['software','软件流程与批次验收','每图先生成比对结果，再做 INFO；批末关闭会话、STOP 后最终提交。'],['shared-core','Dense / 2:4 Sparse 共用阵列','运行时 mode 改变取数与激活选择，复用同一套 32×16 MAC。']].map(([name,title,caption])=>`<article class="card lab-figure"><a href="/architecture/${name}/${name}.html" target="_blank" rel="noreferrer"><img src="/architecture/report/${name}.svg" alt="${title}" loading="lazy"></a><div class="card-body"><h3>${title}</h3><p>${caption}</p><div class="result-actions"><a class="btn btn-sm" href="/architecture/${name}/${name}.html" target="_blank" rel="noreferrer">交互图与源码 ↗</a><a class="btn btn-sm btn-quiet" href="/architecture/report/${name}.svg" download>报告 SVG ↓</a></div></div></article>`).join('')}</div><div class="lab-limit">同一计算核通过 mode 切换 Dense／Sparse，不是每层切换位流。此处的流水仅指已实现的单层计算／写回组织，不声明跨视频帧流水。</div><a class="btn btn-primary" href="/architecture/index.html" target="_blank" rel="noreferrer">阅读八个技术实现锚点 ↗</a>${identities(data)}`;
}
