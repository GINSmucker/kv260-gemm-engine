"""Batch-bound guardian review with immutable-prefix and fresh OS checkpoints.

The initial and final reviews use the original complete resident verifier. In
between, previously decoded immutable events retain their complete stat identity;
every added event is read and validated, and fresh live guardian/OS observations
are required. This module does not acquire locks or change hardware.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
import types


def need(ok, why):
    if not ok:
        raise ValueError(why)


def identity(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def metadata(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_birthtime_ns if os.name == 'nt' else info.st_ctime_ns,
            info.st_mode, info.st_uid, info.st_gid, info.st_nlink)


def private_file(info):
    need(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and
         not info.st_mode & 0o077 and info.st_nlink == 1,
         'guardian checkpoint requires private single-link regular files')


def without_heartbeat(value):
    return {key: item for key, item in value.items() if key != 'heartbeat_monotonic'}


PUBLICATION_SHA='b234d0fd9232b71abfd938da2f0f8460f8040d0a7cef68fb4a54e2c1e6a3b386'


def publisher_api(c):
    path=Path(__file__).absolute().parent/'support_py/publisher_snapshot.py'
    raw=c.read_file(path,private=Path(__file__).absolute().parent.name=='tools')
    need(identity(raw)['sha256']==PUBLICATION_SHA,'fixed guardian publication helper changed')
    value=types.ModuleType('_guardian_publisher_snapshot'); value.__file__=str(path)
    exec(compile(raw,value.__file__,'exec'),value.__dict__)
    return value


class Checkpoint:
    def __init__(self, a, plan):
        self.c = a.c
        self.c.root_linux()
        self.plan = {name: plan[name] for name in (
            'guardian_plan', 'guardian_plan_pin', 'guardian_plan_inode',
            'boot_id', 'guardian_pid', 'guardian_start_ticks')}
        self.c.configure(self.plan['guardian_plan'])
        self.guardian_path = Path(self.plan['guardian_plan'])
        self.folder = self.guardian_path.parent/'execution/events'
        self._identity()
        self.initial_review = self.c.held_review()
        actual = self.initial_review['guardian']
        need(actual['boot_id'] == self.plan['boot_id'] and actual['pid'] == self.plan['guardian_pid'] and
             actual['start_ticks'] == self.plan['guardian_start_ticks'], 'full entry guardian identity differs')
        self.flock = actual.get('flock')
        need(type(self.flock) is dict and self.flock == plan.get('held_review', {}).get('guardian', {}).get('flock'),
             'full entry guardian flock differs from sealed plan')
        self.entry_pin = identity(encoded(self.initial_review))
        self.guardian_plan, listing = self.c._guardian_bundle()
        self.board = self.c._load_readonly(self.guardian_path.parent, listing, 'board.py')
        self.directories = {}
        for folder in (self.guardian_path.parent.parent, self.guardian_path.parent,
                       self.guardian_path.parent/'tools', self.guardian_path.parent/'execution', self.folder):
            self.c.private_dir(folder)
            info = folder.lstat()
            self.directories[folder] = (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)
        self.files = {}
        for file in (self.guardian_path, self.guardian_path.parent/'tools/tools_manifest.json',
                     *(self.guardian_path.parent/'tools'/name for name in listing['files'])):
            before = metadata(file.lstat()); private_file(file.lstat())
            raw = self.c.read_file(file, private=True)
            expected = (self.plan['guardian_plan_pin'] if file == self.guardian_path else
                        self.c.LOAD_MANIFEST if file.name == 'tools_manifest.json' else listing['files'][file.name])
            need(identity(raw) == expected and metadata(file.lstat()) == before,
                 'guardian checkpoint static source changed during binding')
            self.files[file] = before
        state, records = self.c._journal_and_state(self.guardian_plan)
        self.initial_state = without_heartbeat(state)
        self.last_status = state
        self.publisher = publisher_api(self.c)
        self.events = {}
        self.sequence = 0
        self.last_heartbeat = state['heartbeat_monotonic']
        for sequence, event in enumerate(records, 1):
            path = self.folder/('event_%06d.json' % sequence)
            before = metadata(path.lstat())
            raw = self.c.read_file(path, private=True)
            need(self.c.decode_json(raw) == event, 'guardian prefix changed after full review')
            private_file(path.lstat())
            need(metadata(path.lstat()) == before, 'guardian prefix changed during binding')
            self.events[path.name] = metadata(path.lstat())
            self.sequence = sequence
        self._identity()
        self._tail()
        self.finished = False

    def _identity(self):
        c, expected = self.c, self.plan
        need(Path(c.OLD_PLAN) == self.guardian_path, 'guardian checkpoint configuration changed')
        need(identity(c.read_file(self.guardian_path, private=True)) == expected['guardian_plan_pin'],
             'guardian checkpoint plan content changed')
        info = self.guardian_path.lstat()
        need({'device': info.st_dev, 'inode': info.st_ino} == expected['guardian_plan_inode'],
             'guardian checkpoint plan inode changed')
        for path, wanted in getattr(self, 'directories', {}).items():
            c.private_dir(path)
            current = path.lstat()
            need((current.st_dev, current.st_ino, current.st_mode, current.st_uid, current.st_gid) == wanted,
                 'guardian checkpoint directory identity changed')
        for path, wanted in getattr(self, 'files', {}).items():
            c.no_links(path)
            private_file(path.lstat())
            need(metadata(path.lstat()) == wanted, 'guardian checkpoint source/plan metadata changed')

    def _prefix_stats(self):
        self.c.private_dir(self.folder)
        # One validated directory handle avoids re-walking every ancestor for
        # each immutable heartbeat. No descriptor escapes this read operation.
        if os.name == 'posix':
            fd = os.open(self.folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                current = {name: os.stat(name, dir_fd=fd, follow_symlinks=False)
                           for name in self.events}
            finally:
                os.close(fd)
        else:
            current = {name: (self.folder/name).lstat() for name in self.events}
        for name, info in current.items():
            private_file(info)
            need(metadata(info) == self.events[name], 'guardian immutable event prefix changed')

    def _tail(self):
        deadline=time.monotonic()+10.0
        pub=self.publisher
        candidates={}; last_live=[-float('inf')]
        def guard():
            self._identity(); self._prefix_stats()
            now=time.monotonic()
            if now-last_live[0]>=0.1:
                live=self.c.monitor_guardian(self.plan,deadline=deadline)
                need(live['boot_id']==self.plan['boot_id'] and live['pid']==self.plan['guardian_pid'] and
                     live['start_ticks']==self.plan['guardian_start_ticks'] and live.get('flock')==self.flock,
                     'guardian checkpoint live identity/flock changed during publication wait')
                last_live[0]=time.monotonic()
            for name,(event,pin) in candidates.items():
                info=(self.folder/name).lstat(); private_file(info)
                need(metadata(info)==pin,'guardian validated new event prefix changed')
        def scan(check):
            check(); guard()
            names=sorted(path.name for path in self.folder.iterdir())
            need(0 < len(names) <= 10000 and names==[
                'event_%06d.json'%n for n in range(1,len(names)+1)],
                'guardian checkpoint event gap/extra entry')
            need(len(names)>=self.sequence,'guardian checkpoint journal shrank')
            states=[]; heartbeat=self.last_heartbeat
            for number in range(self.sequence+1,len(names)+1):
                check(); name='event_%06d.json'%number; path=self.folder/name
                if name in candidates: event,pin=candidates[name]
                else:
                    try:
                        event,pin=pub.read_event(path,self.c.read_file,self.c.decode_json,metadata,private_file,
                                                last_new=number==len(names),check=check)
                    except pub.PublicationPending as pending:
                        pending.known_states=[self.last_status]+states
                        raise
                    need(type(event) is dict and event.get('schema')=='opt50_durable_event_v1' and
                         type(event.get('sequence')) is int and event['sequence']==number and
                         event.get('action')=='state' and event.get('phase')=='LOAD_ONLY_OBSERVED_AWAITING_REVIEW',
                         'guardian checkpoint unexpected new event')
                    state=event.get('state'); self.c._validate_state(state,self.guardian_plan)
                    need(without_heartbeat(state)==self.initial_state,'guardian checkpoint held state changed')
                    candidates[name]=(event,pin)
                state=event['state']
                need(state['heartbeat_monotonic']>heartbeat,'guardian checkpoint tail heartbeat did not advance')
                heartbeat=state['heartbeat_monotonic']; states.append(state)
            return {'states':states,'last_state':states[-1] if states else self.last_status,
                    'payload':{'pins':{n:pin for n,(_,pin) in candidates.items()},'sequence':len(names)}}
        def status():
            return pub.read_status(self.guardian_path.parent/'execution/status.json',self.c.decode_json,
                                   metadata,private_file)
        state,payload=pub.consistent_snapshot(status,scan,lambda value:self.c._validate_state(value,self.guardian_plan),
                                             guard,seed=self.last_status,deadline=deadline)
        # No public checkpoint cache advances until event/status match and the
        # second status read plus every immutable prefix identity have passed.
        self.events.update(payload['pins']); self.sequence=payload['sequence']
        self.last_heartbeat=state['heartbeat_monotonic']; self.last_status=state
        return {'last_sequence':self.sequence,'new_records_checked':len(payload['pins']),
                'heartbeat_monotonic':self.last_heartbeat}

    def _live(self, plan):
        need(not self.finished, 'guardian checkpoint already completed')
        need(all(plan.get(name) == value for name, value in self.plan.items()),
             'sample guardian identity differs from batch checkpoint')
        tail = self._tail()
        live = self.c.monitor_guardian(plan)
        need(live['boot_id'] == self.plan['boot_id'] and live['pid'] == self.plan['guardian_pid'] and
             live['start_ticks'] == self.plan['guardian_start_ticks'] and live.get('flock') == self.flock,
             'guardian checkpoint live identity/flock changed')
        self._identity()
        return live, tail

    def monitor(self, plan):
        live, tail = self._live(plan)
        return {'schema': 'opt50_b24_guardian_checkpoint_monitor_v1', 'guardian': live,
                'entry_full_review': self.entry_pin, 'journal': tail,
                'scope': 'Fresh guardian identity/heartbeat and checked immutable journal prefix/new tail; no occupancy scan'}

    def boundary(self, plan):
        live, tail = self._live(plan)
        current = self.board.collect(Path(self.guardian_plan['cases']), self.guardian_plan['command_tools'])
        observation = self.board.loaded(current, self.plan['boot_id'])
        final, final_tail = self._live(plan)
        return {'schema': 'opt50_b24_guardian_boundary_checkpoint_v1',
                'status': 'CHECKPOINT_OS_OBSERVATIONS_MATCH_NO_CSR_NO_START',
                'boot_id': self.plan['boot_id'], 'guardian': final,
                'entry_full_review': self.entry_pin, 'journal': final_tail,
                'resident_OS_observations': observation,
                'scope': 'Fresh guardian/OS boundary observation with bound full batch-entry review; static source/prefix identity checkpoint, not a new full historical audit',
                'CSR': 'BLOCKED', 'START': 'BLOCKED', 'PL_START_commands_issued': 0}

    def finish(self, plan):
        self._live(plan)
        value = self.c.held_review()
        self._live(plan)
        self.finished = True
        return {'schema': 'opt50_b24_guardian_batch_final_review_v1',
                'entry_full_review': self.entry_pin, 'full_final_review': value,
                'journal_last_sequence': self.sequence, 'scope': 'Fresh complete original guardian review after all image owners closed'}


def bind_checkpoint(a, plan):
    return Checkpoint(a, plan)
