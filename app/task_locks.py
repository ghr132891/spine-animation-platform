"""Reentrant per-task serialization; unrelated tasks may execute independently."""
import threading
class TaskLocks:
 def __init__(self):self.guard=threading.RLock();self.locks={}
 def for_task(self,key):
  with self.guard:return self.locks.setdefault(key,threading.RLock())
