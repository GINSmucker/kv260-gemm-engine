"""Persistent CPU4/PL4 worker under a separately authorized, bounded coordinator."""
import ctypes as C
import hashlib
import io
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,'/opt/opt50-demo-v1/private')
from common import create, encoded, need
from management import Admin
import resident_proof
import model_runtime
sys.path.insert(0,str(Path(__file__).parent))
from runtime_options import pl_options


def main():
    plan=json.loads(Path(sys.argv[1]).read_bytes());root=Path(plan['root'])
    need(Path('/proc/sys/kernel/random/boot_id').read_text().strip()==plan['boot_id'],'worker boot')
    np,ort=model_runtime.dependencies()
    checked=model_runtime.verify_bundle(root/'model')
    cpu=ort.InferenceSession(checked.reference_bytes,model_runtime.session_options(ort),providers=['CPUExecutionProvider'])
    options=pl_options(ort,plan.get('ORT_intra_spinning',0),force_stop=bool(plan.get('ORT_force_spinning_stop',0)),
                       dynamic_block=bool(plan.get('ORT_dynamic_block_base',0)))
    if plan['ort_profile']:
        options.enable_profiling=True;options.profile_file_prefix=str(root/'execution/ort')
    runner=model_runtime.ResidentRunner(root/'model',root/'libopt50_model.so',root/'execution/runtime.sock',
                                       max_runs=plan['sample_count'],checked_bundle=checked,options=options)
    admin=Admin(root/'execution/runtime.sock.ctl',plan['owner_pid'],checked['manifest']['logical_weights_sha256'])
    # Diagnostic tooling is explicitly copied/pinned by the coordinator.
    sys.path.insert(0,str(root/'tools'))
    from capture import diagnostic_run,complete_capture
    for run in range(1,plan['sample_count']+1):
        need(0<=time.monotonic_ns()-plan['created_monotonic_ns']<840_000_000_000,'worker admission')
        image=np.load(root/'inputs'/plan['inputs'][(run-1)%len(plan['inputs'])]['name'],allow_pickle=False)
        cpu_start=time.monotonic_ns();reference=cpu.run(None,{'image':image})[0]
        cpu_ms=(time.monotonic_ns()-cpu_start)/1e6
        owner_stat=Path('/proc/%d/schedstat'%plan['owner_pid'])
        def schedstat(path):return [int(v) for v in path.read_text().split()]
        owner_sched_before=schedstat(owner_stat);main_sched_before=schedstat(Path('/proc/self/schedstat'))
        main_cpu_before=time.thread_time_ns();process_cpu_before=time.process_time_ns()
        started=time.monotonic_ns()
        if plan['variant']=='diagnostic':
            actual,capture=diagnostic_run(runner,image,run)
        else:
            actual=runner.run(image,run_id=run);capture=None
        elapsed=(time.monotonic_ns()-started)/1e6
        main_cpu_ms=(time.thread_time_ns()-main_cpu_before)/1e6
        process_cpu_ms=(time.process_time_ns()-process_cpu_before)/1e6
        owner_sched_after=schedstat(owner_stat);main_sched_after=schedstat(Path('/proc/self/schedstat'))
        if capture:
            elapsed=(capture['runner_end_ns']-capture['runner_start_ns'])/1e6
        info_start=time.monotonic_ns();admin.call(8);info_ms=(time.monotonic_ns()-info_start)/1e6
        native_path=root/'execution'/('performance_native_%06d.json'%run)
        native=json.loads(native_path.read_bytes());resident_proof.performance(native,plan,run)
        need(reference.shape==actual.shape==(1,1000) and reference.dtype==actual.dtype==np.float32 and
             np.isfinite(reference).all() and np.isfinite(actual).all(),'valid logits')
        mismatch=int(np.count_nonzero(reference.view(np.uint32)!=actual.view(np.uint32)))
        for name,value in (('cpu',reference),('pl',actual)):
            stream=io.BytesIO();np.save(stream,value,allow_pickle=False)
            create(root/'execution'/('%06d_%s.npy'%(run,name)),stream.getvalue())
        need(mismatch==0,'numerical mismatch; retain, no next graph')
        row={'status':'PASS','run_id':run,'variant':plan['variant'],'ort_profile':plan['ort_profile'],
             'ORT_intra_spinning':plan.get('ORT_intra_spinning',0),
             'ORT_force_spinning_stop':plan.get('ORT_force_spinning_stop',0),
             'ORT_dynamic_block_base':plan.get('ORT_dynamic_block_base',0),
             'Runner_main_CPU_ms':main_cpu_ms,'Runner_process_CPU_ms':process_cpu_ms,
             'owner_schedstat_delta':[b-a for a,b in zip(owner_sched_before,owner_sched_after)],
             'main_schedstat_delta':[b-a for a,b in zip(main_sched_before,main_sched_after)],
             'Runner_run_ms':elapsed,'CPU_reference_ms':cpu_ms,'INFO_ms':info_ms,'boot_id':plan['boot_id'],
             'input_sha256':hashlib.sha256(image.tobytes()).hexdigest(),'bitwise_mismatches':mismatch,
             'CPU_logits_sha256':hashlib.sha256(reference.tobytes()).hexdigest(),
             'PL_logits_sha256':hashlib.sha256(actual.tobytes()).hexdigest()}
        if capture:
            capture=complete_capture(capture,reference,actual,native)
            create(root/'execution'/('%06d_capture.json'%run),encoded(capture))
        else:
            # Export existing diagnostics outside Runner; preserve raw per-layer measurements.
            function=runner.lib.boundary_layers
            function.argtypes=[C.c_void_p,C.POINTER(C.c_uint64)];function.restype=C.c_int
            values=(C.c_uint64*(49*4))();runner._check(function(runner.handle,values))
            row['legacy_linear_ns']=[list(values[i*4:(i+1)*4]) for i in range(49)]
        create(root/'execution'/('%06d_result.json'%run),encoded(row))
        print(json.dumps(row),flush=True)
    profile=runner.session.end_profiling() if plan['ort_profile'] else None
    runner.close()
    create(root/'execution/worker_closed.json',encoded({'status':'PASS','graphs':plan['sample_count'],'ORT_profile':profile}))
    # Use the existing management connection, avoiding a second HELLO racing its close.
    admin.call(7);admin.close()


if __name__=='__main__':
    try:main()
    except BaseException as error:
        print('RETAINED_WORKER '+repr(error),file=sys.stderr,flush=True)
        while True:time.sleep(5)
