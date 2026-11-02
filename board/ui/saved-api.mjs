import path from 'node:path';
import {loadSavedEvidence} from './saved-data.mjs';

export async function serveSaved(req,res,route) {
  if(route!=='/api/saved'&&!route.startsWith('/api/saved/'))return false;
  res.setHeader('Content-Type','application/json; charset=utf-8');
  if(req.method!=='GET'){res.writeHead(405);res.end(JSON.stringify({error:{code:'READ_ONLY_SAVED_EVIDENCE'},hardwareContacted:false}));return true;}
  try {
    const {data,files}=await loadSavedEvidence();
    if(route==='/api/saved'){res.end(JSON.stringify(data));return true;}
    const key=route.slice('/api/saved/files/'.length);
    if(!route.startsWith('/api/saved/files/')||!files.has(key)){res.writeHead(404);res.end(JSON.stringify({error:{code:'SAVED_FILE_NOT_FOUND'}}));return true;}
    const spec=data.files[key],ext=path.extname(spec.path);
    res.setHeader('Content-Type',ext==='.jpg'?'image/jpeg':ext==='.md'?'text/plain; charset=utf-8':'application/json; charset=utf-8');
    if(new URL(req.url,'http://localhost').searchParams.get('download')==='1')res.setHeader('Content-Disposition',`attachment; filename="${path.basename(spec.path)}"`);
    res.end(files.get(key));
  } catch {
    res.writeHead(503);res.end(JSON.stringify({error:{code:'SAVED_EVIDENCE_UNAVAILABLE',message:'实板存档缺失或身份核对失败；不以DEMO替代。'},source:'SAVED_BOARD_EVIDENCE',hardwareContacted:false}));
  }
  return true;
}
