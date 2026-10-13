"""Keep usable drafts separate from formal acceptance and worker state."""
import json
import threading
import time
from .core import atomic_json

_guard=threading.RLock()

def save_delivery(storage,task_id,entry):
    path=storage/'workflow-deliveries'/(task_id+'.json')
    with _guard:
        previous=json.loads(path.read_text()) if path.exists() else None
        entry={**entry,'task_id':task_id,'status':'draft'}
        if previous and all(previous.get(k)==v for k,v in entry.items()):return previous
        entry['updated_at']=time.time()
        entry['history']=(previous.get('history',[])+[{k:v for k,v in previous.items() if k!='history'}]) if previous else []
        atomic_json(path,entry)
        return entry
