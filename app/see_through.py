"""Durable client for the official See-through Gradio demo.

No anonymous-upload workaround, no resubmission after an ambiguous POST. Public
demo access is an experiment, not a production availability guarantee.
"""
import json
import time
import urllib.request
import urllib.parse
import uuid
from pathlib import Path
from .core import UserError, atomic_json, sha, read_image

BASE = 'https://24yearsold-see-through-demo.hf.space'

def request(path, data=None, content_type='application/json', timeout=45):
    req = urllib.request.Request(BASE + path, data=data,
        headers={'Content-Type': content_type, 'User-Agent': 'SpinePartsLab/1.0'})
    return urllib.request.urlopen(req, timeout=timeout)

def run(folder, image_bytes, resolution=768):
    """Resume recorded event; a failed/unknown inference is never auto-reposted."""
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    path = folder/'job.json'; digest = sha(image_bytes)
    read_image(image_bytes)
    job = json.loads(path.read_text()) if path.exists() else {
        'provider': 'see-through', 'endpoint': BASE, 'source_sha256': digest,
        'resolution': resolution, 'seed': 42, 'status': 'prepared',
        'created_at': time.time(), 'events': []}
    if job['source_sha256'] != digest or job['resolution'] != resolution:
        raise UserError('此试验已有不同输入，请保留原记录并新建试验')
    if job['status'] in ('completed','failed','submission_unknown'): return job
    def save(status, **fields):
        job.update(status=status, updated_at=time.time(), **fields)
        atomic_json(path, job)
    try:
        if not job.get('uploaded_path'):
            save('checking_service')
            with request('/config') as response: config=json.load(response)
            if not any(d.get('api_name')=='inference' for d in config.get('dependencies',[])):
                raise UserError('See-through 未公开 inference 接口')
            boundary='spine'+uuid.uuid4().hex
            body=(f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="character.png"\r\nContent-Type: image/png\r\n\r\n').encode()+image_bytes+f'\r\n--{boundary}--\r\n'.encode()
            with request('/gradio_api/upload', body, 'multipart/form-data; boundary='+boundary) as response:
                uploaded=json.load(response)
            if not isinstance(uploaded,list) or not uploaded or not isinstance(uploaded[0],str):
                raise UserError('See-through 未返回上传文件编号')
            save('uploaded', uploaded_path=uploaded[0], gradio_version=config.get('version'))
        if not job.get('event_id'):
            payload={'data':[{'path':job['uploaded_path'],'meta':{'_type':'gradio.FileData'}},resolution,42,True]}
            save('submission_unknown')
            with request('/gradio_api/call/inference',json.dumps(payload).encode()) as response: receipt=json.load(response)
            event=receipt.get('event_id')
            if not isinstance(event,str) or not event.isalnum():raise UserError('推理回执没有合法事件编号')
            save('pending',event_id=event)
        # Read-only reconnect is safe. A timeout leaves the original event pending.
        event_type=''
        with request('/gradio_api/call/inference/'+job['event_id'], timeout=60) as response:
            for line in response:
                line=line.decode().strip()
                if line.startswith('event:'):event_type=line[6:].strip()
                elif line.startswith('data:'):
                    payload=json.loads(line[5:].strip())
                    job['events'].append({'type':event_type,'at':time.time(),'data':payload})
                    if event_type=='error':save('failed',error=payload);break
                    if event_type=='complete':save('completed',result=payload);break
                    save('pending')
    except Exception as error:
        # Never retry a potentially accepted inference. Preserve receipt if known.
        status=job['status'] if job['status'] in ('submission_unknown','pending') else 'service_unavailable'
        save(status, error={'type':type(error).__name__,'message':str(error)[:600]})
    return job

def download_outputs(folder):
    folder=Path(folder); job=json.loads((folder/'job.json').read_text())
    if job['status']!='completed':raise UserError('See-through 尚未返回图层')
    result=job['result']; entries=[('layers.psd', result[0])]
    for i, row in enumerate(result[1]):entries.append((f'layer-{i:02d}.png',row['image']))
    manifest=[]
    for name, data in entries:
        url=data.get('url') or BASE+'/gradio_api/file='+urllib.parse.quote(data['path'])
        if urllib.parse.urlsplit(url).netloc!=urllib.parse.urlsplit(BASE).netloc:
            raise UserError('See-through 返回了非本站下载地址')
        dest=folder/name
        if not dest.exists():
            with urllib.request.urlopen(url,timeout=60) as response:blob=response.read(100*1024*1024+1)
            if len(blob)>100*1024*1024:raise UserError('图层文件过大')
            if name.endswith('.png'):read_image(blob)
            dest.write_bytes(blob)
        manifest.append({'file':name,'sha256':sha(dest.read_bytes()),'caption':None if name.endswith('.psd') else result[1][len(manifest)-1].get('caption')})
    atomic_json(folder/'files.json',manifest)
    return manifest
