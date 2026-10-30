"""Copy the exact parent24 weights and masks, with no new pruning."""
import shutil
from large24_common import *

def main():
    check_policy();assert not (EXP/'construction.json').exists()
    initial=EXP/'candidates/fixed24/initial';(initial/'weights').mkdir(parents=True,exist_ok=True)
    layers=read(PARENT/'layers.json')
    assert tuple(l['layer_index'] for l in layers if l['weights'])==SELECTED
    for name in ['model_fp32.onnx','model_w8a8.onnx','layers.json']:
        shutil.copyfile(PARENT/name,initial/name);assert sha(initial/name)==sha(PARENT/name)
    for l in layers:
        rel=l['weights'] or l['dense_weights']
        assert Path(rel).parts[0]=='weights'
        shutil.copyfile(PARENT/rel,initial/rel);assert sha(PARENT/rel)==sha(initial/rel)
    save(EXP/'construction.json',{'status':'PASS','no_new_pruning':True,
        'parent_fp32_sha256':EXPECTED_FP,'parent_w8a8_sha256':EXPECTED_Q,
        'all24_masks_inherited_exactly':True,'initial_model_byte_identical':True,
        'candidates':[{'id':'fixed24','added_layers':[],'selected_layers':list(SELECTED),
            'initial_folder':str(initial),'hashes':{n:sha(initial/n) for n in ['model_fp32.onnx','model_w8a8.onnx']}}]})
    print('COPIED parent24 exactly; no re-pruning or new sparse layers',flush=True)

if __name__=='__main__':main()
