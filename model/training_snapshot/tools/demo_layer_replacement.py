"""Demonstrate an actual graph MatMul replacement, explicitly using a CPU backend."""
from pathlib import Path
import argparse,sys
sys.dont_write_bytecode=True
import numpy as np
from release_common import ROOT,read,save,session
from gemm_backend import CpuReferenceBackend,ModelLayerAdapter

def test(root=ROOT,layer=2):
    root=Path(root);base=session(root/'models/final/model_w8a8.onnx');results=[]
    for mode in [0,1]:
        backend=CpuReferenceBackend(root);adapter=ModelLayerAdapter(backend,root,layer,mode)
        for sample in read(root/'samples/samples.json'):
            x=np.load(root/f"samples/{sample['id']}_input.npy");expected=base.run(None,{base.get_inputs()[0].name:x})[0]
            observed=adapter.predict(x)
            if not np.array_equal(observed,expected):raise AssertionError(('Layer replacement logits differ',float(np.max(np.abs(observed-expected)))))
            results.append({'sample_id':sample['id'],'mode':mode,'backend':backend.name,
                'bitwise_equal_full_logits':True,'max_abs_error':0.,'top5':np.argsort(-observed[0],kind='stable')[:5].tolist()})
        adapter.close()
    return {'status':'PASS_CPU_REFERENCE_TRUE_GRAPH_LAYER_REPLACEMENT','layer_index':layer,'tests':results,
        'hardware_accessed':False,'board_layer_replacement':'NOT_RUN','no_silent_fallback':True}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=ROOT);p.add_argument('--layer',type=int,default=2)
    p.add_argument('--output',type=Path);a=p.parse_args();result=test(a.root,a.layer)
    if a.output:save(a.output,result)
    import json;print(json.dumps(result,ensure_ascii=False,indent=2))
