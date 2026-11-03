import {createHash} from 'node:crypto';
import {AppError} from './domain.mjs';

export const SPARSE24_MODEL='ea5017791e47dbefe2b06178767f1cf59c9eb75b8cf0871f3173fb2cc4ff4d43';
export const REQUEST_ID=/^[0-9a-f]{32}$/;
const SHA=/^[0-9a-f]{64}$/;
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const fail=(message,code='BAD_SPARSE24_REPLY',status=502)=>{throw new AppError(code,message,status);};
const need=(value,message)=>{if(!value)fail(message);};

export function boardEndpoint(input) {
  let url;try{url=new URL(input);}catch{fail('请输入板卡的完整 HTTP 地址。','BAD_BOARD_URL',400);}
  const octets=url.hostname.split('.').map(Number);
  const privateIP=octets.length===4 && octets.every(n=>Number.isInteger(n)&&n>=0&&n<256) &&
    (octets[0]===10 || octets[0]===192&&octets[1]===168 || octets[0]===172&&octets[1]>=16&&octets[1]<=31 || url.hostname==='127.0.0.1');
  if(url.protocol!=='http:'||!privateIP||url.username||url.password||url.pathname!=='/'||url.search||url.hash)fail('仅接受局域网或本机转发的 HTTP IPv4 地址，不含账号、路径或参数。','BAD_BOARD_URL',400);
  return url.origin;
}

export function validateSparseStatus(value) {
  need(value?.schema==='opt50_live_demo_status_v1' && UUID.test(value.boot_id??'') &&
    value.source_model_sha256===SPARSE24_MODEL && typeof value.busy==='boolean' &&
    typeof value.stage==='string' && value.automatic_replay===false,'板端 Sparse24 模型、启动或协议身份不匹配。');
  if(value.stage==='READY')need(value.selftest_completed_PL_calls===36 && value.selftest_bitwise_mismatches===0,'板端未提供匹配的当前启动自检。');
  return value;
}

export function validateSparseResult(value,id,expected={}) {
  need(value && typeof value.stage==='string' && /^[A-Z0-9_]+$/.test(value.stage),'板端结果阶段无效。');
  need(!value.request_id || value.request_id===id,'板端请求编号不匹配。');
  if(value.stage!=='PASS')return value;
  need(value.request_id===id && UUID.test(value.boot_id??'') && value.source_model_sha256===SPARSE24_MODEL &&
    value.execution_source==='REAL_FPGA_OPT50_SPARSE24','真实计算回执身份不匹配。');
  if(expected.boot_id)need(value.boot_id===expected.boot_id,'回执属于其他启动，不能作为本次完成结果。');
  if(expected.image_sha256)need(value.image_sha256===expected.image_sha256,'回执与上传图片的 SHA256 不匹配。');
  need(['image_sha256','input_sha256','decoded_RGB_sha256','CPU_logits_sha256','PL_logits_sha256','native_proof_sha256'].every(key=>SHA.test(value[key]??'')), '回执缺少输入、输出或 Native 证明摘要。');
  need(value.bitwise_mismatches===0 && value.CPU_logits_sha256===value.PL_logits_sha256,'CPU / PL 输出一致性检查失败。');
  const n=value.native;
  need(n?.completed_PL_calls===36 && n.START_commands_issued===36 && n.PL_dense===12 && n.PL_sparse===24 &&
    n.native_END_verified===true && n.postrun_buffer_checks_verified===true &&
    Number.isInteger(value.run_id)&&value.run_id>=1 && n.run_id===value.run_id &&
    typeof value.device_closed==='boolean' && n.device_closed===value.device_closed,'Native 层数、模式或完成检查不完整。');
  need(['Runner_run_ms','CPU_reference_ms','preprocessing_ms','request_work_ms'].every(key=>Number.isFinite(value[key])&&value[key]>=0),'实际耗时无效。');
  need(Array.isArray(value.top5)&&value.top5.length===5,'板端未提供完整 Top5。');
  const seen=new Set();let sum=0;
  value.top5.forEach((row,i)=>{
    need(Number.isInteger(row.index)&&row.index>=0&&row.index<1000&&!seen.has(row.index)&&typeof row.name==='string'&&row.name.length<=500&&
      Number.isFinite(row.logit)&&Number.isFinite(row.probability)&&row.probability>=0&&row.probability<=1,'Top5 类别、数值或概率无效。');
    if(i){const previous=value.top5[i-1];need(previous.logit>row.logit || previous.logit===row.logit&&previous.index<row.index,'Top5 排序不一致。');need(previous.probability>=row.probability,'Top5 概率顺序不一致。');}
    seen.add(row.index);sum+=row.probability;
  });
  need(sum<=1+1e-6,'Top5 概率总和无效。');
  return value;
}

export class Sparse24Client {
  constructor(){this.endpoint=null;this.lastStatus=null;this.active=null;this.requests=new Map();}
  async request(endpoint,path,options={}) {
    let response;
    try{response=await fetch(endpoint+path,{...options,redirect:'error',signal:AbortSignal.timeout(options.method==='POST'?150000:10000),headers:{Origin:endpoint,...options.headers}});}
    catch{fail('板卡连接中断；提交状态可能未知，只查询原编号，不自动重发。','SPARSE24_UNREACHABLE',503);}
    let payload;try{const raw=await response.text();need(raw.length<=2*1024*1024,'板端响应过大。');payload=JSON.parse(raw);}catch{fail('板端响应不是有界 JSON。');}
    if(!response.ok||payload.ok!==true)fail(typeof payload.error==='string'?payload.error:payload.error?.message??'板端拒绝请求。','SPARSE24_REJECTED',response.ok?409:response.status);
    return payload.value;
  }
  async connect(input) {
    const endpoint=boardEndpoint(input);
    if(this.active&&this.endpoint!==endpoint)fail('原请求尚未确认完成，不能切换板卡。','REQUEST_UNRESOLVED',409);
    const status=validateSparseStatus(await this.request(endpoint,'/api/status'));
    this.endpoint=endpoint;this.lastStatus=status;
    return {endpoint,status};
  }
  configured(){return {configured:Boolean(this.endpoint),endpoint:this.endpoint,active_request:this.active,active_context:this.requests.get(this.active)??null};}
  async status() {
    if(!this.endpoint)fail('请先连接 Sparse24 板端服务。','BOARD_NOT_CONNECTED',409);
    this.lastStatus=validateSparseStatus(await this.request(this.endpoint,'/api/status'));
    return {endpoint:this.endpoint,status:this.lastStatus};
  }
  async submit(id,jpeg,expectedBoot) {
    if(!REQUEST_ID.test(id))fail('请求编号无效。','BAD_REQUEST_ID',400);
    if(!this.endpoint)fail('请先连接板卡。','BOARD_NOT_CONNECTED',409);
    if(this.requests.has(id))fail('这个编号已尝试提交，请查询原编号；不会重复发送。','NO_REPLAY',409);
    if(this.active)fail('已有未确认完成的请求，先查询原编号。','REQUEST_UNRESOLVED',409);
    if(!jpeg.length||jpeg.length>8*1024*1024||jpeg[0]!==255||jpeg[1]!==216||jpeg[2]!==255)fail('仅支持不超过 8 MiB 的 JPEG 图片。','BAD_JPEG',400);
    this.active=id;
    let sent=false;
    try {
      const {status}=await this.status();
      if(status.stage!=='READY'||status.busy)fail('板卡未就绪或正在执行其他请求。','BOARD_NOT_READY',409);
      if(expectedBoot&&status.boot_id!==expectedBoot)fail('板卡启动已变化，请刷新状态后重新选择本次请求。','BOOT_CHANGED',409);
      const expected={boot_id:status.boot_id,image_sha256:createHash('sha256').update(jpeg).digest('hex')};
      this.requests.set(id,expected);sent=true;
      const result=validateSparseResult(await this.request(this.endpoint,'/api/infer/'+id,{method:'POST',headers:{'Content-Type':'image/jpeg'},body:jpeg}),id,expected);
      if(['PASS','REJECTED_IMAGE_NO_GRAPH'].includes(result.stage))this.active=null;
      return {endpoint:this.endpoint,expected,result};
    } catch(error) {if(!sent){this.active=null;error.noGraphSubmitted=true;}throw error;}
  }
  async result(id,expected={}) {
    if(!REQUEST_ID.test(id))fail('请求编号无效。','BAD_REQUEST_ID',400);
    if(!this.endpoint)fail('请先连接板卡。','BOARD_NOT_CONNECTED',409);
    if(expected.boot_id&&!UUID.test(expected.boot_id)||expected.image_sha256&&!SHA.test(expected.image_sha256))fail('查询上下文无效。','BAD_REQUEST_CONTEXT',400);
    const bound=this.requests.get(id)??expected;
    const result=validateSparseResult(await this.request(this.endpoint,'/api/result/'+id),id,bound);
    if(this.active===id&&['PASS','REJECTED_IMAGE_NO_GRAPH'].includes(result.stage))this.active=null;
    return {endpoint:this.endpoint,result};
  }
  async history() {
    if(!this.endpoint)fail('请先连接板卡。','BOARD_NOT_CONNECTED',409);
    const value=await this.request(this.endpoint,'/api/history');
    need(Array.isArray(value?.records)&&value.records.length<=100&&value.records.every(row=>REQUEST_ID.test(row.request_id??'')&&typeof row.stage==='string'),'板端历史索引无效。');
    return {endpoint:this.endpoint,...value};
  }
}
