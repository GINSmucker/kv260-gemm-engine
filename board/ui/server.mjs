import http from 'node:http';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { AppError, JobStore, POLICY, MODEL, LAYERS, validateProfile } from './domain.mjs';
import { BoardClient } from './board-client.mjs';
import { presentationData } from './presentation.mjs';
import { serveFinal } from './final-api.mjs';
import { Sparse24Client } from './sparse24-client.mjs';
import { serveSparse24 } from './sparse24-api.mjs';

const PUBLIC = path.resolve(fileURLToPath(new URL('./public/', import.meta.url)));
const MIME = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml', '.json': 'application/json; charset=utf-8' };
async function body(req) {
  if (!(req.headers['content-type'] ?? '').startsWith('application/json')) throw new AppError('JSON_REQUIRED', '请求须为JSON。', 415);
  let size = 0; const chunks = [];
  for await (const chunk of req) { size += chunk.length; if (size > 15 * 1024 * 1024) throw new AppError('BODY_TOO_LARGE', '请求过大。', 413); chunks.push(chunk); }
  try { return JSON.parse(Buffer.concat(chunks).toString('utf8')); } catch { throw new AppError('BAD_JSON', 'JSON格式错误。'); }
}
export function createWorkbenchServer({ clock, boardUrl } = {}) {
  const store = new JobStore(clock); const profiles = {};
  const board = boardUrl ? new BoardClient(boardUrl) : null;
  const sparse24 = new Sparse24Client();
  const server = http.createServer(async (req, res) => {
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'");
    const json = (status, data) => { res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' }); res.end(JSON.stringify(data)); };
    try {
      const origin = req.headers.origin;
      if (origin && ![`http://${req.headers.host}`].includes(origin)) throw new AppError('ORIGIN_BLOCKED', '仅允许同源本机请求。', 403);
      const host = (req.headers.host ?? '').split(':')[0];
      if (!['127.0.0.1', 'localhost'].includes(host)) throw new AppError('HOST_BLOCKED', '本服务仅支持loopback地址。', 403);
      const url = new URL(req.url, 'http://127.0.0.1'); const p = url.pathname;
      if (await serveSparse24(req,res,url,sparse24,body)) return;
      if (await serveFinal(req,res,p)) return;
      if (p === '/api/presentation' && req.method === 'GET') {
        try { return json(200,await presentationData()); }
        catch { throw new AppError('PRESENTATION_UNAVAILABLE','已保存的实测展示资料缺失或身份不匹配。',503); }
      }
      const savedExport=p.match(/^\/api\/presentation\/benchmark\.(json|csv)$/);
      if(savedExport&&req.method==='GET') {
        let saved;try { saved=await presentationData(); }
        catch { throw new AppError('PRESENTATION_UNAVAILABLE','已保存的实测展示资料缺失或身份不匹配。',503); }
        const ext=savedExport[1],job=saved.benchmark;
        const content=ext==='json'?JSON.stringify(job,null,2):'\uFEFF'+[['record_kind','source','job_id','sample','h576_fp32_ms','h576_w8a8_ms','h576_ps_pl_ms'],...job.result.samples.map(s=>['SAVED_MEASUREMENTS',job.source,job.id,s.index,s.fp32Ms,s.w8a8Ms,s.plMs])].map(row=>row.map(v=>'"'+String(v).replaceAll('"','""')+'"').join(',')).join('\r\n');
        res.writeHead(200,{'Content-Type':ext==='csv'?'text/csv; charset=utf-8':'application/json; charset=utf-8','Content-Disposition':`attachment; filename="saved_${job.id}.${ext}"`});
        return res.end(content);
      }
      if (p === '/api/connect/network' && req.method === 'POST' && board) { await body(req); return json(200, await board.request('/health')); }
      if (p === '/api/board/health' && req.method === 'GET' && board) return json(200, await board.request('/health'));
      if (p === '/api/board/logs' && req.method === 'GET' && board) return json(200, await board.request('/logs'));
      if (p.startsWith('/api/connect') || p.startsWith('/api/serial') || p.startsWith('/api/hardware') || p.startsWith('/api/board')) throw new AppError('HARDWARE_LOCKED', POLICY.reason, 423);
      if (p === '/api/policy' && req.method === 'GET') return json(200, board ? { ...POLICY, source: 'BOARD', hardwareIOEnabled:true, networkConnection:'SSH_TUNNEL_CONFIGURED', serialConnection:'LOCKED', reason:'真实网口经SSH转发；PL能力来自当前已验证owner窗口。' } : POLICY);
      if (p === '/api/catalog' && req.method === 'GET') {
        if(board) {const health=await board.request('/health');if(health.catalog)return json(200,health.catalog);}
        return json(200, { model: MODEL, layers: LAYERS, capabilities: { modes: ['dense_w8a8'], sparse: 'UNSUPPORTED', w4: 'UNSUPPORTED', source: 'DEMO_CONFIGURATION' } });
      }
      if (p === '/api/jobs' && req.method === 'POST') { const data = await body(req); if (data.source === 'BOARD') { if (!board) throw new AppError('HARDWARE_LOCKED','未配置真实板端通道，DEMO服务拒绝设备任务。',423); if (store.active) throw new AppError('DEVICE_BUSY','请先停止演示任务。',409); return json(201,await board.request('/jobs',data)); } if (board?.lastHealth?.active_job) throw new AppError('DEVICE_BUSY','已有板端任务运行，请先完成或停止。',409); return json(201, store.create(data)); }
      if (p === '/api/jobs' && req.method === 'GET') { let actual = []; let boardError = null; if (board) { try { actual = (await board.request('/jobs')).jobs; } catch (err) { boardError = { code:err.code, message:err.message }; } } return json(200, { jobs:[...actual,...store.list()], boardError }); }
      const exported = p.match(/^\/api\/jobs\/((?:demo_|board_)[\w-]+)\/export\.(json|csv)$/);
      if (exported && req.method === 'GET') {
        const job = exported[1].startsWith('board_') ? await board.request('/jobs/'+exported[1]) : store.get(exported[1]); const ext = exported[2];
        let data = JSON.stringify(job, null, 2);
        if (ext === 'csv') {
          let rows;
          if(job.result?.route_manifest)rows=[['source','job_id','layer_index','layer_id','M','K','N','backend','evidence'],...job.result.route_manifest.map(r=>['BOARD',job.id,r.layer_index,r.layer_id,r.M,r.K,r.N,r.backend,r.evidence])];
          else if(job.source==='BOARD' && job.result?.layers) rows=[['source','job_id','layer_index','layer_id','M','K','N','starts_before','starts_after','gemm_api_us','native_us','X_sha256','W_sha256','Y_sha256'],...job.result.layers.map(r=>['BOARD',job.id,r.layer_index,r.layer_id,r.M,r.K,r.N,r.starts_before,r.starts_after,r.gemm_api_us,r.native_us,r.X_sha256,r.W_sha256,r.Y_sha256])];
          else {
            if (!job.result?.samples) throw new AppError('CSV_UNAVAILABLE', '该任务没有逐次样本或实际层回执。', 409);
            rows = job.result.model_protocol==='h576_v5' ? [['source','job_id','sample','h576_fp32_ms','h576_w8a8_ms','h576_ps_pl_ms'],...job.result.samples.map(s=>[s.source,job.id,s.index,s.fp32Ms,s.w8a8Ms,s.plMs])] : job.source === 'BOARD' ? [['source','job_id','sample','fp32_cpu_ms','w8a8_reference_cpu_ms'], ...job.result.samples.map(s=>[s.source,job.id,s.index,s.fp32Ms,s.w8a8Ms])] : [['source','job_id','sample','arm_synthetic_ms','pl_demo_synthetic_ms'], ...job.result.samples.map(s => [s.source,job.id,s.index,s.armMs,s.demoMs])];
          }
          data = '\uFEFF' + rows.map(row => row.map(v => '"' + String(v).replaceAll('"','""') + '"').join(',')).join('\r\n');
        }
        res.writeHead(200, { 'Content-Type': ext === 'csv' ? 'text/csv; charset=utf-8' : 'application/json; charset=utf-8', 'Content-Disposition': `attachment; filename="${job.id}.${ext}"` });
        return res.end(data);
      }
      const boardMatch = p.match(/^\/api\/jobs\/(board_[0-9a-f]{12})(\/cancel)?$/);
      if (boardMatch && board && ((req.method === 'GET' && !boardMatch[2]) || (req.method === 'POST' && boardMatch[2]))) return json(200, await board.request('/jobs/'+boardMatch[1]+(boardMatch[2] ?? ''), req.method === 'POST' ? await body(req) : undefined));
      const match = p.match(/^\/api\/jobs\/(demo_[\w-]+)(\/cancel)?$/);
      if (match && !match[2] && req.method === 'GET') return json(200, store.get(match[1]));
      if (match?.[2] && req.method === 'POST') { await body(req); return json(200, store.cancel(match[1])); }
      if (p === '/api/profiles' && req.method === 'POST') { const profile = validateProfile(await body(req)); profiles[profile.kind] = profile; return json(200, { profile, hardwareContacted: false }); }
      if (p === '/api/diagnostics' && req.method === 'POST') return json(200, { snapshot: store.diagnostics((await body(req)).action), logs: store.audit });
      if (p === '/api/logs' && req.method === 'GET') return json(200, { logs: store.audit });
      if (p.startsWith('/api/')) throw new AppError('NOT_FOUND', '接口不存在。', 404);
      if (req.method !== 'GET') throw new AppError('METHOD_NOT_ALLOWED', '方法不支持。', 405);
      const filename = ['/', '/index.html', '/legacy', '/legacy/', '/final.html'].includes(p) ? 'index.html' : decodeURIComponent(p).slice(1);
      const full = path.resolve(PUBLIC, filename);
      if (!full.startsWith(PUBLIC + path.sep) || !MIME[path.extname(full)]) throw new AppError('NOT_FOUND', '页面不存在。', 404);
      let data; try { data = await readFile(full); } catch { throw new AppError('NOT_FOUND', '页面不存在。', 404); }
      res.writeHead(200, { 'Content-Type': MIME[path.extname(full)] }); res.end(data);
    } catch (error) {
      const sparse=String(req.url).startsWith('/api/sparse24/');
      json(error.status ?? 500, { error: { code: error.code ?? 'INTERNAL_ERROR', message: error.status ? error.message : '本机服务内部错误。' }, ...(sparse?{source:'BOARD',automaticReplay:false,submission_not_sent:error.noGraphSubmitted===true}:{source:'DEMO',hardwareContacted:false}) });
    }
  });
  return { server, store };
}
if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  const port = Number(process.env.KV260_UI_PORT ?? 4173);
  if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('KV260_UI_PORT must be 1024..65535');
  const { server } = createWorkbenchServer({boardUrl:process.env.KV260_UI_BOARD_URL});
  server.listen(port, '127.0.0.1', () => process.stdout.write(`KV260 LAB http://127.0.0.1:${port}/#overview\n${process.env.KV260_UI_BOARD_URL ? 'BOARD via SSH tunnel; capabilities come from current board health.' : 'Saved final results and labeled demonstrations. Hardware I/O locked.'}\n`));
  server.on('error', e => { process.stderr.write(e.message + '\n'); process.exitCode = 1; });
}
