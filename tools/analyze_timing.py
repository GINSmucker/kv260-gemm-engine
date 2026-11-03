"""Decompose the matched 195-image Runner intervals from pinned board receipts."""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import statistics
import tarfile

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_SHA = '3dd66ac7e784153ce6d479be2017f54d678870cbc02d4f02dbdc681d366f591b'
SPARSE_SHA = 'f14b1a7e3ec26fd7205e4b32ca57b0ae5ab55972cbcb353fbcb406003bc182fb'
PARTS = ('start_to_completion_ms', 'native_prepare_ms', 'native_verify_ms',
         'between_calls_ms', 'runner_boundary_ms')
PHASES = ('DMA_ACTIVE_BEFORE_START', 'START_WRITE_INTENT', 'START_WRITE_RETURNED',
          'DMA_COMPLETION_PROVEN', 'LAYER_COMPLETION_VERIFIED')


def need(value, message):
    if not value:
        raise ValueError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def decompose(receipt, runner_ns, sparse):
    events = receipt['ram_events']
    need(len(events) == 182 and events[0]['phase'] == 'MODEL_BEGIN' and
         events[-1]['phase'] == 'MODEL_END_COMPLETED', 'exact BEGIN/36 calls/END required')
    times = [e['monotonic_ns'] for e in events]
    need(all(type(t) is int for t in times) and all(a < b for a, b in zip(times, times[1:])),
         'strictly monotonic receipt required')
    need(receipt['completed_PL_calls'] == receipt['START_commands_issued'] == 36 and
         receipt['PL_dense'] == (12 if sparse else 36) and receipt['PL_sparse'] == (24 if sparse else 0),
         'fixed model route required')
    totals = dict(start_to_completion_ms=0, native_prepare_ms=0, native_verify_ms=0)
    layer_rows = []
    route = [i for i in range(48) if i % 4 != 1]
    for call, layer in enumerate(route):
        group = events[1+5*call:6+5*call]
        mode = int(sparse and layer % 4 in (2, 3))
        need(tuple(e['phase'] for e in group) == PHASES and
             all(e['layer'] == layer and e['command'] == call+1 and e['mode'] == mode for e in group),
             'ordered layer phases and modes required')
        active, intent, started, completed, verified = [e['monotonic_ns'] for e in group]
        values = {'start_to_completion_ms': (completed-started)/1e6,
                  'native_prepare_ms': (started-active)/1e6,
                  'native_verify_ms': (verified-completed)/1e6}
        for key, value in values.items():
            totals[key] += value
        layer_rows.append({'layer': layer, 'mode': mode, **values})
    graph_ms = (times[-1]-times[0])/1e6
    totals['between_calls_ms'] = graph_ms-sum(totals.values())
    totals['runner_boundary_ms'] = runner_ns/1e6-graph_ms
    need(all(value >= 0 for value in totals.values()), 'native graph must fit inside Runner')
    need(abs(sum(totals.values())-runner_ns/1e6) < 1e-7, 'disjoint intervals must close to Runner')
    need(receipt['postrun_checks_completed_ns'] >= receipt['postrun_checks_started_ns'] > times[-1],
         'buffer checks must follow END')
    return {**totals, 'runner_ms': runner_ns/1e6,
            'postrun_buffer_checks_ms': (receipt['postrun_checks_completed_ns']-receipt['postrun_checks_started_ns'])/1e6,
            'layers': layer_rows}


def read_receipts(raw):
    records = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
        for member in archive:
            name = member.name
            if name.endswith(('/worker.json', '/accuracy.json')) or '/execution/performance_native_' in name and name.endswith('.json'):
                need(member.isfile() and not member.issym() and not member.islnk() and member.size < 2*1024*1024,
                     'bounded regular receipt required')
                need(name not in records, 'duplicate receipt')
                records[name] = archive.extractfile(member).read()
    return records


def analyze(path):
    raw = Path(path).read_bytes()
    need(digest(raw) == ARCHIVE_SHA, 'exact paired500 archive required')
    outer = read_receipts(raw)
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
        sparse_raw = archive.extractfile('sparse_reference/evidence.tar.gz').read()
    need(digest(sparse_raw) == SPARSE_SHA, 'exact nested Sparse archive required')
    sparse_records = read_receipts(sparse_raw)
    paired = list(csv.DictReader((ROOT/'results/board500/paired195_cpu_pl.csv').read_text().splitlines()))
    need(len(paired) == 195 and [int(r['image_1based']) for r in paired] == list(range(306, 501)),
         'matched 306..500 sample range required')
    output = {'schema': 'opt50_matched195_timing_breakdown_v1', 'status': 'PASS',
              'source_revision': 'bd5d8cc056a12b19f757949324986079ea675b59',
              'archive_sha256': ARCHIVE_SHA, 'nested_sparse_sha256': SPARSE_SHA,
              'image_range_1based': [306, 500], 'samples_per_model': 195,
              'new_board_execution': False, 'pure_PL_cycles_measured': False,
              'definitions': {
                  'start_to_completion_ms': 'START write returned to completion proven; includes polling, MMIO and completion/drain checks, not pure PL compute.',
                  'native_prepare_ms': 'DMA active marker to START write returned.',
                  'native_verify_ms': 'Completion proven to layer completion verified.',
                  'between_calls_ms': 'Remaining BEGIN-to-END interval; PS operations, quant/dequant, IPC and sync are not separately identifiable.',
                  'runner_boundary_ms': 'Runner.run outside native BEGIN-to-END.',
                  'postrun_buffer_checks_ms': 'Post-END buffer-check interval outside Runner.run; not the entire INFO call.',
              }, 'models': {}}
    for label, records, prefix in (('Dense', outer, 'dense'), ('Sparse24', sparse_records, 'original')):
        workers = {}
        for name, data in records.items():
            if name.startswith(prefix+'/') and name.endswith('/worker.json'):
                worker = json.loads(data)
                ordinal = worker['sample_ordinal']
                if 305 <= ordinal <= 499:
                    need(ordinal not in workers, 'one accepted worker per selected image required')
                    workers[ordinal] = (name, worker)
        need(set(workers) == set(range(305, 500)), 'complete matched receipt set required')
        rows = []
        for pair in paired:
            ordinal = int(pair['image_1based'])-1
            name, worker = workers[ordinal]
            folder = name.rsplit('/', 1)[0]
            accepted = json.loads(records[folder+'/accuracy.json'])
            need(accepted['device_closed'] and accepted['bitwise_mismatches'] == 0 and
                 accepted['owner_exit_code'] == 0, 'accepted batch-close proof required')
            need(worker['sample_id'] == pair['id'] and worker['input_sha256'] == pair['input_sha256'] and
                 worker['bitwise_mismatches'] == 0, 'matching verified input required')
            column = 'Sparse_PS_PL_ms' if label == 'Sparse24' else 'Dense_PS_PL_ms'
            need(abs(worker['runner_run_ns']/1e6-float(pair[column])) < 1e-8, 'CSV timing identity required')
            plan = folder.split('/')[1]
            receipt_name = f'{prefix}/{plan}/execution/performance_native_{worker["run_id"]:06d}.json'
            receipt_raw = records[receipt_name]
            receipt = json.loads(receipt_raw)
            need(receipt['boot_id'] == worker['boot_id'] and receipt['run_id'] == worker['run_id'] and
                 receipt['source_model_sha256'] == worker['source_model_sha256'], 'worker/native identity required')
            row = decompose(receipt, worker['runner_run_ns'], label == 'Sparse24')
            row.update(image_1based=ordinal+1, id=pair['id'], receipt=receipt_name,
                       receipt_sha256=digest(receipt_raw), worker_sha256=digest(records[name]),
                       cpu_reference_ms=worker['CPU_reference_ms'])
            rows.append(row)
        keys = (*PARTS, 'runner_ms', 'postrun_buffer_checks_ms', 'cpu_reference_ms')
        means = {key: statistics.mean(row[key] for row in rows) for key in keys}
        layers = [{**{key: rows[0]['layers'][i][key] for key in ('layer', 'mode')},
                   **{key: statistics.mean(row['layers'][i][key] for row in rows) for key in PARTS[:3]}}
                  for i in range(36)]
        for row in rows:
            del row['layers']
        output['models'][label] = {'mean_ms': means, 'layers': layers, 'images': rows}
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--out', type=Path, default=ROOT/'results/board500/timing_breakdown.json')
    args = parser.parse_args()
    output = analyze(args.archive)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2)+'\n', encoding='utf-8', newline='\n')
    print(json.dumps({key: value['mean_ms'] for key, value in output['models'].items()}, indent=2))


if __name__ == '__main__':
    main()
