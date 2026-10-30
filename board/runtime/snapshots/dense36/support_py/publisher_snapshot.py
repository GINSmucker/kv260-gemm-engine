"""Read-only bounded observation of append-event, fsync, replace-status publishers.

Only explicit PublicationPending conditions wait. Semantic, identity, inventory
and immutable-prefix errors propagate immediately. No deadline is renewed.
"""
from __future__ import annotations
import json
import os
import re
import time
import types

TIMEOUT_SECONDS=10.0
POLL_SECONDS=0.1


def need(value,message):
    if not value: raise ValueError(message)


def immutable(state):
    return {k:v for k,v in state.items() if k != 'heartbeat_monotonic'}


class PublicationPending(Exception):
    def __init__(self,reason,known_states=None):
        super().__init__(reason); self.known_states=known_states


def incomplete_json(raw,error):
    text=raw.decode('utf-8')
    if not text.strip(): return True
    if error.msg.startswith('Unterminated string'): return True
    if error.pos >= len(text.rstrip()): return True
    rest=text[error.pos:].strip()
    return bool(rest) and any(word.startswith(rest) for word in ('true','false','null'))


def read_event(path,read_file,decode,stamp,private_file,*,last_new=False,check=lambda:None):
    """A new final event may be observed during the writer's exclusive write."""
    check(); before=path.lstat(); private_file(before)
    identity=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink)
    try: raw=read_file(path,private=True)
    except ValueError:
        after=path.lstat(); private_file(after)
        need(identity(before)==identity(after),'new guardian event identity changed')
        if last_new and stamp(before)!=stamp(after):
            raise PublicationPending('new heartbeat event is still being written')
        raise
    check(); after=path.lstat(); private_file(after)
    need(identity(before)==identity(after),'guardian event identity changed')
    if stamp(before)!=stamp(after):
        if last_new: raise PublicationPending('new heartbeat event changed during read')
        raise ValueError('guardian immutable event changed during read')
    try: event=decode(raw)
    except UnicodeDecodeError as error:
        if last_new and error.reason=='unexpected end of data' and error.end==len(raw):
            raise PublicationPending('new heartbeat UTF-8 event is incomplete') from error
        raise
    except json.JSONDecodeError as error:
        if last_new:
            # Once visible, an explicit wrong action/phase/schema is never a
            # publication race, even when the remaining JSON is incomplete.
            for key,want in (('action','state'),('phase','LOAD_ONLY_OBSERVED_AWAITING_REVIEW'),
                             ('schema','opt50_durable_event_v1')):
                found=re.search(r'"'+key+r'"\s*:\s*"([^"\\]*)"',raw.decode('utf-8'))
                need(found is None or found[1]==want,'unexpected in-progress guardian event '+key)
            if incomplete_json(raw,error): raise PublicationPending('new heartbeat event is incomplete') from error
        raise
    return event,stamp(after)


def read_status(path,decode,stamp,private_file,check=lambda:None):
    """Read a complete opened status inode across the publisher's atomic rename.

    A replaced old fd can legitimately lose its directory link. Only that old
    inode may have nlink=0; the current named status remains private/nlink=1.
    In-place data edits and every other identity/policy error remain fatal.
    """
    check(); private_file(path.lstat())
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_BINARY',0))
    def opened_policy(info):
        need(info.st_nlink in (0,1),'guardian opened status has extra hard links')
        if info.st_nlink==1: private_file(info)
        else:
            values={name:getattr(info,name) for name in dir(info) if name.startswith('st_')}
            values['st_nlink']=1; private_file(types.SimpleNamespace(**values))
    with os.fdopen(fd,'rb') as stream:
        before=os.fstat(stream.fileno()); opened_policy(before)
        need(0 <= before.st_size <= 1024*1024,'bounded guardian status required')
        raw=stream.read(1024*1024+1); after=os.fstat(stream.fileno()); opened_policy(after)
        need(len(raw)==before.st_size,'guardian status short/growing read')
    current=path.lstat(); private_file(current)
    if (current.st_dev,current.st_ino)==(after.st_dev,after.st_ino):
        need(stamp(before)==stamp(after)==stamp(current),'guardian status inode edited in place')
    else:
        key=lambda info:(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_mode,info.st_uid,info.st_gid)
        need(key(before)==key(after) and after.st_nlink<=before.st_nlink,
             'replaced guardian status inode was also edited')
    check(); return decode(raw)


def consistent_snapshot(read_status,scan,validate,guard,*,seed=None,clock=None,sleep=None,deadline=None):
    """Return one fully consistent point; payload/caches commit only on success.

    scan(check) validates immutable prefix/inventory/events and returns states,
    last_state and arbitrary payload. Its candidate cache is local to this call.
    """
    clock=time.monotonic if clock is None else clock
    sleep=time.sleep if sleep is None else sleep
    started=clock(); deadline=started+TIMEOUT_SECONDS if deadline is None else deadline
    base=immutable(seed) if seed is not None else None
    last_reason='publisher did not expose a complete consistent snapshot'
    first=None
    def check():
        need(clock() <= deadline,'guardian publication did not settle within fixed 10 seconds: '+last_reason)
    def status():
        nonlocal base
        check(); value=read_status(); validate(value); check()
        fields=immutable(value)
        if base is None: base=fields
        need(fields==base,'guardian held state changed (non-heartbeat fields)')
        if seed is not None:
            need(value['heartbeat_monotonic']>=seed['heartbeat_monotonic'],'guardian status heartbeat moved backwards')
        return value
    while True:
        check(); guard(); first=status(); observed_statuses=[first]
        try:
            observed=scan(check)
            known=([seed] if seed is not None else [])+observed['states']
            need(first in known,'guardian status is absent from validated event history')
            second=status(); observed_statuses.append(second)
            need(second['heartbeat_monotonic']>=first['heartbeat_monotonic'],'guardian status heartbeat moved backwards')
            if second not in known:
                # It may advance after the directory snapshot. Validate the
                # corresponding visible event before allowing any waiting.
                observed=scan(check)
                known=([seed] if seed is not None else [])+observed['states']
                need(second in known,'advanced guardian status is absent from validated event history')
            if first==second==observed['last_state']:
                guard(); check(); return second,observed['payload']
            raise PublicationPending('guardian tail and status differ or status changed during read',known)
        except PublicationPending as pending:
            if pending.known_states is not None:
                need(all(value in pending.known_states for value in observed_statuses),
                     'guardian status is absent from validated complete events')
            last_reason=str(pending); check()
            remaining=deadline-clock()
            need(remaining>0,'guardian publication did not settle within fixed 10 seconds: '+last_reason)
            sleep(min(POLL_SECONDS,remaining))
