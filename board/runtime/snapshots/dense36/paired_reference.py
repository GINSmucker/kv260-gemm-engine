"""Bind Dense inputs to the completed Sparse500 evidence; no device access."""
from pathlib import Path
import hashlib
import io
import json
import tarfile

ROOT=Path('/home/ubuntu/fpga_project/opt50_integration/b24_acceptance500_reboot_exports_20261030_v1/action_a77e3fa15328')
ARCHIVE_SHA='f14b1a7e3ec26fd7205e4b32ca57b0ae5ab55972cbcb353fbcb406003bc182fb'
DATASET={'bytes':807589,'sha256':'2fd9915aa9425563869467aa0c1cf9e3aba62bd933c07cf2fdb14b4b4b84e796'}
def need(ok,why):
    if not ok:raise ValueError(why)
def pin(raw):return {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
def paired_reference_proof(a,path):
    root=Path(path).absolute();need(root==ROOT,'fixed completed Sparse500 reference required')
    raw=a.c.read_file(root/'evidence.tar.gz',limit=512*1024*1024)
    need(pin(raw)['sha256']==ARCHIVE_SHA,'completed Sparse500 archive changed')
    summary=a.c.read_file(root/'summary.json',limit=2*1024*1024)
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as t:
        m=t.getmember('report/summary.json');need(m.isfile() and not m.issym() and not m.islnk(),'ordinary original report member')
        need(t.extractfile(m).read()==summary,'Sparse500 report/archive binding differs')
    r=a.c.decode_json(summary)
    need(r.get('status')=='SUBSET500_NUMERICAL_AND_LABELED_ACCURACY_VERIFIED' and r.get('target_500_complete') is True and
         r.get('images')==500 and r.get('ordinal_start')==0 and r.get('ordinal_stop_exclusive')==500 and
         r.get('bitwise_mismatches')==0 and r.get('compared_FP32_values_bitwise')==500000 and
         r.get('source_model_sha256')=='ea5017791e47dbefe2b06178767f1cf59c9eb75b8cf0871f3173fb2cc4ff4d43' and
         r.get('dataset_manifest')==DATASET,'Sparse500 original acceptance scope differs')
    return {'schema':'opt50_dense_paired_sparse500_reference_v1','report_root':str(root),'summary':pin(summary),'archive':pin(raw),
        'dataset_manifest':DATASET,'paired_ordinals':[0,500],'Sparse_reference_only':True,'current_hardware_residency_proved_by_this_receipt':False}
