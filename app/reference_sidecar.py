"""Private loopback reference publisher, independent of workbench restarts.

Only PNG publication RPC is exposed locally. Public surface remains the original
PNG-only tunnel, with the same authorization, quotas and expiry.
"""
import base64,hmac,json,os,secrets,subprocess,sys,time,urllib.request,threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from .reference_tunnel import ReferenceTunnel,MAX_IMAGE_BYTES
from .core import UserError
class DurableReferenceTunnel:
    def __init__(self,storage):
        self.root=Path(storage).resolve();self.local=ReferenceTunnel(self.root)
        self.descriptor=self.root/'reference-sidecar.json'
    def _record(self):
        try:return json.loads(self.descriptor.read_text())
        except (ValueError,OSError):return None
    def _rpc(self,action,data=None,timeout=50):
        r=self._record()
        if not r:raise UserError('参考图独立通道未启动')
        req=urllib.request.Request('http://127.0.0.1:'+str(int(r['port']))+'/'+action,data=json.dumps(data or {}).encode(),headers={'X-Reference-Key':r['key'],'Content-Type':'application/json'})
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=timeout) as response:result=json.load(response)
        except Exception:raise UserError('参考图独立通道连接失败') from None
        if 'error' in result:raise UserError(result['error'])
        return result
    def _start(self):
        import fcntl
        self.root.mkdir(parents=True,exist_ok=True)
        with (self.root/'reference-sidecar.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            try:self._rpc('status',timeout=2);return
            except UserError:pass
            subprocess.Popen([sys.executable,'-m','app.reference_sidecar',str(self.root)],cwd=Path(__file__).resolve().parents[1],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True,close_fds=True)
            for _ in range(60):
                time.sleep(.1)
                try:self._rpc('status',timeout=1);return
                except UserError:pass
            raise UserError('参考图独立通道无法启动')
    def config(self):return self.local.config()
    def status(self):
        state=self.local.status()
        try:return {**state,**self._rpc('status',timeout=2),'lifetime':'independent-process'}
        except UserError:return {**state,'active':False,'lifetime':'independent-process'}
    def publish(self,data,image_id):
        if not self.config()['enabled']:raise UserError('平台临时参考图通道未启用；未提交生图')
        self._start();return self._rpc('publish',{'data':base64.b64encode(data).decode(),'image_id':image_id})
    def active_reference(self,url):
        try:return self._rpc('active',{'url':url},timeout=2)['active']
        except UserError:return False
    def enable(self,url_ttl=3600):return self.local.enable(url_ttl)
    def disable(self):
        state=self.local.disable()
        try:self._rpc('disable')
        except UserError:pass
        return state
    def close(self):pass # Workbench lifetime must not invalidate leased images.

def serve(storage):
    root=Path(storage);tunnel=ReferenceTunnel(root);key=secrets.token_hex(32);last=[time.time()]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            if not hmac.compare_digest(self.headers.get('X-Reference-Key',''),key):self.send_error(403);return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<MAX_IMAGE_BYTES*2:raise UserError('请求大小无效')
                data=json.loads(self.rfile.read(size));last[0]=time.time()
                if self.path=='/status':result=tunnel.status()
                elif self.path=='/active':result={'active':tunnel.active_reference(data.get('url'))}
                elif self.path=='/publish':result=tunnel.publish(base64.b64decode(data['data'],validate=True),data['image_id'])
                elif self.path=='/disable':result=tunnel.disable()
                else:raise UserError('未知参考图操作')
            except Exception as e:result={'error':str(e) if isinstance(e,UserError) else '参考图操作失败'}
            body=json.dumps(result).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
    path=root/'reference-sidecar.json';tmp=path.with_suffix('.tmp')
    fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as f:json.dump({'port':server.server_port,'key':key,'pid':os.getpid()},f)
    tmp.replace(path)
    def cleanup():
        while True:
            time.sleep(30)
            with tunnel.lock:
                tunnel._cleanup_locked(time.time());empty=not tunnel._objects
            if empty and time.time()-last[0]>120:server.shutdown();return
    threading.Thread(target=cleanup,daemon=True).start()
    try:server.serve_forever()
    finally:
        tunnel.close();server.server_close()
        try:
            if json.loads(path.read_text()).get('key')==key:path.unlink()
        except (OSError,ValueError):pass
if __name__=='__main__':serve(sys.argv[1])
