"""Restore one exact frozen bundle from Git text plus a separately pinned asset tar."""
import argparse
import json
from pathlib import Path
import shutil
import tarfile
from check_release import ROOT,need,pin,safe

def restore(name,out,asset=None):
    lock=json.loads((ROOT/'release_sources.json').read_bytes());need(name in lock['bundles'],'known snapshot required')
    b=lock['bundles'][name];snapshot=ROOT/'board/runtime/snapshots'/name;out=Path(out).absolute()
    need(not out.exists() and not out.is_symlink() and out.parent.is_dir(),'exclusive NEW output under an existing parent required')
    need(not out.is_relative_to(ROOT/'board/runtime/snapshots'),'do not write into frozen Git sources')
    payload={}
    for rel,wanted in b['stored_files'].items():
        raw=safe(snapshot,rel).read_bytes();need(pin(raw)==wanted,'stored source changed');payload[rel]=raw
    if b['external_files']:
        need(asset is not None,'this bundle requires its external model/case asset')
        assets=json.loads((ROOT/'assets_manifest.json').read_bytes())['assets'];filename=name+'-payload.tar.gz'
        need(filename in assets and pin(Path(asset).read_bytes())==assets[filename]['identity'],'exact declared external asset required')
        with tarfile.open(asset,'r:gz') as t:
            members=t.getmembers();need(len(members)==len({m.name for m in members}) and {m.name for m in members}==set(b['external_files']),'exact external member inventory required')
            for m in members:
                need(m.isfile() and not m.issym() and not m.islnk() and m.size<=64*1024*1024,'regular bounded asset member required')
                raw=t.extractfile(m).read();need(pin(raw)==b['external_files'][m.name],'external payload changed');payload[m.name]=raw
    need(set(payload)==set(b['complete_payload_files']),'complete bundle inventory required')
    out.mkdir(mode=0o700)
    for rel,raw in payload.items():
        path=safe(out,rel);path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('xb') as f:f.write(raw)
    for rel,wanted in b['complete_payload_files'].items():need(pin(safe(out,rel).read_bytes())==wanted,'restored source differs')
    return {'status':'PASS','snapshot':name,'restored_files':len(payload),'out':str(out),'hardware_accessed':False,
        'scope':'Exact source/asset restoration only. Original boot,path,compiler and history constraints remain.'}
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--snapshot',required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--asset',type=Path);a=p.parse_args()
    print(json.dumps(restore(a.snapshot,a.out,a.asset),indent=2))
if __name__=='__main__':main()
