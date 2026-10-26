"""Synthetic fair receipts only; no owner, service, socket or device actions."""
import copy
import hashlib
import json
from pathlib import Path
import types
import unittest
from unittest.mock import Mock
import perf_proof as p


def receipt(execution='mixed'):
    rows = [('MODEL_BEGIN', 49, 0)]
    for command, layer in enumerate(p.PL, 1):
        rows.extend((phase, layer, command) for phase in ('DMA_ACTIVE_BEFORE_START', 'START_WRITE_INTENT',
                    'START_WRITE_RETURNED', 'DMA_COMPLETION_PROVEN', 'LAYER_COMPLETION_VERIFIED'))
    rows.append(('MODEL_END_COMPLETED', 49, 36))
    dense, sparse = p.route_counts(execution)
    return dict(schema='opt50_fair_native_v1', profile=p.PROFILE, boot_id=p.BOOT, run_id=1,
        execution_mode=execution, active_data_sha256=p.ACTIVE_DATA_SHA[execution],
        active_data_capacity_bytes=3981312, active_metadata_sha256=p.META_SHA,
        START_commands_issued=36, completed_PL_calls=36, PL_dense=dense, PL_sparse=sparse,
        device_closed=False, native_END_completed=True, guards_verified_after_graph=True,
        static_weights_verified_after_graph=True, postrun_buffer_checks_verified=True,
        graph_owner_sync_calls=0, graph_owner_sync_bytes=0, per_layer_input_sha_checks=0,
        per_layer_guard_checks=0, per_layer_y_poison_writes=0, postrun_owner_sync_calls=4,
        postrun_owner_sync_bytes=4722688, postrun_guard_checks=4, postrun_static_weight_sha_checks=2,
        initial_weight_to_device_sync_calls=2, initial_weight_to_device_sync_bytes=4153344,
        postrun_checks_started_ns=2000, postrun_checks_completed_ns=3000,
        hot_path_fsync_count=0, hot_path_disk_events=0, poll_delay_us=50, deadline_seconds=120,
        ram_event_capacity=256, hot_operations_count=5600,
        ram_events=[dict(phase=name, layer=layer, command=command,
                        mode=p.layer_mode(execution, layer), monotonic_ns=1000+i)
                    for i, (name, layer, command) in enumerate(rows)])


class ProofTests(unittest.TestCase):
    def test_both_arms_have_exact_182_events_fixed_bo_budget_and_mode_counts(self):
        for execution, counts in (('all_dense', (36, 0)), ('mixed', (24, 12))):
            with self.subTest(execution=execution):
                result = p.compact(receipt(execution), execution)
                self.assertEqual((result['PL_dense'], result['PL_sparse']), counts)
                self.assertEqual(result['execution_mode'], execution)
                self.assertEqual(result['compact_lifecycle_events'], 182)
                self.assertEqual(result['completed_PL_calls'], 36)
                self.assertEqual(result['active_data_capacity_bytes'], 3981312)
                self.assertEqual(result['postrun_owner_sync_bytes'], 4722688)
                self.assertEqual(result['initial_weight_to_device_sync_bytes'], 4153344)
                self.assertFalse(result['device_closed'])
                self.assertEqual(result['graph_owner_sync_calls'], 0)
                self.assertTrue(result['postrun_buffer_checks_verified'])
                self.assertNotIn('guards_verified_at_END', result)

    def test_native_arm_must_match_immutable_plan_not_only_self_declared_counts(self):
        for actual, wanted in (('mixed', 'all_dense'), ('all_dense', 'mixed')):
            with self.assertRaisesRegex(ValueError, 'frozen plan'):
                p.compact(receipt(actual), wanted)
        value = receipt()
        value.update(execution_mode='all_dense', PL_dense=36, PL_sparse=0,
                     active_data_sha256=p.ACTIVE_DATA_SHA['all_dense'])
        with self.assertRaisesRegex(ValueError, 'mode/order'):
            p.compact(value, 'all_dense')  # Old Sparse trace cannot be relabelled.

    def test_each_identity_counter_and_safety_change_refused(self):
        for execution in p.ACTIVE_DATA_SHA:
            changes = [('START_commands_issued', 37), ('completed_PL_calls', 35), ('PL_dense', 0),
                ('PL_sparse', 36), ('execution_mode', 'dense'), ('active_data_sha256', '0'*64),
                ('active_metadata_sha256', '0'*64), ('active_data_capacity_bytes', 3317760),
                ('device_closed', True), ('hot_path_fsync_count', 1), ('hot_path_disk_events', 1),
                ('deadline_seconds', 1), ('poll_delay_us', 10000), ('guards_verified_after_graph', False),
                ('static_weights_verified_after_graph', False), ('postrun_buffer_checks_verified', False),
                ('native_END_completed', False), ('profile', 'h576-aligned-low-overhead-v2'),
                ('schema', 'opt50_performance_native_v2'), ('boot_id', '1db5ef5e-1934-4a3d-b374-692eb8f184be'),
                ('graph_owner_sync_calls', 1), ('graph_owner_sync_bytes', 64),
                ('per_layer_input_sha_checks', 1), ('per_layer_guard_checks', 1),
                ('per_layer_y_poison_writes', 1), ('postrun_owner_sync_calls', 3),
                ('postrun_owner_sync_bytes', 4059136), ('postrun_guard_checks', 3),
                ('postrun_static_weight_sha_checks', 1), ('initial_weight_to_device_sync_calls', 4),
                ('initial_weight_to_device_sync_bytes', 3489792), ('run_id', True),
                ('graph_owner_sync_calls', False), ('postrun_checks_started_ns', 1181),
                ('postrun_checks_completed_ns', 2000)]
            for key, wrong in changes:
                with self.subTest(execution=execution, key=key):
                    value = receipt(execution); value[key] = wrong
                    with self.assertRaises(ValueError):
                        p.compact(value, execution)

    def test_all_integer_counters_reject_bool(self):
        source = receipt()
        for key, expected in source.items():
            if type(expected) is not int:
                continue
            with self.subTest(key=key):
                value = receipt(); value[key] = bool(expected)
                with self.assertRaises(ValueError):
                    p.compact(value)

    def test_extra_missing_keys_and_false_per_layer_proof_are_refused(self):
        for mutate in (lambda x: x.pop('execution_mode'),
                       lambda x: x.update(guards_verified_at_END=True),
                       lambda x: x.update(extra=True)):
            value = receipt(); mutate(value)
            with self.assertRaises(ValueError):
                p.compact(value)

    def test_lost_extra_reordered_mode_or_nonmonotonic_events_refused(self):
        for execution in p.ACTIVE_DATA_SHA:
            changes = (lambda v: v['ram_events'].pop(),
                lambda v: v['ram_events'].append(v['ram_events'][-1]),
                lambda v: v['ram_events'][1].update(layer=2),
                lambda v: v['ram_events'][1].update(mode=1),
                lambda v: v['ram_events'][1].update(monotonic_ns=0),
                lambda v: v['ram_events'][2].update(phase='DMA_COMPLETION_PROVEN'),
                lambda v: v['ram_events'][1].update(command=True))
            for mutate in changes:
                value = receipt(execution); mutate(value)
                with self.subTest(execution=execution), self.assertRaises(ValueError):
                    p.compact(value, execution)

    def lifecycle(self, closed=False, hot=False):
        phases = ['NATIVE_ENTERED', 'NATIVE_READY', 'PERFORMANCE_COMPACT_PUBLISHED']
        if hot:
            phases.insert(2, 'MODEL_BEGIN')
        if closed:
            phases += ['RESOURCE_CLOSE_INTENT', 'RESOURCES_CLOSED']
        rows = [dict(phase=phase, details='') for phase in phases]
        if closed:
            rows[-1]['details'] = 'released_BO_count=4; exported client handles must already be closed; resident firmware unchanged'
        return rows

    def reader(self, execution='mixed'):
        return types.SimpleNamespace(read_json=lambda *_a, **_kw: receipt(execution),
            read_file=lambda *_a, **_kw: b'proof',
            identity=lambda raw: {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})

    def test_file_boundary_requires_expected_arm_before_any_file_access(self):
        c = types.SimpleNamespace(read_json=Mock(), read_file=Mock())
        for expected in (None, '', 'dense', True):
            with self.subTest(expected=expected), self.assertRaises(ValueError):
                p.native_completion(c, '/fake', lambda *_: self.lifecycle(), execution_mode=expected)
        c.read_json.assert_not_called(); c.read_file.assert_not_called()

    def test_INFO_proof_and_normal_close_proof_require_matching_arm(self):
        for execution in p.ACTIVE_DATA_SHA:
            before = p.native_completion(self.reader(execution), '/fake',
                        lambda *_: self.lifecycle(), execution_mode=execution)
            after = p.native_completion(self.reader(execution), '/fake',
                        lambda *_: self.lifecycle(closed=True), closed=True, execution_mode=execution)
            self.assertFalse(before['device_closed'])
            self.assertTrue(after['device_closed'])
            self.assertEqual(after['execution_mode'], execution)
            self.assertEqual(after['native_event_count'], 5)
        with self.assertRaisesRegex(ValueError, 'frozen plan'):
            p.native_completion(self.reader('mixed'), '/fake',
                        lambda *_: self.lifecycle(), execution_mode='all_dense')

    def test_hot_disk_premature_missing_and_bad_close_proofs_refused(self):
        bad_close = self.lifecycle(closed=True); bad_close[-1]['details'] = 'not closed'
        for events, closed in (([], False), (self.lifecycle(hot=True), False),
                               (self.lifecycle(closed=True), False), (self.lifecycle(), True),
                               (bad_close, True)):
            with self.subTest(events=events), self.assertRaises(ValueError):
                p.native_completion(self.reader(), '/fake', lambda *_: events,
                                    closed=closed, execution_mode='mixed')

    def test_pinned_active_hashes_rederive_from_unchanged_original_canonical_model(self):
        model = Path(__file__).resolve().parents[1]/'full_model_v1/generated_model_v1'
        if not model.is_dir():
            self.skipTest('frozen local model absent; source-independent fixture test unavailable')
        manifest = json.loads((model/'manifest.json').read_bytes())
        canonical = (model/'weights.bin').read_bytes()
        self.assertEqual(hashlib.sha256(canonical).hexdigest(), '88a553bfd6bf0df05017b0497582fe09ac03cc6fdeb8e84e6bcf23ac022320f6')
        dense = b''.join(canonical[row['canonical_offset']:row['canonical_offset']+row['k']*row['n']]
                         for row in manifest['layers'] if row['route'] == 'PL')
        mixed = (model/'pl_w_data.bin').read_bytes()
        self.assertEqual(len(dense), p.DATA_CAPACITY)
        self.assertEqual(hashlib.sha256(dense).hexdigest(), p.ACTIVE_DATA_SHA['all_dense'])
        self.assertEqual(hashlib.sha256(mixed+bytes(p.DATA_CAPACITY-len(mixed))).hexdigest(), p.ACTIVE_DATA_SHA['mixed'])
        self.assertEqual(hashlib.sha256((model/'pl_w_meta.bin').read_bytes()).hexdigest(), p.META_SHA)


if __name__ == '__main__':
    unittest.main()
