"""Real file/prefix tests with simulated guardian, proc and OS observations.

Windows fixtures explicitly simulate POSIX owner/mode policy; these tests do not
claim Linux permission or hardware verification.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('guardian_checkpoint_test', ROOT/'guardian_checkpoint.py')
g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)


def write(path, value):
    path.write_text(json.dumps(value, sort_keys=True), encoding='utf-8')
    path.chmod(0o600)


class Fixture:
    def __init__(self, root):
        self.root = root; self.planfile = root/'plan.json'
        (root/'tools').mkdir(); (root/'execution').mkdir(); (root/'execution/events').mkdir()
        self.now = 1000.; self.full_count = self.collect_count = self.prefix_reads = 0
        self.live = {'boot_id': 'fixture-boot', 'pid': 73, 'start_ticks': 405,
                     'flock': {'pid': 73, 'inode': 999, 'device_major': 1, 'device_minor': 2}}
        self.state = {'schema': 'guardian-state', 'stage': 'LOAD_ONLY_OBSERVED_AWAITING_REVIEW',
                      'boot_id': 'fixture-boot', 'guardian_pid': 73, 'child_pid': None,
                      'heartbeat_monotonic': self.now, 'START': 'BLOCKED'}
        self.guardian = {'cases': str(root/'cases'), 'command_tools': {}}
        write(self.planfile, self.guardian)
        (root/'tools/board.py').write_bytes(b'# frozen load helper\n')
        (root/'tools/board.py').chmod(0o600)
        self.listing = {'files': {'board.py': g.identity((root/'tools/board.py').read_bytes())}}
        write(root/'tools/tools_manifest.json', self.listing)
        for n in range(1, 4): self.event(n, self.state)
        write(root/'execution/status.json', self.state)
        info = self.planfile.lstat()
        self.plan = {'guardian_plan': str(self.planfile), 'guardian_plan_pin': g.identity(self.planfile.read_bytes()),
                     'guardian_plan_inode': {'device': info.st_dev, 'inode': info.st_ino},
                     'boot_id': 'fixture-boot', 'guardian_pid': 73, 'guardian_start_ticks': 405,
                     'held_review': {'guardian': copy.deepcopy(self.live)}}
        def read_file(path, **kwargs):
            if Path(path).parent.name == 'events': self.prefix_reads += 1
            return Path(path).read_bytes()
        def read_json(path, **kwargs): return json.loads(read_file(path))
        def full():
            self.full_count += 1
            return {'guardian': copy.deepcopy(self.live), 'scope': 'complete fixture review'}
        def collect(*args):
            self.collect_count += 1
            return {'boot_id': self.live['boot_id'], 'driver_ok': True}
        def loaded(value, boot):
            g.need(value['boot_id'] == boot and value['driver_ok'], 'OS binding changed')
            return {'status': 'MATCH', 'boot': boot}
        self.board = types.SimpleNamespace(collect=collect, loaded=loaded)
        def validate(state, plan):
            g.need(isinstance(state, dict) and state['stage'] == self.state['stage'] and
                   0 <= self.now-state['heartbeat_monotonic'] <= 90, 'stale/foreign guardian state')
        def journal(plan):
            return read_json(root/'execution/status.json'), [read_json(p) for p in sorted((root/'execution/events').iterdir())]
        self.c = types.SimpleNamespace(root_linux=lambda: None, configure=lambda _: None, OLD_PLAN=self.planfile,
                    LOAD_MANIFEST=g.identity((root/'tools/tools_manifest.json').read_bytes()),
                    held_review=full, _guardian_bundle=lambda: (self.guardian, self.listing),
                    _load_readonly=lambda *args: self.board, read_file=read_file, read_json=read_json,
                    decode_json=json.loads, _journal_and_state=journal, _validate_state=validate,
                    no_links=lambda p: p, private_dir=lambda p: g.need(Path(p).is_dir(), 'directory missing'),
                    monitor_guardian=lambda p,**kwargs: copy.deepcopy(self.live))
        self.a = types.SimpleNamespace(c=self.c)

    def event(self, number, state, action='state', phase='LOAD_ONLY_OBSERVED_AWAITING_REVIEW'):
        write(self.root/'execution/events'/('event_%06d.json' % number), {
            'schema': 'opt50_durable_event_v1', 'sequence': number, 'action': action,
            'phase': phase, 'state': copy.deepcopy(state)})

    def append(self, **changed):
        self.now += 1
        value = dict(self.state, heartbeat_monotonic=self.now, **changed)
        self.event(4, value); write(self.root/'execution/status.json', value)


class GuardianCheckpointTests(unittest.TestCase):
    def setUp(self):
        temporary = ROOT/'.test_build'; temporary.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temporary, prefix='guard_')
        self.addCleanup(self.temp.cleanup)
        self.f = Fixture(Path(self.temp.name))
        if os.name == 'nt':
            self.patch = patch.object(g, 'private_file', side_effect=lambda info: g.need(
                stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'fixture regular/single-link policy'))
            self.patch.start(); self.addCleanup(self.patch.stop)

    def bind(self): return g.bind_checkpoint(self.f.a, self.f.plan)

    def test_prefix_reused_tail_read_once_and_full_review_only_at_entry_finish(self):
        guard = self.bind(); before = self.f.prefix_reads
        receipt = guard.boundary(self.f.plan)
        self.assertEqual(self.f.full_count, 1)
        self.assertEqual(self.f.prefix_reads, before)
        self.assertEqual(receipt['journal']['last_sequence'], 3)
        self.f.append(); guard.monitor(self.f.plan)
        self.assertEqual(self.f.prefix_reads-before, 1)
        guard.monitor(self.f.plan)
        self.assertEqual(self.f.prefix_reads-before, 1)
        done = guard.finish(self.f.plan)
        self.assertEqual(self.f.full_count, 2)
        self.assertEqual(done['journal_last_sequence'], 4)
        with self.assertRaisesRegex(ValueError, 'already completed'): guard.monitor(self.f.plan)

    def test_prefix_modified_or_missing_is_refused(self):
        guard = self.bind()
        path = self.f.root/'execution/events/event_000001.json'
        value = json.loads(path.read_bytes()); value['extra'] = 'tamper'
        write(path, value)
        with self.assertRaisesRegex(ValueError, 'prefix changed'): guard.monitor(self.f.plan)

    def test_journal_gap_or_extra_member_is_refused(self):
        guard = self.bind(); self.f.event(5, self.f.state)
        with self.assertRaisesRegex(ValueError, 'gap/extra'): guard.monitor(self.f.plan)

    def test_unexpected_new_action_is_refused(self):
        guard = self.bind(); self.f.append()
        self.f.event(4, json.loads((self.f.root/'execution/status.json').read_bytes()), action='command')
        with self.assertRaisesRegex(ValueError, 'unexpected new event'): guard.monitor(self.f.plan)

    def test_tail_state_changes_or_publication_mismatch_are_refused(self):
        guard = self.bind(); self.f.append(START='CHANGED')
        with self.assertRaisesRegex(ValueError, 'held state changed'): guard.monitor(self.f.plan)

    def test_unpublished_tail_is_refused_without_consuming_sequence(self):
        guard = self.bind(); self.f.now += 1
        self.f.event(4, dict(self.f.state, heartbeat_monotonic=self.f.now))
        clock=[0.]
        with patch.object(g.time,'monotonic',side_effect=lambda:clock[0]), \
             patch.object(g.time,'sleep',side_effect=lambda delay:clock.__setitem__(0,clock[0]+delay)):
            with self.assertRaisesRegex(ValueError,'tail and status differ'): guard.monitor(self.f.plan)
        self.assertEqual(guard.sequence,3)
        self.assertAlmostEqual(clock[0],10.)

    def test_stale_guardian_or_changed_boot_pid_ticks_flock_is_refused(self):
        for field, value in [('boot_id', 'other'), ('pid', 74), ('start_ticks', 406), ('flock', {})]:
            with self.subTest(field=field):
                guard = self.bind(); old = self.f.live[field]; self.f.live[field] = value
                with self.assertRaisesRegex(ValueError, 'live identity/flock'): guard.monitor(self.f.plan)
                self.f.live[field] = old
        guard = self.bind(); self.f.now += 91
        with self.assertRaisesRegex(ValueError, 'stale'): guard.monitor(self.f.plan)

    def test_changed_source_plan_inode_or_sample_binding_is_refused(self):
        guard = self.bind()
        with self.assertRaisesRegex(ValueError, 'sample guardian identity'):
            guard.monitor(dict(self.f.plan, guardian_pid=999))
        (self.f.root/'tools/board.py').write_bytes(b'changed source')
        with self.assertRaisesRegex(ValueError, 'source/plan metadata'): guard.monitor(self.f.plan)

    def test_os_binding_failure_propagates_before_next_image(self):
        guard = self.bind()
        self.f.board.collect = lambda *args: {'boot_id': 'fixture-boot', 'driver_ok': False}
        with self.assertRaisesRegex(ValueError, 'OS binding changed'): guard.boundary(self.f.plan)

    def test_entry_flock_must_equal_plan_and_full_entry_failure_does_not_fallback(self):
        self.f.plan['held_review']['guardian']['flock'] = {}
        with self.assertRaisesRegex(ValueError, 'flock differs'): self.bind()
        self.f.c.held_review = lambda: (_ for _ in ()).throw(ValueError('full review failed'))
        with self.assertRaisesRegex(ValueError, 'full review failed'): self.bind()


if __name__ == '__main__': unittest.main()
