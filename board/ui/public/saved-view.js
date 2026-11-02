import {escape as e} from './api-client.js';
import {pairedChart,bindFinalPerformance} from './final-lab.js';

const ms=value=>value.toFixed(3)+' ms';
const file=(key,download=false)=>'/api/saved/files/'+encodeURIComponent(key)+(download?'?download=1':'');
const unavailable=()=>'<div class="alert"><strong>实板存档不可用</strong><p>数据缺失或身份核对失败。请检查本机证据文件；此处不会显示DEMO结果。</p></div>';
const header=title=>`<div class="page-head"><h1>${title}</h1><span class="badge badge-success">已保存真实板测 · 只读</span></div>`;
const banner='<div class="saved-source">SAVED BOARD EVIDENCE · 查看已完成的真实板测，不重新运行、不播放模拟进度。</div>';
const rows=items=>`<table class="numeric-table"><tbody>${items.map(([k,v])=>`<tr><td>${e(k)}</td><td>${e(String(v))}</td></tr>`).join('')}</tbody></table>`;
const metric=(label,value,scope)=>`<article><label>${label}</label><strong>${value}</strong><span>${scope}</span></article>`;

export function savedTabs(page,view='saved') {
  const other=page==='inference'?'live':'demo',name=page==='inference'?'现场上传推理':'DEMO操作排练';
  return `<nav class="saved-tabs" aria-label="${page}数据来源"><a class="btn ${view==='saved'?'btn-primary':'btn-quiet'}" href="#${page}?view=saved" ${view==='saved'?'aria-current="page"':''}>已保存实板结果</a><a class="btn ${view===other?'btn-primary':'btn-quiet'}" href="#${page}?view=${other}" ${view===other?'aria-current="page"':''}>${name}</a></nav>`;
}

function sources(data,keys) {
  return `<details class="card source-details saved-sources"><summary>实测来源与SHA256</summary><div class="card-body"><p>只读载入并核对文件身份；CPU／PL比较、调用完成与关闭状态按各自原始回执展示。</p><div class="source-list">${keys.map(key=>{
    const spec=data.files[key];
    return `<div><b><a href="${file(key)}" target="_blank" rel="noreferrer">${e(spec.path)} ↗</a></b><code>${e(spec.sha256)}</code></div>`;
  }).join('')}</div><p>500张原始证据归档SHA256</p><code class="saved-hash">${e(data.archive_sha256)}</code></div></details>`;
}

export function savedInference(data,params=new URLSearchParams()) {
  if(!data)return savedTabs('inference')+unavailable();
  const record=data.inference.find(row=>row.id===(params.get('sample')??'guitar'));
  if(!record)return savedTabs('inference')+unavailable();
  const r=record.receipt;
  return savedTabs('inference')+header('推理工作台')+banner+
    `<div class="saved-stats">${metric('模型计算 · PS + PL',ms(r.Runner_run_ms),'Runner.run · 含36次PL调用')}${metric('CPU数值参考',ms(r.CPU_reference_ms),'相同模型／相同输入')}${metric('CPU／PL逐位差异',String(r.bitwise_mismatches),'原板端报告 · 输出摘要相等')}${metric('Native完成调用','36 / 36','12 Dense + 24 Sparse')}</div>
    <div class="saved-inference-grid"><section class="card"><div class="card-head"><span class="card-title">对应原始图片</span><span class="badge">已核对图片SHA256</span></div><div class="card-body"><label for="saved-image">选择已保存任务</label><select id="saved-image" data-saved-selector="sample">${data.inference.map(row=>`<option value="${row.id}" ${record.id===row.id?'selected':''}>${e(row.title)} · ${row.receipt.request_id.slice(0,8)}</option>`).join('')}</select><img class="saved-photo" src="${file(record.image)}" alt="${e(record.title)}：与原回执SHA256一致的照片">${rows([['模型','H576 Sparse24 · W8A8'],['输入','RGB → 224×224'],['结果状态',r.stage],['标签评估','现场照片无人工标签，不计算准确率']])}</div></section>
    <section class="card"><div class="card-head"><span class="card-title">实际分类输出</span><span class="badge badge-success">REAL FPGA / SPARSE24</span></div><div class="card-body"><div class="saved-winner"><span>TOP 1 · #${r.top5[0].index}</span><strong>${e(r.top5[0].name)}</strong><b>${(r.top5[0].probability*100).toFixed(2)}%</b></div><div class="top5-title">TOP 5 / 1000 类 · 原板端全类softmax</div>${r.top5.map((row,i)=>`<div class="prediction"><div class="prediction-label"><span>0${i+1}</span><strong>${e(row.name)}</strong><em>${(row.probability*100).toFixed(2)}%</em></div><div class="prediction-bar"><i style="width:${row.probability*100}%"></i></div></div>`).join('')}<div class="saved-checks"><span>✓ Native END已核验</span><span>✓ 图后缓冲检查通过</span><span>✓ CPU／PL摘要一致</span></div><p class="saved-boundary">设备常驻：原回执未声明关闭。上述36次为Native调用／命令记录，物理START未独立计数。</p><div class="result-actions"><a class="btn btn-primary" href="${file(record.report,true)}" download>下载原始实测回执</a><a class="btn btn-quiet" href="${file(record.report)}" target="_blank" rel="noreferrer">查看完整JSON ↗</a></div></div></section></div>
    <section class="card saved-section"><div class="card-head"><span class="card-title">任务身份与计时范围</span></div><div class="card-body">${rows([['request_id',r.request_id],['原受检boot_id',r.boot_id],['图片SHA256',r.image_sha256],['量化输入SHA256',r.input_sha256],['模型SHA256',r.source_model_sha256],['CPU／PL输出SHA256',r.PL_logits_sha256],['预处理／板端处理',ms(r.preprocessing_ms)+' / '+ms(r.request_work_ms)]])}<p class="saved-boundary">Runner.run包含ARM工作、36次PL调用和必要同步；不含预处理、初始化、CPU参考及INFO。softmax分数不是校准置信度。原板端报告了逐位比较，本页核对摘要和完成字段，没有重新下载全部1000个logits复算。</p></div></section>${sources(data,[record.image,record.report])}`;
}

export function savedLayer(data,params=new URLSearchParams()) {
  if(!data)return savedTabs('layer')+unavailable();
  const index=Number(params.get('layer')??2),layer=data.layer.layers.find(x=>x.index===index);
  if(!layer)return savedTabs('layer')+unavailable();
  const sample=data.layer.sample;
  return savedTabs('layer')+header('线性层实验')+banner+
    `<div class="saved-stats">${metric('真实模型层',e(layer.layer_id),'整网板测中的单层调用')}${metric('输出规格',(layer.M*layer.N).toLocaleString(),'INT8 × INT8 → INT32')}${metric('整图logits差异','0 / 1000','各自CPU参考 · 非单层Golden计数')}${metric('图片序号',String(sample.image_1based),e(sample.id))}</div>
    <div class="task-grid"><section class="card"><div class="card-head"><span class="card-title">已保存的层配置</span></div><div class="card-body"><label for="saved-layer">选择真实调用层</label><select id="saved-layer" data-saved-selector="layer">${data.layer.layers.map(row=>`<option value="${row.index}" ${row.index===index?'selected':''}>${e(row.layer_id)}</option>`).join('')}</select><div class="dimensions">${['M','K','N'].map(k=>`<div class="dimension"><small>${k}</small><strong>${layer[k]}</strong></div>`).join('')}</div>${rows([['接口','128-bit AXI · W8A8'],['Dense模型路由','mode=0 · Dense'],['Sparse24模型路由',layer.mode===1?'mode=1 · K向2:4':'mode=0 · Dense'],['证据类型','真实整网运行中的单层调用记录']])}</div></section>
    <section class="card"><div class="card-head"><span class="card-title">原始调用与完成证据</span><span class="badge">相同图片 · 不同模型权重</span></div><div class="card-body"><div class="saved-layer-results">${data.layer.observations.map(observation=>{
      const at=observation.pending.owner_metrics.layers.findIndex(row=>row.layer_index===index),row=observation.pending.owner_metrics.layers[at];
      return `<div class="saved-layer-result"><h2>${observation.model}</h2>${rows([['执行模式',row.mode===0?'Dense / mode 0':'Sparse2:4 / mode 1'],['调用序号',`${at+1} / 36`],['Owner dispatch',ms(row.owner_Run_dispatch_ms)],['整图完成／提交',`${observation.accuracy.completed_PL_calls}次PL完成；批次关闭后提交`],['整图CPU／PL逐位差异',observation.accuracy.bitwise_mismatches]])}</div>`;
    }).join('')}</div></div></section></div>
    <p class="saved-boundary">Owner dispatch包含寄存器编程及完成检查，与Runner.run重叠，不是纯PL周期。这里展示两个不同模型的真实层调用，不能据此计算同权重稀疏核加速比，也不能把整图零差异作为单层INT32 Golden逐元素校验。</p>
    <section class="card saved-section"><div class="card-head"><span class="card-title">同权重双模式整数验真</span><span class="badge">项目验收文档记录</span></div><div class="card-body"><p>原项目验收文档记录layer02同一剪枝权重、Dense／Sparse两模式、226,944个INT32输出一致：<strong>${data.layer.published_acceptance_pass?'PASS':'未找到对应记录'}</strong>。</p><p class="saved-boundary">该独立单层试验的原始结果JSON未包含在当前归档中；本页没有重做该试验，也不生成替代回执。输入清单只证明用例身份，不单独证明板测通过。</p><div class="result-actions"><a class="btn" href="${file('verification')}" target="_blank" rel="noreferrer">查看验收文档</a><a class="btn btn-quiet" href="${file('layer-case')}" target="_blank" rel="noreferrer">查看原始用例身份</a></div></div></section>
    <section class="card saved-section"><div class="card-head"><span class="card-title">36次真实PL调用 · 图片${sample.image_1based}</span><a class="btn btn-sm" href="${file('Sparse24-pending',true)}" download>下载逐层原始记录</a></div><div class="table-wrap"><table class="data-table"><thead><tr><th>序号／层</th><th>M / K / N</th><th>Dense调度</th><th>Sparse24调度</th></tr></thead><tbody>${data.layer.layers.map((row,i)=>`<tr${row.index===index?' class="lab-featured"':''}><td>${i+1} · ${e(row.layer_id)}</td><td>${row.M} / ${row.K} / ${row.N}</td>${data.layer.observations.map(o=>`<td>mode ${o.pending.owner_metrics.layers[i].mode} · ${ms(o.pending.owner_metrics.layers[i].owner_Run_dispatch_ms)}</td>`).join('')}</tr>`).join('')}</tbody></table></div></section>${sources(data,['Dense-pending','Dense-accuracy','Dense-commit','Sparse24-pending','Sparse24-accuracy','Sparse24-commit','layers','layer-case','verification'])}`;
}

export function savedBenchmark(data,final) {
  if(!data||!final)return savedTabs('benchmark')+unavailable();
  const m=final.metrics;
  return savedTabs('benchmark')+header('测评对照')+banner+
    `<div class="saved-stats">${metric('Dense · PS + PL均值',ms(m.latency.Dense_PS_PL_ms.mean_ms),'相对自身CPU '+(m.latency.Dense_CPU_ms.mean_ms/m.latency.Dense_PS_PL_ms.mean_ms).toFixed(3)+'×')}${metric('Sparse24 · PS + PL均值',ms(m.latency.Sparse_PS_PL_ms.mean_ms),'相对自身CPU '+(m.latency.Sparse_CPU_ms.mean_ms/m.latency.Sparse_PS_PL_ms.mean_ms).toFixed(3)+'×')}${metric('正式样本','195 对','同调度 · 第306–500张')}${metric('平均模型调用耗时降低',m.Sparse_mean_latency_reduction_pct.toFixed(2)+'%','不同权重／训练的共同影响')}</div>
    <section class="card"><div class="card-head"><span class="card-title">同一图片 · 四组真实计时</span><div class="segmented" aria-label="计时路径"><button class="segment selected" data-final-series="pl" aria-pressed="true">ARM + FPGA</button><button class="segment" data-final-series="cpu" aria-pressed="false">CPU参考</button></div></div><div class="card-body"><div class="lab-legend"><span class="lab-dense">Dense</span><span class="lab-sparse">Sparse24</span><small>纵轴ms · 横轴图片序号</small></div><div id="lab-chart">${pairedChart(final).svg}</div><div class="lab-sample"><label for="lab-sample">图片 <output id="lab-sample-number">306</output></label><input id="lab-sample" type="range" min="306" max="500" value="306"><span id="lab-sample-detail">${pairedChart(final).detail}</span></div><div id="saved-pair-detail"></div><div class="result-actions"><a class="btn btn-primary" href="/api/final/paired.csv" download>下载195张原始CSV</a><a class="btn btn-quiet" href="/api/final/timing.json" download>下载计时分解</a><a class="btn btn-quiet" href="#performance">完整性能与耗时 ↗</a></div></div></section>
    <p class="saved-boundary">batch=1，ORT线程4:1，每图一次保存记录，未以网页DEMO预热／次数替代。Runner.run包含ARM、36次PL调用及同步，排除初始化、预处理、CPU参考和后续INFO；不是纯PL时间。CPU为ORT W8A8数值参考，未宣称优化CPU性能上限。</p>
    <details class="card source-details"><summary>实测来源与模型身份</summary><div class="card-body">${rows([['源码基准',data.source_revision],['原始归档SHA256',data.archive_sha256],['Dense模型SHA256',m.model_sha256.Dense],['Sparse24模型SHA256',m.model_sha256.Sparse24],['固件UUID',m.hardware.firmware_uuid],['平台','125MHz · 128-bit AXI · 32×16 MAC阵列']])}<a class="btn" href="/api/final" target="_blank" rel="noreferrer">查看核验后的统计JSON ↗</a></div></details>`;
}

export function bindSavedView(root,page,final,state) {
  for(const select of root.querySelectorAll('[data-saved-selector]'))select.addEventListener('change',()=>{
    const query=new URLSearchParams({view:'saved',[select.dataset.savedSelector]:select.value});
    location.hash=page+'?'+query;
  });
  if(page==='benchmark'&&final) {
    bindFinalPerformance(root,final,state);
    const slider=root.querySelector('#lab-sample');
    const show=()=>{
      const row=final.samples[Number(slider.value)-306];
      root.querySelector('#saved-pair-detail').innerHTML=rows([['图片ID',row.id],['Dense CPU / PS+PL',ms(row.Dense_CPU_ms)+' / '+ms(row.Dense_PS_PL_ms)],['Sparse24 CPU / PS+PL',ms(row.Sparse_CPU_ms)+' / '+ms(row.Sparse_PS_PL_ms)],['图片SHA256',row.image_sha256]]);
    };
    slider.addEventListener('input',show);root.querySelector('#lab-chart').addEventListener('pointermove',show);show();
  }
}
