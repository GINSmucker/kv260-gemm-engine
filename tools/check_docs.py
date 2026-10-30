"""Validate local Markdown link targets in current documentation, excluding frozen history."""
import json
from pathlib import Path
import re
from urllib.parse import unquote
from check_release import ROOT,need
def main():
    count=0
    files=[ROOT/'README.md',*[p/'README.md' for p in (ROOT/'hls',ROOT/'board',ROOT/'model',ROOT/'docs',ROOT/'results',ROOT/'tools')],*sorted((ROOT/'docs').glob('*.md'))]
    for path in set(files):
        for target in re.findall(r'\]\(([^\s)]+)(?:\s+[^)]*)?\)',path.read_text(encoding='utf-8')):
            if target.startswith(('https:','http:','#','mailto:')):continue
            target=unquote(target.split('#')[0]);need((path.parent/target).exists(),'missing link '+str(path)+': '+target);count+=1
    print(json.dumps({'status':'PASS','local_links_checked':count,'scope':'Current documentation only; original frozen snapshot docs retain historical paths','hardware_accessed':False}))
if __name__=='__main__':main()
