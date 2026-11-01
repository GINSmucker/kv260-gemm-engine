"""Stream closed experiment evidence and actual selected inputs, excluding immutable model payloads."""
import json
from pathlib import Path
import sys
import tarfile


def main():
    work=Path('/var/lib/opt50-final-20261101')
    if not all((work/name/'closed.json').exists() for name in ('quality_campaign','latency_campaign')):
        raise ValueError('both finite campaigns must close before evidence collection')
    original=Path('/home/ubuntu/fpga_project/opt50_integration/b24_accuracy_ready_20261028_v1/dataset')
    index=json.loads((work/'quality_inputs/index.json').read_bytes())
    records=sorted(work.glob('window-*.json'))
    sys.path.insert(0,'/opt/opt50-demo-v1/private')
    import resident_proof
    for record in records:
        folder=Path(json.loads(record.read_bytes())['root'])
        plan=json.loads((folder/'plan.json').read_bytes())
        events=[json.loads(p.read_bytes()) for p in sorted((folder/'execution/native_events').glob('event_*.json'))]
        checked=resident_proof.owner_close(events,plan['sample_count'])
        if (folder/'closed.json').exists():
            if checked!=json.loads((folder/'closed.json').read_bytes()):raise ValueError('normal close proof differs')
        else:
            reconciled=json.loads((folder/'warning_reconciliation.json').read_bytes())
            if reconciled['status']!='PASS' or reconciled['closed']!=checked:raise ValueError('unclosed/uncertain plan; do not archive as complete')
    with tarfile.open(fileobj=sys.stdout.buffer,mode='w|gz') as archive:
        for name in ('quality_campaign','quality_campaign_run_scoped','quality_campaign_dynamic','latency_campaign','latency_campaign_run_scoped','latency_campaign_dynamic','latency_campaign_three_arm'):
            if (work/name).exists():archive.add(work/name,arcname='work/'+name)
        for path in records:
            archive.add(path,arcname='work/'+path.name)
            folder=Path(json.loads(path.read_bytes())['root']);base='plans/'+folder.name
            for name in ('execution','plan.json','closed.json','job.kv','worker_plan.json','warning_reconciliation.json','tools'):
                if (folder/name).exists():archive.add(folder/name,arcname=base+'/'+name)
        for name in ('parallel-cpu-checks.json','parallel-cpu-provenance.json','diagnostic-breakdown.json'):
            archive.add(work/name,arcname='work/'+name)
        if (work/'dynamic-cpu-check.json').exists():archive.add(work/'dynamic-cpu-check.json',arcname='work/dynamic-cpu-check.json')
        archive.add(original/'manifest.json',arcname='dataset_manifest.json')
        archive.add(work/'quality_inputs/index.json',arcname='input_index.json')
        for row in index['samples']:
            archive.add(original/row['image']['file'],arcname='dataset/'+row['image']['file'])
            archive.add(Path(row['path']),arcname='inputs/%04d.npy'%row['ordinal'])
        for variant in ('baseline','diagnostic','openssl-info'):
            archive.add(work/'candidates'/variant/'candidate_manifest.json',arcname='candidates/'+variant+'/candidate_manifest.json')
            archive.add(work/'candidates'/variant/'source',arcname='candidates/'+variant+'/source')
            archive.add(work/'builds'/variant,arcname='builds/'+variant)
        archive.add(work/'service-openssl-info',arcname='service-openssl-info')
        archive.add(work/'candidates/baseline/model/manifest.json',arcname='model_manifest.json')
        archive.add(work/'candidates/baseline/model/model_catalog.hpp',arcname='model_catalog.hpp')
        archive.add(work/'tools',arcname='experiment_tools')


if __name__=='__main__':main()
