// Loopback-only form for saving CUA observations and its JPEG screenshot.
// This is a test artifact sink, separate from the product and the PL protocol.
import http from 'node:http';
import { mkdir, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';

const digest = data => createHash('sha256').update(data).digest('hex');
export async function createEvidenceSink(output, accept) {
  await mkdir(output, { recursive: true });
  const server = http.createServer(async (req, res) => {
    const send = (code, content) => {
      res.writeHead(code, { 'Content-Type': 'text/html; charset=utf-8' });
      res.end(content);
    };
    if (req.url === '/' && req.method === 'GET') return send(200,
      '<!doctype html><html lang="zh"><meta charset="utf-8"><title>本机浏览器取证</title>' +
      '<h1>本机浏览器取证</h1><p>将实际浏览器观察与截图保存到当前测试窗口。</p>' +
      '<form method="post" action="/save"><label for="payload">取证内容</label>' +
      '<textarea id="payload" name="payload" rows="8" cols="80"></textarea>' +
      '<button type="submit">保存本机证据</button></form></html>');
    if (req.url !== '/save' || req.method !== 'POST') return send(404, 'Not found');
    try {
      const chunks = []; let bytes = 0;
      for await (const chunk of req) {
        bytes += chunk.length; assert(bytes <= 2 * 1024 * 1024, 'evidence body too large'); chunks.push(chunk);
      }
      const body = JSON.parse(new URLSearchParams(Buffer.concat(chunks).toString()).get('payload'));
      assert(/^board_[a-f0-9]{12}$/.test(body.browser_job_id));
      assert(Array.isArray(body.observations) && body.observations.length >= 3);
      assert(body.observations.every(v => typeof v === 'string'));
      const jpeg = Buffer.from(body.screenshot_base64, 'base64');
      assert(jpeg.length > 1000 && jpeg[0] === 0xff && jpeg[1] === 0xd8, 'JPEG screenshot required');
      const observations = JSON.stringify(body.observations, null, 2);
      const record = { schema: 'CUA_actual_browser_capture_v1', status: 'OBSERVED_ONLY_NOT_GATE',
        browser_job_id: body.browser_job_id, observation_sha256: digest(observations),
        screenshot_sha256: digest(jpeg), screenshot_file: 'browser_success.jpg',
        observation_file: 'browser_observations.json', client_utc: new Date().toISOString() };
      // The caller derives a gate only after validating live job and HTTP exports.
      const verification = accept ? await accept(body, record) : null;
      await writeFile(path.join(output, record.observation_file), observations, { flag: 'wx' });
      await writeFile(path.join(output, record.screenshot_file), jpeg, { flag: 'wx' });
      await writeFile(path.join(output, 'browser_capture_receipt.json'), JSON.stringify(record, null, 2), { flag: 'wx' });
      if (verification) await writeFile(path.join(output, 'browser_verification.json'), JSON.stringify(verification, null, 2), { flag: 'wx' });
      send(200, '<h1>证据已保存</h1><p>截图和原始浏览器观察已写入本机当前窗口。</p>');
    } catch (err) {
      console.error('Browser capture failed:', err.message);
      send(400, '<h1>保存失败</h1><p>查看本机取证服务错误；本次不签发 PASS。</p>');
    }
  });
  await new Promise(resolve => server.listen(4175, '127.0.0.1', resolve));
  return server;
}
if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  const output = path.resolve(process.argv[2]);
  await createEvidenceSink(output);
  console.log('Local screenshot/observations form http://127.0.0.1:4175');
}
