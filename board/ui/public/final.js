const $=selector=>document.querySelector(selector);
const ms=value=>value.toFixed(3)+' ms';
const segments=[
  ['start_to_completion_ms','START → 完成证明','#00877b'],
  ['native_prepare_ms','Native 启动准备','#c89745'],
  ['native_verify_ms','层完成验证','#906ba4'],
  ['between_calls_ms','图内其余时间','#7c96ae'],
  ['runner_boundary_ms','Runner 边界','#cad7df']
];
let data,series='pl';
function drawChart() {
  const dense=series==='pl'?'Dense_PS_PL_ms':'Dense_CPU_ms';
  const sparse=series==='pl'?'Sparse_PS_PL_ms':'Sparse_CPU_ms';
  const values=data.samples.flatMap(row=>[row[dense],row[sparse]]);
  const low=Math.floor((Math.min(...values)-5)/10)*10,high=Math.ceil((Math.max(...values)+5)/10)*10;
  const x=i=>55+i/194*925,y=v=>245-(v-low)/(high-low)*220;
  const selected=Number($('#sample').value)-306,row=data.samples[selected];
  let svg='';
  for(let i=0;i<=4;i++) {const value=low+(high-low)*i/4,at=y(value);svg+=`<line x1="55" x2="980" y1="${at}" y2="${at}" stroke="#e0e7ea"/><text x="45" y="${at+4}" text-anchor="end" fill="#516775" font-size="12">${value.toFixed(0)}</text>`;}
  for(const n of [306,350,400,450,500]) svg+=`<text x="${x(n-306)}" y="275" text-anchor="middle" fill="#516775" font-size="12">${n}</text>`;
  for(const [key,color] of [[dense,'#315d91'],[sparse,'#00877b']]) svg+=`<polyline fill="none" stroke="${color}" stroke-width="1.7" points="${data.samples.map((r,i)=>`${x(i)},${y(r[key])}`).join(' ')}"/>`;
  svg+=`<line x1="${x(selected)}" x2="${x(selected)}" y1="20" y2="245" stroke="#152d42" stroke-dasharray="3 4"/>`;
  for(const [key,color] of [[dense,'#315d91'],[sparse,'#00877b']]) svg+=`<circle cx="${x(selected)}" cy="${y(row[key])}" r="4" fill="${color}" stroke="white" stroke-width="1.5"/>`;
  $('#latency-chart').innerHTML=svg;
  $('#sample-number').textContent=row.image_1based;
  $('#sample-detail').textContent=`Dense ${ms(row[dense])} / Sparse24 ${ms(row[sparse])}`;
}
function render() {
  const m=data.metrics,t=data.timing.models;
  $('#kpi-latency').textContent=ms(m.latency.Sparse_PS_PL_ms.mean_ms);
  $('#kpi-speedup').textContent=(m.latency.Sparse_CPU_ms.mean_ms/m.latency.Sparse_PS_PL_ms.mean_ms).toFixed(3)+'×';
  $('#kpi-accuracy').textContent=m.accuracy.Sparse24.top1_pct.toFixed(1)+'% / '+m.accuracy.Sparse24.top5_pct.toFixed(1)+'%';
  $('#metrics-table').innerHTML=[['Dense','CPU','原始 Dense · ARM CPU'],['Dense','PS_PL','原始 Dense · ARM + FPGA'],['Sparse','CPU','Sparse24 · ARM CPU'],['Sparse','PS_PL','Sparse24 · ARM + FPGA']].map(([model,path,label])=>{
    const a=m.accuracy[model==='Sparse'?'Sparse24':'Dense'],v=m.latency[model+'_'+path+'_ms'];
    const ratio=path==='CPU'?'1×':(m.latency[model+'_CPU_ms'].mean_ms/v.mean_ms).toFixed(3)+'×';
    return `<tr${model==='Sparse'&&path==='PS_PL'?' class="featured"':''}><td>${label}</td><td>${a.top1_pct.toFixed(1)}% / ${a.top5_pct.toFixed(1)}%</td><td>${ms(v.mean_ms)}</td><td>${ms(v.median_ms)}</td><td>${ms(v.p95_ms)}</td><td>${ratio}</td></tr>`;
  }).join('');
  $('#comparison-note').textContent=`Dense → Sparse24 整网平均延迟降低 ${m.Sparse_mean_latency_reduction_pct.toFixed(2)}%，约 ${m.speedup?.Dense_over_Sparse??(m.latency.Dense_PS_PL_ms.mean_ms/m.latency.Sparse_PS_PL_ms.mean_ms).toFixed(3)}×。两种模型权重不同，不能单独归因为稀疏 MAC 加速。`;
  const maximum=t.Dense.mean_ms.runner_ms;
  $('#breakdown-bars').innerHTML=['Dense','Sparse24'].map(name=>`<div class="breakdown-row"><span>${name}</span><div class="stack" aria-label="${name}分段耗时">${segments.map(([key,label,color])=>`<i style="width:${t[name].mean_ms[key]/maximum*100}%;background:${color}" title="${label} ${ms(t[name].mean_ms[key])}"></i>`).join('')}</div><b>${ms(t[name].mean_ms.runner_ms)}</b></div>`).join('');
  $('#breakdown-legend').innerHTML=segments.map(([,label,color])=>`<span><i style="background:${color}"></i>${label}</span>`).join('');
  $('#timing-table').innerHTML=segments.map(([key,label])=>`<tr><td>${label}</td><td>${ms(t.Dense.mean_ms[key])}</td><td>${ms(t.Sparse24.mean_ms[key])}</td></tr>`).join('');
  $('#excluded-times').innerHTML=['Dense','Sparse24'].map(name=>`<article><strong>${name} · 计时外部分验收成本</strong><span>CPU 参考：${ms(t[name].mean_ms.cpu_reference_ms)}</span><span>图后缓冲检查：${ms(t[name].mean_ms.postrun_buffer_checks_ms)}</span><span>缓冲检查为 INFO 内的部分区间，不是完整 INFO 耗时。</span></article>`).join('');
  $('#architecture-cards').innerHTML=[['system','系统整体框图'],['hardware','硬件模块与数据通路'],['software','软件流程与批次验收'],['shared-core','Dense / Sparse 共用阵列']].map(([name,title])=>`<article class="figure-card"><a href="/architecture/${name}/${name}.html"><img src="/architecture/report/${name}.svg" alt="${title}" loading="lazy"></a><div class="caption"><h3>${title}</h3><a href="/architecture/${name}/${name}.html">交互图与源码 ↗</a><a href="/architecture/report/${name}.svg" download>报告精简 SVG ↓</a></div></article>`).join('');
  const identities=[['源码基准',data.timing.source_revision],['原始证据 SHA256',data.timing.archive_sha256],['Dense 模型 SHA256',m.model_sha256.Dense],['Sparse24 模型 SHA256',m.model_sha256.Sparse24],['固件 UUID',m.hardware.firmware_uuid],['板端环境',`ORT ${m.environment.ORT} / NumPy ${m.environment.NumPy} / ${m.environment.compiler}`],['运行配置',`batch=1 / ORT ${m.environment.ORT_intra_threads}:${m.environment.ORT_inter_threads} / graph optimization ${m.environment.graph_optimization}`]];
  for(const [label,value] of identities) {const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=value;$('#identities').append(dt,dd);}
  $('#load-status').hidden=true;$('#results').hidden=false;drawChart();
}
$('#sample').addEventListener('input',drawChart);
document.querySelectorAll('[data-series]').forEach(button=>button.addEventListener('click',()=>{
  series=button.dataset.series;document.querySelectorAll('[data-series]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));drawChart();
}));
$('#latency-chart').addEventListener('pointermove',event=>{
  if(!data)return;const bounds=event.currentTarget.getBoundingClientRect();
  const index=Math.round(((event.clientX-bounds.left)/bounds.width*1000-55)/925*194);
  $('#sample').value=String(306+Math.max(0,Math.min(194,index)));drawChart();
});
try {
  const response=await fetch('/api/final');
  if(!response.ok)throw Error('evidence unavailable');data=await response.json();render();
} catch {
  $('#load-status').textContent='最终证据缺失或核对失败，未显示结果。请检查服务日志与 results/board500 文件。';
  $('#load-status').setAttribute('role','alert');
}
