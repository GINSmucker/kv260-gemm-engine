"""Read only Dense36 records from this exact release; no foreign versions."""
from pathlib import Path
import re
import types
from verify_tools import need,identity,decode,read
RECORDS=Path('/var/lib/opt50-dense-model-v1')
def load_closed_entry(plan_path,current_tools,*,access=None):
    path=Path(plan_path).absolute();root=path.parent
    need(path.name=='plan.json' and root.parent==RECORDS and re.fullmatch('plan_[a-f0-9]{12}',root.name),'canonical Dense plan')
    need(access is not None,'strict private record access required')
    for d in (RECORDS,root,root/'tools',root/'model'):access.private_dir(d)
    raw=access.read_file(root/'tools/tools_manifest.json',private=True)
    need(identity(raw)==current_tools['manifest'],'Dense source release differs')
    v=decode(raw);need(v['files']==current_tools['files'],'Dense source inventory differs')
    for n,p in v['files'].items():
        target=root/n if n.startswith('model/') else root/'tools'/n
        need(identity(access.read_file(target,private=True))==p,'private Dense file differs: '+n)
    code=access.read_file(root/'tools/run_model.py',private=True)
    entry=types.ModuleType('_dense_closed_'+root.name);entry.__file__=str(root/'tools/run_model.py')
    exec(compile(code,entry.__file__,'exec'),entry.__dict__)
    return entry,root,{'release':'current','tools_manifest':identity(raw)}
