"""Codex visual assessment records, bound to the exact character artwork."""
import re

from .core import UserError

VERDICTS=('ready','needs_view','needs_parts','unsupported')
POLICIES=('asset-sheet','auto','preserve')
CAMERA_VIEWS=('front','back','side','three-quarter-side','unknown')
JOINTS=('shoulder','elbow','wrist','hip','knee','ankle')

def locomotion(actions):
    for action in actions:
        positive=re.sub(r'(?:不要|不需(?:要)?|无需|不制作|不做|避免)(?:制作|生成|做)?(?:行走|走路|步行|跑步)','',action.casefold())
        positive=re.sub(r'\b(?:no|not|without|do not|don\x27t)\s+(?:(?:generate|make|create)\s+)?(?:walk(?:_loop|ing)?|run(?:ning)?)\b','',positive)
        if re.search(r'(?<![a-z])(?:walk(?:_loop|ing)?|run(?:ning)?)(?![a-z])',positive) or any(word in positive for word in ('行走','走路','步行','跑步')):return True
    return False

def checked_connections(value):
    if not isinstance(value,dict) or set(value)!=set(JOINTS) or any(type(v)!=bool for v in value.values()):
        raise UserError('joint_review 须逐项提供 shoulder/elbow/wrist/hip/knee/ankle 的布尔检查结果')
    return dict(value)

def require_side_camera(assessment, requirements=''):
    if locomotion([*assessment.get('target_actions',[]),requirements]) and (assessment.get('camera_view') not in ('side','three-quarter-side') or not assessment.get('direction_consistent')):
        raise UserError('侧向行走须先准备头、衣装与双鞋朝向一致的侧面／三分之四侧向素材；正面横移不能作为侧向行走通过')

def checked_part_review(data):
    if data.get('verdict') not in ('ready','needs_adjustment','unsupported'):
        raise UserError('部件检查 verdict 须为 ready/needs_adjustment/unsupported')
    review={k:data.get(k) for k in ('verdict','reason','camera_view','direction_consistent')}
    if not isinstance(review['reason'],str) or not 1<=len(review['reason'].strip())<=2000:raise UserError('部件检查须说明实际缺陷与裁切／修整计划')
    if review['camera_view'] not in CAMERA_VIEWS or type(review['direction_consistent'])!=bool:raise UserError('部件检查须记录 camera_view 与 direction_consistent')
    review['joint_review']=checked_connections(data.get('joint_review'))
    for key in ('identity_preserved','silhouette_intact','separated'):
        if type(data.get(key))!=bool:raise UserError('部件检查须提供 '+key+' 布尔值')
        review[key]=data[key]
    if review['verdict']=='ready' and not all([*review['joint_review'].values(),review['identity_preserved'],review['silhouette_intact'],review['separated']]):
        raise UserError('仍有部件缺失、粘连或连接问题时不能记录 ready，请先标记 needs_adjustment 并修整')
    return review

def requested_camera_policy(text):
    """Interpret explicit camera intent, never infer the camera of actual pixels."""
    if not isinstance(text,str):raise UserError('视角要求须为文本')
    target=text.casefold()
    # Do not turn "no profile" or "not three-quarter" into positive intent.
    positive=re.sub(r'\b(?:not|no|non|avoid)[ -]+(?:(?:true|strict|full|pure)\s+)?(?:profile(?:\s+view)?|side[ -]on)\b','',target)
    positive=re.sub(r'(?:不要|不需|不是|避免|非)(?:正|纯|严格|完全)?侧(?:面|视)(?:视角|视图)?','',positive)
    positive=re.sub(r'(?:不要|不需|不是|避免|非)(?:正面|背面)(?:视角|视图)?','',positive)
    positive=re.sub(r'\b(?:not|no)[ -]+(?:three[ -]quarter|3\s*/\s*4)\b','',positive)
    positive=re.sub(r'\b(?:not|no|avoid)[ -]+(?:front|rear|back)\s+view\b','',positive)
    profile=bool(re.search(r'\bprofile\b|\bside[ -]on\b|(?:正|纯|严格|完全)侧(?:面|视)|(?<!\d)90\s*(?:°|度|degrees?)',positive))
    partial=bool(re.search(r'\b(?:near|almost|semi|mild)[ -]+(?:side[ -]+)?profile\b|three[ -]quarter|3\s*/\s*4|三分之四|近(?:正)?侧面|偏侧面',positive))
    directions=[]
    for match in re.finditer(r'\bfacing\s+(?:to\s+)?(?:the\s+)?(left|right)\b|\b(left|right)[ -]+facing\b',target):
        directions.append(match.group(1) or match.group(2))
    for match in re.finditer(r'(?:朝|面向|向)\s*(左|右)',target):
        directions.append('left' if match.group(1)=='左' else 'right')
    facing=next(iter(set(directions))) if len(set(directions))==1 else None
    explicit=bool(re.search(r'\bprofile\b|\bside[ -]on\b|three[ -]quarter|\b(?:front|rear|back|side)\s+view\b|三分之四|(?:正|背|侧)面|(?<!\d)90\s*(?:°|度|degrees?)',positive))
    frontal=not profile and bool(re.search(r'\b(?:front|rear|back)\s+view\b|正面|背面',positive))
    return {'view':'profile' if profile and not partial else 'requested','explicit_view':explicit,
            'front_or_back':frontal,'facing':facing,'uniform_camera':True,'preserve_side_asymmetry':True}

def checked_assessment(data,image_id):
    if not isinstance(data,dict) or data.get('verdict') not in VERDICTS:
        raise UserError('图片检查 verdict 须为 ready/needs_view/needs_parts/unsupported')
    result={'image_id':image_id,'reviewer':'codex-vision'}
    source_kind=data.get('source_kind','character')
    if source_kind not in ('character','asset_sheet'):raise UserError('source_kind 须为 character 或 asset_sheet')
    reviewed=data.get('parts_reviewed',False)
    if type(reviewed)!=bool:raise UserError('parts_reviewed 须为布尔值')
    result.update(source_kind=source_kind,parts_reviewed=reviewed)
    if 'camera_view' in data:
        if data['camera_view'] not in CAMERA_VIEWS:raise UserError('camera_view 无效')
        result['camera_view']=data['camera_view']
    if 'direction_consistent' in data:
        if type(data['direction_consistent'])!=bool:raise UserError('direction_consistent 须为布尔值')
        result['direction_consistent']=data['direction_consistent']
    if 'joint_review' in data:result['joint_review']=checked_connections(data['joint_review'])
    for key in ('observed_view','target_view','reason'):
        value=data.get(key)
        if not isinstance(value,str) or not 1<=len(value.strip())<=1000:
            raise UserError('图片检查须提供 '+key)
        result[key]=value.strip()
    for key in ('target_actions','missing_parts'):
        values=data.get(key,[])
        if not isinstance(values,list) or len(values)>32 or any(not isinstance(v,str) or not 1<=len(v.strip())<=100 for v in values):
            raise UserError('图片检查 '+key+' 须为短文本列表')
        result[key]=[v.strip() for v in values]
    if not result['target_actions']:raise UserError('图片检查须说明目标动作')
    if data['verdict']=='needs_parts' and not result['missing_parts']:raise UserError('须说明缺失或遮挡的部件')
    if data['verdict']=='ready' and result['missing_parts']:raise UserError('ready 不能同时声明动作所需部件缺失，请补图或改为 needs_parts/needs_view')
    result['verdict']=data['verdict']
    return result

def selected_sheets(value):
    if value is None:return ['body','arms','legs']
    if not isinstance(value,list) or not value or len(value)>3 or any(not isinstance(v,str) or v not in ('body','arms','legs','limbs','character-assets','turnaround','costume') for v in value) or len(set(value))!=len(value):
        raise UserError('sheets 须为 body/arms/legs/limbs/character-assets/turnaround/costume 中不重复的部件组')
    if 'costume' in value and any(v in value for v in ('body','character-assets')):raise UserError('costume 不与 body/character-assets 重复生成主衣装')
    if 'character-assets' in value and set(value)-{'character-assets','turnaround'}:raise UserError('character-assets 须独立生成，不与其他部件组重复')
    if 'limbs' in value and any(v in value for v in ('arms','legs')):
        raise UserError('完整肢体 limbs 不与 arms/legs 重复生成')
    return value
