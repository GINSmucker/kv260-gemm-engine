"""Publish reviewed adoption metadata for an already validated, unchanged service pair."""
import json
from pathlib import Path
import sys

from install_trial import APP,WORK,publish
from prepare import pin


def main():
    sys.path.insert(0,str(APP))
    import backend
    from common import need
    receipt=backend.verify_install()
    review_path=Path(sys.argv[1]);review=json.loads(review_path.read_bytes())
    trial=receipt['final_optimization_trial']
    need(trial['profile'] in ('exact-run-scoped-spin-sha-parallel-v1','exact-run-scoped-spin-dynamic4-sha-parallel-v1','exact-run-scoped-spin-model-only-v1')
         and trial['final_adoption'] is False,'matching unadopted pair')
    need(review['status']=='PASS' and review['quality']['bitwise_mismatches']==0
         and review['quality']['images']==500 and review['model']['threshold_2ms_and_3percent'] is True,'quality/model reviewed')
    need(len(review['complete_request_comparisons'])==2 and all(p['bitwise_mismatches']==0 for p in review['complete_request_comparisons']),'two complete-request numerical reviews')
    if trial['profile']!='exact-run-scoped-spin-model-only-v1':
        need(review['complete_request_adoption_status']=='PASS' and all(
             p['median_gain_ms']>50 and p['median_reduction_percent']>3
             for p in review['complete_request_comparisons']),'complete-request gains beyond thresholds and observed control variation')
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    need(review['final_boot_id']==boot and review['installed_files']==receipt['files'],'final boot/pair identity')
    status=json.loads(Path('/run/opt50-demo/status.json').read_bytes())
    need(status['boot_id']==boot and status['stage']=='READY' and status['selftest_bitwise_mismatches']==0,'current READY selftest')
    need(trial['quality_receipt']==pin((WORK/'quality_campaign_run_scoped/closed.json').read_bytes())
         and trial['performance_receipt']==pin((WORK/'latency_campaign_three_arm/closed.json').read_bytes()),'validation receipts unchanged')
    actual_raw=(WORK/'service_quality/closed.json').read_bytes();actual=json.loads(actual_raw)
    need(actual['status']=='PASS' and actual['images']==195 and actual['FP32_logits_compared']==195000
         and actual['bitwise_mismatches']==0 and actual['boot_id']==boot and actual['installed_files']==receipt['files'],
         'actual final installed pair fixed195 proof')
    need(review['quality']['service_quality_receipt']==pin(actual_raw),'independent quality receipt identity')
    need(review['model']['selected_candidate_arm']==('dynamic' if trial['ORT_dynamic_block_base']==4 else 'scoped'),'reviewed selected thread policy')
    receipt['final_optimization']=dict(receipt.pop('final_optimization_trial'),final_adoption=True,
                                      adoption_review=pin(review_path.read_bytes()),validated_boot_id=boot,
                                      actual_service_quality_receipt=pin(actual_raw))
    receipt['status']='PASS'
    publish(APP/'install_receipt.json',(json.dumps(receipt,sort_keys=True)+'\n').encode())
    backend.verify_install()
    result={'status':'PASS','final_adoption':True,'profile':receipt['final_optimization']['profile'],
            'boot_id':boot,'install_receipt':pin((APP/'install_receipt.json').read_bytes()),
            'changed_runtime_files':False,'normal_reboot_required':False,'adoption_review':pin(review_path.read_bytes())}
    publish(WORK/'adoption_receipt.json',(json.dumps(result,indent=2)+'\n').encode())
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
