import {escape as e} from './api-client.js';

const STORE='kv260-lab-sparse24-pending';
const ms=value=>Number.isFinite(value)?value.toFixed(3)+' ms':'—';
const idPattern=/^[0-9a-f]{32}$/;
const safeJSON=(key,fallback)=>{try{return JSON.parse(sessionStorage.getItem(key))??fallback;}catch{return fallback;}};
const header=title=>`<div class="page-head"><div><h1>${title}</h1></div><span class="badge">真实板端服务</span></div>`;
const short=value=>e(value?value.slice(0,16)+'…':'—');

async function request(path,options) {
  let response;
  try{response=await fetch('/api/sparse24/'+path,{cache:'no-store',...options});}
  catch{throw Error('本机连接中断；保留原请求编号，不重发计算。');}
  const value=await response.json();
  if(!response.ok){const error=Error(value.error?.message??'Sparse24 请求失败。');error.code=value.error?.code;error.notSent=value.submission_not_sent===true;throw error;}
  return value;
}

export function livePrediction(result,status,clientMs) {
  if(!result)return '<div class="result-empty"><strong>等待本次真实推理</strong></div>';
  if(result.stage!=='PASS')return `<div class="result-empty"><strong>${e(result.stage)}</strong><p>${e(result.reason??'尚无完成回执；不估计完成进度，不自动重新提交。')}</p></div>`;
  return `<div class="winner"><div class="tiny-label">TOP 1 · SPARSE24 实际输出</div><strong>${e(result.top5[0].name)}</strong><div class="english-label">ImageNet-1k · #${result.top5[0].index}</div><div class="winner-score">${(result.top5[0].probability*100).toFixed(1)}<small>%</small></div></div><div class="top5-title">TOP 5 / 1000 类 · 板端全类 softmax</div>${result.top5.map((row,i)=>`<div class="prediction"><div class="prediction-label"><span>0${i+1}</span><strong>${e(row.name)}</strong><em>${(row.probability*100).toFixed(2)}%</em></div><div class="prediction-bar"><i style="width:${row.probability*100}%"></i></div></div>`).join('')}<div class="result-meta">${Number.isFinite(clientMs)?`<div><label>网页往返（含查询等待）</label><strong>${ms(clientMs)}</strong></div>`:''}<div><label>模型计算（PS + PL）</label><strong>${ms(result.Runner_run_ms)}</strong></div><div><label>CPU4 数值参考</label><strong>${ms(result.CPU_reference_ms)}</strong></div><div><label>CPU / PL 逐位差异 · 板端报告</label><strong>${result.bitwise_mismatches}</strong></div><div><label>Native 核验调用</label><strong>36 · 12 Dense / 24 Sparse</strong></div><div><label>预处理</label><strong>${ms(result.preprocessing_ms)}</strong></div><div><label>板端处理</label><strong>${ms(result.request_work_ms)}</strong></div></div><p class="result-status">${result.boot_id===status?.boot_id?'当前板卡':'历史回执'} · ${result.device_closed?'设备已关闭':'设备常驻（未声明关闭）'}</p><div class="result-actions"><a class="btn btn-sm" href="/api/sparse24/result/${result.request_id}?download=1" download>下载真实结果 JSON</a><button class="btn btn-sm btn-quiet" data-live-action="details">查看完整回执</button></div>`;
}

export function createSparse24Live(onChange) {
  const restored=safeJSON(STORE,null);
  const state={endpoint:localStorage.getItem('kv260-lab-board')??'http://192.168.137.2:8080',connected:false,status:null,file:null,url:null,dimensions:null,
    pending:restored&&idPattern.test(restored.id??'')?restored:null,result:null,error:null,submitting:false,connecting:false,polling:false,history:[],clientMs:null};
  const busy=()=>Boolean(state.pending||state.submitting||state.status?.busy);
  const ready=()=>state.connected&&state.status?.stage==='READY'&&!busy();
  const changed=()=>onChange();
  const remember=()=>{if(state.pending)sessionStorage.setItem(STORE,JSON.stringify(state.pending));else sessionStorage.removeItem(STORE);};
  function resultPath(id,context) {
    const query=new URLSearchParams();if(context?.boot_id)query.set('boot',context.boot_id);if(context?.image_sha256)query.set('image',context.image_sha256);
    return 'result/'+id+(query.size?'?'+query:'');
  }
  async function refreshStatus(){try{const value=await request('status');state.status=value.status;state.connected=true;return value;}catch(error){state.status=null;throw error;}}
  async function refreshHistory(){try{state.history=(await request('history')).records;}catch(error){state.error=error.message;}changed();}
  function adoptActive(info) {
    if(!state.pending&&info.active_request){state.pending={id:info.active_request,endpoint:info.endpoint,...info.active_context};remember();}
  }
  async function connect() {
    state.connecting=true;state.error=null;changed();
    try {
      if(state.pending&&state.pending.endpoint!==state.endpoint)throw Error('原请求尚未解决，请连接提交它的原板卡地址。');
      const value=await request('connect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({endpoint:state.endpoint})});
      state.endpoint=value.endpoint;state.status=value.status;state.connected=true;localStorage.setItem('kv260-lab-board',value.endpoint);
      adoptActive(await request('connection'));await refreshHistory();if(state.pending)poll();
    } catch(error){state.connected=false;state.status=null;state.error=error.message;}
    finally{state.connecting=false;changed();}
  }
  async function init() {
    try {
      const info=await request('connection');
      if(info.configured && (!state.pending||state.pending.endpoint===info.endpoint)){
        state.endpoint=info.endpoint;adoptActive(info);await refreshStatus();await refreshHistory();if(state.pending)poll();
      }
    }catch(error){state.error=error.message;}
    changed();
  }
  async function loadImage(file) {
    if(!file||busy())return;
    if(file.type!=='image/jpeg'||!file.size||file.size>8*1024*1024){state.error='请选择非空、最大 8 MiB 的 JPEG 图片。';changed();return;}
    const url=URL.createObjectURL(file);
    try {
      const image=new Image();image.src=url;await image.decode();
      if(!image.naturalWidth||image.naturalWidth*image.naturalHeight>40_000_000)throw Error('图片尺寸超过支持范围。');
      if(state.url)URL.revokeObjectURL(state.url);state.url=url;state.file=file;state.dimensions=`${image.naturalWidth} × ${image.naturalHeight}`;state.result=null;state.error=null;
    }catch(error){URL.revokeObjectURL(url);state.error=error.message;}
    changed();
  }
  async function poll() {
    if(state.polling||!state.connected||!state.pending)return;
    state.polling=true;
    try {
      while(state.pending&&state.connected) {
        const pending=state.pending;
        try {
          const {result}=await request(resultPath(pending.id,pending));
          state.result=result;
          if(['PASS','REJECTED_IMAGE_NO_GRAPH'].includes(result.stage)){
            state.clientMs=Number.isFinite(pending.startedAt)?Date.now()-pending.startedAt:null;
            state.pending=null;remember();state.error=result.stage==='PASS'?null:result.reason??'图片被拒绝，未运行计算';
            await refreshStatus();await refreshHistory();changed();break;
          }
          if(['RETAINED_NO_REPLAY','INTERRUPTED_OLD_BOOT_NO_REPLAY','INCOMPLETE_ADMISSION_NO_REPLAY'].includes(result.stage)){
            state.error='请求未得到成功完成证明；保留编号，不重放、强停或更换板端运行配置。';changed();break;
          }
          state.error=result.stage==='UNKNOWN_REQUEST'?'暂未找到原编号的回执；只继续查询，不自动重新提交。':null;changed();
        }catch(error){state.error=error.message;changed();}
        await new Promise(resolve=>setTimeout(resolve,100));
      }
    }finally{state.polling=false;}
  }
  async function submit() {
    if(!ready()||!state.file)return;
    const startedAt=Date.now();state.clientMs=null;
    const boot=state.status.boot_id;
    const image_sha256=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await state.file.arrayBuffer())),b=>b.toString(16).padStart(2,'0')).join('');
    if(!ready())return;
    const id=crypto.randomUUID().replaceAll('-','');
    state.pending={id,boot_id:boot,image_sha256,endpoint:state.endpoint,startedAt};remember();state.submitting=true;state.result=null;state.error=null;changed();
    try {
      const value=await request('infer/'+id,{method:'POST',headers:{'Content-Type':'image/jpeg','X-Sparse24-Boot':boot},body:state.file});
      if(value.expected?.image_sha256!==image_sha256||value.expected?.boot_id!==boot)throw Error('提交上下文不一致，保留编号查询原请求。');
      state.result=value.result;
    }catch(error){state.error=error.message;if(error.notSent||['BAD_JPEG','JPEG_REQUIRED','IMAGE_TOO_LARGE','BOARD_NOT_CONNECTED'].includes(error.code)){state.pending=null;remember();}}
    finally{state.submitting=false;changed();if(state.pending)poll();}
  }
  function inferenceView() {
    return header('推理工作台','连接现有 Sparse24 服务，上传 JPEG，查看真实分类结果与 CPU / PL 一致性。')+
    `<div class="demo-strip"><span>${state.connected?`BOARD · ${e(state.endpoint)} · ${e(state.status?.stage??'状态待刷新')}`:'尚未连接板卡；请选择“连接板卡”后进行实测。'}</span><a class="btn btn-sm" href="#connection">连接板卡 ↗</a></div><div class="stats-grid"><div class="stat-card"><div><label>模型</label><div class="stat-value">H576<small>Sparse24</small></div></div></div><div class="stat-card"><div><label>每图固定 PL 路由</label><div class="stat-value">12D / 24S</div></div></div><div class="stat-card"><div><label>其余线性层</label><div class="stat-value">13<small>PS</small></div></div></div><div class="stat-card"><div><label>当前选中回执</label><div class="stat-value">${state.result?.stage==='PASS'?'36':'—'}<small>Native 调用</small></div></div></div></div>
    <div class="inference-grid"><section class="card input-panel"><div class="card-head"><span class="card-title">01 / 输入图片</span><span class="badge">真实 JPEG</span></div><div class="card-body"><input id="s24-picker" type="file" accept="image/jpeg" hidden><div class="input-preview" id="s24-drop" tabindex="0" role="button" aria-label="选择或拖入真实 JPEG 图片">${state.url?`<img src="${e(state.url)}" alt="${e(state.file.name)}">`:'<div class="s24-placeholder"><strong>选择或拖入一张图片</strong><p>JPEG · 最大 8 MiB</p></div>'}<button class="btn btn-sm upload-overlay" data-live-action="upload">选择 JPEG</button></div><div class="image-info"><strong>${e(state.file?.name??'尚未选择图片')}</strong><span>${e(state.dimensions??'224 × 224 模型输入')}</span></div><p class="input-hint">原始 JPEG · 最大 8 MiB</p><div class="segmented"><button class="segment selected" disabled>Sparse24 PS + PL<small>12 Dense + 24 Sparse</small></button><button class="segment selected" disabled>CPU4 数值参考<small>逐位校验</small></button></div><div class="run-controls"><span class="badge">${busy()?'原请求尚未完成':e(state.status?.stage??'未连接')}</span><button class="btn btn-primary" data-live-action="start" ${!ready()||!state.file?'disabled':''}>${state.submitting?'正在提交':'开始板端推理'}</button></div></div></section><section class="card result-panel"><div class="card-head"><span class="card-title">02 / 真实预测结果</span><span class="badge">${state.result?.stage==='PASS'?'已核验板端回执':'等待运行'}</span></div><div class="card-body">${livePrediction(state.result,state.status,state.clientMs)}</div></section></div>${state.pending?`<div class="card progress-card"><strong>查询原请求 · 不重放计算</strong><p class="mono">${e(state.pending.id)}</p><p>不估计逐层进度。${e(state.result?.stage??'等待板端接收与完成回执')}</p><button class="btn btn-sm" data-live-action="query">查询原请求</button></div>`:''}${errorView()}`;
  }
  function connectionView() {
    return header('连接与诊断','本页连接现有 HTTP 服务并读取状态；连接操作不会提交推理或加载固件。')+
    `<div class="connection-grid"><section class="card"><div class="card-head"><span class="card-title">Sparse24 板端服务</span></div><div class="card-body"><div class="field"><label for="s24-endpoint">板卡 HTTP 地址</label><input id="s24-endpoint" value="${e(state.endpoint)}" placeholder="http://192.168.137.2:8080" ${state.pending?'disabled':''}></div><div class="button-row"><button class="btn btn-primary" data-live-action="connect" ${state.connecting?'disabled':''}>${state.connecting?'正在连接':'连接板卡'}</button><button class="btn" data-live-action="status" ${!state.connected?'disabled':''}>刷新状态</button><a class="btn btn-quiet" href="#inference">上传图片推理 ↗</a></div></div></section><section class="card"><div class="card-head"><span class="card-title">当前连接状态</span><span class="badge">${state.connected?'BOARD':'未连接'}</span></div><div class="card-body"><table class="numeric-table"><tbody>${[['服务阶段',state.status?.stage??'未取得'],['当前占用',state.status?state.status.busy?'执行中':'空闲':'未查询'],['Python 服务',state.status?state.status.python_profile==='exact-rgb-path-audit-v1'?'原图等价优化':'原实现':'未查询'],['boot ID',state.status?.boot_id??'—'],['启动自检调用',state.status?.selftest_completed_PL_calls??'—'],['启动自检位级差异',state.status?.selftest_bitwise_mismatches??'—'],['模型 SHA256',state.status?.source_model_sha256??'—']].map(([label,value])=>`<tr><td>${label}</td><td class="status-code">${e(value)}</td></tr>`).join('')}</tbody></table></div></section></div>${errorView()}`;
  }
  function historyView() {
    return header('板端记录','已有服务最近100条保存索引；其他启动记录不当作本次实测。')+
    `<section class="card"><div class="card-head"><span class="card-title">真实请求与结果</span><button class="btn btn-sm" data-live-action="history" ${!state.connected?'disabled':''}>刷新板端记录</button></div><div class="table-wrap"><table class="data-table"><thead><tr><th>请求 / 图片</th><th>启动</th><th>状态</th><th>PS + PL</th><th>CPU参考</th><th>操作</th></tr></thead><tbody>${state.history.map(row=>`<tr><td class="mono">${short(row.request_id)}<br>SHA ${short(row.image_sha256)}</td><td>${row.boot_id===state.status?.boot_id?'当前启动':'历史启动'}</td><td>${e(row.stage)}</td><td>${ms(row.Runner_run_ms)}</td><td>${ms(row.CPU_reference_ms)}</td><td><button class="btn btn-sm" data-live-record="${e(row.request_id)}">查看回执</button></td></tr>`).join('')}</tbody></table></div>${!state.history.length?'<div class="table-empty">连接板卡后刷新记录。</div>':''}</section>${errorView()}`;
  }
  function errorView(){return state.error?`<div class="alert"><strong>实测通道提示</strong>${e(state.error)}</div>`:'';}
  async function details(id=state.result?.request_id) {
    if(!id)return;
    try {
      const {result}=await request('result/'+id);
      const summary=result.stage==='PASS'?`<div class="lab-limit">${result.boot_id===state.status?.boot_id?'当前启动':'历史启动'} · ${e(result.request_id)}<br>PS + PL ${ms(result.Runner_run_ms)} · CPU参考 ${ms(result.CPU_reference_ms)} · 板端报告逐位差异 ${result.bitwise_mismatches}<br>图片 SHA256 ${e(result.image_sha256)}</div><table class="data-table"><thead><tr><th>类别</th><th>Logit</th><th>Softmax</th></tr></thead><tbody>${result.top5.map(row=>`<tr><td>${e(row.name)} · #${row.index}</td><td>${row.logit.toFixed(4)}</td><td>${(row.probability*100).toFixed(2)}%</td></tr>`).join('')}</tbody></table>`:`<div class="lab-limit">${e(result.stage)} · ${e(result.reason??'尚无完成证明')}</div>`;
      document.querySelector('#dialog-content').innerHTML=`<div class="dialog-head"><strong>BOARD · 真实保存回执</strong><button class="btn btn-sm" data-live-close>关闭</button></div><div class="dialog-body">${summary}<details><summary>完整 JSON 回执</summary><pre class="s24-receipt">${e(JSON.stringify(result,null,2))}</pre></details><a class="btn btn-primary" href="/api/sparse24/result/${id}?download=1" download>下载完整 JSON</a></div>`;
      const dialog=document.querySelector('#details-dialog');dialog.querySelector('[data-live-close]').onclick=()=>dialog.close();dialog.showModal();
    }
    catch(error){state.error=error.message;changed();}
  }
  function bind(root) {
    root.querySelectorAll('[data-live-action]').forEach(button=>button.addEventListener('click',async()=>{
      const action=button.dataset.liveAction;
      if(action==='upload')root.querySelector('#s24-picker')?.click();
      else if(action==='connect'){state.endpoint=root.querySelector('#s24-endpoint').value.trim();await connect();}
      else if(action==='start')await submit();
      else if(action==='status'){try{await refreshStatus();state.error=null;}catch(error){state.error=error.message;}changed();}
      else if(action==='query')poll();
      else if(action==='history')refreshHistory();
      else if(action==='details')details();
    }));
    root.querySelectorAll('[data-live-record]').forEach(button=>button.addEventListener('click',()=>details(button.dataset.liveRecord)));
    const picker=root.querySelector('#s24-picker'),drop=root.querySelector('#s24-drop');
    if(picker)picker.addEventListener('change',()=>loadImage(picker.files[0]));
    if(drop){drop.addEventListener('click',event=>{if(!event.target.closest('button')&&!busy())picker.click();});drop.addEventListener('keydown',event=>{if(['Enter',' '].includes(event.key)){event.preventDefault();if(!busy())picker.click();}});drop.addEventListener('dragover',event=>event.preventDefault());drop.addEventListener('drop',event=>{event.preventDefault();loadImage(event.dataTransfer.files[0]);});}
  }
  return {init,bind,inferenceView,connectionView,historyView,snapshot:()=>({connected:state.connected,status:state.status,pending:state.pending})};
}
