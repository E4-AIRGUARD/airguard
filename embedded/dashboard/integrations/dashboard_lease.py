"""Expiring per-tab leases. The watchdog stops fans independently of inference."""
import re
import threading
import time


class DashboardLease:
    def __init__(self, timeout=15, clock=time.monotonic):
        self.timeout=timeout;self.clock=clock
        self.lock=threading.RLock();self.sessions={}

    def update(self, client, action, sequence):
        if not isinstance(client,str) or not re.fullmatch(r'[a-zA-Z0-9-]{16,64}',client):
            raise ValueError('Invalid client')
        if action not in ('heartbeat','close') or type(sequence) is not int or not 0<=sequence<=10**12:
            raise ValueError('Invalid action/sequence')
        with self.lock:
            now=self.clock()
            self.sessions={k:v for k,v in self.sessions.items() if now-v['seen']<max(120,self.timeout*4)}
            previous=self.sessions.get(client)
            # A close is final for this tab ID, even if a heartbeat arrives late.
            if previous and (previous['closed'] or sequence<=previous['seq']):return self.status()
            if not previous and len(self.sessions)>=512:raise ValueError('Too many clients')
            self.sessions[client]=dict(seq=sequence,closed=action=='close',seen=now)
            return self.status()

    def status(self):
        with self.lock:
            now=self.clock()
            active=[v for v in self.sessions.values() if not v['closed'] and now-v['seen']<self.timeout]
            return dict(active=bool(active),clients=len(active),timeoutSec=self.timeout)


class FanLeaseGuard:
    def __init__(self,lease,send):
        self.lease=lease;self.send_raw=send;self.last_zero=None
        self.stop_event=threading.Event();self.thread=None;self.error=None

    def send(self,intake,exhaust):
        with self.lease.lock:
            allowed=self.lease.status()['active']
            if not allowed and (intake or exhaust):
                self._zero();return False
            self.send_raw(intake,exhaust)
            if intake==exhaust==0:self.last_zero=time.monotonic()
            else:self.last_zero=None
            return True

    def _zero(self):
        self.send_raw(0,0);self.last_zero=time.monotonic()

    def check(self):
        with self.lease.lock:
            if not self.lease.status()['active'] and (self.last_zero is None or time.monotonic()-self.last_zero>=2):
                self._zero()

    def start(self):
        def watch():
            while not self.stop_event.wait(.2):
                try:self.check();self.error=None
                except Exception as error:self.error=str(error)
        self.check()
        self.thread=threading.Thread(target=watch,daemon=True);self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=2)
        with self.lease.lock:self._zero()
