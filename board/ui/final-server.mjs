import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {createWorkbenchServer} from './server.mjs';
import {finalData} from './final-data.mjs';

export function createFinalServer() {
  return createWorkbenchServer().server;
}

if(process.argv[1] && import.meta.url===pathToFileURL(path.resolve(process.argv[1])).href) {
  const port=Number(process.env.KV260_FINAL_PORT??4179);
  if(!Number.isInteger(port)||port<1024||port>65535) throw Error('Invalid workbench port');
  await finalData();
  const server=createFinalServer();
  server.listen(port,'127.0.0.1',()=>process.stdout.write(`KV260 LAB http://127.0.0.1:${port}/#overview\nSaved final evidence; labeled demonstrations; no automatic board commands.\n`));
  server.on('error',error=>{process.stderr.write(error.message+'\n');process.exitCode=1;});
}
