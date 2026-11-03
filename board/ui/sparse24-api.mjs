import {AppError} from './domain.mjs';

export async function serveSparse24(req,res,url,client,readJson) {
  const path=url.pathname;
  if(!path.startsWith('/api/sparse24/'))return false;
  const json=(status,value)=>{res.writeHead(status,{'Content-Type':'application/json; charset=utf-8'});res.end(JSON.stringify({source:'BOARD',protocol:'sparse24',...value}));};
  if(path==='/api/sparse24/connection'&&req.method==='GET'){json(200,client.configured());return true;}
  if(path==='/api/sparse24/connect'&&req.method==='POST'){const data=await readJson(req);json(200,await client.connect(data.endpoint));return true;}
  if(path==='/api/sparse24/status'&&req.method==='GET'){json(200,await client.status());return true;}
  if(path==='/api/sparse24/history'&&req.method==='GET'){json(200,await client.history());return true;}
  const result=path.match(/^\/api\/sparse24\/result\/([0-9a-f]{32})$/);
  if(result&&req.method==='GET') {const value=await client.result(result[1],{boot_id:url.searchParams.get('boot')??undefined,image_sha256:url.searchParams.get('image')??undefined});if(url.searchParams.get('download')==='1')res.setHeader('Content-Disposition',`attachment; filename="sparse24-${result[1]}.json"`);json(200,value);return true;}
  const infer=path.match(/^\/api\/sparse24\/infer\/([0-9a-f]{32})$/);
  if(infer&&req.method==='POST') {
    if(req.headers['content-type']!=='image/jpeg')throw new AppError('JPEG_REQUIRED','请选择 JPEG 图片。',415);
    let size=0;const chunks=[];
    for await(const chunk of req){size+=chunk.length;if(size>8*1024*1024)throw new AppError('IMAGE_TOO_LARGE','JPEG 不得超过 8 MiB。',413);chunks.push(chunk);}
    json(202,await client.submit(infer[1],Buffer.concat(chunks),req.headers['x-sparse24-boot']));return true;
  }
  throw new AppError('NOT_FOUND','Sparse24 接口不存在或方法不支持。',404);
}
