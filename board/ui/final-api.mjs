import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {finalData} from './final-data.mjs';

const ROOT=path.resolve(fileURLToPath(new URL('../../',import.meta.url)));
const mime={'.html':'text/html; charset=utf-8','.svg':'image/svg+xml','.json':'application/json; charset=utf-8','.csv':'text/csv; charset=utf-8','.md':'text/plain; charset=utf-8','.txt':'text/plain; charset=utf-8'};
const downloads={
  '/api/final/paired.csv':'results/board500/paired195_cpu_pl.csv',
  '/api/final/comparison.csv':'results/board500/per_image_comparison.csv',
  '/api/final/timing.json':'results/board500/timing_breakdown.json'
};

export async function serveFinal(req,res,route) {
  if(route!=='/api/final' && !downloads[route] && !route.startsWith('/architecture/') && !route.startsWith('/evidence/')) return false;
  if(req.method!=='GET') {res.writeHead(405);res.end('Read-only evidence');return true;}
  try {
    if(route==='/api/final') {
      res.setHeader('Content-Type',mime['.json']);
      res.end(JSON.stringify(await finalData()));
      return true;
    }
    let relative;
    if(downloads[route]) {
      await finalData();
      relative=downloads[route];
      res.setHeader('Content-Disposition',`attachment; filename="${path.basename(relative)}"`);
    } else if(route.startsWith('/architecture/')) {
      relative='docs/diagrams/opt50/'+decodeURIComponent(route.slice('/architecture/'.length));
    } else {
      const name=route.slice('/evidence/'.length);
      if(!['measurement.md','verification.md','architecture.md'].includes(name)) throw Error('Unknown document');
      relative='docs/'+name;
    }
    const full=path.resolve(ROOT,relative),ext=path.extname(full);
    if(!full.startsWith(ROOT+path.sep)||!mime[ext]||route.startsWith('/architecture/') && !full.startsWith(path.join(ROOT,'docs/diagrams/opt50')+path.sep)) throw Error('Invalid path');
    const raw=await readFile(full);
    if(route.startsWith('/architecture/')) res.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; worker-src 'self' blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'");
    res.setHeader('Content-Type',mime[ext]);res.end(raw);
  } catch {
    res.writeHead(route.startsWith('/api/final')?503:404,{'Content-Type':mime['.json']});
    res.end(JSON.stringify({error:{code:'FINAL_EVIDENCE_UNAVAILABLE',message:'最终证据缺失、核对失败或文件不存在。'},source:'SAVED_BOARD_EVIDENCE',hardwareContacted:false}));
  }
  return true;
}
