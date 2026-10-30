"""Recover a mkdir race: preserve frozen scoring code and reuse verified completed inference."""
from pathlib import Path
from datetime import datetime,timezone,timedelta
import os
import numpy as np
import select_large24 as scoring
from large24_common import EXP,ROOT,read,save,sha

def main():
    assert not (EXP/'selection.json').exists()
    policy=read(EXP/'policy.json')
    assert sha(ROOT/'scripts/select_large24.py')==policy['scripts_sha256']['select_large24.py']
    cached={}
    for p in (EXP/'development').rglob('result.json'):
        result=read(p);z=p.parent/'logits.npz'
        assert sha(z)==result['logits_sha256']
        assert sha(result['model_path'])==result['model_sha256']
        cached[os.path.normcase(str(Path(result['model_path']).resolve()))]=(result,z)
    # Sequentially create all directories before threaded jobs can enter mkdir.
    for r in [*[f'fixed24/epoch{i}' for i in range(6)],'parent24/control','dense/control']:
        (EXP/'development'/r).mkdir(parents=True,exist_ok=True)
    original_inputs=scoring.inputs;original_infer=scoring.infer;identity={};events=[]
    def checked_inputs(folder):
        x,y,rows,h=original_inputs(folder);identity.update(labels=y,inputs_sha256=h)
        return x,y,rows,h
    def checked_infer(path,x,tag=''):
        key=os.path.normcase(str(Path(path).resolve()))
        if key in cached:
            result,npz=cached[key]
            assert result['preprocessed_inputs_sha256']==identity['inputs_sha256']
            with np.load(npz) as z:
                assert np.array_equal(z['labels'],identity['labels']);values=z['logits'].copy()
            assert values.shape==(len(x),1000) and np.isfinite(values).all()
            events.append({'model':str(path),'action':'reuse_verified_logits','original_logits_sha256':sha(npz)})
            return values
        events.append({'model':str(path),'action':'infer_missing_checkpoint'})
        return original_infer(path,x,tag)
    receipt={'status':'FROZEN_RECOVERY_BEFORE_COMPLETING_SELECTION',
        'created_at':datetime.now(timezone(timedelta(hours=8))).isoformat(),
        'reason':'Windows concurrent parent-directory mkdir returned WinError5 for epoch2; training complete',
        'frozen_select_source_sha256':sha(ROOT/'scripts/select_large24.py'),
        'recovery_source_sha256':sha(Path(__file__)),'cached_results_count':len(cached),
        'sampling_training_checkpoint_selection_rules_unchanged':True,'acceptance_not_evaluated':True}
    save(EXP/'selection_recovery.json',receipt)
    scoring.inputs=checked_inputs;scoring.infer=checked_infer
    scoring.main()
    receipt.update(status='COMPLETE_VERIFIED_CACHE_REUSED_AND_MISSING_INFERENCE_SCORED',
        jobs=events,selection_sha256=sha(EXP/'selection.json'))
    save(EXP/'selection_recovery.json',receipt)

if __name__=='__main__':main()
