"""CPU-only50 exact-reference check of the dynamic-block setting before owner dispatch."""
import json
from pathlib import Path
import sys

sys.path.insert(0,'/opt/opt50-demo-v1/private')
import model_runtime
from common import create,encoded,need,pin
sys.path.insert(0,str(Path(__file__).parent))
from runtime_options import pl_options


def main():
    work=Path('/var/lib/opt50-final-20261101');target=work/'dynamic-cpu-check.json'
    need(not target.exists(),'new bounded CPU validation record required')
    index=json.loads((work/'quality_inputs/index.json').read_bytes())
    checked=model_runtime.verify_bundle(work/'candidates/baseline/model');np,ort=model_runtime.dependencies()
    original=ort.InferenceSession(checked.reference_bytes,model_runtime.session_options(ort),providers=['CPUExecutionProvider'])
    candidate=ort.InferenceSession(checked.reference_bytes,pl_options(ort,1,force_stop=True,dynamic_block=True),providers=['CPUExecutionProvider'])
    selected=[index['samples'][305+group*39+i] for group in range(5) for i in range(10)]
    rows=[]
    for item in selected:
        image=np.load(item['path'],allow_pickle=False)
        old=original.run(None,{'image':image})[0];new=candidate.run(None,{'image':image})[0]
        need(old.shape==new.shape==(1,1000) and old.dtype==new.dtype==np.float32,'exact CPU layout')
        mismatches=int(np.count_nonzero(old.view(np.uint32)!=new.view(np.uint32)))
        rows.append({'ordinal':item['ordinal'],'bitwise_mismatches':mismatches,'original':pin(old.tobytes()),'candidate':pin(new.tobytes())})
        if mismatches:
            create(target,encoded({'status':'FAIL','hardware_accessed':False,'rows':rows}))
            raise ValueError('dynamic CPU numeric mismatch; no owner dispatch')
    create(target,encoded({'status':'PASS','hardware_accessed':False,'images':50,'compared_logits':50000,'bitwise_mismatches':0,
                          'runtime_options':pin(Path(__file__).with_name('runtime_options.py').read_bytes()),'rows':rows}))
    print('DYNAMIC_CPU50_EXACT_PASS_NO_HARDWARE_ACCESS',flush=True)


if __name__=='__main__':main()
