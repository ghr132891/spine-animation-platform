"""Garment-specific assembly contracts. Measurements belong to one reference.

Templates never invent anatomy, copy cloth across materials or infer approval.
"""
import copy,json
import numpy as np
from .core import UserError,sha

CATALOG={
 'trousers':{'label':'上衣＋长裤','continuous_hips':True,'insert_ratio':.18,'ownership':{'collar':'torso','waistband':'torso','pockets':'torso','hip_cloth':'shared-costume-master','sleeves':'arms','fixed_shoulder_straps':'torso-behind-near-arm','shoes':'legs'},'note':'裤腰、裆部和左右大腿根共用同一完整裤装母版；裤缝、褶皱、纹理尺度连续，独立腿片保留隐藏裤料。'},
 'shorts':{'label':'上衣＋短裤','continuous_hips':True,'insert_ratio':.15,'ownership':{'collar':'torso','waistband':'torso','pockets':'torso','hip_cloth':'shared-costume-master','bare_legs':'legs','shoes':'legs'},'note':'腰胯与短裤裤管使用同源连续图面，裤口和裸腿分材质处理；腿部皮肤不能用裤料补齐。'},
 'skirt':{'label':'连衣裙／裙装','continuous_hips':False,'insert_ratio':.16,'ownership':{'collar':'torso','waist_and_skirt':'torso','lining':'torso','bare_legs':'legs','shoes':'legs'},'note':'腰线至裙摆保留完整连续裙料，腿片独立位于裙下，保持原实色内衬与透明外纱。裙料不能复制到裸腿。'},
 'split-robe':{'label':'开衩长袍／长衣','continuous_hips':False,'insert_ratio':.18,'ownership':{'collar':'torso','waist':'torso','front_back_panels':'torso','inner_trousers':'legs','shoes':'legs'},'note':'衣领到衣摆连续，前后衣片和内穿裤装分开归属；保留原开衩与后侧遮挡，不用外袍纹理覆盖内裤。'},
 'armor':{'label':'分片铠甲','continuous_hips':False,'insert_ratio':.12,'ownership':{'chest_waist_plates':'torso','shoulder_plates':'arms','underlayer':'shared-underlayer-master','leg_plates':'legs','shoes':'legs'},'note':'胸腰甲、肩甲、腿甲按真实搭接分层；接缝补内衬，保留硬甲边界，不能把硬甲拉伸成连续布料。'}
}
PARTS=('l-upper-arm','r-upper-arm','l-upper-leg','r-upper-leg')

def finite(v,label):
 if type(v) not in (float,int) or not np.isfinite(v):raise UserError(label+'须为有限数值')
 return float(v)

def basis(c):
 keys=('l-shoulder','r-shoulder','l-hip','r-hip','neck','hip')
 if not c.meta.get('image_id') or any(k not in c.meta.get('landmarks',{}) for k in keys):raise UserError('先准备角色原图与肩髋定位，再设置服装模板')
 return {'image_id':c.meta['image_id'],'size':list(c.image().size),'anchors':{k:c.meta['landmarks'][k] for k in keys}}

def contract(c,data):
 if not isinstance(data,dict) or not isinstance(data.get('type'),str) or data['type'] not in CATALOG:raise UserError('选择有效服装模板：'+', '.join(CATALOG))
 spec=copy.deepcopy(CATALOG[data['type']]);base=basis(c);size=np.array(base['size']);lm=c.meta['landmarks']
 if 'basis' in data and data['basis']!=base:raise UserError('旧模板测量不属于当前母版，请重新测量而不是沿用旧尺寸')
 dimensions={name:float(np.linalg.norm((np.array(lm['l-'+joint])-lm['r-'+joint])*size)) for name,joint in [('shoulder_span','shoulder'),('hip_span','hip')]}
 openings=data.get('openings',{})
 if not isinstance(openings,dict) or set(openings)-set(PARTS):raise UserError('连接开口须按近远侧臂腿分别登记')
 checked={}
 for name,item in openings.items():
  if not isinstance(item,dict):raise UserError('连接开口格式无效')
  side=name[:2];leg=name.endswith('leg');root='hip' if leg else 'shoulder';end='knee' if leg else 'elbow'
  span=item.get('span')
  if not isinstance(span,list) or len(span)!=2:raise UserError('开口 span 须为母版左右边界')
  left,right=[finite(v,'开口边界') for v in span];join=finite(item.get('join_y'),'连接高度');release=finite(item.get('release_y'),'过渡终点')
  root_point=np.array(lm[side+root])*size
  if not 0<=left<root_point[0]<right<=size[0] or not 0<=join<release<lm[side+end][1]*size[1]-2:raise UserError('开口须覆盖原关节，过渡结束于膝肘之前')
  depth=finite(item.get('insert_depth',round((right-left)*spec['insert_ratio'],2)),'插接深度')
  if not 1<=depth<=min((right-left)*.5,release-join) or join-depth<0:raise UserError('插接深度超出局部覆盖范围')
  if not isinstance(item.get('evidence'),str) or not item['evidence'].strip():raise UserError('开口测量须记录母版实图依据')
  if type(item.get('fit_required',True))!=bool:raise UserError('fit_required须为布尔值')
  checked[name]={'fit_required':item.get('fit_required',True),'span':[left,right],'join_y':join,'release_y':release,'insert_depth':depth,'evidence':item['evidence'][:400]}
 missing=data.get('missing_regions',[])
 if not isinstance(missing,list) or len(missing)>8:raise UserError('最多登记8处定向补全区域')
 for r in missing:
  if not isinstance(r,dict) or r.get('part') not in ('torso',*PARTS) or not isinstance(r.get('reason'),str) or not r['reason'].strip():raise UserError('补全区域须指定部件与缺失原因')
  box=r.get('box')
  if not isinstance(box,list) or len(box)!=4:raise UserError('补全区域须提供母版像素 box')
  x,y,x1,y1=[finite(v,'补全区域') for v in box]
  if not 0<=x<x1<=size[0] or not 0<=y<y1<=size[1]:raise UserError('补全区域超出母版')
 sources=data.get('shared_sources',{})
 if not isinstance(sources,dict) or set(sources)-set(PARTS):raise UserError('共享来源须对应实际臂腿')
 for name,s in sources.items():
  if not isinstance(s,dict) or set(s)!={'donor_layout','texture_join'} or not isinstance(s.get('donor_layout'),dict) or not isinstance(s.get('texture_join'),dict) or s['texture_join'].get('mode')!='shared-pixels':raise UserError('共享来源须提供实际母版与 shared-pixels 范围')
  if not spec['continuous_hips'] and name.endswith('leg'):raise UserError('此模板不可将外裙或甲片裤料复制到腿部；按分层材质单独补全')
  from .connection_fit import registered_donor
  registered_donor(c,s['donor_layout'])
 result={'version':'garment-template-v1','type':data['type'],'profile':spec,'basis':base,'dimensions':dimensions,'openings':checked,'shared_sources':copy.deepcopy(sources),'missing_regions':copy.deepcopy(missing),
  'dimension_note':'当前母版投影关节间距，不是人体解剖肩宽；开口轮廓需逐侧实测','status':'measured' if len(checked)==4 else 'needs_opening_measurements'}
 result['digest']=sha(json.dumps(result,sort_keys=True,ensure_ascii=False).encode());return result

def current(c):
 saved=c.meta.get('garment_template')
 if saved and saved['basis']!=basis(c):raise UserError('服装模板母版或肩髋定位已变化，请重新测量模板，保留已有素材')
 return saved

def plan(c):
 t=current(c)
 if not t:return {'status':'needs_garment_type','catalog':CATALOG,'connections':[],'completion_requests':[]}
 connections=[];blocked=[];layers=c.meta.get('part_layers',{})
 for name,o in t['openings'].items():
  if not o['fit_required']:continue
  if name not in layers or 'torso' not in layers:continue
  r=layers[name]
  if r.get('garment_fit',{}).get('template_digest')==t['digest'] and r['garment_fit'].get('result_sha256')==r['image_id']:continue
  source=t['shared_sources'].get(name)
  if name.endswith('leg') and t['profile']['continuous_hips'] and not source:
   blocked.append({'part':name,'reason':'需要同源腰胯到大腿的连续母版，不能以调色代替','box':[o['span'][0],o['join_y']-o['insert_depth'],o['span'][1],o['release_y']]});continue
  if name.endswith('leg') and not t['profile']['continuous_hips']:continue
  connections.append({'part':name,'donor':'torso','source_sha256':r['image_id'],'donor_sha256':layers['torso']['image_id'],'join_y':o['join_y'],'release_y':o['release_y'],'target_span':o['span'],'reason':o['evidence'],**(source or {})})
 requests=[*copy.deepcopy(t['missing_regions']),*blocked]
 return {'status':t['status'],'template':t,'connections':connections,'completion_requests':requests,'completion_sheets':sorted({'costume' if r['part']=='torso' or t['profile']['continuous_hips'] and r['part'].endswith('leg') else 'limbs' for r in requests})}

def execute(c):
 """Run the measured template on import; never re-warp unchanged results."""
 if not c.meta.get('garment_template'):return
 p=plan(c);meta=copy.deepcopy(c.meta);meta['garment_preparation']={k:v for k,v in p.items() if k not in ('template','connections')};c.save(meta)
 if not p['connections']:return
 for conn in p['connections']:
  r=c.meta['part_layers'][conn['part']];opening=p['template']['openings'][conn['part']]
  if 'canvas_offset' not in r:raise UserError('先保存同坐标完整图层')
  from .core import read_image
  a=np.array(read_image((c.directory/'part-images'/(r['image_id']+'.png')).read_bytes()))[:,:,3]
  pivot=r['canvas_pins']['hip' if conn['part'].endswith('leg') else 'shoulder'];x=round(pivot[0]-r['canvas_offset'][0]);y=round(opening['join_y']-opening['insert_depth']-r['canvas_offset'][1]);end=round(opening['join_y']-r['canvas_offset'][1])
  if not (0<=x<a.shape[1] and 0<=y<end<a.shape[0]) or np.any(a[y:end+1,x]<128):raise UserError('连接隐藏插接图面不足：'+conn['part']+'；按模板补齐真实衣料，不能拉长空白')
 from .connection_fit import apply
 apply(c,p['connections']);meta=copy.deepcopy(c.meta)
 for conn in p['connections']:
  r=meta['part_layers'][conn['part']];r['garment_fit']={'template_digest':p['template']['digest'],'result_sha256':r['image_id']}
 c.save(meta)

def generation_request(c,data):
 """The same notes reach preview, stored job and the provider call."""
 from .part_prompts import complete_generation_request,part_instructions
 out=complete_generation_request(data);out.pop('_garment_notes',None);out.pop('_garment_type',None)
 t=contract(c,data['garment_template']) if data.get('garment_template') else current(c)
 if not t:return out,None
 if data.get('completion_only'):
  regions=t['missing_regions']
  if not regions:raise UserError('没有已确认缺失区域，不重复提交补图')
  out['sheets']=sorted({'costume' if r['part']=='torso' or t['profile']['continuous_hips'] and r['part'].endswith('leg') else 'limbs' for r in regions})
 shared='服装模板：'+t['profile']['label']+'。'+t['profile']['note']+' 所有裁片沿用同一母版相机与像素尺度，袖根、髋根覆盖量按同一套实测尺寸。'
 if c.meta.get('back_accessory_part')=='torso':shared+=' 已有背包包体与固定肩带保留在主衣装，近臂位于前方；只在实图确需跨肩覆盖时另取局部前景，不能把整条垂带复制到袖子前。空格不代表删除背包。'
 elif c.meta.get('back_accessory_part')=='back':shared+=' 背包独立保留在背部图层，前肩带属于对应前景层。'
 shared+=' 装配母版%s×%s像素；裁片排版位置可变，装配尺度固定。'%tuple(t['basis']['size'])
 shared+=' 母版投影肩关节间距%.1f、髋关节间距%.1f像素。'%(t['dimensions']['shoulder_span'],t['dimensions']['hip_span'])
 if t['openings']:shared+='连接规格：'+json.dumps({n:{k:v for k,v in o.items() if k!='evidence'} for n,o in t['openings'].items()},ensure_ascii=False,separators=(',',':'))+'。'
 notes={}
 for sheet in out['sheets']:
  original=part_instructions(data,sheet)
  note=shared
  if data.get('completion_only'):note+='仅补全这些母版区域，保留周围身份、颜色和纹理：'+json.dumps(t['missing_regions'],ensure_ascii=False,separators=(',',':'))
  notes[sheet]=original+' '+note
  if len(notes[sheet])>1200:raise UserError('模板与补全说明过长，请缩短备注或拆分补全区域')
 # Internal provider notes may exceed the public 400-char per-sheet user input.
 out['_garment_notes']=notes;out['_garment_type']=t['type'];return out,t


def adapt_prompt(text,kind):
 if kind not in CATALOG:raise UserError('服装类型无效')
 if kind in ('trousers','shorts','armor'):
  text=text.replace('主衣装保留从腰线连到前后裙底的完整衣料，包括被遮挡部分；衣裙仅归属衣装，活动组合独立且轮廓完整。保持原实色与轻纱材质，边缘干净、裙摆自然垂落。','主衣装保留领口、腰胯与袖后完整图面；保持原材质，活动部件独立完整。')
  text=text.replace('若参考为长衣装，保留原开衩、后侧实色遮挡与轻纱分区。','')
  text=text.replace(' For long costumes preserve openings, opaque and sheer panels.','')
 if kind in ('shorts','skirt','split-robe','armor'):
  text=text.replace('相接裁片从同一衣料母版延续：开口宽度、裤缝走向、褶皱位置与纹理尺度一致，裤腰和腿根使用连续的同源裤料，口袋与腰线只保留在主衣装，腿片保留对应的隐藏插接面。','同材质相接区沿用母版纹理，不同材质按真实分层；裸腿、内衬和甲片分别保留原图面。')
  text=text.replace('裤腰图案只保留在一块连续主素材，裤装插接区留无重复腰线的覆盖余量。','腰部装饰仅归主体，独立活动部件保留原材质和隐藏覆盖。')
  text=text.replace('裤装在裤腰下连续延伸','活动部件在主衣装下保留完整隐藏覆盖')
  text=text.replace('裤装','腿部素材').replace('裤料过渡自然','相同材质过渡自然')
  text=text.replace('trouser','leg').replace('waistband','waist trim')
 return text
