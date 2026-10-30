"""Merged preparation and truthful three-child lifecycle tests; no device access."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT/filename)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


r = module('image_pipeline_v3_entry', 'run_model.py')
w = module('image_pipeline_v3_worker', 'model_worker.py')


def write(path, value): path.write_text(json.dumps(value), encoding='utf-8')


class ImagePipelineTests(unittest.TestCase):
    def setUp(self):
        temporary = ROOT/'.test_build'; temporary.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temporary, prefix='pipeline_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plan = {'plan_path': str(self.root/'plan.json'), 'boot_id': 'fixture',
                     'unit': 'fixture-unit', 'guardian_pid': 73}
        self.events, self.statuses = [], []
        class Journal:
            def __init__(self, *args): pass
            def append(other, action, phase, **fields): self.events.append((action, phase, fields))
        self.c = types.SimpleNamespace(read_json=lambda p, **kwargs: json.loads(Path(p).read_bytes()),
                                       read_file=lambda p, **kwargs: Path(p).read_bytes(), decode_json=json.loads)
        self.a = types.SimpleNamespace(c=self.c,
            durable=types.SimpleNamespace(Journal=Journal, replace_json=lambda p,v: self.statuses.append(copy.deepcopy(v))),
            guard=types.SimpleNamespace(monitor=Mock(return_value={'scope': 'monitor'}),
                                        boundary=Mock(return_value={'scope': 'checkpoint'})))

    def test_prepare_orders_input_then_fresh_closed_cpu_and_reports_zero_hardware(self):
        order = []; inp = {'input': True}; cpu = {'CPU_session_closed': True}
        with patch.object(w, 'prepare_input', side_effect=lambda ctx: (order.append('input') or inp, 0)), \
             patch.object(w, 'prepare_cpu', side_effect=lambda ctx: (order.append('cpu') or cpu, 0)):
            result, code = w.prepare(object())
        self.assertEqual(order, ['input', 'cpu']); self.assertEqual(code, 0)
        self.assertEqual(result, {'schema': 'opt50_b24_merged_preparation_v1', 'input': inp,
                                 'CPU_reference': cpu, 'hardware_accessed': False,
                                 'PL_START_commands_issued_by_worker': 0})

    def test_prepare_failure_never_calls_cpu_or_claims_closed_session(self):
        with patch.object(w, 'prepare_input', side_effect=ValueError('input damaged')), patch.object(w, 'prepare_cpu') as cpu:
            with self.assertRaisesRegex(ValueError, 'input damaged'): w.prepare(object())
            cpu.assert_not_called()
        with patch.object(w, 'prepare_input', return_value=({},0)), \
             patch.object(w, 'prepare_cpu', return_value=({'CPU_session_closed': False},0)):
            with self.assertRaisesRegex(ValueError, 'did not complete and close'): w.prepare(object())

    def test_progress_does_not_sync_but_owner_intent_and_final_states_do(self):
        image = r.Coordinator(self.a, self.plan, 3)
        image.update('INPUT_AND_CPU_PREPARATION_RUNNING')
        self.assertEqual(self.events, []); self.assertEqual(self.statuses, [])
        self.assertFalse(image.state['durable_transition'])
        image.update('OWNER_INTENT_DURABLE')
        self.assertEqual(len(self.events), 1); self.assertEqual(len(self.statuses), 1)
        image.update(r.IMAGE_PASS, owner_pid=None, child_pid=None, completion={})
        self.assertEqual(len(self.events), 2); self.assertEqual(len(self.statuses), 2)

    def test_unbound_guardian_is_refused_and_tick_does_not_sync_logs(self):
        broken = types.SimpleNamespace(c=self.c, durable=self.a.durable)
        with self.assertRaisesRegex(ValueError, 'checkpoint must be bound'): r.Coordinator(broken, self.plan, 3)
        image = r.Coordinator(self.a, self.plan, 3); image.logs = [Mock()]
        with patch.object(r.os, 'fsync', side_effect=AssertionError('progress must not sync logs')):
            image.tick('MODEL_WORKER_RUNNING')
        self.a.guard.monitor.assert_called_once_with(self.plan)

    def parent_fixture(self):
        folder = self.root/'execution'; folder.mkdir(); (folder/'events').mkdir()
        input_record = {'input_checked': True}
        cpu, worker = {'CPU_session_closed': True}, {'Runner_closed': True}
        complete = {'owner_pid': 61, 'CPU_reference': cpu, 'worker': worker,
                    'input_preparation': input_record,
                    'parent_lifecycle': 'opt50_b24_merged_prepare_parent_v1'}
        state = {'stage': r.IMAGE_PASS, 'boot_id': self.plan['boot_id'], 'unit': self.plan['unit'],
                 'owner_pid': None, 'child_pid': None, 'completion': complete}
        events = []
        def event(action, phase, **fields): events.append(dict(action=action, phase=phase, **fields))
        def child(action, pid, phase):
            fields = {'argv': [action]} if phase == 'INTENT' else {'pid': pid}
            if phase == 'SPAWNED': fields['argv'] = [action]
            if phase == 'RETURNED': fields['returncode'] = 0
            if action == 'owner' and phase == 'INTENT': fields['expected_START_commands'] = 36
            event(action, phase, **fields)
        for phase in ('INTENT', 'SPAWNED', 'RETURNED'): child('prepare_worker', 51, phase)
        child('owner', 61, 'INTENT'); child('owner', 61, 'SPAWNED')
        for phase in ('INTENT', 'SPAWNED', 'RETURNED'): child('model_worker', 71, phase)
        event('management', 'STOP_INTENT', owner_pid=61, Runner_closed=True,
              native_END_completed=True, postrun_buffer_checks_verified=True)
        event('management', 'STOP_RETURNED', owner_pid=61)
        child('owner', 61, 'RETURNED'); event('state', r.IMAGE_PASS, state=state)
        preparation = {'schema': 'opt50_b24_merged_preparation_v1', 'input': input_record,
                       'CPU_reference': cpu, 'hardware_accessed': False,
                       'PL_START_commands_issued_by_worker': 0}
        write(folder/'status.json', state); write(folder/'prepare_worker.stdout', preparation)
        write(folder/'model_worker.stdout', worker)
        (folder/'owner.stdout').write_bytes(b'KV5_READY 61\nKV5_STOP calls=36\n')
        for name in ('prepare_worker', 'model_worker', 'owner'): (folder/(name+'.stderr')).write_bytes(b'')
        return folder, complete, events, input_record

    def publish_events(self, folder, events):
        for index, row in enumerate(events, 1):
            write(folder/'events'/('event_%06d.json' % index), dict(row,
                  schema='opt50_durable_event_v1', sequence=index))

    def test_complete_three_child_proof_passes_with_no_fabricated_cpu_child(self):
        folder, complete, events, inp = self.parent_fixture(); self.publish_events(folder, events)
        with patch.object(r, 'validate_input', return_value=inp):
            proof = r.sample_parent_proof(self.c, folder, self.plan, complete)
        self.assertTrue(proof['children_reaped_exit0']); self.assertTrue(proof['STOP_verified'])

    def test_failed_child_reap_is_refused(self):
        folder, complete, events, inp = self.parent_fixture()
        events[2]['returncode'] = 1; self.publish_events(folder, events)
        with patch.object(r, 'validate_input', return_value=inp), self.assertRaisesRegex(ValueError, 'exit/reap'):
            r.sample_parent_proof(self.c, folder, self.plan, complete)

    def test_mismatched_prepare_stdout_is_refused(self):
        folder, complete, events, inp = self.parent_fixture(); self.publish_events(folder, events)
        write(folder/'prepare_worker.stdout', {'wrong': True})
        with patch.object(r, 'validate_input', return_value=inp), self.assertRaisesRegex(ValueError, 'stdout/stderr'):
            r.sample_parent_proof(self.c, folder, self.plan, complete)

    def test_stop_before_model_worker_reap_is_refused(self):
        folder, complete, events, inp = self.parent_fixture()
        events[7], events[8] = events[8], events[7]
        self.publish_events(folder, events)
        with patch.object(r, 'validate_input', return_value=inp), self.assertRaisesRegex(ValueError, 'order differs'):
            r.sample_parent_proof(self.c, folder, self.plan, complete)

    def test_missing_lifecycle_marker_is_refused(self):
        folder, complete, events, inp = self.parent_fixture()
        complete.pop('parent_lifecycle')
        write(folder/'status.json', events[-1]['state'])
        self.publish_events(folder, events)
        with patch.object(r, 'validate_input', return_value=inp), self.assertRaisesRegex(ValueError, 'lifecycle marker'):
            r.sample_parent_proof(self.c, folder, self.plan, complete)


if __name__ == '__main__': unittest.main()
