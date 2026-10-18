import {createHash} from 'node:crypto';
import {appendFile,mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {createWorkbenchServer} from './server.mjs';

const folder=process.argv[2];
if(!folder)throw new Error('An explicit local evidence directory is required');
const out=path.resolve(folder);
await mkdir(out,{recursive:true});
const port=Number(process.env.KV260_UI_PORT??4174);
const {server}=createWorkbenchServer({boardUrl:process.env.KV260_UI_BOARD_URL});
let writes=Promise.resolve();
server.on('request',(req,res)=>{
  const end=res.end;
  let body;
  res.end=function(data,...args){body=data;return end.call(this,data,...args);};
  res.once('finish',()=>{
    const record={client_utc:new Date().toISOString(),method:req.method,url:req.url,status:res.statusCode,user_agent:req.headers['user-agent']??''};
    if(body!==undefined)record.response_sha256=createHash('sha256').update(body).digest('hex');
    writes=writes.then(()=>appendFile(path.join(out,'http.jsonl'),JSON.stringify(record)+'\n'));
  });
});
server.listen(port,'127.0.0.1',()=>console.log(`Browser acceptance http://127.0.0.1:${port}; ${process.env.KV260_UI_BOARD_URL?'BOARD':'DEMO preflight'}`));
server.on('error',error=>{console.error(error.message);process.exitCode=1;});
async function close(){
  server.closeAllConnections();
  await new Promise(resolve=>server.close(resolve));
  await writes;
  await writeFile(path.join(out,'server_closed.json'),JSON.stringify({status:'CLOSED',client_utc:new Date().toISOString()})+'\n');
}
process.once('SIGINT',()=>{void close();});
process.once('SIGTERM',()=>{void close();});
