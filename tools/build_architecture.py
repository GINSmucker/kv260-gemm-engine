"""Build source-backed OPT50 diagrams and their portable reading index with Archify."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
from html import escape
import json
from pathlib import Path
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from report_architecture import report_figures
from architecture_readability import readable_renderer

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'docs/diagrams/opt50'


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8', newline='\n')


def digest(path):
    raw = path.read_bytes()
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def static_svg(html, notes=()):
    """Extract the delivered drawing as a light, script-free SVG snapshot."""
    drawings = re.findall(r'<svg\b.*?</svg>', html, flags=re.S)
    if len(drawings) != 1:
        raise ValueError('Expected one complete native diagram SVG')
    styles = '\n'.join(re.findall(r'<style\b[^>]*>(.*?)</style>', html, flags=re.S))
    drawing = drawings[0].replace('<svg ', '<svg xmlns="http://www.w3.org/2000/svg" data-theme="light" ', 1)
    if notes:
        bounds = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', drawing)
        if not bounds:
            raise ValueError('Expected a zero-origin SVG viewBox for static notes')
        width, height = map(float, bounds.groups())
        drawing = drawing.replace(bounds.group(0), f'viewBox="0 0 {width:g} {height+32+28*len(notes):g}"', 1)
        footer = ''.join(f'<text x="24" y="{height+30+28*i:g}" font-family="Microsoft YaHei, sans-serif" font-size="16" fill="#243d50">{escape(note)}</text>' for i,note in enumerate(notes))
        drawing = drawing.replace('</svg>', footer+'</svg>')
    head, tail = drawing.split('>', 1)
    result = head+'><style><![CDATA['+styles+']]></style><rect width="100%" height="100%" fill="#ffffff"/>'+tail
    ET.fromstring(result)
    return result


def source_links(items, revision, markdown=False):
    links = []
    for item in items:
        label = item['path']+':'+str(item['line'])
        url = f"https://github.com/GINSmucker/kv260-gemm-engine/blob/{revision}/{item['path']}#L{item['line']}-L{item['end_line']}"
        links.append(f'[{label}]({url})' if markdown else f'<a href="{escape(url)}" target="_blank" rel="noreferrer">{escape(label)}</a>')
    return ' · '.join(links)


def reading_pages(data):
    rev = data['revision']
    sections, md = [], [f"# {data['title']}", '', f'源码基准：`{rev}`。', '', data['summary'], '']
    figures = []
    for i, figure in enumerate(data['figures'], 1):
        name, title = figure['id'], figure['title']
        figures.append(f'''<article class="figure"><a href="{name}/{name}.html"><img src="report/{name}.svg" alt="{escape(title)}"/></a>
<div class="figure-body"><span class="number">{i:02}</span><h2>{escape(title)}</h2><p>{escape(figure['description'])}</p>
<div class="links"><a href="report/{name}.svg" download>报告精简版 SVG</a><a href="{name}/{name}.html">打开交互图 ↗</a><a href="{name}/{name}.html" download>下载 HTML</a><a href="{name}/{name}.svg" download>详细 SVG</a><a href="{name}/candidate.json" download>编辑源 JSON</a></div></div></article>''')
        md += [f'## {title}', '', f'[交互图](diagrams/opt50/{name}/{name}.html) · [报告精简 SVG](diagrams/opt50/report/{name}.svg) · [JSON 源](diagrams/opt50/{name}/candidate.json)', '', f'![{title}](diagrams/opt50/report/{name}.svg)', '']
    md += ['## 主要技术实现', '']
    for i, anchor in enumerate(data['anchors'], 1):
        paragraphs = ''.join(f'<p>{escape(p)}</p>' for p in anchor['paragraphs'])
        sections.append(f'<section id="anchor-{i}" class="anchor"><h3><span>{i:02}</span>{escape(anchor["title"])}</h3>{paragraphs}<div class="sources">源码：{source_links(anchor["sources"], rev)}</div></section>')
        md += [f'### {anchor["title"]}', '', *sum(([p, ''] for p in anchor['paragraphs']), []), '源码：'+source_links(anchor['sources'], rev, True), '']
    history = ''.join('<tr><td>'+escape(a)+'</td><td>'+escape(b)+'</td></tr>' for a,b in data['historical_assessment'])
    old_sources = [{'path':'docs/diagrams/architecture.json','line':1,'end_line':10}, {'path':'docs/workbench.md','line':1,'end_line':8}, {'path':'docs/version-history.md','line':1,'end_line':17}]
    md += ['## 历史图与当前版本的关系', '', '|历史资料中的内容|当前解释|', '|---|---|', *[f'|{a}|{b}|' for a,b in data['historical_assessment']], '', '来源：'+source_links(old_sources,rev,True), '', '## 使用与复现', '', '[统一阅读入口](diagrams/opt50/index.html)。点击图中节点查看源码卡片；在图右上角导出 PNG、SVG 或其他支持格式。详细 SVG 是交互图的浅色静态快照，附关键说明；报告版 SVG 依据同一源码事实精简排版，保留每图检查、批末关闭及模型范围的限定。', '', '使用本机已安装的 Archify 和 Node.js：', '', '```powershell', 'python tools/build_architecture.py', '```', '', '脚本将图表校验记录保存在忽略的 `.test_build/.archify/`，更新文档图、阅读入口及 `results/architecture/opt50.json`。依赖的固定源码提交必须在本地 Git 对象库中。', '', '## 适用范围', '', data['scope'], '', data['license'], '']
    css = '''*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:#f3f5f8;color:#13243b;font:16px/1.8 "Segoe UI","Microsoft YaHei",sans-serif}a{color:#076d74;text-underline-offset:4px}header{background:#10283e;color:#fff;padding:48px max(5vw,24px)}header .eyebrow{color:#76ddd1;letter-spacing:2px;font-size:13px}h1{font-size:34px;line-height:1.4;max-width:1100px;margin:12px 0}header p{max-width:1050px;color:#d9e4ed}code{font:13px Consolas,monospace;overflow-wrap:anywhere}.badges{display:flex;gap:10px;flex-wrap:wrap}.badges span{border:1px solid #436176;border-radius:5px;padding:4px 12px;font-size:13px}nav{display:flex;gap:24px;flex-wrap:wrap;padding:14px max(5vw,24px);background:white;border-bottom:1px solid #dce3e9;position:sticky;top:0;z-index:3}main{max-width:1440px;margin:auto;padding:32px 5vw 60px}.intro{background:#e7f3f1;border-left:4px solid #0c847b;padding:16px 22px;margin-bottom:28px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px}.figure,.anchor,.history{background:white;border:1px solid #dce3e9;border-radius:12px;overflow:hidden}.figure img{display:block;width:100%;height:270px;object-fit:contain;background:white;padding:12px}.figure-body{padding:20px 24px}.number{color:#0c847b;font:600 15px Consolas,monospace}h2{font-size:22px;margin:8px 0}p{margin:10px 0}.links{display:flex;gap:14px;flex-wrap:wrap;font-size:13px}.anchor{margin:18px 0;padding:24px 30px;scroll-margin-top:80px}.anchor h3{font-size:20px;margin:0 0 14px}.anchor h3 span{margin-right:14px;color:#0c847b;font-family:Consolas,monospace}.sources{font-size:12px;overflow-wrap:anywhere;border-top:1px solid #e4eaf0;padding-top:14px;margin-top:16px}.section-title{margin:46px 0 18px}.history{padding:22px 26px}table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:14px;text-align:left;border-bottom:1px solid #e1e7ed;vertical-align:top}th{background:#f4f7fa}footer{font-size:13px;color:#54657a;margin-top:28px}.note{color:#54657a;font-size:14px}@media(max-width:850px){.grid{grid-template-columns:1fr}h1{font-size:26px}main{padding:22px 18px}.anchor{padding:20px}nav{gap:14px}td,th{padding:9px}}@media print{nav{position:static}.grid{grid-template-columns:1fr}.figure,.anchor{break-inside:avoid}body{background:white}header{background:white;color:#13243b}header p{color:#13243b}}'''
    css = re.sub(r'font-size:(\d+)px', lambda m: 'font-size:'+str(28 if int(m[1])>=26 else 20 if int(m[1])>=20 else 16)+'px', css)
    css += '.grid{grid-template-columns:1fr}.figure>a{display:block;overflow:auto}.figure img{height:auto;min-width:1000px;padding:0}.figure-body h2{font-size:28px}code,.number{font-size:16px}'
    html = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(data['title'])}</title><style>{css}</style></head><body>
<header><div class="eyebrow">KV260 / H576 / OPT50</div><h1>{escape(data['title'])}</h1><p>{escape(data['summary'])}</p><div class="badges"><span>49 Linear · 36 PL / 13 PS</span><span>Sparse24：12D + 24S；Dense：36D</span><span>共享 512 MAC · 125 MHz</span><span>Archify 3.2.0</span></div><p class="note" style="color:#b9cbd9">固定源码：<code>{rev}</code></p></header>
<nav><a href="#figures">四张架构图</a><a href="#anchors">技术实现解析</a><a href="#history">历史图核对</a><a href="architecture.md">Markdown 文档</a></nav><main>
<div class="intro">点击框图查看交互图与源码。报告请下载精简版 SVG。</div>
<div id="figures" class="grid">{''.join(figures)}</div><h2 id="anchors" class="section-title">主要技术锚点的实现解析</h2>{''.join(sections)}
<h2 id="history" class="section-title">历史图与当前版本的关系</h2><section class="history"><table><thead><tr><th>历史资料中的内容</th><th>当前解释</th></tr></thead><tbody>{history}</tbody></table><div class="sources">依据：{source_links(old_sources,rev)}</div></section>
<footer><p>{escape(data['scope'])}</p><p>{escape(data['license'])} 查看 <a href="LICENSE.archify.txt">Archify 许可证</a>、<a href="NOTICE.archify.txt">NOTICE</a>、<a href="THIRD_PARTY_NOTICES.archify.md">第三方说明</a>。</p></footer></main></body></html>'''
    return '\n'.join(md), html


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archify-cli', type=Path, default=Path.home()/'.codex/skills/archify/bin/archify.mjs')
    args = parser.parse_args()
    cli = args.archify_cli.resolve()
    if not cli.is_file() or not shutil.which('node'):
        raise RuntimeError('An existing Archify installation and Node.js are required')
    data = json.loads((DEST/'analysis.json').read_text(encoding='utf-8'))
    for anchor in data['anchors']:
        for source in anchor['sources']:
            result = subprocess.run(['git','show',data['revision']+':'+source['path']], cwd=ROOT, capture_output=True, check=True)
            if not 1 <= source['line'] <= source['end_line'] <= len(result.stdout.splitlines()):
                raise ValueError('Explanation source range is outside the pinned file: '+source['path'])
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d-%H%M%S-%f')
    run = ROOT/'.test_build/.archify'/('architecture-opt50-'+stamp)
    run.mkdir(parents=True)
    cli = readable_renderer(cli, run/'archify-readable')
    records = []
    for figure in data['figures']:
        name = figure['id']
        source = DEST/name/'candidate.json'
        spec = json.loads(source.read_text(encoding='utf-8'))
        if spec['meta']['repository']['revision'] != data['revision']:
            raise ValueError('Diagram and explanation revisions differ')
        output = run/name/(name+'.html')
        result = subprocess.run(['node', str(cli), 'finalize', spec['diagram_type'], str(source), str(output), '--repo-root', str(ROOT), '--quality', 'showcase', '--json'], cwd=run, capture_output=True)
        (run/(name+'-console.json')).write_bytes(result.stdout)
        (run/(name+'-stderr.log')).write_bytes(result.stderr)
        receipt = json.loads(result.stdout)
        if result.returncode or not receipt.get('ok') or any(value!='pass' for value in receipt['gates'].values()):
            raise RuntimeError(f'{name}: Archify failed; see {run}')
        shutil.copyfile(output, DEST/name/(name+'.html'))
        (DEST/name/(name+'.svg')).write_text(static_svg(output.read_text(encoding='utf-8'),figure.get('static_notes',[])), encoding='utf-8', newline='\n')
        records.append({'diagram':name,'gates':receipt['gates'],'html':digest(DEST/name/(name+'.html')),'svg':digest(DEST/name/(name+'.svg')),'candidate':digest(source),'receipt':str(Path(receipt['evidence']['receipt']).relative_to(ROOT)).replace('\\','/')})
    for original, target in [('LICENSE','LICENSE.archify.txt'),('LICENSE-MIT','LICENSE-MIT.archify.txt'),('NOTICE','NOTICE.archify.txt'),('THIRD_PARTY_NOTICES.md','THIRD_PARTY_NOTICES.archify.md')]:
        shutil.copyfile(cli.parent.parent/original, DEST/target)
    report_records = []
    (DEST/'report').mkdir(exist_ok=True)
    for name, drawing in report_figures(data).items():
        ET.fromstring(drawing)
        report_path = DEST/'report'/(name+'.svg')
        report_path.write_text(drawing, encoding='utf-8', newline='\n')
        report_records.append({'diagram':name, 'svg':digest(report_path)})
    markdown, html = reading_pages(data)
    (ROOT/'docs/architecture.md').write_text(markdown, encoding='utf-8', newline='\n')
    (DEST/'architecture.md').write_text(markdown.replace('(diagrams/opt50/', '('), encoding='utf-8', newline='\n')
    (DEST/'index.html').write_text(html, encoding='utf-8', newline='\n')
    save_json(ROOT/'results/architecture/opt50.json', {'schema':'opt50_architecture_artifacts_v1','status':'PASS','scope':'SOURCE_DIAGRAM_ARTIFACTS_AND_AUTOMATED_BROWSER_ONLY','source_revision':data['revision'],'archify_version':json.loads((cli.parent.parent/'package.json').read_bytes())['version'],'presentation':{'profile':'kv260-readable-16-20-28','renderer_adapter':digest(ROOT/'tools/architecture_readability.py'),'stylesheet':digest(ROOT/'tools/architecture_readability.css'),'isolated_renderer':True},'hardware_accessed':False,'HLS_RTL_Vivado_board':'NOT RUN','diagrams':records,'report_figures':report_records,'entry':digest(DEST/'index.html')})
    print(json.dumps({'status':'PASS','entry':str(DEST/'index.html'),'evidence_directory':str(run),'diagrams':len(records)},ensure_ascii=False))


if __name__ == '__main__':
    main()
