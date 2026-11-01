"""Apply the project's readable presentation to an isolated Archify renderer."""
from pathlib import Path
import json
import re
import shutil


def readable_renderer(cli, destination):
    package = cli.parent.parent
    if json.loads((package / 'package.json').read_bytes())['version'] != '3.2.0':
        raise ValueError('The readable presentation requires Archify 3.2.0')
    shutil.copytree(package, destination)
    template = destination / 'assets/template.html'
    text = template.read_text(encoding='utf-8')

    def fonts(match):
        css = re.sub(r'font-size:\s*([\d.]+)(rem|px)',
                     lambda m: 'font-size: ' + ('28px' if float(m[1]) * (16 if m[2] == 'rem' else 1) >= 24 else '20px' if float(m[1]) * (16 if m[2] == 'rem' else 1) >= 18 else '16px'), match[0])
        return css

    text = re.sub(r'<style\b[^>]*>.*?</style>', fonts, text, flags=re.S)
    css = (Path(__file__).with_name('architecture_readability.css')).read_text(encoding='utf-8')
    head, tail = text.rsplit('</style>', 1)
    text = head + css + '\n</style>' + tail
    template.write_text(text, encoding='utf-8', newline='\n')
    for relative in ['architecture/render-architecture.mjs', 'workflow/workflow-compiler.mjs']:
        renderer = destination / 'renderers' / relative
        text = renderer.read_text(encoding='utf-8')
        text = text.replace('labelPreferred: 11', 'labelPreferred: 20').replace('labelMinimum: 9', 'labelMinimum: 20')
        text = text.replace('sublabelPreferred: 9', 'sublabelPreferred: 16').replace('sublabelPreferred: 8', 'sublabelPreferred: 16')
        text = text.replace('sublabelMinimum: 6', 'sublabelMinimum: 16')
        text = text.replace('data-reader-min-text="7.5"', 'data-reader-min-text="16"').replace('data-reader-primary-text="14"', 'data-reader-primary-text="20"')
        text = text.replace('data-reader-fit="intrinsic-height"', 'data-reader-fit="width-first"')
        if relative.startswith('workflow/'):
            text = re.sub(r"  const readerFit = workflow.schema_version === 2.*?    : '';",
                          '  const readerFit = \' data-reader-fit="width-first" data-reader-min-text="16" data-reader-primary-text="20"\';', text, count=1, flags=re.S)
        text = text.replace('brandLabelFitWidth(c, c.width), 11, 8', 'brandLabelFitWidth(c, c.width), 20, 20')
        text = text.replace('font-size="10" font-weight="600">${prefix}', 'font-size="16" font-weight="600">${prefix}')
        text = text.replace('c.height / 2 - 2', 'c.height / 2 - 4').replace('c.height / 2 + 14', 'c.height / 2 + 22')
        text = text.replace('* 4.8 + 10', '* 9.6 + 16')
        text = text.replace('y: ly - 10, width, height: 14', 'y: ly - 18, width, height: 26')
        text = text.replace('font-size="8" text-anchor="middle">${esc(', 'font-size="16" text-anchor="middle">${esc(')
        text = text.replace('y="${ly - 10}" width="${labelW}" height="14"', 'y="${ly - 18}" width="${labelW}" height="26"')
        if relative.startswith('workflow/'):
            text = text.replace('font-size="16" font-weight="600">${prefix}', 'font-size="20" font-weight="600">${prefix}')
        renderer.write_text(text, encoding='utf-8', newline='\n')
    return destination / 'bin/archify.mjs'
