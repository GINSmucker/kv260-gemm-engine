"""Audit the W8A8-selected checkpoint and create a fixed-mask24 delivery."""
import shutil
import torch
from large24_common import *
from finetune_model import load_onnx_weights


def main():
    policy=check_policy(); selection=read(EXP/'selection.json')
    assert selection['status']=='24_TRAIN100K_W8A8_CHECKPOINT_FROZEN_BEFORE_ACCEPTANCE'
    assert read(OUT/'search_verification.json')['status']=='PASS'
    winner=selection['winner']; INITIAL=Path(winner['initial_folder'])
    run=INITIAL.parent/'training'; folder=run/f"epoch{winner['epoch']}"
    config=read(run/'config.json'); SELECTED=tuple(winner['selected_layers'])
    assert config['policy_sha256']==sha(EXP/'policy.json')
    checkpoint=torch.load(folder/'model_trainable.pt',map_location='cpu',weights_only=True)
    assert checkpoint['epoch']==winner['epoch']
    assert tuple(checkpoint['selected_layers'])==SELECTED and len(SELECTED)==24
    assert sha(folder/'model_w8a8.onnx')==winner['model_sha256']
    mask_hash=digest(b''.join(n.encode()+checkpoint['sparse_masks'][n].numpy().tobytes() for n in sorted(checkpoint['sparse_masks'])))
    assert mask_hash==checkpoint['mask_sha256']==config['mask_sha256']
    PKG.mkdir(parents=True,exist_ok=True)
    for name in ['model_fp32.onnx','model_w8a8.onnx']:
        assert sha(folder/name)==read(folder/'receipt.json')['models_sha256'][name]
        shutil.copyfile(folder/name,PKG/name)
    shutil.copyfile(folder/'model_trainable.pt',PKG/'model_trainable.pt')
    fp=onnx.load(PKG/'model_fp32.onnx'); q=onnx.load(PKG/'model_w8a8.onnx')
    oldfp=onnx.load(INITIAL/'model_fp32.onnx'); oldq=onnx.load(INITIAL/'model_w8a8.onnx')
    assert topology(fp)==topology(oldfp) and topology(q)==topology(oldq)
    onnx.checker.check_model(fp); onnx.checker.check_model(q)
    fa,qa,oldqa=arrays(fp),arrays(q),arrays(oldq)
    assert set(checkpoint['model'])==set(fa)
    for name,value in fa.items():
        assert np.array_equal(checkpoint['model'][name].numpy(),value) and np.isfinite(value).all(),name
    layers=read(INITIAL/'layers.json'); assert len(layers)==49
    (PKG/'weights').mkdir(exist_ok=True); (PKG/'cases').mkdir(exist_ok=True)
    checks=[]; replaced=set()
    for layer in layers:
        i=layer['layer_index']; name=layer['layer_id']; w=fa[name+'.weight'].T.copy()
        # Recompute quantization from FP32, independently of the ONNX exporter.
        quant,scale=quantize(w); bias=fa[name+'.bias']
        assert np.array_equal(qa[f'BQL_{i}_Wf'],quant.astype(np.float32))
        assert np.array_equal(qa[f'BQL_{i}_sf'],scale.ravel())
        assert np.array_equal(qa[f'BQL_{i}_bf'],bias)
        replaced.update([f'BQL_{i}_Wf',f'BQL_{i}_sf',f'BQL_{i}_bf'])
        values={'W_fp32':w,'Wq':quant,'sw':scale,'bias':bias}
        if i in SELECTED:
            with np.load(INITIAL/layer['weights']) as old:
                mask=old['mask'].copy(); positions=old['positions'].copy()
            assert np.array_equal(checkpoint['sparse_masks'][name+'.weight'].numpy(),mask.T)
            assert not np.any(w[~mask]) and not np.any(quant[~mask])
            assert np.all(mask.reshape(-1,4,w.shape[1]).sum(1)==2)
            # Position metadata must encode exactly the same two K-axis slots.
            reconstructed=np.zeros((w.shape[0]//4,w.shape[1],4),dtype=np.bool_)
            np.put_along_axis(reconstructed,positions,True,axis=2)
            assert np.array_equal(reconstructed.transpose(0,2,1).reshape(w.shape),mask)
            slots=np.take_along_axis(quant.reshape(-1,4,w.shape[1]).transpose(0,2,1),positions,axis=2)
            values.update(mask=mask,positions=positions,values=slots)
            rng=np.random.default_rng(SEED+i)
            x=rng.integers(-127,128,size=(9,w.shape[0]),dtype=np.int16).astype(np.int8)
            x[0]=0;x[1]=-127;x[2]=127
            dense=x.astype(np.int64)@quant.astype(np.int64); sparse=np.zeros_like(dense)
            for g in range(w.shape[0]//4):
                for slot in range(2):
                    sparse+=x[:,4*g+positions[g,:,slot].astype(np.int64)].astype(np.int64)*slots[g,:,slot].astype(np.int64)
            assert np.array_equal(dense,sparse)
            layer['integer_case']=f'cases/layer_{i:02d}.npz'
            np.savez_compressed(PKG/layer['integer_case'],Xq=x,Yi32=dense.astype(np.int32))
            layer['weights']=f'weights/layer_{i:02d}.npz'; layer['dense_weights']=None
        else:
            assert not layer['weights']
            layer['dense_weights']=f'weights/layer_{i:02d}.npz'
        layer['recovery_finetuned']=True
        layer.pop('weight_changed_vs_trained16',None)
        layer.pop('newly_pruned_from_trained16',None)
        np.savez_compressed(PKG/f'weights/layer_{i:02d}.npz',**values)
        checks.append({'index':i,'fixed_sparse':i in SELECTED})
    assert sum(c['fixed_sparse'] for c in checks)==24
    # Check every other initializer: trained tensors or unchanged quant constants.
    for name,value in qa.items():
        if name in replaced: continue
        assert np.array_equal(value,fa[name] if name in fa else oldqa[name]),name
    save(PKG/'layers.json',layers)
    for name in ['quantization_protocol.json','preprocessing.json','labels_imagenet1k.json']:
        shutil.copyfile(PARENT/name,PKG/name)
    save(PKG/'selection.json',{'sparse_layers_count':24,'selected_layers':list(SELECTED),
        'fixed_masks':'Exactly inherited24 masks; kept weights trained, zero positions frozen',
        'checkpoint_epoch':winner['epoch'],'checkpoint_selection':policy['checkpoint_selection'],'added_layers':winner['added_layers'],'development_w8a8':winner['metrics'],
        'next_layer_policy':'All24 MLP linears are pruned; attention linears and classifier remain dense'})
    save(PKG/'provenance.json',{'parent':str(PARENT),'initial24':str(INITIAL),'selection_sha256':sha(EXP/'selection.json'),'training':config,'policy_sha256':sha(EXP/'policy.json'),
        'continued_fine_tuning':'COMPLETED','additional_pruning_in_this_run':0,'additional_epochs_budget':EPOCHS,'training_images':TRAIN_IMAGES,'new_development_images':TUNE_IMAGES})
    save(EXP/'winner_receipt.json',{'status':'CHECKPOINT_FROZEN_BEFORE_ACCEPTANCE',
        'best_epoch':winner['epoch'],'metrics_w8a8':winner['metrics'],'selection_sha256':sha(EXP/'selection.json'),'mask_sha256':mask_hash,
        'best_checkpoint_sha256':sha(folder/'model_trainable.pt'),
        'model_hashes':{n:sha(PKG/n) for n in ['model_fp32.onnx','model_w8a8.onnx','model_trainable.pt']}})
    torch.set_num_threads(1)
    model=load_onnx_weights(PKG/'model_fp32.onnx').eval()
    engine=session(PKG/'model_fp32.onnx'); rows=read_rows(DATA/'tune'); errors=[]
    from eval_models import preprocess_image
    with torch.inference_mode():
        for index in [0,199,499,899,1499,2499,3499,4499]:
            x=preprocess_image(DATA/'tune'/rows[index]['image'])
            z=model(torch.from_numpy(x)).numpy(); target=engine.run(None,{engine.get_inputs()[0].name:x})[0]
            assert np.allclose(z,target,atol=5e-4,rtol=5e-4)
            errors.append(float(np.max(np.abs(z-target))))
    save(OUT/'parity.json',{'status':'PASS','images':8,'max_abs_error':max(errors),'acceptance_used':False})
    save(OUT/'package_verification.json',{'status':'PASS','graph_topology_unchanged':True,
        'linear_quantization_checks':49,'fixed_masks_verified':24,'sparse_integer_slot_cases':24,
        'trainable_checkpoint_matches_fp32':True,'checkpoint_selected_only_on_development':True,
        'package_hashes':read(EXP/'winner_receipt.json')['model_hashes']})
    print('PACKAGED fixed24 checkpoint epoch',winner['epoch'],flush=True)


if __name__=='__main__':main()
