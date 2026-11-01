"""Package the verified final model-only pair, original rollback pair and raw evidence; no hardware access."""
import argparse,hashlib,json,subprocess,tarfile,zipfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args()
repo=Path.cwd();work=repo/'.test_build/final-opt-20261101'
assert not a.out.exists()
assert subprocess.check_output(['git','status','--porcelain']).strip()==b''
head=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
manifest={'schema':'opt50_final_model_only_delivery_v1','status':'PASS','source_commit':head,'default_profile':'exact-run-scoped-spin-model-only-v1','requires_existing_verified_opt50_demo_install':True,'automatic_hardware_start':False,'files':{}}
def pin(raw):return {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
with zipfile.ZipFile(a.out,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
 def put(name,raw):
  assert name not in manifest['files']
  z.writestr(name,raw,compress_type=zipfile.ZIP_STORED if name.endswith(('.tar.gz','.zip')) else zipfile.ZIP_DEFLATED)
  manifest['files'][name]=pin(raw)
 for name in subprocess.check_output(['git','ls-files','-z']).decode().split('\0'):
  if name:put('source/'+name,(repo/name).read_bytes())
 with tarfile.open(work/'final-service-quality/service-quality-evidence.tar.gz') as t:
  files={m.name:t.extractfile(m).read() for m in t if m.isfile()}
 final_raw=(work/'final-install-receipt.json').read_bytes();current=json.loads(final_raw);old_raw=files['original_service/install_receipt.json'];old=json.loads(old_raw)
 assert current['final_optimization']['final_adoption'] is True
 assert current['files']==json.loads(files['installed/install_receipt.json'])['files']
 for prefix,receipt in [('current',current),('original',old)]:
  for name,wanted in receipt['files'].items():
   key=('original_service/'+name) if prefix=='original' and 'original_service/'+name in files else 'installed/'+name
   raw=files[key];assert pin(raw)==wanted
   put('runtime/'+prefix+'/'+name,raw)
  put('runtime/'+prefix+'/install_receipt.json',final_raw if prefix=='current' else old_raw)
 put('runtime/original/pairing.json',files['original_service/pairing.json'])
 assets=json.loads((repo/'assets_manifest.json').read_bytes())['assets']
 baseline_paths=sorted((repo/'.test_build/submission-20261101/assets').glob('*.tar.gz'))
 assert {path.name for path in baseline_paths}=={'dense36-payload.tar.gz','fd96539d-platform.tar.gz','layer02-payload.tar.gz','paired500-evidence.tar.gz','sparse24-payload.tar.gz'}
 for path in baseline_paths:
  raw=path.read_bytes();assert pin(raw)==assets[path.name]['identity']
  put('baseline-assets/'+path.name,raw)
 evidences=[work/'resident-final-evidence.tar.gz',work/'diagnostic-evidence.tar.gz',work/'http-full-evidence.tar.gz',work/'partial-b1-evidence.tar.gz',work/'partial-a2-evidence.tar.gz',work/'final-service-quality/service-quality-evidence.tar.gz']
 for folder in ('requests-original-a0','requests-original-a0-restored','requests-optimized-b1-complete','requests-original-a2-complete','requests-optimized-b2','requests-model-only-final'):
  evidences.append(work/folder/'request-evidence.tar.gz')
 expected={work/'resident-final-evidence.tar.gz':json.loads((repo/'results/final_optimization/20261101/model_review.json').read_bytes())['archive'],work/'final-service-quality/service-quality-evidence.tar.gz':json.loads((repo/'results/final_optimization/20261101/service_quality_review.json').read_bytes())['archive']}
 for path in evidences:
  raw=path.read_bytes()
  if path in expected:assert pin(raw)==expected[path]
  put('evidence/'+str(path.relative_to(work)).replace('\\','/'),raw)
 for name in ('scheduler-context2.txt','scheduler-tail-context2.txt','clean-tail.json','three-arm-summary.json','environment.txt','pre-request-identity.json','verified-archive-for-compaction.json'):
  put('evidence/context/'+name,(work/name).read_bytes())
 for name in ('adoption-review.json','adoption-receipt.json','final-install-receipt.json','final-status.json'):
  put('evidence/'+name,(work/name).read_bytes())
 put('交付说明.txt',(repo/'results/final_optimization/20261101/交付说明.txt').read_bytes())
 z.writestr('delivery_manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
with zipfile.ZipFile(a.out) as z:
 assert z.testzip() is None
 for name,wanted in manifest['files'].items():assert pin(z.read(name))==wanted
raw=a.out.read_bytes();digest=hashlib.sha256(raw).hexdigest()
a.out.with_suffix(a.out.suffix+'.sha256').write_text(digest+'  '+a.out.name+'\n',encoding='utf-8',newline='\n')
a.out.with_suffix('.verification.json').write_text(json.dumps({'status':'PASS','archive':pin(raw),'members_verified':len(manifest['files']),'source_commit':head,'runtime_profile':manifest['default_profile'],'hardware_accessed':False},indent=2)+'\n',encoding='utf-8',newline='\n')
print(json.dumps({'status':'PASS','archive':str(a.out),'bytes':len(raw),'sha256':digest,'verified_members':len(manifest['files'])}))
