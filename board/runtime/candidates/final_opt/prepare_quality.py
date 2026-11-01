"""Reconstruct only the fixed first500 tensors from their actual, pinned original JPEGs."""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import sys


def main():
    work=Path('/var/lib/opt50-final-20261101')
    pins=json.loads((work/'dataset_source/pins.json').read_bytes())
    if set(pins)!= {'dataset.py','data/acceptance.csv'}:raise ValueError('exact frozen preprocessing sources required')
    for name,expected in pins.items():
        raw=(work/'dataset_source'/name).read_bytes()
        if {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}!=expected:raise ValueError('dataset source changed')
    spec=importlib.util.spec_from_file_location('frozen_quality_dataset',work/'dataset_source/dataset.py')
    dataset=importlib.util.module_from_spec(spec);spec.loader.exec_module(dataset)
    original=Path('/home/ubuntu/fpga_project/opt50_integration/b24_accuracy_ready_20261028_v1/dataset')
    checked=dataset.verify_dataset(original,verify_payloads=False)
    output=work/'quality_inputs';os.umask(0o077);output.mkdir(mode=0o700,exist_ok=False)
    rows=[]
    for sample in checked['manifest']['samples'][:500]:
        raw=dataset.materialize_input(original,sample)
        name='%04d.npy'%sample['ordinal']
        with (output/name).open('xb') as stream:stream.write(raw)
        rows.append({'id':sample['id'],'ordinal':sample['ordinal'],'label':sample['label'],
                     'path':str(output/name),'image':sample['image'],'input':sample['input']})
        if len(rows)%50==0:print(json.dumps({'prepared':len(rows),'total':500}),flush=True)
    result={'schema':'opt50_final_fixed500_inputs_v1','status':'PASS','count':500,'hardware_executed':False,
            'dataset_manifest':checked['manifest_pin'],'samples':rows}
    (output/'index.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'status':'PASS','count':500,'dataset_manifest':checked['manifest_pin']}),flush=True)


if __name__=='__main__':main()
