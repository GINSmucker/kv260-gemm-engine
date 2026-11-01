"""Bounded current-service requests, preserving JPEG bytes and 100 ms query policy."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import uuid


def fetch(url, data=None, headers=None):
    request = Request(url, data=data, headers=headers or {})
    with urlopen(request, timeout=150) as response:
        return json.load(response)


def distribution(values):
    values = sorted(values)
    position = .95*(len(values)-1)
    low = int(position)
    return {'n':len(values), 'mean':statistics.mean(values), 'median':statistics.median(values),
            'P95':values[low]+(values[min(low+1,len(values)-1)]-values[low])*(position-low), 'max':max(values)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default='http://127.0.0.1:4179/api/sparse24/')
    parser.add_argument('--jpeg', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--count', type=int, default=6)
    args = parser.parse_args()
    if not 2 <= args.count <= 10:
        raise ValueError('bounded count 2..10 required')
    args.out.mkdir(parents=True, exist_ok=False)
    raw = args.jpeg.read_bytes()
    raw_sha = hashlib.sha256(raw).hexdigest()
    rows = []
    # No POST is retried, including after timeout or an uncertain response.
    for ordinal in range(args.count):
        waiting=time.monotonic()
        while True:
            status = fetch(args.base+'status')['status']
            if status['stage']!='READY':raise ValueError('service not READY; no request submitted')
            if not status['busy']:break
            if time.monotonic()-waiting>=30:raise ValueError('service busy; no request submitted')
            time.sleep(.1)
        token = uuid.uuid4().hex
        intent = {'request_id':token, 'boot_id':status['boot_id'], 'image_sha256':raw_sha,
                  'ordinal':ordinal, 'poll_period_ms':100}
        (args.out/(token+'.intent.json')).write_text(json.dumps(intent, indent=2))
        began = time.perf_counter_ns()
        fetch(args.base+'infer/'+token, raw, {'Content-Type':'image/jpeg', 'X-Sparse24-Boot':status['boot_id']})
        post_ms = (time.perf_counter_ns()-began)/1e6
        polls = 0;query_ms=0.0;poll_sleep_ms=0.0;query_errors=[]
        while True:
            query_started=time.perf_counter_ns()
            try:result = fetch(args.base+'result/'+token)['result']
            except HTTPError as error:
                if error.code not in (409,502,503):raise
                query_errors.append({'code':error.code,'body':error.read(4096).decode(errors='replace'),
                                     'elapsed_ms':(time.perf_counter_ns()-began)/1e6})
                result=None
            polls += 1
            query_ms+=(time.perf_counter_ns()-query_started)/1e6
            if result is not None and result['stage'] == 'PASS':
                break
            if (result is not None and result['stage'] not in ('SUBMITTED', 'QUEUED')) or time.perf_counter_ns()-began >= 150_000_000_000:
                (args.out/(token+'.query-errors.json')).write_text(json.dumps(query_errors,indent=2)+'\n')
                raise RuntimeError('request failed/uncertain; retain request ID, never resubmit: '+token)
            sleep_started=time.perf_counter_ns();time.sleep(.1)
            poll_sleep_ms+=(time.perf_counter_ns()-sleep_started)/1e6
        elapsed = (time.perf_counter_ns()-began)/1e6
        if query_errors:(args.out/(token+'.query-errors.json')).write_text(json.dumps(query_errors,indent=2)+'\n')
        (args.out/(token+'.result.json')).write_text(json.dumps(result, indent=2)+'\n')
        if not (result['image_sha256'] == raw_sha and result['boot_id'] == status['boot_id'] and
                result['bitwise_mismatches'] == 0 and result['CPU_logits_sha256'] == result['PL_logits_sha256']):
            raise ValueError('request identity or reported numerical comparison differs')
        row = {**intent, 'client_complete_ms':elapsed, 'POST_admission_ms':post_ms, 'polls':polls,
               'GET_query_ms':query_ms,'poll_sleep_ms':poll_sleep_ms,'GET_transient_errors':len(query_errors),
               'client_other_ms':elapsed-post_ms-query_ms-poll_sleep_ms,
               **{k:result[k] for k in ('session','run_id','Runner_run_ms','CPU_reference_ms','preprocessing_ms',
                                         'request_work_ms','input_sha256','decoded_RGB_sha256',
                                         'CPU_logits_sha256','PL_logits_sha256','source_model_sha256')}}
        row['role'] = 'first_observed_request' if ordinal == 0 else 'subsequent_request'
        for key in ('python_profile','measurement_profile','ORT_intra_spinning','ORT_force_spinning_stop','ORT_dynamic_block_base',
                    'INFO_ms','bundle_admission_ms','graph_intent_persistence_ms','logit_persistence_ms',
                    'Session_infer_ms','inference_other_ms','input_persistence_ms','session_lifecycle_ms','request_other_ms'):
            if key in result:row[key]=result[key]
        rows.append(row)
        (args.out/'samples.json').write_text(json.dumps(rows, indent=2)+'\n')
        print(json.dumps(row), flush=True)
    keys = ('client_complete_ms','Runner_run_ms','CPU_reference_ms','preprocessing_ms','request_work_ms')
    keys += tuple(k for k in ('POST_admission_ms','GET_query_ms','poll_sleep_ms','client_other_ms','INFO_ms',
                              'bundle_admission_ms','graph_intent_persistence_ms','logit_persistence_ms','Session_infer_ms',
                              'inference_other_ms','input_persistence_ms','session_lifecycle_ms','request_other_ms') if all(k in r for r in rows))
    report = {'schema':'opt50_final_request_baseline_v1', 'status':'PASS', 'base':args.base,
              'image_sha256':raw_sha, 'image_bytes':len(raw), 'samples':rows,
              'all_samples':{key:distribution([r[key] for r in rows]) for key in keys},
              'subsequent_samples':{key:distribution([r[key] for r in rows[1:]]) for key in keys},
              'scope':'Current service only; comparisons require paired reports; CPU/PL check is server-reported until independently downloaded logits are checked',
              'cold_scope':'First observed request is not guaranteed cold; session/run ID retained',
              'firmware_binary_identity':'Identity and environment require the separately collected protected installation/boot evidence'}
    (args.out/'report.json').write_text(json.dumps(report, indent=2)+'\n')


if __name__ == '__main__':
    main()
