"""OPT30 monitor injection; original frozen A1 monitor remains unchanged."""
from pathlib import Path
import argparse, hashlib, json, re, shutil
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--run',type=Path,required=True);args=ap.parse_args()
    run=args.run.resolve();sim=run/'model_gemm_hls/solution1/sim/verilog'
    config=json.loads((run/'config.json').read_text(encoding='utf-8-sig'))
    assert config['cosim_profile']=='rtl'
    tb=sim/'model_gemm_top.autotb.v';original=tb.read_text()
    assert 'mg_log' not in original
    assert re.search(r'parameter AUTOTB_TRANSACTION_NUM\s*=\s*20;',original)
    cap=json.loads((ROOT/'hardware/model_gemm/contracts/capabilities.json').read_text(encoding='utf-8-sig'))
    cap['shapes'].append(dict(M=197,K=576,N=192))
    valid=' || '.join('(mg_M==%d && mg_K==%d && mg_N==%d)'%(s['M'],s['K'],s['N']) for s in cap['shapes'])
    monitor=(HERE/'axi_monitor.vh').read_text().replace('__MODEL_GEMM_VALID_SHAPE__',valid)
    top=(sim/'model_gemm_top.v').read_text()
    instances=re.findall(r'model_gemm_top_dense_layer\s+(\w+)\s*\(',top);assert len(instances)==1
    dense=instances[0]
    dense_rtl=(sim/'model_gemm_top_dense_layer.v').read_text()
    wiring=['wire [15:0] mg_bank_ce,mg_bank_we;','wire [14:0] mg_bank_addr[0:15];']
    for b in range(16):
        for dest,signal in [('ce','ce0'),('we','we0'),('addr','address0')]:
            name=f'wbank_{b}_{signal}';assert re.search(r'\b'+name+r'\b',dense_rtl)
            wiring.append(f'assign mg_bank_{dest}[{b}]=`AUTOTB_DUT_INST.{dense}.{name};')
    monitor=monitor.replace('__MODEL_GEMM_BANK_WIRES__','\n'.join(wiring))
    xwiring=['wire [31:0] mg_xbank_ce,mg_xbank_we;','wire [8:0] mg_xbank_addr[0:31];']
    for b in range(32):
        for dest,signal in [('ce','ce0'),('we','we0'),('addr','address0')]:
            name=f'xt_V_{b}_{signal}';assert re.search(r'\b'+name+r'\b',dense_rtl)
            xwiring.append(f'assign mg_xbank_{dest}[{b}]=`AUTOTB_DUT_INST.{dense}.{name};')
        assert not re.search(r'\bxt_V_'+str(b)+r'_ce1\b',dense_rtl),'Unexpected second X bank port'
    monitor=monitor.replace('__MODEL_GEMM_XBANK_WIRES__','\n'.join(xwiring))
    for signal in set(re.findall(r'\bgmem[012]_[A-Z]+\b',monitor)):
        assert re.search(r'\b'+signal+r'\b',original),'Missing '+signal
    # Controlled testbench address translation: DUT sees full 64-bit byte bases.
    # Only the AESL memory model converts accepted addresses to its local array.
    # Bases are 64B-aligned, just before a 4KiB boundary, on three different high pages.
    bases={'X':0x100000fc0,'W':0x200000fc0,'Y':0x300000fc0}
    changed_files={}
    for i,port in enumerate(('X','W','Y')):
        dat=sim.parent/'tv/cdatafile'/('c.model_gemm_top.autotvin_'+port+'.dat')
        text=dat.read_text();words=re.findall(r'^0x[0-9a-fA-F]+$',text,re.M)
        assert len(words)==20 and all(int(w,16)==0 for w in words)
        shutil.copyfile(dat,dat.with_suffix('.original.dat'))
        dat.write_text(re.sub(r'^0x[0-9a-fA-F]+$',f'0x{bases[port]:016x}',text,flags=re.M),newline='\n')
        changed_files[str(dat.relative_to(run))]=sha(dat)
        mem=sim/('AESL_axi_master_gmem'+str(i)+'.v')
        old=mem.read_text();shutil.copyfile(mem,mem.with_suffix('.original.v.txt'))
        text=old
        for channel in ('AR','AW'):
            pattern=r'(FIFO_'+channel+r'_req_ADDR \[FIFO_'+channel+r'_req_ptr_w\]\s*=\s*TRAN_gmem'+str(i)+'_'+channel+r'ADDR);'
            text,count=re.subn(pattern,lambda m:m[1]+f" - 64'h{bases[port]:016x};",text)
            assert count==1,(port,channel)
        # At least five ready cycles out of every seven; no unbounded stalls.
        for channel in ('AR','AW','W'):
            pattern=r'(assign TRAN_gmem'+str(i)+'_'+channel+r'READY\s*=\s*)([^;]+);'
            text,count=re.subn(pattern,lambda m:m[1]+'('+m[2].strip()+') && ((clk_counter % 7) >= 2);',text)
            assert count==1
        text=text.replace("parameter ReadReqLatency = 32'd 1;","parameter ReadReqLatency = 32'd 7;")
        text=text.replace("parameter WriteReqLatency = 32'd 1;","parameter WriteReqLatency = 32'd 11;")
        if i==0:
            pattern=r'(assign TRAN_gmem0_ARREADY\s*=\s*)([^;]+);'
            text,count=re.subn(pattern,lambda m:'reg mg_x_ar_waited=0;\n'+m[1]+'('+m[2].strip()+') && mg_x_ar_waited;',text)
            assert count==1
            delay="always @(posedge clk) begin\n    if(reset===0) mg_x_ar_waited<=0;\n    else mg_x_ar_waited<=TRAN_gmem0_ARVALID && !TRAN_gmem0_ARREADY;\nend\n"
            assert text.count('endmodule')==1
            text=text.replace('endmodule',delay+'endmodule')
        if i==2:
            # Guarantee an observed AW wait, even if periodic READY gating aliases
            # with a particular burst schedule. At most one extra wait per request.
            pattern=r'(assign TRAN_gmem2_AWREADY\s*=\s*)([^;]+);'
            text,count=re.subn(pattern,lambda m:'reg mg_aw_waited=0;\n'+m[1]+'('+m[2].strip()+') && mg_aw_waited;',text)
            assert count==1
            delay="always @(posedge clk) begin\n    if(reset===0) mg_aw_waited<=0;\n    else mg_aw_waited<=TRAN_gmem2_AWVALID && !TRAN_gmem2_AWREADY;\nend\n"
            assert text.count('endmodule')==1
            text=text.replace('endmodule',delay+'endmodule')
        mem.write_text(text,newline='\n');changed_files[str(mem.relative_to(run))]=sha(mem)
    backup=sim/'model_gemm_top.autotb.original.v.txt';assert not backup.exists()
    assert len(re.findall(r'^endmodule\s*$',original,re.M))==1
    shutil.copyfile(tb,backup)
    tb.write_text(re.sub(r'^endmodule\s*$',lambda _:monitor+'\nendmodule',original,count=1,flags=re.M),newline='\n')
    evidence=dict(schema='opt36_axi_monitor_v1',scope='generated_testbench_only_DUT_unchanged',dense_instance=dense,bank_checks=16,
        testbench_original_sha256=sha(backup),testbench_monitored_sha256=sha(tb),
        monitor_template_sha256=sha(HERE/'axi_monitor.vh'),expected_valid=12,expected_invalid=8,
        high_bases=bases,bounded_backpressure='AR/AW/W periodic2/7 plus guaranteed one X AR and Y AW wait; R latency7/W latency11',
        testbench_environment_sha256=changed_files,
        dut_rtl_sha256={p.name:sha(p) for p in sorted(sim.glob('*.v')) if p!=tb and not p.name.startswith('AESL_')})
    (run/'axi_monitor_injection.json').write_text(json.dumps(evidence,indent=2)+'\n')
    print('OPT31_MONITOR_INJECTED',tb)
if __name__=='__main__':main()
