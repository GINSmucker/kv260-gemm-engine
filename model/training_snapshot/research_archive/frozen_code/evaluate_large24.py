"""Post-selection evaluation of a fixed-mask24 continuation and frozen controls."""
from concurrent.futures import ThreadPoolExecutor,as_completed
import time
from large24_common import *

def main():
    check_policy();selection_hash=sha(EXP/'selection.json');receipt_hash=sha(EXP/'winner_receipt.json')
    receipt=read(EXP/'winner_receipt.json')
    assert receipt['selection_sha256']==selection_hash
    assert read(OUT/'package_verification.json')['status']=='PASS'
    for name,h in receipt['model_hashes'].items():assert sha(PKG/name)==h
    base={'dense_w8a8':SOURCE/'h576_w8.onnx','parent20_w8a8':PARENT20/'model_w8a8.onnx',
        'parent22_w8a8':PARENT22/'model_w8a8.onnx','parent24_w8a8':PARENT/'model_w8a8.onnx',
        'original24_w8a8':ORIGINAL24/'model_w8a8.onnx',
        'selected24_w8a8':PKG/'model_w8a8.onnx'}
    for part in PARTS:
        folder=evaluation_folder(part);models=dict(base)
        if part=='fresh900_1800':
            models.update(dense_fp32=SOURCE/'h576_fp32.onnx',
                parent24_fp32=PARENT/'model_fp32.onnx',selected24_fp32=PKG/'model_fp32.onnx')
        x,y,rows,input_sha=inputs(folder);outputs={};started=time.time()
        def run(item):
            name,path=item;z=infer(path,x);return name,z,metrics(z,y)
        with ThreadPoolExecutor(max_workers=8) as pool:
            for f in as_completed([pool.submit(run,item) for item in models.items()]):
                name,z,m=f.result();outputs[name]=(z,m)
                print('EVALUATED',part,name,m,flush=True)
        out=OUT/part;out.mkdir(exist_ok=True)
        np.savez_compressed(out/'logits.npz',labels=y,**{name:z for name,(z,m) in outputs.items()})
        ranks={name:np.argsort(-z,axis=1,kind='stable')[:,:5] for name,(z,m) in outputs.items()}
        comparisons=[(k,'selected24_w8a8') for k in ['parent24_w8a8','original24_w8a8','parent22_w8a8','parent20_w8a8','dense_w8a8']]
        comparisons.extend([('parent24_fp32','selected24_fp32'),('dense_fp32','selected24_fp32')])
        pairs={}
        for before,after in comparisons:
            if before not in ranks:continue
            paired={}
            for k,metric in enumerate(['top1','top5']):
                a=ranks[before][:,0]==y if k==0 else np.any(ranks[before]==y[:,None],axis=1)
                b=ranks[after][:,0]==y if k==0 else np.any(ranks[after]==y[:,None],axis=1)
                paired[metric]={'new_errors':int(np.sum(a&~b)),'recovered':int(np.sum(~a&b)),
                    'selected24_minus_reference_pp':float(100*(b.astype(np.float64)-a).mean())}
            pairs[after+'_vs_'+before]=paired
        save(out/'identities.json',[{'image':r['image'],'label':int(r['label']),
            'image_sha256':r['image_sha256']} for r in rows])
        save(out/'summary.json',{'status':'PASS','images':len(y),'classes':len(set(y.tolist())),
            'metrics':{name:m for name,(z,m) in outputs.items()},'paired_comparisons':pairs,
            'model_sha256':{name:sha(path) for name,path in models.items()},'preprocessed_inputs_sha256':input_sha,
            'logits_sha256':sha(out/'logits.npz'),'selection_sha256':selection_hash,
            'winner_receipt_sha256':receipt_hash,'acceptance_used_to_reselect':False,
            'seconds':time.time()-started,'inference':'ONNX CPU1 intra/1 inter thread, graph optimization disabled',
            'role':'fresh post-selection acceptance, now exposed regression' if part=='fresh900_1800' else 'previously exposed regression'})
        del x,outputs
    assert sha(EXP/'selection.json')==selection_hash and sha(EXP/'winner_receipt.json')==receipt_hash
    print('100K ACCEPTANCE AND REGRESSION COMPLETE; NO RESELECTION',flush=True)

if __name__=='__main__':main()
