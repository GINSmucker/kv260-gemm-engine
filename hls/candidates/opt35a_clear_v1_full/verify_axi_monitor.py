"""Full-W reuse checker; public same-address sequence and full traffic order."""
from pathlib import Path
import argparse,csv,hashlib,json,importlib.util
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
spec=importlib.util.spec_from_file_location('packing',HERE.parent/'opt30_pack32_v1/verify_axi_monitor.py')
packing=importlib.util.module_from_spec(spec);spec.loader.exec_module(packing)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def descriptors(m,k,n,xb,wb,yb):
    result=packing.descriptors(m,k,n,xb,wb,yb)
    result['W']=[]
    addr=wb;words=k*n//4
    while words:
        count=min(words,16,(4096-addr%4096)//4)
        result['W'].append((addr,count));addr+=4*count;words-=count
    result['Y']=[]
    for m0 in range(0,m,8):
        for n0 in range(0,n,64):
            for mm in range(min(8,m-m0)):
                addr=yb+((m0+mm)*n+n0)*4;words=min(64,n-n0)
                while words:
                    count=min(words,16,(4096-addr%4096)//4)
                    result['Y'].append((addr,count));addr+=4*count;words-=count
    return result
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--run',type=Path,required=True);args=ap.parse_args()
    run=args.run.resolve();sim=run/'model_gemm_hls/solution1/sim/verilog'
    inj=json.loads((run/'axi_monitor_injection.json').read_text())
    for name,digest in inj['dut_rtl_sha256'].items():assert sha(sim/name)==digest,name
    for name,digest in inj['testbench_environment_sha256'].items():assert sha(run/name)==digest,name
    assert sha(sim/'model_gemm_top.autotb.v')==inj['testbench_monitored_sha256']
    log=(sim/'xsim.log').read_text(errors='replace')
    assert 'AXI_MONITOR_FAIL' not in log and 'AXI_MONITOR_PASS valid=8 invalid=8 completed=16' in log
    manifest=json.loads((ROOT/'results/A/A3/opt35a_clear_v1_full/experiment_manifest.json').read_text())
    cases=manifest['profiles']['rtl']['ordered_cases']
    rows=list(csv.DictReader((sim/'axi_monitor.csv').open()));assert len(rows)==16
    all_desc=list(csv.DictReader((sim/'axi_descriptors.csv').open()))
    for tx,r in enumerate(rows,1):
        assert int(r['transaction'])==tx and r['status']=='PASS'
        actual={p:[(int(d['address']),int(d['beats'])) for d in all_desc if int(d['transaction'])==tx and d['port']==p] for p in ('X','W','Y')}
        if tx<=8:
            c=cases[tx-1];assert r['valid']=='1'
            m,k,n=[int(r[v]) for v in ('M','K','N')];assert [m,k,n]==[c['M'],c['K'],c['N']]
            for port,key in [('X','x_base'),('W','w_base'),('Y','y_base')]:assert int(r[key])==inj['high_bases'][port] and int(r[key])>=2**32
            expected=descriptors(m,k,n,*[int(r[key]) for key in ('x_base','w_base','y_base')])
            assert actual==expected,(tx,'address/length sequence')
            for p,ar,beats in [('X','x_AR','x_R'),('W','w_AR','w_R'),('Y','y_AW','y_W')]:
                assert int(r[ar])==len(expected[p]) and int(r[beats])==sum(b for a,b in expected[p])
            assert int(r['y_B'])==int(r['y_AW'])
            assert all(int(r[key])>0 for key in ('x_stall','w_stall','aw_stall','y_stall'))
        else:
            assert r['valid']=='0' and not any(actual.values())
            assert all(int(r[key])==0 for key in ('x_AR','w_AR','y_AW','x_R','w_R','y_W','y_B'))
    pc=run/'model_gemm_hls/solution1/sim/wrapc_pc'
    summary=json.loads((pc/'rtl_tb_summary.json').read_text());assert summary['status']=='PASS' and summary['valid_cases_passed']==8 and summary['invalid_cases_rejected']==8 and summary['output_elements_compared']==418064
    case_rows=list(csv.DictReader((pc/'rtl_cases.csv').open()));assert len(case_rows)==8
    for c,r in zip(cases,case_rows):
        assert r['family']==c['family'] and int(r['x_test'])==c['x_test'] and int(r['w_test'])==c['w_test'] and r['status']=='PASS'
        for port in ('X','W'):
            file=pc/(c['family']+'_X'+str(c['x_test'])+'_W'+str(c['w_test'])+'_'+port+'.bin')
            assert sha(file)==c[port+'_sha256']
    for field in ('X_pointer','W_pointer','Y_pointer'):assert len({r[field] for r in case_rows})==1
    banks=list(csv.DictReader((sim/'wbank_monitor.csv').open()));assert len(banks)==128
    for tx in range(1,17):
        bs=[r for r in banks if int(r['transaction'])==tx];assert [int(r['bank']) for r in bs]==list(range(8))
        for b in bs:
            assert b['status']=='PASS'
            if tx<=8:
                words=cases[tx-1]['K']*cases[tx-1]['N']//8
                assert int(b['writes'])==words and int(b['reads'])>0 and int(b['max_address'])==words-1
            else:assert int(b['writes'])==int(b['reads'])==0 and int(b['max_address'])==-1
    result=dict(schema='opt31_axi_monitor_result_v2',status='PASS',transactions=rows,
        Wbank_bounds_all8='PASS_CE_ADDRESS_EVERY_ENABLED_CYCLE',bank_transactions=banks,bank_csv_sha256=sha(sim/'wbank_monitor.csv'),
        RTL_output_elements=418064,valid=8,invalid=8,same_storage_addresses='PASS',no_reset_between_requests='PASS',
        W_exact_KN_bytes_each_call='PASS',X_unchanged_repeat='PASS',Y_contiguous_burst='PASS_EXACT_ALL_AW_ADDRESSES_LENGTHS_AND_MN_DATA',
        high_base='PASS_THREE_FIXED_64BIT_BASES',controlled_backpressure='PASS_FIXED_AR_AW_W_READY_PATTERN',
        coverage='full address/length sequence, accepted AR/R/AW/W/B/LAST/4KiB/const/no Y read/invalid zero/Done drain',
        board='NOT_RUN',csv_sha256=sha(sim/'axi_monitor.csv'),descriptor_sha256=sha(sim/'axi_descriptors.csv'),postcheck_sha256=sha(pc/'rtl_tb_summary.json'))
    (run/'axi_monitor_result.json').write_text(json.dumps(result,indent=2)+'\n');print('OPT31_AXI_PASS 8+8 outputs=418064')
if __name__=='__main__':main()
