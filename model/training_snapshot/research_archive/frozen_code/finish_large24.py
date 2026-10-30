"""Independently audit100k identities and results, then apply the prespecified adoption gate."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone,timedelta
import shutil
from large24_common import *
from prepare_large24 import known

def check_data():
    policy=check_policy();plan=read(EXP/'sampling_plan.json');manifest=read(DATA/'manifest.json')
    past_source,past_raw,past_rgb=known(plan)
    original={(r['split'],int(r['row_index'])):r for r in read_rows(OLD_DATA/'train')}
    seen_source=set();seen_raw=set();seen_rgb=set();verified={};kept=0;new=0
    def verify(row):
        folder,r=row;raw=(folder/r['image']).read_bytes();h=digest(raw);rgb=image_identity(raw)
        assert h==r['image_sha256'] and rgb==r['decoded_rgb_sha256']
        return r,h,rgb
    for role,folder,count,classes,per in [('train',DATA/'train',100000,1000,100),
        ('tune',DATA/'tune',4500,900,5),('fresh',FRESH,1800,900,2)]:
        rows=read_rows(folder);counts=Counter(int(r['label']) for r in rows)
        assert len(rows)==count and len(counts)==classes and set(counts.values())=={per}
        if role!='fresh':assert sha(folder/'labels.csv')==manifest['csv_sha256'][role]
        with ThreadPoolExecutor(max_workers=8) as pool:
            for n,(r,h,rgb) in enumerate(pool.map(verify,[(folder,r) for r in rows]),1):
                key=(r['split'],int(r['row_index']))
                assert key not in seen_source and h not in seen_raw and rgb not in seen_rgb
                seen_source.add(key);seen_raw.add(h);seen_rgb.add(rgb)
                if r['source_role']=='original20k':
                    assert role=='train' and key in original
                    assert h==original[key]['image_sha256'] and rgb==original[key]['decoded_rgb_sha256'];kept+=1
                else:
                    assert key not in past_source and h not in past_raw and rgb not in past_rgb
                    if role=='train':assert r['source_role']=='new80k';new+=1
                assert r['split']==('train' if role=='train' else 'val')
                if n%10000==0:print('DATA AUDIT',role,n,'/',count,flush=True)
        verified[role]={'images':count,'classes':classes,'labels_sha256':sha(folder/'labels.csv')}
        print('DATA ROLE VERIFIED',role,count,flush=True)
    assert (kept,new)==(20000,80000) and len(seen_source)==106300
    for path,h in plan['excluded_labels_sha256'].items():assert sha(path)==h
    save(OUT/'data_verification.json',{'status':'PASS','verified':verified,'old_training_images_exactly_preserved':20000,
        'new_training_images':80000,'new_development_images':4500,'fresh_acceptance_images':1800,
        'new_images_exclude_registered_source_byte_rgb_identities':True,'cross_role_exact_duplicates':0,
        'limits':'No near-duplicate or original-pretraining exposure exclusion claim.'})

def verify_logits(part):
    folder=OUT/part;s=read(folder/'summary.json')
    assert sha(folder/'logits.npz')==s['logits_sha256']
    assert s['winner_receipt_sha256']==sha(EXP/'winner_receipt.json')
    assert s['selection_sha256']==sha(EXP/'selection.json') and not s['acceptance_used_to_reselect']
    with np.load(folder/'logits.npz') as z:
        y=z['labels'];correct={}
        assert np.array_equal(y,[int(r['label']) for r in read_rows(evaluation_folder(part))])
        if part!='fresh900_1800':
            old_part={'previous_continue1800_regression':'fresh900_1800',
                'previous24_1800_regression':'previous24_1800_regression',
                'exposed_development1000':'exposed_development1000'}[part]
            with np.load(PREVIOUS_REPORT/old_part/'logits.npz') as old:
                assert np.array_equal(y,old['labels'])
                for key,oldkey in [('dense_w8a8','dense_w8a8'),('parent20_w8a8','parent20_w8a8'),
                    ('parent22_w8a8','parent22_w8a8'),('original24_w8a8','parent24_w8a8'),('parent24_w8a8','selected24_w8a8')]:
                    assert np.array_equal(z[key],old[oldkey]),key
        for name in z.files:
            if name=='labels':continue
            values=z[name];assert values.shape==(len(y),1000) and np.isfinite(values).all()
            ranks=[sorted(range(1000),key=lambda k:(-float(row[k]),k))[:5] for row in values]
            a=np.array([r[0]==int(label) for r,label in zip(ranks,y)])
            b=np.array([int(label) in r for r,label in zip(ranks,y)])
            m=s['metrics'][name]
            assert (int(a.sum()),int(b.sum()))==(m['top1_correct'],m['top5_correct'])
            ce=float(np.mean(np.logaddexp.reduce(values.astype(np.float64),axis=1)-values[np.arange(len(y)),y]))
            assert abs(ce-m['cross_entropy'])<1e-10
            correct[name]=(a,b)
        delta=correct['selected24_w8a8'][0].astype(np.int16)-correct['parent24_w8a8'][0].astype(np.int16)
        rng=np.random.default_rng(SEED+700)
        boot=[100*delta[rng.integers(0,len(y),len(y))].mean() for _ in range(10000)]
        ci=np.quantile(boot,[.025,.975]).tolist()
    for key,values in s['paired_comparisons'].items():
        after,before=key.split('_vs_')
        for k,metric in enumerate(['top1','top5']):
            a,b=correct[before][k],correct[after][k]
            assert values[metric]['new_errors']==int(np.sum(a&~b))
            assert values[metric]['recovered']==int(np.sum(~a&b))
            assert abs(values[metric]['selected24_minus_reference_pp']-100*(b.astype(np.float64)-a).mean())<1e-10
    save(folder/'independent_verification.json',{'status':'PASS','independent_python_rank_and_ce':True,
        'paired_top1_vs_parent24_bootstrap95pct_pp':ci,'resamples':10000,
        'scope':'Descriptive conditional interval; no population equivalence or superiority guarantee'})
    return s

def main():
    policy=check_policy();selection=read(EXP/'selection.json');winner=selection['winner']
    assert selection['status']=='24_TRAIN100K_W8A8_CHECKPOINT_FROZEN_BEFORE_ACCEPTANCE'
    assert winner['added_layers']==[] and len(read(EXP/'ranking.json'))==1
    assert selection['ranking_sha256']==sha(EXP/'ranking.json')
    receipt=read(EXP/'winner_receipt.json');assert receipt['selection_sha256']==sha(EXP/'selection.json')
    for name in ['search_verification.json','package_verification.json','parity.json']:
        assert read(OUT/name)['status']=='PASS'
    for name,h in receipt['model_hashes'].items():assert sha(PKG/name)==h
    for parent in [PARENT,PARENT20,PARENT22,ORIGINAL24]:
        for rel,r in read(parent/'PACKAGE_MANIFEST.json')['files'].items():assert sha(parent/rel)==r['sha256']
    run=Path(winner['initial_folder']).parent/'training';config=read(run/'config.json')
    assert (config['epochs'],config['updates'],config['seed'],config['fixed_masks'])==(5,7815,SEED,24)
    assert (config['training_images'],config['tuning_images'])==(100000,4500)
    assert config['optimizer_reinitialized'] and config['additional_pruned_layers']==[]
    assert config['teacher_fp32_sha256']==sha(SOURCE/'h576_fp32.onnx')=='b07d7279aecaf64b7907c6849ad8bcce4acaafeeb1110bfdd1de2d5b4843fb21'
    assert config['mask_sha256']==read(ROOT/'experiments/B_mlp24_continue10_20261025_run01/candidates/fixed24/training/config.json')['mask_sha256']
    for item in winner['all_epochs']:
        assert sha(EXP/'development'/winner['id']/f"epoch{item['epoch']}"/'logits.npz')==item['logits_sha256']
    assert tuple(l['layer_index'] for l in read(PKG/'layers.json') if l['weights'])==SELECTED
    check_data();results={part:verify_logits(part) for part in PARTS};save(PKG/'evaluation.json',results)
    fresh=results['fresh900_1800']['metrics'];a=fresh['selected24_w8a8'];b=fresh['parent24_w8a8'];d=fresh['dense_w8a8']
    promote=a['top1_correct']>b['top1_correct']
    fmt=lambda m:f"{m['top1_pct']:.2f} / {m['top5_pct']:.2f}"
    delta=a['top1_pct']-b['top1_pct'];outcome='通过预定采用门槛，更新当前模型入口' if promote else '未通过预定采用门槛，保留原当前模型入口'
    report=['# 24层模型扩大到10万训练图片的微调与精度评估','',
        f"本轮新1800张验收图片上，100k模型W8A8 Top-1/Top-5为 **{fmt(a)}%**，原当前24层为{fmt(b)}%，Top-1变化 **{delta:+.2f}个百分点**；Dense为{fmt(d)}%。{outcome}。候选在验收前已由新开发集冻结，未使用验收结果改选检查点或追加训练。",'',
        '## 数据扩展与划分','',
        '| 用途 | 数量 | 类别覆盖 | 来源与用途 |','|---|---:|---|---|',
        '| 训练 | 100000 | 1000类 每类100张 | 原20000张全部保留，新增80000张原训练分区图片 |',
        '| 新开发集 | 4500 | 900类 每类5张 | 原验证分区，未参与此前本地训练或模型选择，用于本轮检查点选择 |',
        '| 新验收集 | 1800 | 900类 每类2张 | 原验证分区，选定并冻结模型后一次性验收 |','',
        '新增训练样本按类别抽取两个随机且互不重叠的48行窗口，每个窗口40张为首选，合计16张备用用于精确排重替换。该规则用于限制传输量，并非完全独立均匀逐图抽样；相邻来源行的潜在相关性和近重复未排除。抽样顺序在下载前固定，不使用模型预测。保留原20张/类后，每类共100张。开发和验收从此前未使用的900类验证样本的独立种子顺序抽取，精确排重后前5张开发、后2张验收。二者图片不重叠，但同属900类分布。','',
        '新开发集替代多次重复选优的旧2000张开发集，因此不能把此前旧开发集分数与新开发集绝对分数直接比较。新开发集用于本轮选择后即为已暴露开发数据；新验收集只在检查点冻结后评估。旧100类图片已在历史评估中广泛使用，本轮新开发/验收均排除这些类；旧1000张仍用于回归，所有准确率在完整1000类输出上排名。','',
        '## 固定训练预算与模型选择','',
        f"从当前24层继续微调模型加载权重，保留全部24层原有2:4掩码；未新增剪枝、重算位置或改变网络。额外训练5轮、7815次更新，随机种子{SEED}。微批32、梯度累积2、AdamW、峰值学习率1e-5、100步预热、余弦降至1e-6、权重衰减0.01、梯度裁剪1。Dense FP32冻结教师，0.5交叉熵+0.5温度平方缩放KL蒸馏，温度2。BF16训练、FP32参数与导出，未使用QAT。优化器重新初始化，非恢复上一轮动量状态。",'',
        '保存epoch0至5，全部5轮完成后，按新开发集的W8A8 Top-1正确数、Top-5正确数、较低交叉熵和较早epoch选择检查点，epoch0未继续训练的父模型也参与排序。采用门槛在训练前固定：所选候选在新验收集W8A8 Top-1正确数严格超过冻结父模型，才更新当前入口；否则保留父模型入口，仅保存候选实验结果。验收只做这一次采用/不采用判断，不对其他检查点重排名。','',
        '| epoch | 更新数 | 新开发集W8A8 Top-1 / Top-5 (%) | 交叉熵 |','|---:|---:|---:|---:|']
    for r in winner['all_epochs']:
        report.append(f"| {r['epoch']} | {UPDATES_PER_EPOCH*r['epoch']} | {fmt(r['metrics'])} | {r['metrics']['cross_entropy']:.6f} |")
    report.extend(['',f"所选检查点：epoch **{winner['epoch']}**。GPU训练流程{config['elapsed_seconds']:.1f}秒，包含逐轮FP32验证和导出，不包含下载、CPU W8A8评分及验收。",'',
        '## 同批图片的精度比较','',
        '| 评估集 | Dense | 20层 | 22层 | 24层原3轮版本 | 本轮父24层 | 100k候选 | 候选相对父模型变化 Top-1 / Top-5 (百分点) |',
        '|---|---:|---:|---:|---:|---:|---:|---:|'])
    labels={'fresh900_1800':'本轮新1800张','previous_continue1800_regression':'上轮继续微调1800张回归',
        'previous24_1800_regression':'此前24层1800张回归','exposed_development1000':'旧1000张回归'}
    for part,s in results.items():
        vals=[s['metrics'][k] for k in ['dense_w8a8','parent20_w8a8','parent22_w8a8','original24_w8a8','parent24_w8a8','selected24_w8a8']]
        before,after=vals[-2:]
        report.append('| '+labels[part]+' | '+' | '.join(fmt(x) for x in vals)+f" | {after['top1_pct']-before['top1_pct']:+.2f} / {after['top5_pct']-before['top5_pct']:+.2f} |")
    report.extend(['','表中均为W8A8 Top-1 / Top-5百分数。差值由原始正确数计算后四舍五入。不同样本集的类别和难度不同，应只在同一行比较模型。','',
        '| 新1800张模型 | Top-1正确数 | Top-5正确数 |','|---|---:|---:|'])
    for k in ['dense_w8a8','parent20_w8a8','parent22_w8a8','original24_w8a8','parent24_w8a8','selected24_w8a8']:
        m=fresh[k];report.append(f"| {k} | {m['top1_correct']} | {m['top5_correct']} |")
    pair=results['fresh900_1800']['paired_comparisons']['selected24_w8a8_vs_parent24_w8a8']['top1']
    ci=read(OUT/'fresh900_1800/independent_verification.json')['paired_top1_vs_parent24_bootstrap95pct_pp']
    report.extend(['',f"相对父模型，候选新增{pair['new_errors']}张Top-1错误，同时纠正{pair['recovered']}张。10000次图像级配对重采样的描述性95%差值区间为[{ci[0]:+.2f}, {ci[1]:+.2f}]个百分点。区间条件于固定模型和固定样本，仅为描述，不作总体优越性/等价性保证。",'',
        '## FP32对照与计算口径','',
        '| 新1800张 | FP32 Top-1 / Top-5 (%) | W8A8 Top-1 / Top-5 (%) |','|---|---:|---:|'])
    for prefix in ['dense','parent24','selected24']:
        report.append(f"| {prefix} | {fmt(fresh[prefix+'_fp32'])} | {fmt(fresh[prefix+'_w8a8'])} |")
    report.extend(['','预处理、网络图和量化协议保持一致：RGB、短边248、Pillow双三次、224中心裁剪、ImageNet均值标准差、FP32 NCHW，不自动EXIF转置；ONNX Runtime 1.22.1，CPU算子内1线程、算子间1线程、关闭图优化、批量1。激活逐行动态absmax、权重逐输出通道absmax、零点0、最近偶数舍入、范围[-127,127]。','',
        '## 局限与核验','',
        '本轮同时改变训练样本量、总更新次数、随机种子及检查点选择用开发集，不是仅改变数据量的等计算量消融，不能将差异全部因果归于新增样本。只运行一个种子、一种5轮日程，不表示充分收敛或最优超参数。所有对照冻结，没有获得本轮相同额外训练预算。','',
        '原ImageNet训练数据可能已被Dense预训练接触。这里未使用指本地剪枝/微调流程未使用；不声称原模型预训练完全未见。所有新增本地样本按来源行号、文件SHA-256和解码RGB排除已登记精确身份，但没有排除近重复。数据来自256像素派生集，且验收只覆盖900类，不是完整ImageNet验证精度。验收后该集属于已暴露回归数据。','',
        '核验包括：原20000张精确保留；新增80000张、开发4500张和验收1800张的来源、字节与RGB身份，全部106300张跨用途精确排重；父模型完整文件清单；六个检查点的图拓扑、49层量化及24层固定零位置；epoch0与父模型开发logits逐位一致；开发/验收logits独立排序与交叉熵；检查点与FP32初始化器一致；24层稀疏整数槽样例；8张开发图片FP32训练结构与ONNX对齐；历史五个冻结对照logits与上轮逐位一致。','',
        '全部24个MLP线性层维持原2:4位置，注意力与分类头仍稠密，不表示全模型50%稀疏。本轮没有板端部署或端到端速度测量。','',
        '## 文件与复现','',f"候选模型包：`{PKG}`；父模型保留：`{PARENT}`；原24层、22层和20层模型也保留。",'',
        f"全部检查点和冻结代码：`{EXP}`；评估及核验：`{OUT}`；当前模型入口：`{ROOT/'active_model.json'}`。",'',
        f"固定数据提交：[Hugging Face](https://huggingface.co/datasets/{DS}/tree/{REV})。",'',
        '| 文件 | SHA-256 |','|---|---|'])
    for name,h in receipt['model_hashes'].items():report.append(f'| {name} | {h} |')
    (OUT/'REPORT.md').write_text('\n'.join(report)+'\n',encoding='utf-8')
    shutil.copyfile(EXP/'ranking.csv',OUT/'checkpoint_selection.csv')
    (PKG/'README.md').write_text('# 固定24层 100k训练图片微调\n\n训练集由2万扩大到10万，全部24层掩码保持不变。5轮、7815次更新；新开发集4500张按W8A8选中epoch '+str(winner['epoch'])+'，再用新1800张验收。'+outcome+'。精度及局限见evaluation.json与工作区报告。原模型均保留，未进行板端速度测试。\n',encoding='utf-8')
    save(PKG/'PACKAGE_MANIFEST.json',{'files':{p.relative_to(PKG).as_posix():{'sha256':sha(p),'bytes':p.stat().st_size}
        for p in sorted(PKG.rglob('*')) if p.is_file() and p.name!='PACKAGE_MANIFEST.json'}})
    for rel,r in read(PKG/'PACKAGE_MANIFEST.json')['files'].items():assert sha(PKG/rel)==r['sha256']
    save(OUT/'verification.json',{'status':'PASS','sparse_layers':24,'training_images':100000,'new_training_images':80000,
        'new_development_images':4500,'additional_updates':7815,'epochs_run':5,'selected_epoch':winner['epoch'],
        'selection_sha256':sha(EXP/'selection.json'),'winner_receipt_sha256':sha(EXP/'winner_receipt.json'),
        'package_manifest_sha256':sha(PKG/'PACKAGE_MANIFEST.json'),'report_sha256':sha(OUT/'REPORT.md'),
        'package_files_verified':len(read(PKG/'PACKAGE_MANIFEST.json')['files']),
        'data_verification_sha256':sha(OUT/'data_verification.json'),
        'evaluation_verifications':{p:sha(OUT/p/'independent_verification.json') for p in PARTS},
        'fresh_top1_improved':promote,'adoption_gate':'one-shot fresh Top1 correct count greater than parent',
        'candidate_promoted':promote})
    active=read(ROOT/'active_model.json');assert active['models']['w8a8']['sha256']==EXPECTED_Q
    save(OUT/'previous_active_model.json',active)
    usage={'historical_reference':str(ROOT/'references/data_usage_after_continue24.json'),
        'train':{'path':str(DATA/'train'),'images':100000,'new_images':80000,'epochs_run':5},
        'development':{'path':str(DATA/'tune'),'images':4500,'classes':900,'role':'new local checkpoint selection, now exposed'},
        'fresh1800':{'path':str(FRESH),'images':1800,'role':'post-selection assessment and one-shot adoption, now exposed regression'},
        'no_acceptance_checkpoint_reselection_or_retraining':True}
    save(ROOT/'references/data_usage_after_large24.json',usage)
    if promote:
        active={'status':'ACTIVE_FINETUNED24_TRAIN100K','updated_at':datetime.now(timezone(timedelta(hours=8))).isoformat(),
            'reason':'Fixed24 masks,100k train,new4500 dev-selected checkpoint; passes prespecified fresh Top1 improvement gate.',
            'sparse_layers_count':24,'selected_layers':list(SELECTED),'added_layers':[],
            'candidate_folder':str(PKG),'models':{key:{'path':str(PKG/name),'sha256':sha(PKG/name),'bytes':(PKG/name).stat().st_size}
                for key,name in [('w8a8','model_w8a8.onnx'),('fp32','model_fp32.onnx'),('trainable','model_trainable.pt')]},
            'previous_candidate_retained':str(PARENT),'original24_retained':str(ORIGINAL24),
            'training_images':100000,'epochs_run':5,'selected_epoch':winner['epoch'],
            'experiment':str(EXP),'package_manifest':str(PKG/'PACKAGE_MANIFEST.json'),
            'verification':str(OUT/'verification.json'),'verification_sha256':sha(OUT/'verification.json'),
            'evaluation_report':str(OUT),'data_usage_reference':str(ROOT/'references/data_usage_after_large24.json'),
            'development_metrics':winner['metrics'],
            'finetuned_metrics':{p:s['metrics']['selected24_w8a8'] for p,s in results.items()},
            'scope':'Workspace research model,point-estimate adoption; no population superiority or hardware speed guarantee'}
        save(ROOT/'active_model.json',active)
    path=ROOT/'README.md';text=path.read_text(encoding='utf-8')
    note='\n## 10万训练图片实验\n\n已保留原2万张，新增8万张，训练集覆盖1000类各100张。固定24层掩码完成5轮、7815次更新，新开发集4500张选中epoch '+str(winner['epoch'])+'，冻结后新1800张验收。'+outcome+'。完整结果见 [100k训练报告](reports/B_mlp24_train100k_20261025_run01/REPORT.md)。本轮开发和验收集均已暴露，原模型保留。\n'
    anchor='## 方法正确性与科学性审查';pos=text.index(anchor);text=text[:pos]+note+'\n'+text[pos:]
    if promote:
        start=text.index('## 当前工作基线：');end=text.index('## 10万训练图片实验',start)
        text=text[:start]+'## 当前工作基线：固定24层 100k训练图片微调模型\n\n当前入口见 [active_model.json](active_model.json)。新开发集选择检查点，并通过预先约定的新验收集Top-1提升门槛。所有旧模型保留。\n\n'+text[end:]
    path.write_text(text,encoding='utf-8')
    save(EXP/'completion.json',{'status':'COMPLETE_FIXED24_TRAIN100K_EVALUATED','selected_epoch':winner['epoch'],
        'candidate_promoted':promote,'package':str(PKG),'report':str(OUT/'REPORT.md'),
        'active_model_sha256':sha(ROOT/'active_model.json'),'verification_sha256':sha(OUT/'verification.json')})
    print('COMPLETE100K fixed24 epoch',winner['epoch'],'promoted',promote,flush=True)

if __name__=='__main__':main()
