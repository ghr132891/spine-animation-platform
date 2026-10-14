"""Scoped reference prompts. Requested layouts still require visual inspection."""
import copy
import re

from .core import UserError
from .image_preflight import requested_camera_policy


# The layout contract is deliberately described as art assets rather than anatomy.
# Source pixels still need visual review; wording alone cannot certify the result.
COMMON=('Match the reference character, camera, palette, scale and original image-side details. '
 'Complete occluded garment and accessory artwork. Closed cut-outs in each occupied cell. '
 'Align portrait inside collar; continuous waistband, nonrepeating textile transitions. '
 'Complete silhouettes, sleeve-hidden textiles and concealed trouser inserts. '
 'Backpacks use the worn side view, strap face inward, outer pockets outward. ')
COMMON_ZH=('保持参考角色、视角、配色、材质和比例。单侧服装装饰保留在原图对应侧。'
 '按原风格补齐服装与配件被遮挡的图案。每个有素材的格放一个独立切片，格间留白。'
 '轮廓闭合，连接边缘圆润，成对素材比例一致，纯白背景。'
 '肖像连接边准确进入原衣领开口。裤腰图案只保留在一块连续主素材，裤装插接区留无重复腰线的覆盖余量。'
 '每片素材包含独立完整轮廓和隐藏插接余量，袖后主衣料连续，裤装在裤腰下连续延伸；移开相邻素材后仍完整。'
 '背包使用与角色一致的穿戴侧视角，肩带面贴衣装，外袋朝外，独立完整包体补齐袖后遮挡图面。裤料过渡自然，不使用镜像平铺纹理。')
SHEETS={
 'costume':{'ratio':'2:3','columns':1,'rows':1,'parts':['torso'],'groups':{'torso':'costume'},
 'requires_reviewed_layout':True,
 'prompt':'One complete isolated costume cut-out, matching the reference camera and scale. Preserve the neckline, waist, garment opening and all front and rear fabric from the waist to the hem. Continue occluded fabric in the original material. Every hanging panel connects to its original upper garment. Neutral gravity-hanging silhouette and clean mounting overlap. Keep original opaque and sheer textile regions distinct. This image contains only the complete main garment; independent animation assemblies are delivered separately.',
 'prompt_zh':'一块完整独立主衣装素材，保持参考视角与比例。保留领口、腰线、开衩和从腰部连至裙底的全部前后衣料，按原材质补齐遮挡处衣料。每片垂落衣料连接到所属上部衣装，轮廓自然下垂，连接区留干净覆盖余量。区分原实色衣料和轻纱区域。本图仅包含完整主衣装，其余活动素材单独制作。'},
 'turnaround':{'ratio':'3:2','columns':3,'rows':1,'parts':[], 'reference_only':True,
 'prompt':'Character turnaround reference sheet, three complete standing views: FRONT, RIGHT PROFILE, BACK. Same character, same costume, hairstyle, palette and proportions in every view. Orthographic camera, shared height, aligned ground line, relaxed neutral stance. Preserve reference shoulder shape, arm placement, garment slit and hem, knee height, shoe shape and the full back-hair outline. Separate each figure with white space. Crisp complete silhouettes on a plain white background. This is a visual reference sheet for comparing animation assets.',
 'prompt_zh':'角色三视图参考板，依次为正面、朝右的严格侧面、背面，均为完整自然站姿。同一角色、服装、发型、配色与比例，正交视角、统一高度、脚底对齐。保持原肩部轮廓、手臂位置、衣裙开衩与下摆、膝部高度、鞋型及完整后发轮廓。三个人物间留白，轮廓清晰完整，纯白背景。这张图用于比对动画素材。'},
 'body':{'ratio':'1:1','columns':2,'rows':2,'parts':['head','torso','pelvis','back'],'prompt':
 'Exactly 2 columns x 2 rows of character asset cut-outs. TOP LEFT: the original portrait and headwear with a short mounting edge. '
 'TOP RIGHT: the main costume panel, from collar to waist. BOTTOM LEFT: the waist costume panel. '
 'BOTTOM RIGHT: the reference backpack as one complete accessory.',
 'prompt_zh':'角色素材切片，严格2列2行。左上：原肖像与头饰，保留短连接边缘。右上：从衣领到腰线的主衣片。左下：腰部衣片。右下：参考背包，作为完整配件。'},
 'arms':{'ratio':'2:3','columns':2,'rows':3,'parts':['l-upper-arm','r-upper-arm','l-lower-arm','r-lower-arm','l-hand','r-hand'],'prompt':
 'Exactly 2 columns x 3 rows of character asset cut-outs. LEFT COLUMN: source image-left sleeve assembly; '
 'RIGHT COLUMN: source image-right sleeve assembly. TOP ROW: the upper sleeve sections. '
 'MIDDLE ROW: the lower sleeve sections. BOTTOM ROW: the two matching terminal cut-outs from the reference. '
 'Arrange each section vertically with rounded connection edges. Preserve the original cuff on its corresponding section.',
 'prompt_zh':'角色素材切片，严格2列3行。左列属于原图左侧袖装组合，右列属于原图右侧袖装组合。首行：两片上段袖装。中行：两片下段袖装。末行：参考图中对应的两片末端素材。各段竖直摆放，连接边缘圆润。原袖口保留在所属段。'},
 'legs':{'ratio':'2:3','columns':2,'rows':3,'parts':['l-upper-leg','r-upper-leg','l-lower-leg','r-lower-leg','l-foot','r-foot'],'prompt':
 'Exactly 2 columns x 3 rows of character asset cut-outs. LEFT COLUMN: source image-left trouser assembly; '
 'RIGHT COLUMN: source image-right trouser assembly. TOP ROW: the upper trouser sections. '
 'MIDDLE ROW: the lower trouser sections with narrow shoe inserts. BOTTOM ROW: two complete shoes, '
 'both pointing RIGHT, heels LEFT and toes RIGHT, with horizontal soles. '
 'Arrange trouser sections vertically with rounded connection edges. Keep each shoe opening and front cuff rim readable, '
 'with the trouser insert seated inside its shoe.',
 'prompt_zh':'角色素材切片，严格2列3行。左列属于原图左侧裤装组合，右列属于原图右侧裤装组合。首行：两片上段裤装。中行：两片下段裤装，保留窄鞋口插接部分。末行：两只完整鞋，均朝右，鞋跟向左、鞋尖向右，鞋底水平。裤装竖直摆放，连接边缘圆润。鞋口与前侧口沿清楚，裤装插接部分进入鞋内。'},
 'limbs':{'ratio':'1:1','columns':2,'rows':2,
 'parts':['l-upper-arm','r-upper-arm','l-upper-leg','r-upper-leg'],
 'requires_reviewed_layout':True,'prompt':
 'Exactly 2 columns x 2 rows of character asset cut-outs. LEFT COLUMN: source image-left assemblies; '
 'RIGHT COLUMN: source image-right assemblies. TOP ROW: two complete sleeve-to-terminal assemblies. '
 'BOTTOM ROW: two complete trouser-to-shoe assemblies. Each assembly is ONE unbroken silhouette, '
 'with all matching terminal artwork and the whole shoe connected. Keep the reference camera/side angle and original cuffs. '
 'Both shoes pointing RIGHT, heels LEFT, toes RIGHT, with horizontal soles. '
 'Keep trouser inserts seated inside their original shoe collars.',
 'prompt_zh':'角色素材切片，严格2列2行。左列属于原图左侧素材，右列属于原图右侧素材。首行：两套从袖装到末端素材的完整组合。末行：两套从裤装到整鞋的完整组合。每套保持一条连续完整轮廓，末端素材与整鞋连在所属组合上。保持参考视角和原袖口。两鞋均朝右，鞋跟向左、鞋尖向右，鞋底水平。裤装插接部分进入原鞋口。'},
 'character-assets':{'ratio':'3:2','columns':4,'rows':2,
 'parts':['head','torso','pelvis','back','l-upper-arm','r-upper-arm','l-upper-leg','r-upper-leg'],
 'requires_reviewed_layout':True,'prompt':
 'Exactly 4 columns x 2 rows of animation assets. TOP ROW, left to right: original head/headwear; '
 'collar-to-waist torso; waist/pelvis costume; complete reference back accessory. '
 'BOTTOM ROW, left to right: source image-left complete arm/palm; source image-right complete arm/palm; '
 'source image-left complete leg/shoe; source image-right complete leg/shoe. '
 'Each arm/palm and leg/shoe stays connected. '
 'Preserve face, orientation, reference footwear size and ONE assembly scale. '
 'Rounded shoulder/hip overlaps and natural elbow/knee contours support bending. '
 'Both shoes pointing RIGHT, with horizontal soles.',
 'prompt_zh':'角色动画素材，严格4列2行。上排从左到右：原头部与头饰；衣领至腰线的躯干衣装；腰胯衣装；参考图中完整背部配件。'
 '下排从左到右：原图左完整臂连手掌；原图右完整臂连手掌；原图左完整腿连鞋；原图右完整腿连鞋。'
 '每条臂腿保持一条连接完整的轮廓，手掌和整鞋清楚。保留原脸、头部角度与比例，所有素材使用同一组装尺度。'
 '肩胯连接处留圆润重叠面，肘膝轮廓适合自然弯曲。两鞋均朝右，鞋底水平。'},
}

BODY_WITHOUT_BACK={
 'ratio':'3:2','columns':3,'rows':1,'parts':['head','torso','pelvis'],
 'prompt':('Exactly 3 columns x 1 row of character asset cut-outs. LEFT CELL: the original portrait and headwear '
           'with a short mounting edge. CENTER CELL: the main costume panel, from collar to waist. '
           'RIGHT CELL: the waist costume panel. This sheet contains these three pieces only.'),
 'prompt_zh':'角色素材切片，严格3列1行。左格：原肖像与头饰，保留短连接边缘。中格：从衣领到腰线的主衣片。右格：腰部衣片。本图只包含以上三片素材。'}

PROMPT_VERSION='animation-parts-v21'


def prompt_language(*texts):
    """Match a normal Chinese request without rejecting product/action names."""
    text=' '.join(str(value) for value in texts)
    chinese=len(re.findall(r'[\u3400-\u9fff]',text))
    prose=re.sub(r'\b(?:Spine|PNG|GPT|Image|Flare|idle|walk|walk_loop|run|jump|API|Codex)\b','',text,flags=re.I)
    latin=len(re.findall(r'[a-z]',prose,re.I))
    return 'zh' if chinese and chinese*4>=latin else 'en'


def _motion_names(actions,language):
    names={'idle':'待机','walk':'行走','walk_loop':'循环行走','run':'跑步','jump':'跳跃'}
    return '、'.join(names.get(action,action) for action in actions) if language=='zh' else ', '.join(actions)

def sheet_spec(sheet,include_back=True,body_layout='separate'):
    """Snapshot the exact requested layout; optional back is chosen by visual assessment."""
    if not isinstance(sheet,str) or sheet not in SHEETS:raise UserError('未知部件组')
    if type(include_back) is not bool:raise UserError('include_back 须为布尔值')
    if body_layout not in ('separate','combined'):raise UserError('body_layout 须为 separate 或 combined')
    spec=copy.deepcopy(BODY_WITHOUT_BACK if sheet=='body' and not include_back else SHEETS[sheet])
    if sheet=='character-assets' and body_layout=='combined':
        spec['parts'].remove('pelvis');spec['groups']={'torso':'costume'}
        spec['prompt']=spec['prompt'].replace('collar-to-waist torso; waist/pelvis costume; complete reference back accessory.',
            'ONE continuous main costume from the reference neckline through waist and hem; complete reference back accessory; an empty white cell.')
        spec['prompt_zh']=spec['prompt_zh'].replace('衣领至腰线的躯干衣装；腰胯衣装；参考图中完整背部配件。',
            '从原领口经腰线至衣摆的一整块主衣装；参考图中完整背部配件；纯白空格。')
    if sheet=='character-assets' and not include_back:
        spec['parts'].remove('back')
        spec['prompt']=spec['prompt'].replace('complete reference back accessory','an empty white cell')
        spec['prompt_zh']=spec['prompt_zh'].replace('参考图中完整背部配件','纯白空格')
    if sheet=='character-assets':
        spec['prebind_contract']='complete-assets-v1'
        spec['prompt']+=' Complete cut-outs. Sleeveless costume; open collar with front/back rims, clean portrait insert. '
        spec['prompt_zh']+=' 独立完整部件优先于紧密排版。独立袖装对应的主体保留完整袖后衣料，不重复绘制整条袖装。衣领只归主衣装，保留可读的前后领边与真实领口开口，肖像保留干净领口插接区。连接余量延续所属材质，所有部件统一组装尺度，部件间充分留白。'
        spec['prompt']+=' Match overlaps and continuous textiles; separate existing long hair from backpack.'
        spec['prompt_zh']+=' 肖像与衣装保留连续连接图面，肩端覆盖区颜色一致。主衣装保留从腰线连到前后裙底的完整衣料，包括被遮挡部分；衣裙仅归属衣装，活动组合独立且轮廓完整。保持原实色与轻纱材质，边缘干净、裙摆自然垂落。仅当参考存在长后发时单独提供，保留已有背包，不互相替代。'
    if sheet in ('character-assets','costume'):
        spec['prompt']+=' For long costumes preserve openings, opaque and sheer panels.'
        spec['prompt_zh']+=' 若参考为长衣装，保留原开衩、后侧实色遮挡与轻纱分区。活动组合采用自然舒展的连续轮廓，连接区仅留短而圆润的隐藏重叠量，衣片间距与侧面参考一致。'
    if sheet in ('arms','limbs','character-assets'):
        spec['prompt']+=' Relaxed downward sleeve assemblies, reference terminal orientation, aligned cuffs, readable overlaps and distinct near/far designs.'
        spec['prompt_zh']+=' 两侧袖装组合自然下垂，末端素材保持参考自然站姿的朝向，近远侧设计分别保留，袖口对齐，连接覆盖区清楚。'
    spec.setdefault('requires_reviewed_layout',False)
    spec['body_layout']=body_layout
    return spec


def part_instructions(data,sheet):
    """Per-sheet notes replace shared legacy notes; mixing the two is ambiguous."""
    if not isinstance(sheet,str) or sheet not in SHEETS:raise UserError('未知部件组')
    if 'instructions_by_sheet' not in data:
        return str(data.get('instructions','')).strip()
    notes=data['instructions_by_sheet']
    if (not isinstance(notes,dict) or set(notes)-set(SHEETS) or
            any(not isinstance(value,str) or len(value)>400 for value in notes.values())):
        raise UserError('instructions_by_sheet 须按 body/arms/legs/limbs/character-assets 提供各组不超过400字的短要求')
    if str(data.get('instructions','')).strip():
        raise UserError('请使用各部件组独立要求，不混用 instructions 与 instructions_by_sheet')
    return notes.get(sheet,'').strip()

def view_prompt(target_view,actions,instructions=''):
    if not isinstance(target_view,str) or not 1<=len(target_view)<=200:raise UserError('请说明目标视角')
    if not isinstance(instructions,str) or len(instructions)>1200:raise UserError('补充要求最多1200字')
    if not isinstance(actions,list) or not actions or any(not isinstance(a,str) or len(a)>100 for a in actions):raise UserError('请说明目标动作')
    walking=any(re.search(r'(?<![a-z])(?:walk(?:ing)?|run(?:ning)?)(?![a-z])',action.lower()) or
                any(word in action for word in ('行走','走路','步行','跑步')) for action in actions)
    policy=requested_camera_policy(target_view)
    language=prompt_language(target_view,instructions,*actions)
    if language=='zh':
        camera='采用所要求朝向的严格正侧面，所有素材共享该侧面视角。' if policy['view']=='profile' else '所有素材共享所要求的视角。'
        visibility='保持自然直立准备姿态，近侧袖装与主衣片适度分离，远侧配件按原视角可读程度表现。被遮挡的服装与配件图案另作素材。' if policy['view']=='profile' else '保持自然直立准备姿态，两侧袖装与主衣片适度分离，左右裤装与鞋清楚区分。'
        shoes='两鞋沿所要求方向摆放，鞋侧面清楚，鞋底水平，裤装插接部分连入原鞋口。' if walking and not policy['front_or_back'] else '裤装与整鞋连接自然。'
        return ('画面中央只有一个角色、一个完整自然站姿，作为二维素材动画的单人准备立绘。保持原角色肖像、发型、比例、服装、配色、材质与画风。'
                '只调整准备姿态与视角。目标视角：'+target_view+'。目标动作：'+_motion_names(actions,language)+'。采用正交视角。'
                +camera+'单侧补丁与装饰保留在原侧。'+visibility+shoes+
                '保留参考图中已有配件，配件与移动素材轮廓清楚。完整轮廓四周留白，纯白背景。'+instructions)
    profile='Use a true lateral profile for the requested direction. ' if policy['view']=='profile' else ''
    visibility=('Neutral standing pose with the near sleeve assembly clear of the main costume panel. '
                'Keep far-side artwork readable within the same profile camera; complete occluded garment and accessory artwork in separate cut-outs. ') if policy['view']=='profile' else (
                'Neutral standing pose with clear spacing around sleeve assemblies and between trouser/shoe assemblies. ')
    shoes=('Both shoes must point in the specified facing direction, with readable shoe sides and horizontal soles. '
           'Seat trouser inserts inside their original shoe collars. ') if walking and not policy['front_or_back'] else 'Keep trouser-to-shoe connections continuous. '
    return ('Draw ONE complete character preparation pose for 2D cutout animation. '
        'Preserve the reference portrait, hairstyle, proportions, costume, colors, materials and illustration style. '
        'Change only the preparation pose and camera. Target camera: '+target_view+'. Target motions: '+_motion_names(actions,language)+'. '
        'Orthographic camera. '+profile+'Use ONE camera consistently across all costume panels and shoes. '
        'Keep asymmetric costume details on their original near/far side. '+visibility+shoes+
        'Preserve accessories only when present in the reference, with distinct readable outlines. '
        'Full silhouette with generous margin on a plain white background. '+instructions)


def character_prompt(description):
    description=str(description).strip()
    if not 5<=len(description)<=2000:raise UserError('角色描述须为 5–2000 字')
    policy=requested_camera_policy(description);language=prompt_language(description)
    facing=(policy['facing'] or 'right')
    if language=='zh':
        camera='严格正侧面' if policy['view']=='profile' else '角色要求中指定的视角' if policy['explicit_view'] else '轻微三分之四视角'
        direction='' if policy['front_or_back'] and not policy['facing'] else '，朝'+('左' if facing=='left' else '右')
        shoes='鞋遵循所要求的正面或背面视角，鞋底水平。' if policy['front_or_back'] else '两鞋均朝'+('左' if facing=='left' else '右')+'，鞋侧面清楚，鞋底水平。'
        visibility='近侧袖装与主衣片适度分离，远侧服装与配件按同一侧面视角表现，被遮挡的图案可另作素材。' if policy['view']=='profile' else '袖装与主衣片适度分离，左右裤装与鞋轮廓清楚。'
        return ('绘制一张用于二维素材动画的完整游戏角色立绘。正交视角，'+camera+direction+'，自然直立准备姿态。'
                '所有素材共享同一视角，单侧服装装饰保留在指定侧。'+visibility+shoes+
                '连接轮廓圆润完整，裤装插接部分进入原鞋口，鞋口前沿可读。保留要求中的配件与清楚轮廓。'
                '线条清晰，光照统一，完整轮廓四周留约百分之八空白，纯白背景。角色要求：'+description)
    camera=('true lateral profile' if policy['view']=='profile' else
            'the camera view specified in Character design' if policy['explicit_view'] else 'slight three-quarter view')
    facing_clause='' if policy['front_or_back'] and not policy['facing'] else ' facing '+facing.upper()
    shoes=('Both shoes follow the requested front/rear camera with horizontal soles. ') if policy['front_or_back'] else (
           'Both shoes point '+facing.upper()+' with readable shoe sides and horizontal soles. ')
    visibility=('Keep the near sleeve assembly clear of the main costume panel. Far-side costume artwork follows the same profile camera. '
                'Occluded garment and accessory artwork can be completed as separate cut-outs. ') if policy['view']=='profile' else (
                'Use clear spacing around sleeve assemblies and between trouser/shoe assemblies. ')
    return ('Create ONE complete 2D game character for cutout animation. Orthographic, '+camera+facing_clause+', neutral standing preparation pose. '
        'All costume panels and shoes share ONE camera angle. Keep one-sided costume details on their requested side. '+visibility+shoes+
        'Use rounded continuous mounting outlines; seat narrow trouser inserts inside the original shoe collars, with readable front cuff rims. '
        'Requested accessories have distinct readable outlines. Crisp outlines and consistent illustration lighting. '
        'Entire silhouette within frame with 8 percent margin on a plain white background. Character design: '+description)


def part_prompt(sheet,instructions='',facing='right',include_back=True,body_layout='separate'):
    if not isinstance(sheet,str) or sheet not in SHEETS:raise UserError('未知部件组')
    instructions=str(instructions).strip()
    if len(instructions)>1200:raise UserError('补充要求最多 1200 字')
    if facing not in ('right','left'):raise UserError('角色朝向无效')
    spec=sheet_spec(sheet,include_back,body_layout);language=prompt_language(instructions)
    layout=spec['prompt_zh'] if language=='zh' else spec['prompt']
    if facing=='left' and sheet in ('legs','limbs','character-assets','turnaround'):
        if language=='zh':layout=layout.replace('朝右的严格侧面','朝左的严格侧面').replace('均朝右','均朝左').replace('鞋跟向左、鞋尖向右','鞋跟向右、鞋尖向左')
        else:layout=layout.replace('RIGHT PROFILE','LEFT PROFILE').replace('pointing RIGHT','pointing LEFT').replace('heels LEFT and toes RIGHT','heels RIGHT and toes LEFT').replace('heels LEFT, toes RIGHT','heels RIGHT, toes LEFT')
    if sheet=='character-assets' and requested_camera_policy(instructions)['front_or_back']:
        layout=layout.replace('Both shoes pointing RIGHT, with horizontal soles.','Both shoes retain the specified reference camera, with level soles.').replace('Both shoes pointing LEFT, with horizontal soles.','Both shoes retain the specified reference camera, with level soles.').replace('两鞋均朝右，鞋底水平。','两鞋保持指定参考视角，鞋底水平。').replace('两鞋均朝左，鞋底水平。','两鞋保持指定参考视角，鞋底水平。')
    # Columns always describe the source image, regardless of the requested facing.
    extra=('本组设计要求：'+instructions) if language=='zh' and instructions else ('Design notes for these cells only: '+instructions) if instructions else ''
    return layout+' '+('' if sheet=='turnaround' else COMMON_ZH if language=='zh' else COMMON)+extra

def generation_plan(data,facing='right'):
    if not isinstance(data,dict):raise UserError('生图计划须为对象')
    mode=data.get('mode','character')
    if mode=='character':
        description=data.get('prompt','')
        items=[{'sheet':'character','label':'角色立绘','prompt':character_prompt(description),'ratio':'3:4','count':1,
                'camera_policy':requested_camera_policy(str(description)),'prompt_language':prompt_language(description)}]
    elif mode=='complete-parts':
        from .image_preflight import selected_sheets
        include_back=data.get('include_back',True)
        body_layout=data.get('body_layout','separate')
        labels={'costume':'完整衣装与前后衣摆补片','turnaround':'角色正侧背三视图（仅供验收）','body':'身体与背包' if include_back else '头、身体与腰胯','arms':'双臂与双手','legs':'双腿与双脚','limbs':'完整双臂与连鞋双腿','character-assets':'完整角色部件素材图'}
        items=[]
        for name in selected_sheets(data.get('sheets')):
            spec=sheet_spec(name,include_back,body_layout)
            items.append({'sheet':name,'label':labels[name],
                          'prompt':part_prompt(name,part_instructions(data,name),facing,include_back,body_layout),
                          'ratio':spec['ratio'],'columns':spec['columns'],'rows':spec['rows'],
                          'parts':spec['parts'],'include_back':include_back,
                          'body_layout':body_layout,'groups':spec.get('groups',{}),
                          'requires_reviewed_layout':spec['requires_reviewed_layout'],'reference_only':spec.get('reference_only',False),'count':1,'prompt_language':prompt_language(part_instructions(data,name))})
    else:
        from .core import UserError
        raise UserError('未知生图模式')
    return {'prompt_version':PROMPT_VERSION,'items':items,'image_count':len(items),
            'requires_reviewed_layout':any(item.get('requires_reviewed_layout',False) for item in items),
            'requires_visual_review':True,'prompt_policy':{'template_style':'concise-positive-artwork','preserve_user_design_notes':True,'moderation_rewrite_retry':False,'provider_switch_retry':False,'separate_groups':True}}
