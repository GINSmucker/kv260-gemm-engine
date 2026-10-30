"""Unpack the fixed release data into mutable work/, verifying bytes and safe paths."""
from pathlib import Path
import argparse,csv,io,hashlib,shutil,sys,zipfile
sys.dont_write_bytecode=True
from release_common import ROOT,read,inside,sha

def unpack(root,role,destination,external_archive=None):
    root=Path(root).resolve();destination=Path(destination).resolve()
    if destination.exists():raise FileExistsError('Use a fresh destination; partial/previous datasets are not overwritten')
    if destination.is_relative_to(root) and not destination.is_relative_to(root/'work'):
        raise ValueError('Inside the release, extraction is allowed only under work/')
    info=read(root/'datasets/archives.json')[role]
    if external_archive is None and not info.get('packaged',True):
        raise FileNotFoundError('Image ZIP is omitted from this lean package. Supply --archive PATH to the original archive; see docs/REPRODUCE.md')
    archive=Path(external_archive).resolve() if external_archive is not None else inside(root,info['path'])
    if sha(archive)!=info['sha256']:raise ValueError('Archive SHA256 mismatch')
    labels=inside(root,info['labels_path']).read_bytes()
    records=list(csv.DictReader(io.StringIO(labels.decode('utf-8-sig'))))
    allowed={r['image']:r for r in records}
    if len(allowed)!=info['images'] or 'labels.csv' in allowed:raise ValueError('Duplicate/bad labels')
    with zipfile.ZipFile(archive) as z:
        names=z.namelist()
        if len(names)!=len(set(names)) or set(names)!=set(allowed)|{'labels.csv'}:raise ValueError('Archive member set mismatch')
        if z.read('labels.csv')!=labels:raise ValueError('Archive labels mismatch')
        for item in z.infolist():
            inside(destination,item.filename)
            if ((item.external_attr>>16)&0o170000)==0o120000:raise ValueError('Symlink forbidden')
        destination.mkdir(parents=True)
        for i,r in enumerate(records):
            b=z.read(r['image'])
            if hashlib.sha256(b).hexdigest()!=r['image_sha256']:raise ValueError('Image SHA mismatch: '+r['image'])
            p=inside(destination,r['image']);p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b)
            if (i+1)%10000==0:print('UNPACK',role,i+1,flush=True)
        (destination/'labels.csv').write_bytes(labels)
    print('PASS',role,len(records),'images ->',destination)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('role',choices=['train','tune','acceptance'])
    p.add_argument('--root',type=Path,default=ROOT);p.add_argument('--destination',type=Path);p.add_argument('--archive',type=Path)
    a=p.parse_args();unpack(a.root,a.role,a.destination or a.root/'work/data'/a.role,a.archive)
