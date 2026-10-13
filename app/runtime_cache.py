"""Bounded immutable caches, with per-key single flight and bounded concurrency."""
import json
import threading
from collections import OrderedDict
from concurrent.futures import Future

class RuntimeReports:
    def __init__(self, max_bytes=64*1024*1024, max_concurrent=2):
        self.max_bytes=max_bytes
        self.entries=OrderedDict()
        self.bytes=0
        self.lock=threading.RLock()
        self.pending={}
        self.capacity=threading.BoundedSemaphore(max_concurrent)

    def encode(self,result):
        return json.dumps(result,separators=(',',':')).encode()

    def decode(self,raw):
        return json.loads(raw)

    def size(self,raw):
        return len(raw)

    def get(self,key,compute):
        with self.lock:
            raw=self.entries.get(key)
            if raw is not None:
                self.entries.move_to_end(key)
                future=None;owner=False
            else:
                future=self.pending.get(key);owner=future is None
                if owner:
                    future=Future();self.pending[key]=future
        if raw is not None:return self.decode(raw)
        if not owner:return self.decode(future.result())
        try:
            with self.capacity:raw=self.encode(compute())
            size=self.size(raw)
            with self.lock:
                if size<=self.max_bytes:
                    while self.bytes+size>self.max_bytes:
                        _,old=self.entries.popitem(last=False);self.bytes-=self.size(old)
                    self.entries[key]=raw;self.bytes+=size
                self.pending.pop(key,None)
                future.set_result(raw)
        except BaseException as exc:
            with self.lock:
                self.pending.pop(key,None);future.set_exception(exc)
            raise
        return self.decode(raw)

class RuntimePreviews(RuntimeReports):
    """Retain PNG bytes without Base64 overhead; mutable reports are isolated."""
    def encode(self,result):
        report,png=result
        return super().encode(report),png
    def decode(self,raw):
        report,png=raw
        return super().decode(report),png
    def size(self,raw):
        return len(raw[0])+len(raw[1])

runtime_reports=RuntimeReports()
runtime_previews=RuntimePreviews(max_bytes=96*1024*1024)
