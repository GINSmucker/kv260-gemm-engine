import http from 'node:http';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createWorkbenchServer} from './server.mjs';
import {finalData} from './final-data.mjs';

const ROOT=path.resolve(fileURLToPath(new URL('../../',import.meta.url)));
const mime={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.svg':'image/svg+xml','.json':'application/json; charset=utf-8','.csv':'text/csv; charset=utf-8','.md':'text/plain; charset=utf-8','.txt':'text/plain; charset=utf-8'};
const downloads={
  '/api/final/paired.csv':'results/board500/paired195_cpu_pl.csv',
  '/api/final/comparison.csv':'results/board500/per_image_comparison.csv',
  '/api/final/timing.json':'results/board500/timing_breakdown.json'
};
export function createFinalServer() {
  const legacy=createWorkbenchServer().server;
  return http.createServer(async(req,res)=>{
    const url=new URL(req.url,'http://127.0.0.1');
    const route=url.pathname;
    if(!['127.0.0.1','localhost'].includes((req.headers.host??'').split(':')[0])) {res.writeHead(403);return res.end('Loopback only');}
    if(route==='/legacy/' || route==='/legacy') req.url='/'+url.search;
    if(!['/','/index.html','/final.js','/final.css','/api/final',...Object.keys(downloads)].includes(route) && !route.startsWith('/architecture/') && !route.startsWith('/evidence/')) return legacy.emit('request',req,res);
    res.setHeader('X-Content-Type-Options','nosniff');
    res.setHeader('Cache-Control','no-store');
    res.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'");
    if(route.startsWith('/architecture/')) res.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; worker-src 'self' blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'");
    if(req.method!=='GET') {res.writeHead(405);return res.end('Read-only evidence');}
    try {
      if(route==='/api/final') {
        res.setHeader('Content-Type',mime['.json']);
        return res.end(JSON.stringify(await finalData()));
      }
      let relative;
      if(downloads[route]) {
        await finalData();
        relative=downloads[route];
        res.setHeader('Content-Disposition',`attachment; filename="${path.basename(relative)}"`);
      } else if(route.startsWith('/architecture/')) {
        relative='docs/diagrams/opt50/'+decodeURIComponent(route.slice('/architecture/'.length));
      } else if(route.startsWith('/evidence/')) {
        const name=route.slice('/evidence/'.length);
        if(!['measurement.md','verification.md','architecture.md'].includes(name)) throw Error('Unknown document');
        relative='docs/'+name;
      } else relative='board/ui/public/'+(route==='/'||route==='/index.html'?'final.html':route.slice(1));
      const full=path.resolve(ROOT,relative),ext=path.extname(full);
      if(!full.startsWith(ROOT+path.sep)||!mime[ext]||route.startsWith('/architecture/') && !full.startsWith(path.join(ROOT,'docs/diagrams/opt50')+path.sep)) throw Error('Invalid path');
      const raw=await readFile(full);
      res.setHeader('Content-Type',route.endsWith('.csv')?'text/csv; charset=utf-8':mime[ext]??'text/csv; charset=utf-8');
      res.end(raw);
    } catch(error) {
      res.writeHead(route.startsWith('/api/final')?503:404,{'Content-Type':mime['.json']});
      res.end(JSON.stringify({status:'FAIL',message:route.startsWith('/api/final')?'最终证据缺失或核对失败。':'文件不存在。',live:false}));
    }
  });
}
if(process.argv[1] && import.meta.url===pathToFileURL(path.resolve(process.argv[1])).href) {
  const port=Number(process.env.KV260_FINAL_PORT??4179);
  if(!Number.isInteger(port)||port<1024||port>65535) throw Error('Invalid final-page port');
  await finalData();
  const server=createFinalServer();
  server.listen(port,'127.0.0.1',()=>process.stdout.write(`OPT50 final results http://127.0.0.1:${port}/\nSaved evidence; no board commands.\n`));
  server.on('error',error=>{process.stderr.write(error.message+'\n');process.exitCode=1;});
}
