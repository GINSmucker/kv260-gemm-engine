"""Synthetic resident proof tests; no systemctl, process, lock or device access."""
import ast
from copy import deepcopy
import json
from pathlib import Path
import types
import unittest
from unittest import mock

import resident


def state():
    return {'schema': 'opt50_loadonly_status_fd96539d_v2',
            'stage': 'LOAD_ONLY_OBSERVED_AWAITING_REVIEW', 'boot_id': resident.BOOT,
            'unit': resident.GUARDIAN_UNIT, 'guardian_pid': 4321, 'child_pid': None,
            'run_dir': str(resident.OLD_PLAN.parent/'execution'), 'CSR': 'BLOCKED', 'START': 'BLOCKED',
            'PL_START_commands_issued': 0, 'physical_PL_starts': 'NOT_MEASURED_NO_CSR_ACCESS',
            'source_package_modified': False, 'automatic_restoration': 'DISABLED',
            'saved_status_only': True, 'heartbeat_monotonic': 100.0}


def stat_bytes(pid=4321, start=5678, phase='S'):
    fields = [phase, '1'] + ['0']*17 + [str(start)] + ['0']*4
    return (str(pid)+' (python ) unusual comm) '+' '.join(fields)+'\n').encode()


def plan():
    return {'boot_id': resident.BOOT, 'unit': resident.GUARDIAN_UNIT,
            'command_tools': {name: {'entry': '/usr/bin/'+name} for name in
                              ('dfx-mgr-client', 'insmod', 'udevadm', 'systemctl', 'python3')}}


class StateTests(unittest.TestCase):
    def test_only_exact_newboot_loadonly_status_is_accepted(self):
        resident._validate_state(state(), plan(), now=101)
        for key, bad in (('boot_id', 'old'), ('stage', 'RETAINED_STATE_UNCERTAIN'),
                         ('guardian_pid', 0), ('child_pid', 7), ('START', 'ENABLED'),
                         ('PL_START_commands_issued', 1), ('PL_START_commands_issued', False),
                         ('automatic_restoration', 'ENABLED'), ('source_package_modified', True)):
            value = state(); value[key] = bad
            with self.assertRaises(ValueError, msg=key):
                resident._validate_state(value, plan(), now=101)

    def test_heartbeat_stale_future_nan_and_boolean_are_rejected(self):
        for heartbeat in (0, 200, float('nan'), float('inf'), True):
            value = state(); value['heartbeat_monotonic'] = heartbeat
            with self.assertRaises(ValueError):
                resident._validate_state(value, plan(), now=101)

    def test_json_duplicate_and_nonfinite_are_rejected(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}'):
            with self.assertRaises(ValueError):
                resident.decode_json(raw)


class ProcessTests(unittest.TestCase):
    def test_proc_stat_handles_spaces_and_parentheses_in_comm(self):
        self.assertEqual(resident.parse_proc_stat(stat_bytes()),
                         {'pid': 4321, 'state': 'S', 'ppid': 1, 'start_ticks': 5678})

    def test_flock_requires_one_granted_whole_file_write_owner(self):
        good = b'1: FLOCK ADVISORY WRITE 4321 01:03:9 0 EOF\n'
        self.assertEqual(resident.parse_flock(good, 4321, (1, 3), 9)['inode'], 9)
        for raw in (good.replace(b'4321', b'4322'), good+good,
                    good.replace(b'1: ', b'1: -> '), good.replace(b'WRITE', b'READ'),
                    good.replace(b'0 EOF', b'0 8')):
            with self.assertRaises(ValueError):
                resident.parse_flock(raw, 4321, (1, 3), 9)

    def observe(self, *, changes=None, expected=None, second_start=5678):
        value = state()
        python = Path('/synthetic/root/python3.12')
        lock = types.SimpleNamespace(st_mode=0o100600, st_dev=7, st_ino=9)
        argv = ['/usr/bin/python3', '-B', str(resident.OLD_PLAN.parent/'tools/window.py'),
                'run', '--plan', str(resident.OLD_PLAN), '--authorized-load-only']
        values = {'boot_id': (resident.BOOT+'\n').encode(),
                  'cmdline': b''.join(item.encode()+b'\0' for item in argv),
                  'cgroup': ('0::/system.slice/'+resident.GUARDIAN_UNIT+'.service\n').encode(),
                  'status': b'Name:\tpython3\nUid:\t0\t0\t0\t0\n',
                  'locks': b'1: FLOCK ADVISORY WRITE 4321 01:03:9 0 EOF\n'}
        values.update(changes or {})
        stats = iter([stat_bytes(), stat_bytes(start=second_start)])
        def read(path, *_):
            return next(stats) if Path(path).name == 'stat' else values[Path(path).name]
        with mock.patch.object(resident, '_service', return_value={'MainPID': '4321'}), \
                mock.patch.object(resident, '_proc_read', side_effect=read), \
                mock.patch.object(resident, '_command_tool', return_value={'entry': '/usr/bin/python3', 'target': str(python)}), \
                mock.patch.object(resident.Path, 'resolve', return_value=python), \
                mock.patch.object(resident, '_regular_board_lock', return_value=lock), \
                mock.patch.object(resident.Path, 'iterdir', return_value=iter([Path('/proc/4321/fd/7')])), \
                mock.patch.object(resident.os, 'readlink', return_value=str(resident.BOARD_LOCK)), \
                mock.patch.object(resident.Path, 'stat', return_value=lock), \
                mock.patch.object(resident.os, 'major', return_value=1, create=True), \
                mock.patch.object(resident.os, 'minor', return_value=3, create=True):
            return resident._observe_guardian(plan(), value, expected)

    def test_guardian_pid_is_derived_not_hardcoded_to_2988(self):
        result = self.observe()
        self.assertEqual((result['pid'], result['start_ticks']), (4321, 5678))
        self.assertFalse(result['old_board_lock_transferred'])

    def test_modelplan_freezes_current_guardian_pid_and_start_ticks(self):
        expected = {'boot_id': resident.BOOT, 'guardian_pid': 4321, 'guardian_start_ticks': 5678}
        self.observe(expected=expected)
        for key, bad in (('guardian_pid', 2988), ('guardian_start_ticks', 1), ('boot_id', 'old')):
            value = dict(expected); value[key] = bad
            with self.assertRaisesRegex(ValueError, 'NEW model plan'):
                self.observe(expected=value)

    def test_pid_reuse_during_observation_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'PID/lock changed'):
            self.observe(second_start=9999)

    def test_wrong_boot_uid_argv_and_cgroup_are_refused(self):
        for change in ({'boot_id': b'old-boot\n'}, {'status': b'Uid:\t1000\t1000\t1000\t1000\n'},
                       {'cmdline': b'/usr/bin/python3\0manual\0'}, {'cgroup': b'0::/user.slice\n'}):
            with self.assertRaises(ValueError, msg=str(change)):
                self.observe(changes=change)


class ManagementTests(unittest.TestCase):
    def data(self):
        value = plan()
        root = resident.OLD_PLAN.parent/'execution'
        commands = [('dfx-mgr-client', '-remove', '0'), ('insmod', str(root/'zocl_candidate.ko')),
                    ('dfx-mgr-client', '-load', 'kv260_opt50_fd96539d_4cbcf732'),
                    ('udevadm', 'settle', '--timeout=5')]
        rows = []
        for index, (phase, command) in enumerate(zip(resident.PHASES, commands), 1):
            argv = [value['command_tools'][command[0]]['entry'], *command[1:]]
            common = {'action': 'command', 'action_stage': phase}
            outputs = {suffix: str(root/('command_%02d.%s' % (index, suffix))) for suffix in ('stdout', 'stderr')}
            rows.extend([dict(common, phase='INTENT', argv=argv, **outputs),
                         dict(common, phase='SPAWNED', argv=argv, pid=100+index),
                         dict(common, phase='RETURNED', pid=100+index, returncode=0,
                              stdout=resident.identity(b''), stderr=resident.identity(b''))])
        return value, rows

    def test_exact_four_reaped_zero_commands_and_log_hashes(self):
        value, rows = self.data()
        with mock.patch.object(resident, 'read_guardian_command_log', return_value=b'') as typed, \
                mock.patch.object(resident, 'read_file', side_effect=AssertionError('generic private reader must not read load logs')):
            resident._validate_commands(value, rows)
        self.assertEqual(typed.call_count, 8)
        self.assertTrue(all(call.args[1] == resident.identity(b'') for call in typed.call_args_list))

    def test_wrong_exit_extra_missing_reordered_or_changed_command_is_refused(self):
        for kind in ('exit', 'missing', 'extra', 'order', 'argv', 'pid', 'log'):
            value, rows = self.data()
            if kind == 'exit': rows[2]['returncode'] = 1
            if kind == 'missing': rows.pop()
            if kind == 'extra': rows.append(rows[-1])
            if kind == 'order': rows[0], rows[1] = rows[1], rows[0]
            if kind == 'argv': rows[0]['argv'] = ['unsafe']
            if kind == 'pid': rows[2]['pid'] = 9
            if kind == 'log': rows[2]['stdout'] = resident.identity(b'not matching')
            with mock.patch.object(resident, 'read_guardian_command_log', return_value=b''), self.assertRaises(ValueError, msg=kind):
                resident._validate_commands(value, rows)

    def test_nonempty_management_stderr_is_refused(self):
        value, rows = self.data()
        rows[2]['stderr'] = resident.identity(b'warning')
        def read(path, *_):
            return b'warning' if Path(path).name == 'command_01.stderr' else b''
        with mock.patch.object(resident, 'read_guardian_command_log', side_effect=read), self.assertRaisesRegex(ValueError, 'diagnostics'):
            resident._validate_commands(value, rows)


class ReviewTests(unittest.TestCase):
    def test_exact_published_manifest_and_all_thirteen_files_are_rechecked(self):
        published = Path(__file__).parent.parent/'deployment/opt50_loadonly_fd96539d_tools_v2'
        if not published.is_dir():
            self.skipTest('development-only immutable published-load source fixture is not bundled')
        raw_manifest = (published/'tools_manifest.json').read_bytes()
        self.assertEqual(resident.identity(raw_manifest), resident.LOAD_MANIFEST)
        manifest = resident.decode_json(raw_manifest)
        current = dict(plan(), schema='opt50_loadonly_plan_fd96539d_v2', token='74abb1e54663',
                       scope=resident.LOAD_SCOPE, hardware_executed=False,
                       uart_logging_operator_confirmed=True, PL_START_commands_issued=0,
                       expires_seconds=900,
                       tool_files={'manifest': resident.LOAD_MANIFEST, 'files': manifest['files']})
        contents = {name: (published/name).read_bytes() for name in resident.LOAD_CODE}
        contents.update({'tools_manifest.json': raw_manifest, 'plan.json': json.dumps(current).encode()})
        tools = resident.OLD_PLAN.parent/'tools'
        def read(path, **_):
            return contents[Path(path).name]
        with mock.patch.object(resident, 'private_dir'), \
                mock.patch.object(resident, 'read_file', side_effect=read), \
                mock.patch.object(resident.Path, 'iterdir', return_value=[tools/name for name in
                                  resident.LOAD_CODE | {'tools_manifest.json'}]):
            self.assertEqual(resident._guardian_bundle(), (current, manifest))
            original = contents['board.py']; contents['board.py'] += b'\n# changed'
            with self.assertRaisesRegex(ValueError, 'tool changed'):
                resident._guardian_bundle()
            contents['board.py'] = original
            current['boot_id'] = 'old-boot'
            contents['plan.json'] = json.dumps(current).encode()
            with self.assertRaisesRegex(ValueError, 'plan identity'):
                resident._guardian_bundle()

    def journal(self, *, corrupt=False):
        current = state()
        execution = resident.OLD_PLAN.parent/'execution'
        files = [execution/'events/event_000001.json', execution/'events/event_000002.json']
        records = [dict(schema='opt50_durable_event_v1', sequence=1, action='snapshot', phase='COMMITTED'),
                   dict(schema='opt50_durable_event_v1', sequence=2, action='state',
                        phase='LOAD_ONLY_OBSERVED_AWAITING_REVIEW', state=current)]
        if corrupt:
            records[-1]['sequence'] = 99
        def read(path, **_):
            return current if Path(path).name == 'status.json' else records[files.index(Path(path))]
        return files, records, read

    def test_durable_journal_and_published_status_must_match(self):
        files, records, read = self.journal()
        with mock.patch.object(resident, 'private_dir'), \
                mock.patch.object(resident, 'read_json', side_effect=read), \
                mock.patch.object(resident.Path, 'glob', return_value=files), \
                mock.patch.object(resident.Path, 'iterdir', return_value=files), \
                mock.patch.object(resident.time, 'monotonic', return_value=101):
            current, result = resident._journal_and_state(plan())
        self.assertEqual(current, state()); self.assertEqual(result, records)

    def test_durable_journal_gap_is_not_accepted_after_bounded_rereads(self):
        files, _, read = self.journal(corrupt=True)
        with mock.patch.object(resident, 'private_dir'), \
                mock.patch.object(resident, 'read_json', side_effect=read), \
                mock.patch.object(resident.Path, 'glob', return_value=files), \
                mock.patch.object(resident.Path, 'iterdir', return_value=files), \
                mock.patch.object(resident.time, 'monotonic', return_value=101), \
                mock.patch.object(resident.time, 'sleep') as sleep:
            with self.assertRaisesRegex(ValueError, 'consistent guardian'):
                resident._journal_and_state(plan())
        self.assertEqual(sleep.call_count, 2)

    def test_complete_review_combines_staged_source_and_fresh_OS_evidence(self):
        current = dict(plan(), package='/synthetic/package', cases='/synthetic/cases', artifacts={'fixed': 'identity'})
        board = types.SimpleNamespace(collect=mock.Mock(return_value={'drm_driver_bindings': ['verified']}),
                                      loaded=mock.Mock(return_value={'CSR': 'BLOCKED', 'START': 'BLOCKED'}))
        artifact = types.SimpleNamespace(audit=mock.Mock(return_value=current['artifacts']))
        modules = {'board.py': board, 'artifacts.py': artifact, 'firmware.py': types.SimpleNamespace()}
        guardian = {'pid': 4321, 'start_ticks': 5678, 'boot_id': resident.BOOT}
        with mock.patch.object(resident, 'root_linux'), \
                mock.patch.object(resident, '_guardian_bundle', return_value=(current, {})), \
                mock.patch.object(resident, '_journal_and_state', return_value=(state(), [])), \
                mock.patch.object(resident, '_observe_guardian', return_value=guardian) as observe, \
                mock.patch.object(resident, '_load_readonly', side_effect=lambda root, mf, name: modules[name]), \
                mock.patch.object(resident, '_validate_commands') as commands, \
                mock.patch.object(resident, '_validate_staged') as staged, \
                mock.patch.object(resident, 'read_json', return_value=state()), \
                mock.patch.object(resident.time, 'monotonic', return_value=101):
            result = resident.held_review()
            artifact.audit.return_value = {'changed': True}
            with self.assertRaisesRegex(ValueError, 'source artifacts'):
                resident.held_review()
        self.assertEqual(result['status'], 'PASS_OS_REVIEW_ONLY_NO_CSR_NO_START')
        self.assertEqual(result['guardian']['pid'], 4321)
        self.assertEqual(commands.call_count, 2); self.assertEqual(staged.call_count, 2)
        self.assertEqual(board.collect.call_count, 1)
        self.assertEqual(observe.call_count, 3)

    def test_monitor_has_no_occupancy_scan_or_source_audit(self):
        expected = {'boot_id': resident.BOOT, 'guardian_pid': 4321, 'guardian_start_ticks': 5678}
        result = {'pid': 4321, 'start_ticks': 5678, 'boot_id': resident.BOOT}
        with mock.patch.object(resident, 'root_linux'), \
                mock.patch.object(resident, '_guardian_bundle', return_value=(plan(), {})), \
                mock.patch.object(resident, 'read_json', return_value=state()), \
                mock.patch.object(resident.time, 'monotonic', return_value=101), \
                mock.patch.object(resident, '_observe_guardian', return_value=result) as observe, \
                mock.patch.object(resident, '_load_readonly') as load:
            self.assertEqual(resident.monitor_guardian(expected), result)
        observe.assert_called_once_with(plan(), state(), expected)
        load.assert_not_called()

    def test_loader_cannot_be_imported_through_review_helper(self):
        with mock.patch.object(resident, 'compiled_module') as compile_module:
            with self.assertRaisesRegex(ValueError, 'read-only'):
                resident._load_readonly(Path('/synthetic'), {}, 'window.py')
        compile_module.assert_not_called()

    def test_resident_module_has_no_MMIO_device_mutation_or_signal_entry(self):
        raw = Path(resident.__file__).read_text(encoding='utf-8')
        tree = ast.parse(raw)
        imports = {name.name for node in ast.walk(tree) if isinstance(node, ast.Import) for name in node.names}
        self.assertFalse({'mmap', 'xrt', 'signal', 'window', 'board'} & imports)
        for text in ("'/dev/mem'", 'os.kill(', 'Popen(', '.terminate(', '.unlink(', 'rmtree('):
            self.assertNotIn(text, raw)
        # The only subprocess call is a bounded service SHOW, not a loader.
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and
                 isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and
                 node.func.value.id == 'subprocess']
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].func.attr, 'run')


if __name__ == '__main__':
    unittest.main()
