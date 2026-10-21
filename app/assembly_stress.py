"""Actual Spine-runtime stress evidence before production binding.

Diagnostic skeletons use production meshes, texture packing and occlusion; they
are temporary ZIPs, never formal rigs or accepted animation deliveries.
"""
import copy,io,json,zipfile,threading
from functools import wraps
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from .core import UserError,atomic_json,sha
CONTRACT='runtime-assembly-stress-v1'
CHECKS=('arm_extremes','alternating_legs','hip_rotation','hip_contour','strap_integrity','occlusion_stable')
CLIPS=('stress-arms','stress-legs','stress-hip')
_RENDER_LOCK=threading.RLock()
def serialized(method):
 @wraps(method)
 def run(*args,**kwargs):
  with _RENDER_LOCK:return method(*args,**kwargs)
 return run

def signature(c):
 from .review_bundle import renderer_signature
 root=Path(__file__).resolve().parents[1]
 code=sha(b''.join((root/n).read_bytes() for n in ('app/assembly_stress.py','app/character.py','app/reviewed_parts.py','app/assembly_pipeline.py','app/ankle_join.py')))
 return sha(json.dumps([CONTRACT,c.prebind_evidence()[0]['digest'],renderer_signature(root),code],sort_keys=True).encode())

def stress_clips(skeleton):
 names={b['name'] for b in skeleton['bones']}
 required={'hip','torso','l-upper-arm','r-upper-arm','l-upper-leg','r-upper-leg'}
 if not required<=names:raise UserError('短程压力测试需要实际人形肩髋骨骼')
 # 8 samples at t=0,.25,...1.75 include each extreme and both directions.
 def timeline(values):return [{'time':i*.25,'value':v} for i,v in enumerate(values)]
 def clip(angles):return {'bones':{bone:{'rotate':timeline(v)} for bone,v in angles.items() if bone in names}}
 return {
  'stress-arms':clip({'l-upper-arm':[0,-35,-35,0,35,35,0,0,0],'r-upper-arm':[0,35,35,0,-35,-35,0,0,0],
                       'l-lower-arm':[0,12,12,0,12,12,0,0,0],'r-lower-arm':[0,-12,-12,0,-12,-12,0,0,0]}),
  'stress-legs':clip({'l-upper-leg':[0,22,22,0,-22,-22,0,0,0],'r-upper-leg':[0,-22,-22,0,22,22,0,0,0],
                     'l-lower-leg':[0,-12,-12,0,12,12,0,0,0],'r-lower-leg':[0,12,12,0,-12,-12,0,0,0]}),
  'stress-hip':clip({'hip':[0,-10,-10,0,10,10,0,0,0],'torso':[0,10,10,0,-10,-10,0,0,0],
                    'l-upper-leg':[0,15,15,0,-15,-15,0,0,0],'r-upper-leg':[0,-15,-15,0,15,15,0,0,0]})}

def diagnostic_zip(c):
 from .character import make_skeleton
 from .rig import pack,validate_state
 im=c.cutout();parts=c.parts();order=c.review_order([p['name'] for p in parts])
 s=make_skeleton(im,c.meta['landmarks'],parts,c.meta['settings'],c.meta.get('masks',{}),order)
 if c.meta.get('part_review',{}).get('shoe_cuffs'):
  from .ankle_join import add_cuffs
  s,parts=add_cuffs(s,parts,c.cuff_textures(parts,c.layered_parts(im,c.meta['landmarks'],c.meta.get('masks',{}),adjust=False),c.meta['landmarks']))
 s['animations']=stress_clips(s);s['skeleton']['diagnostic_only']=CONTRACT
 if not validate_state({'skeleton':s,'parts':[{'name':p['name']} for p in parts]})['passed']:raise UserError('压力测试骨架结构无效')
 files,_=pack([({'name':p['name']},p['image']) for p in parts]);files['skeleton.json']=json.dumps(s).encode()
 out=io.BytesIO()
 with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
  for name,blob in files.items():z.writestr(name,blob)
 return out.getvalue(),s

def read_manifest(c):
 digest=signature(c);path=c.directory/'stress-cache'/digest/'manifest.json'
 if not path.exists():raise UserError('当前素材尚未完成骨骼压力测试')
 r=json.loads(path.read_text())
 if r.get('digest')!=digest:raise UserError('压力测试已失效')
 expected={(clip,anchor) for clip in CLIPS for anchor in (None,'__hip__','__straps__')}
 if len(r.get('panels',[]))!=9 or {(p.get('animation'),p.get('anchor')) for p in r['panels']}!=expected or any(p.get('frames')!=8 or p.get('runtime_passed') is not True for p in r['panels']):raise UserError('压力测试缺少动作、局部或实际runtime采样')
 for p in r['panels']:
  f=path.parent/p['file']
  if not f.is_file() or sha(f.read_bytes())!=p['sha256']:raise UserError('压力测试图缺失或损坏，请重新准备')
 return r

@serialized
def prepare(agent,c):
 with c.lock:
  if not c.asset_status()['ready']:raise UserError('补齐完整部件后才能进行压力测试')
  digest=signature(c)
  try:return read_manifest(c)
  except (UserError,OSError,ValueError,KeyError):pass
  blob,s=diagnostic_zip(c)
 folder=c.directory/'stress-cache'/digest;folder.mkdir(parents=True,exist_ok=True)
 # Whole assembled character plus dedicated hip and shoulder-strap crops.
 jobs=[(clip,anchor) for clip in CLIPS for anchor in (None,'__hip__','__straps__')]
 def render(item):
  clip,anchor=item;key='p'+str(jobs.index(item));path=folder/(key+'.png')
  report,png=agent.runtime_check(blob,clip,8,closeup=anchor)
  temp=path.with_suffix('.png.tmp');temp.write_bytes(png);temp.replace(path)
  return {'key':key,'file':path.name,'animation':clip,'anchor':anchor,'frames':8,'sha256':sha(png),'runtime_passed':report['passed'],
    'url':f'/api/characters/{c.id}/stress-image?panel={key}&digest={digest}'}
 with ThreadPoolExecutor(max_workers=2) as pool:panels=list(pool.map(render,jobs))
 result={'contract':CONTRACT,'digest':digest,'status':'prepared','visual_verdict':'not_reviewed','diagnostic_only':True,'panels':panels,'checks':list(CHECKS),
  'motion':{'arm_extreme_degrees':35,'thigh_alternation_degrees':22,'hip_degrees':10,'seconds_per_clip':2,'frames_per_clip':8},
  'limits':'实际二维骨骼、网格权重与遮挡驱动；髋部为画面内旋转，不是三维转身。压力姿态不验收接地或正式步态。'}
 with c.lock:
  if signature(c)!=digest:raise UserError('测试期间素材已变化，保留旧证据并重新准备当前版')
  atomic_json(folder/'manifest.json',result)
 return result

def panel(c,key,digest=None):
 r=read_manifest(c)
 if digest is not None and digest!=r['digest']:raise UserError('压力测试图已过期')
 p=next((p for p in r['panels'] if p['key']==key),None)
 if not p:raise UserError('压力测试图编号无效')
 return r,p,(c.directory/'stress-cache'/r['digest']/p['file']).read_bytes()

def review(c,data):
 c.check(data['revision']);r=read_manifest(c)
 if data.get('digest')!=r['digest']:raise UserError('先查看当前骨骼压力测试图')
 checks=data.get('checks',{})
 if set(checks)!=set(CHECKS) or any(type(v)!=bool for v in checks.values()):raise UserError('须分别检查摆臂、交替腿、髋旋转、腰胯轮廓、肩带完整与遮挡')
 if data.get('verdict') not in ('ready','needs_repair') or not isinstance(data.get('reason'),str) or not data['reason'].strip():raise UserError('记录压力测试真实观察与结论')
 if data['verdict']=='ready' and not all(checks.values()):raise UserError('压力姿态仍有尖角、碎片或撕裂，不能正式绑定')
 m=copy.deepcopy(c.meta);m['stress_contract']=CONTRACT;m['stress_review']={k:copy.deepcopy(data[k]) for k in ('digest','checks','verdict','reason')};c.save(m);return c.summary()

def require(c):
 r=read_manifest(c);v=c.meta.get('stress_review',{})
 if v.get('digest')!=r['digest'] or v.get('verdict')!='ready' or set(v.get('checks',{}))!=set(CHECKS) or not all(x is True for x in v['checks'].values()):raise UserError('先通过实际骨骼压力测试：摆臂极值、交替腿、髋旋转及腰胯／肩带局部')
 return r
