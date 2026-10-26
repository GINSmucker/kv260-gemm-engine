"""Synthetic record comparison only; no service, socket, process or PL actions."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import compare as cmp
import perf_proof as proof
from test_perf_proof import receipt


def identity(digest='a'*64, size=128):
    return {'bytes': size, 'sha256': digest}


def sample(mode, token, runner_ms):
    files = {'input_zero.npy': identity(cmp.INPUT), 'native_owner': identity('1'*64),
             'plugin.so': identity('2'*64), 'metadata.xclbin': identity(cmp.METADATA),
             'model/weights.bin': identity(cmp.WEIGHTS), 'model/manifest.json': identity(cmp.MANIFEST),
             'model/reference.onnx': identity(cmp.REFERENCE),
             'model/graphs/model_w8a8_pl.onnx': identity(cmp.GRAPH)}
    binaries = {'opt50_model_owner': files['native_owner'], 'libopt50_model.so': files['plugin.so']}
    build = {'receipt': identity('3'*64), 'binaries': binaries}
    source = {'source_root': '/fixed/source', 'source_release': identity('4'*64), 'board_build': build}
    plan = {'schema': 'opt50_fair_model_plan_v1', 'boot_id': cmp.BOOT, 'token': token,
            'execution_mode': mode, 'measurement_profile': cmp.PROFILE,
            'expected_PL_starts': 36, 'run_id': 1, 'logical_weights_sha256': cmp.WEIGHTS,
            'reference_sha256': cmp.REFERENCE, 'input_pin': files['input_zero.npy'],
            'plugin_pin': files['plugin.so'], 'model_manifest_pin': files['model/manifest.json'],
            'inputs': {'files': files, 'board_build': build, 'source_release': source['source_release']},
            'source_summary': source, 'interpreter': {'target': '/usr/bin/python3.12'},
            'command_tools': {'systemctl': {'entry': '/usr/bin/systemctl'}},
            'guardian_pid': 2988, 'guardian_start_ticks': 12345, 'load_guardian_plan': '/fixed/load/plan.json',
            'tool_files': {'manifest': identity('5'*64), 'files': {name: identity('6'*64) for name in
                ('run_model.py', 'model_worker.py', 'perf_proof.py', 'compare.py', 'resident.py')}}}
    common = {'boot_id': cmp.BOOT, 'plan_token': token, 'execution_mode': mode,
              'measurement_profile': cmp.PROFILE, 'single_sample_only': True, 'performance_verified': False,
              'input_sha256': cmp.INPUT, 'reference_sha256': cmp.REFERENCE,
              'logical_weights_sha256': cmp.WEIGHTS, 'model_manifest_sha256': cmp.MANIFEST,
              'logits_raw_sha256': 'b'*64, 'logits_file': identity('c'*64, 4128)}
    cpu = dict(common, numpy_version='1.26.4', ort_version='1.22.1', CPU_session_closed=True,
               shape=[1, 1000], dtype='float32', reference_ns=300_000_000, reference_ms=300.0,
               reference_setup_ns=600_000_000, reference_setup_ms=600.0)
    layers, raw = [], []
    for i in range(49):
        numbers = [100, 200, 100, 500]
        raw.extend(numbers)
        layers.append(dict(layer_index=i, route='PL' if i in cmp.PL else 'PS',
                           **{key+'_ns': ns for key, ns in zip(cmp.PLUGIN_FIELDS, numbers)}))
    aggregate = [4900, 9800, 4900, 24500]
    plugin = {'schema': 'opt50_perf_plugin_timing_v1', 'status': 'AVAILABLE_PLUGIN_MEMORY_ONLY',
              'captured_after_RUN_before_close': True, 'hardware_commands_issued': 0,
              'aggregate_ns': dict(zip(cmp.PLUGIN_FIELDS, aggregate)), 'raw_aggregate_u64': aggregate,
              'raw_layers_u64': raw, 'layers': layers}
    worker = dict(common, custom_graph_sha256=cmp.GRAPH, Runner_closed=True, runner_calls=1,
                  compared_logits=1000, bitwise_mismatches=0, max_abs_difference=0.0,
                  accuracy_verified=False, runner_run_ns=int(runner_ms*1_000_000), runner_run_ms=runner_ms,
                  setup_ns=2_000_000_000, setup_ms=2000.0, timing_scope='one full Runner.run', plugin_timing=plugin)
    held = {'schema': 'opt50_fair_resident_review_v1', 'status': 'PASS_OS_REVIEW_ONLY_NO_CSR_NO_START',
            'boot_id': cmp.BOOT, 'load_plan': plan['load_guardian_plan'], 'old_board_lock_transferred': False,
            'guardian': {'pid': 2988, 'start_ticks': 12345, 'boot_id': cmp.BOOT, 'old_board_lock_transferred': False},
            'resident_OS_observations': {'clock_framework': {'rate_hz': 124999999, 'enable_count': 1, 'prepare_count': 1}}}
    compact = receipt(mode)
    native = proof.compact(compact, mode)
    native.update(device_closed=True, native_event_count=500)
    metrics = {'completed_PL_calls': 36, 'wire_version': 50, 'layer_call_sequence': cmp.PL[:],
               'owner_Run_dispatch_ms': 20.0,
               'layers': [{'layer_index': i, 'mode': int(mode == 'mixed' and i in cmp.SPARSE),
                           'owner_Run_dispatch_ms': 0.5} for i in cmp.PL]}
    report = {'schema': 'opt50_fair_model_completion_v1', 'boot_id': cmp.BOOT, 'plan_token': token,
              'execution_mode': mode, 'measurement_profile': cmp.PROFILE, 'single_sample_only': True,
              'accuracy_verified': False, 'performance_verified': False, 'owner_exit_code': 0, 'owner_pid': 4000,
              'CPU_reference': cpu, 'worker': worker, 'native': native, 'held_after': held,
              'owner_metrics': metrics, 'latency_ms': worker['runner_run_ms'],
              'CPU_reference_ms': cpu['reference_ms'], 'Runner_setup_ms': worker['setup_ms'],
              'CPU_reference_setup_ms': cpu['reference_setup_ms'], 'latency_scope': worker['timing_scope'],
              'plugin_timing': plugin}
    return copy.deepcopy((plan, report, compact))


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.dense = sample('all_dense', '111111111111', 160.0)
        self.mixed = sample('mixed', '222222222222', 140.0)

    def compare(self):
        return cmp.compare_records(self.dense[0], self.dense[1], self.mixed[0], self.mixed[1],
                                   dense_compact=self.dense[2], mixed_compact=self.mixed[2])

    def test_one_pair_reports_real_delta_and_never_claims_mean_p95_or_accuracy(self):
        result = self.compare()
        self.assertEqual(result['Runner_latency_reduction_ms'], 20)
        self.assertAlmostEqual(result['Runner_speedup_Dense_over_Mixed'], 160/140)
        self.assertEqual(result['Runner_latency_reduction_percent'], 12.5)
        self.assertTrue(result['same_logical_pruned_weights'])
        self.assertFalse(result['accuracy_verified'])
        self.assertFalse(result['stable_performance_verified'])
        self.assertFalse(result['hardware_or_processes_modified'])
        self.assertEqual(result['Dense_all36']['PL_sparse'], 0)
        self.assertEqual(result['Mixed_24D_12S']['PL_sparse'], 12)
        self.assertEqual(result['logical_PL_weight_payload_bytes']['selected_12_layers_reduction_percent'], 37.5)
        self.assertEqual(result['logical_PL_weight_payload_bytes']['all_36_PL_layers_reduction_percent'], 12.5)

    def test_negative_savings_are_not_hidden(self):
        self.mixed = sample('mixed', '222222222222', 170.0)
        result = self.compare()
        self.assertEqual(result['Runner_latency_reduction_ms'], -10)
        self.assertLess(result['Runner_speedup_Dense_over_Mixed'], 1)
        self.assertLess(result['Runner_latency_reduction_percent'], 0)

    def test_pure_function_does_not_change_input_records(self):
        before = copy.deepcopy((self.dense, self.mixed))
        self.compare()
        self.assertEqual((self.dense, self.mixed), before)

    def test_runner_setup_cpu_setup_and_reference_timing_copies_cannot_be_forged(self):
        for field in ('latency_ms', 'Runner_setup_ms', 'CPU_reference_setup_ms', 'CPU_reference_ms'):
            with self.subTest(field=field):
                prior = self.mixed[1][field]
                self.mixed[1][field] = prior+1
                with self.assertRaises(ValueError):
                    self.compare()
                self.mixed[1][field] = prior

    def test_nonfinite_nonpositive_or_bool_duration_is_refused(self):
        for wrong in (0, -1, float('nan'), float('inf'), True):
            with self.subTest(wrong=wrong):
                pair = sample('mixed', '222222222222', 140.0)
                self.mixed = pair
                pair[1]['worker']['runner_run_ms'] = wrong
                pair[1]['latency_ms'] = wrong
                with self.assertRaises(ValueError):
                    self.compare()

    def test_mode_slots_boot_profile_and_plan_identity_are_fixed(self):
        for key, wrong in (('execution_mode', 'all_dense'), ('boot_id', 'old'),
                           ('measurement_profile', 'old-profile'), ('token', '111111111111')):
            self.mixed = sample('mixed', '222222222222', 140.0)
            self.mixed[0][key] = wrong
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.compare()

    def test_paired_tools_compiler_source_and_binary_pins_must_match(self):
        for change in (
            lambda p: p['tool_files']['files']['compare.py'].update(sha256='e'*64),
            lambda p: p['source_summary'].update(source_root='/another/build'),
            lambda p: p['interpreter'].update(target='/different/python'),
            lambda p: p['command_tools']['systemctl'].update(entry='/another/systemctl'),
            lambda p: p['inputs']['board_build']['binaries']['libopt50_model.so'].update(sha256='e'*64),
            lambda p: p['inputs']['source_release'].update(sha256='e'*64)):
            self.mixed = sample('mixed', '222222222222', 140.0)
            change(self.mixed[0])
            with self.assertRaises(ValueError):
                self.compare()

    def test_wrong_logical_weight_graph_reference_or_input_refused(self):
        for name in ('input_zero.npy', 'model/weights.bin', 'model/manifest.json', 'model/reference.onnx',
                     'model/graphs/model_w8a8_pl.onnx', 'metadata.xclbin'):
            self.mixed = sample('mixed', '222222222222', 140.0)
            self.mixed[0]['inputs']['files'][name]['sha256'] = 'e'*64
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.compare()

    def test_guardian_pid_start_ticks_and_clock_are_bound_in_both_reports(self):
        for change in (
            lambda r: r['held_after']['guardian'].update(pid=9999),
            lambda r: r['held_after']['guardian'].update(start_ticks=9999),
            lambda r: r['held_after'].update(boot_id='old'),
            lambda r: r['held_after'].update(old_board_lock_transferred=True),
            lambda r: r['held_after']['resident_OS_observations']['clock_framework'].update(rate_hz=100000000),
            lambda r: r['held_after']['resident_OS_observations']['clock_framework'].update(enable_count=0)):
            self.mixed = sample('mixed', '222222222222', 140.0)
            change(self.mixed[1])
            with self.assertRaises(ValueError):
                self.compare()

    def test_closed_and_exact_result_proof_required(self):
        for change in (
            lambda r: r.update(owner_exit_code=1),
            lambda r: r['worker'].update(Runner_closed=False),
            lambda r: r['CPU_reference'].update(CPU_session_closed=False),
            lambda r: r['worker'].update(bitwise_mismatches=1),
            lambda r: r['worker'].update(max_abs_difference=0.01),
            lambda r: r['native'].update(device_closed=False),
            lambda r: r['native'].update(postrun_buffer_checks_verified=False),
            lambda r: r['native'].update(PL_sparse=0),
            lambda r: r['native'].update(graph_owner_sync_calls=1)):
            self.mixed = sample('mixed', '222222222222', 140.0)
            change(self.mixed[1])
            with self.assertRaises(ValueError):
                self.compare()

    def test_both_models_matching_their_own_cpu_but_different_each_other_is_rejected(self):
        report = self.mixed[1]
        report['worker']['logits_raw_sha256'] = report['CPU_reference']['logits_raw_sha256'] = 'e'*64
        with self.assertRaisesRegex(ValueError, 'paired scope or exact model output'):
            self.compare()

    def test_ram_mode_order_and_active_payload_cannot_be_relabelled(self):
        for change in (
            lambda v: v.update(active_data_sha256=cmp.ACTIVE['all_dense']),
            lambda v: v.update(active_data_capacity_bytes=3317760),
            lambda v: v.update(poll_delay_us=100),
            lambda v: v['ram_events'][1].update(monotonic_ns=0),
            lambda v: v['ram_events'][1].update(mode=1),
            lambda v: v['ram_events'][1].update(layer=26),
            lambda v: v.update(postrun_checks_started_ns=1000)):
            self.mixed = sample('mixed', '222222222222', 140.0)
            change(self.mixed[2])
            with self.assertRaises(ValueError):
                self.compare()

    def test_native_intervals_are_host_observations_not_pure_cycles(self):
        result = self.compare()['Mixed_24D_12S']['native_intervals_ms_overlapping']
        self.assertEqual(result['sum_START_returned_to_completion_ms_all36'], 36/1_000_000)
        self.assertEqual(result['sum_START_returned_to_completion_ms_selected12'], 12/1_000_000)
        self.assertIn('NOT pure PL', result['scope'])

    def test_plugin_per_layer_sum_and_scope_are_checked(self):
        self.mixed[1]['plugin_timing']['layers'][0]['custom_op_total_ns'] += 1
        with self.assertRaisesRegex(ValueError, 'aggregate/per-layer'):
            self.compare()

    def test_missing_plugin_measurement_is_reported_missing_not_zero(self):
        missing = {'schema': 'opt50_perf_plugin_timing_v1', 'status': 'UNKNOWN_NOT_EXPORTED',
                   'aggregate_ns': None, 'layers': None}
        self.mixed[1]['plugin_timing'] = self.mixed[1]['worker']['plugin_timing'] = missing
        value = self.compare()['Mixed_24D_12S']['plugin_intervals_ms_overlapping']
        self.assertIsNone(value['aggregate_ms'])
        self.assertIsNone(value['PS_13_Linear_ms'])

    def test_cli_error_has_no_retry_or_hardware_side_effect(self):
        with patch.object(cmp, 'compare', side_effect=ValueError('unclosed owner')) as operation, \
             patch('builtins.print') as output:
            code = cmp.main(['--dense-plan', '/dense/plan.json', '--mixed-plan', '/mixed/plan.json'])
        self.assertEqual(code, 1)
        operation.assert_called_once_with(Path('/dense/plan.json'), Path('/mixed/plan.json'))
        self.assertIn('FAIR_COMPARISON_REFUSED_READ_ONLY', output.call_args.args[0])


class ClosedArmReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='compare_reader_', dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plan, self.report, self.compact = sample('all_dense', '111111111111', 160.0)
        self.folder = self.root/'execution'
        (self.folder/'events').mkdir(parents=True)
        self.plan.update(plan_path=str(self.root/'plan.json'), execution=str(self.folder), unit='opt50-fair-111111111111')
        self.state = {'schema': 'opt50_fair_model_status_v1', 'stage': cmp.PASS, 'boot_id': cmp.BOOT,
                      'unit': self.plan['unit'], 'execution_mode': 'all_dense', 'owner_pid': None,
                      'child_pid': None, 'completion': self.report}
        encode = lambda value: json.dumps(value, sort_keys=True).encode()
        self.raw = {}
        self.values = {self.folder/'status.json': self.state, self.folder/'completion.json': self.report,
                       self.folder/'held_after.json': self.report['held_after'],
                       self.folder/'owner_metrics.json': self.report['owner_metrics'],
                       self.folder/'performance_native.json': self.compact}
        rows = []
        for name, value in (('cpu_worker', self.report['CPU_reference']), ('model_worker', self.report['worker'])):
            self.raw[self.folder/(name+'.stdout')] = encode(value)
            self.raw[self.folder/(name+'.stderr')] = b''
            rows.append({'action': name, 'phase': 'RETURNED', 'returncode': 0,
                         **{suffix: cmp_pin(self.raw[self.folder/(name+'.'+suffix)]) for suffix in ('stdout', 'stderr')}})
        rows.append({'action': 'owner', 'phase': 'RETURNED', 'returncode': 0, 'pid': self.report['owner_pid']})
        rows.append({'action': 'state', 'phase': cmp.PASS, 'state': self.state})
        for index, row in enumerate(rows, 1):
            row.update(schema='opt50_durable_event_v1', sequence=index)
            path = self.folder/'events'/('event_%06d.json' % index)
            path.touch()
            self.values[path] = row
        self.raw[self.folder/'owner.stdout'] = b'KV5_READY 4000\nKV5_STOP calls=36\n'
        self.raw[self.folder/'owner.stderr'] = b''
        self.c = types.SimpleNamespace(read_json=lambda path, **_: copy.deepcopy(self.values[Path(path)]),
            read_file=lambda path, **_: self.raw[Path(path)], decode_json=json.loads, identity=cmp_pin,
            compiled_module=lambda *_: types.SimpleNamespace(validate_warnings=self.warnings))
        self.window = types.SimpleNamespace(load_plan=Mock(return_value=self.plan),
            validate_cpu=Mock(return_value=self.report['CPU_reference']), validate_worker=Mock(return_value=self.report['worker']),
            native_completion=Mock(return_value=self.report['native']), terminal_service=Mock(return_value={'MainPID': '0'}))

    @staticmethod
    def warnings(raw):
        if raw != b'':
            raise ValueError('unexpected diagnostics')

    def read(self):
        return cmp.read_closed_arm(self.window, self.c, self.root/'plan.json', 'all_dense')

    def test_reader_revalidates_native_cpu_worker_logs_and_terminal_service(self):
        value = self.read()
        self.assertEqual(value[:3], (self.plan, self.report, self.compact))
        self.window.load_plan.assert_called_once_with(self.c, self.root/'plan.json', fresh=False)
        self.window.native_completion.assert_called_once_with(self.c, self.folder, closed=True)
        self.window.validate_cpu.assert_called_once_with(self.c, self.plan, self.folder)
        self.window.validate_worker.assert_called_once_with(self.c, self.plan, self.folder)
        self.window.terminal_service.assert_called_once_with(self.c, self.plan, success=True)

    def test_wrong_mode_slot_refused_before_record_reads(self):
        with self.assertRaisesRegex(ValueError, 'wrong comparison slot'):
            cmp.read_closed_arm(self.window, self.c, self.root/'plan.json', 'mixed')
        self.window.native_completion.assert_not_called()

    def test_retained_or_nonempty_owner_diagnostics_refused(self):
        self.state['stage'] = 'RETAINED_MODEL_STATE_UNCERTAIN'
        with self.assertRaisesRegex(ValueError, 'closed successful'):
            self.read()
        self.state['stage'] = cmp.PASS
        self.raw[self.folder/'owner.stderr'] = b'failed guard check'
        with self.assertRaisesRegex(ValueError, 'owner normal'):
            self.read()

    def test_stdout_forgery_and_unknown_warning_refused(self):
        original = self.raw[self.folder/'model_worker.stdout']
        self.raw[self.folder/'model_worker.stdout'] = b'{}'
        with self.assertRaisesRegex(ValueError, 'stdout/saved'):
            self.read()
        self.raw[self.folder/'model_worker.stdout'] = original
        self.raw[self.folder/'model_worker.stderr'] = b'new unexpected warning'
        with self.assertRaisesRegex(ValueError, 'unexpected diagnostics'):
            self.read()

    def test_terminal_service_and_final_journal_must_be_proven(self):
        self.window.terminal_service.side_effect = ValueError('coordinator still running')
        with self.assertRaisesRegex(ValueError, 'still running'):
            self.read()
        self.window.terminal_service.side_effect = None
        self.values[self.folder/'events/event_000004.json']['state'] = {}
        with self.assertRaisesRegex(ValueError, 'last durable'):
            self.read()

    def test_all_sibling_history_is_checked_before_two_arm_summary(self):
        import run_model as window
        other = sample('mixed', '222222222222', 140.0)
        c = types.SimpleNamespace(root_linux=Mock())
        with patch.object(window, 'api', return_value=c), patch.object(window, 'verify_tools') as verify, \
             patch.object(window, 'previous_models_clear') as previous, \
             patch.object(Path, 'read_text', return_value=cmp.BOOT+'\n'), \
             patch.object(cmp, 'read_closed_arm', side_effect=[(*sample('all_dense', '111111111111', 160.0), {}), (*other, {})]) as read:
            result = cmp.compare(Path('/dense/plan.json'), Path('/mixed/plan.json'))
        c.root_linux.assert_called_once()
        verify.assert_called_once_with(c)
        previous.assert_called_once_with(c)
        self.assertEqual(read.call_count, 2)
        self.assertTrue(result['saved_evidence_only'])


def cmp_pin(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


if __name__ == '__main__':
    unittest.main()
