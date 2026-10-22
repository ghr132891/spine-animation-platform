#!/usr/bin/env python3
"""Portable MCP stdio bridge. Stdlib only; platform keeps provider credentials."""
import argparse
import base64
import hashlib
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path


def obj(properties,required=()):return {'type':'object','properties':properties,'required':list(required),'additionalProperties':False}
STRING={'type':'string'};INT={'type':'integer'};DATA={'type':'object'}
NONCE={'type':'string','pattern':'^[a-f0-9-]{16,64}$','description':'使用 32 位十六进制 UUID；只能含 0-9/a-f/连字符，不加文字后缀'}
ENTITY={'kind':{'type':'string','enum':['project','rig','character']},'id':STRING}
FRAMES={'type':'integer','minimum':4,'maximum':32,'default':8}
GAIT={'animation':STRING,'anchor':STRING,'axis':{'type':'string','enum':['x','y'],'default':'x'},'pairs':{'type':'array','minItems':1,'maxItems':8,'items':obj({'arm':STRING,'leg':STRING,'arm_endpoint':{'type':'boolean'},'leg_endpoint':{'type':'boolean'}},('arm','leg'))}}
PART_IMAGES={'type':'array','maxItems':8,'items':obj({'name':STRING,'image_path':STRING},('name','image_path'))}
CONTACT_OPTIONS=obj({'samples':{'type':'integer','minimum':32,'maximum':512,'default':128},'skin':STRING,'scene_speed':{'type':'number','description':'共同场景在 runtime 世界X的平移补偿速度，向右正、向左负；与支撑脚自身世界速度反向。'},'ground_y':{'type':'number'},'stance':{'type':'number','exclusiveMinimum':0,'exclusiveMaximum':1},'pairs':{'type':'array','minItems':1,'maxItems':8,'items':obj({'slot':STRING,'foot':STRING,'hip':STRING,'knee':STRING,'phase':{'type':'number','minimum':0,'exclusiveMaximum':1},'arm':STRING},('slot','foot','hip','knee','phase'))}})
CAMERA={'type':'string','enum':['front','back','side','three-quarter-side','unknown']}
JOINT_REVIEW=obj({k:{'type':'boolean'} for k in ('shoulder','elbow','wrist','hip','knee','ankle')},('shoulder','elbow','wrist','hip','knee','ankle'))
CONTACT={'animation':{'type':'string','default':'walk'},'options':CONTACT_OPTIONS}


def settings():
    path=Path(__file__).resolve().parent.parent/'settings.json'
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
OPERATION={'anyOf':[
    obj({'action':{'const':'bone'},'bone':STRING,'name':STRING,'parent':STRING,**{k:{'type':'number'} for k in ('x','y','rotation','scaleX','scaleY','length','shearX','shearY')}},('action','bone')),
    obj({'action':{'const':'animation'},'name':STRING,'animation':DATA,'delete':{'type':'boolean'}},('action','name')),
    obj({'action':{'const':'copy-animation'},'source_rig_id':STRING,'source_animation':STRING,'name':STRING,'bone_map':DATA,'time_scale':{'type':'number'},'translation_scale':{'type':'number'}},('action','source_rig_id','source_animation')),
    {'type':'object','required':['action'],'properties':{'action':{'type':'string','enum':['add-bone','delete-bone','slot','attachment','delete-attachment','ik','texture','texture-repair','material-finish','occlusion-overlay','recolor','add-part','skeleton']}}}
]}


def tool(name,description,schema,readonly=False):
    return {'name':name,'description':description,'inputSchema':schema,
            'annotations':{'readOnlyHint':readonly,'destructiveHint':False,'openWorldHint':False}}


PUBLIC_TOOLS=[
    tool('spine_reference','服务端自动参考图：ensure 携带当前 revision，平台自动上传原图、回读校验并缓存临时 HTTPS 链接，无需亿创网页登录。generate-view/parts 默认也会自动完成，无需调用者准备链接。prepare 仅查询/导出，register 是已有链接的兼容入口。此工具不提交生图；新图片存储未配置时由平台管理员处理。',obj({'action':{'type':'string','enum':['ensure','prepare','register']},'kind':{'const':'character'},'id':STRING,'url':STRING,'revision':INT},('action','kind','id'))),
    tool('spine_sources','列出平台可用项目、骨骼项目和单图角色，以及后台能力边界。',obj({}),True),
    tool('spine_submit','提交需求，后台自动处理。image_path 为调用电脑上的角色图绝对路径；asset-sheet 默认先准备一张完整角色部件图，保留外观并补全完整手臂、腿和鞋，随后由 Codex 看实际素材、显式裁片并检查组装绑定后制作动画。原图已是完整素材图可直接裁片接入。auto 保留按视角／遮挡缺项补图策略，preserve 不生图；图片默认预算最多8张（首次三视图及完整部件图另计）。也可传 sources 或留空按描述生图。nonce 稳定用于去重。',obj({'nonce':NONCE,'requirements':STRING,'image_path':STRING,'output_scope':{'type':'string','enum':['animation','materials'],'default':'animation'},'image_preparation':{'type':'string','enum':['asset-sheet','auto','preserve'],'default':'asset-sheet'},'sources':{'type':'array','items':obj({**ENTITY,'revision':INT},('kind','id'))},'max_generations':INT,'max_repairs':{'type':'integer','minimum':0,'maximum':5,'default':4},'timeout_seconds':INT},('nonce','requirements'))),
    tool('spine_batch','批量提交，最多 32 个独立任务，每项与 spine_submit 参数相同。结果逐项返回。',obj({'requests':{'type':'array','items':DATA}},('requests',))),
    tool('spine_status','查询状态、任务进度、生成额度和成果下载地址。',obj({'task_id':STRING},('task_id',)),True),
    tool('spine_retry_stage','只重跑失败的固定平台阶段，复用素材、草稿和生图编号，不启动模型或重新生图。',obj({'task_id':STRING,'stage':{'type':'string','enum':['material-preparation','stress-test','bind','dynamic-review']}},('task_id','stage'))),
    tool('spine_resume','继续未完成或中断的同一任务，复用素材、检查记录和生成编号；保留原预算及已使用额度，不会新增历史记录。仅在用户要求继续时调用。',obj({'task_id':STRING,'nonce':NONCE},('task_id','nonce'))),
    tool('spine_cancel','停止后续执行，已提交的亿创任务保留，不会自动重提。',obj({'task_id':STRING},('task_id',))),
    tool('spine_refine','在上一版实际骨架副本上微调，保留版本链，默认不生图。part_images 可上传调用电脑上已补全的透明部件，默认未绑定，由后台按需求绑定；不会覆盖原贴图。后续微调使用本次新 task_id。',obj({'nonce':NONCE,'task_id':STRING,'requirements':STRING,'artifact_index':INT,'max_generations':INT,'max_repairs':{'type':'integer','minimum':0,'maximum':5,'default':4},'timeout_seconds':INT,'part_images':PART_IMAGES},('nonce','task_id','requirements'))),
    tool('spine_result','读取已交付骨架与动作的完整数据，便于微调前确认骨骼名、动画和当前参数。',obj({'task_id':STRING,'artifact_index':INT},('task_id',)),True),
    tool('spine_preview','返回动作联系图。frame_count 默认8，可选4–32，复杂接缝或摆臂使用16/32。先查询实际动作名；未指定时显示初始姿态。',obj({'task_id':STRING,'artifact_index':INT,'animation':STRING,'frame_count':FRAMES},('task_id',)),True),
    tool('spine_gait','测量已交付动作中同侧手臂与腿的运动相关性、最佳相位偏移和循环衔接。需从真实骨架确认 pairs 与 anchor；数值诊断辅助看图，不保证自然步态。',obj({'task_id':STRING,'artifact_index':INT,**GAIT},('task_id','animation','anchor','pairs')),True),
    tool('spine_contact','只读测量真实贴图鞋底接地、统一场景速度下滑步、循环及速度跳变。task_id/artifact_index 或 kind=rig/project,id 二选一；可直接诊断独立交付 rig。已知模板可读取映射；其他骨架须按真实蒙皮给 options.pairs、scene_speed、stance 等，缺条件返回 not_assessed。数值不认证自然步态、侧视或遮挡；不生图、不修改骨架。',obj({'task_id':STRING,'artifact_index':INT,'kind':{'type':'string','enum':['rig','project']},'id':STRING,**CONTACT}),True),
    tool('spine_closeup','1.9.9：长衣装腿部行走默认32帧，其它默认8帧；frame_count可选4–32。isolated=true仅渲染指定链，用于区分网格畸形和衣裙遮挡，不记验收，最终须查看组合图。按真实参考骨骼放大渲染8帧头颈连接，并定位疑似白底；只读。默认anchor=head；其它骨架先确认实际头颈骨。候选白色不是自动不合格，白衣/眼白需视觉辨别。支持task_id/artifact_index或kind/id二选一。',obj({'task_id':STRING,'artifact_index':INT,'kind':{'type':'string','enum':['rig','project']},'id':STRING,'animation':STRING,'anchor':STRING,'frame_count':{'type':'integer','minimum':4,'maximum':32},'isolated':{'type':'boolean','default':False}}),True),
    tool('spine_download','下载已完成成果到调用电脑的绝对 output_path，核对 SHA-256 后保存。默认不覆盖已有不同内容文件。',obj({'task_id':STRING,'output_path':STRING},('task_id','output_path'))),
]

ORIENTATION_CHECKS=obj({k:{'type':'boolean'} for k in ('left_arm_mapping','right_arm_mapping','left_palm_facing','right_palm_facing','left_shoe_facing','right_shoe_facing','mapping_correct','palm_facing','wrist_continuity','occlusion_readable','shoe_facing','shoe_profile')})
MATERIAL_CHECKS=obj({k:{'type':'boolean'} for k in ('connection_overlap','garment_continuity','layer_separation','silhouette_valid','edge_clean','occlusion_valid')})

WORKER_TOOLS=[
    tool('spine_deliver_draft','交付本任务已保存且可加载的骨架草稿到原制作记录，summary 说明待修问题。无需网页会话或管理员权限，不代表验收通过，不消耗修复次数。',obj({'id':STRING,'summary':STRING},('id','summary'))),next(t for t in PUBLIC_TOOLS if t['name']=='spine_reference'),
    tool('spine_assess_image','完整素材与视角确认同时通过后，平台动态启用自动绑定（包含创建时没有素材的任务），交付草稿并异步准备动态复检。返回animation_transition/automatic_animation，勿重复build；review_ready不是美术通过。limb-orientation-v1任务另传orientation_checks，逐侧映射和掌/鞋方向；字段见平台workflow-instructions。Codex 看过当前 character 原图后，记录 source_kind=character/asset_sheet、目标动作所需视角、遮挡、缺失部件与结论。asset-sheet 流程先显式裁片接入，再实际查看当前 assembly 的裁片、完整手臂、腿鞋与绑定位置，确认后以 parts_reviewed=true 记录 ready 才能 build。guide、apply-ai 或部件变化后必须复查。asset-integrity-v1 当前组装须 material_checks 逐项确认连接覆盖、连续衣料、分层、轮廓和边缘。',obj({'id':STRING,'source_kind':{'type':'string','enum':['character','asset_sheet']},'parts_reviewed':{'type':'boolean'},'camera_view':CAMERA,'direction_consistent':{'type':'boolean'},'joint_review':JOINT_REVIEW,'verdict':{'type':'string','enum':['ready','needs_view','needs_parts','unsupported']},'observed_view':STRING,'target_view':STRING,'target_actions':{'type':'array','items':STRING},'missing_parts':{'type':'array','items':STRING},'orientation_checks':ORIENTATION_CHECKS,'material_checks':MATERIAL_CHECKS,'reason':STRING},('id','verdict','observed_view','target_view','target_actions','reason'))),
    tool('spine_review_turnaround','实际查看三视图candidate后核对原图，phase=reference；导入裁片后实际查看assembly并对照三视图，phase=assembly。按identity/scale/arm_position/leg_alignment/hair_silhouette/costume_layers逐项记录。ready必须全部通过；改图或改组装使记录失效，三视图不是可直接导入的atlas。',obj({'id':STRING,'job_id':STRING,'phase':{'type':'string','enum':['reference','assembly']},'verdict':{'type':'string','enum':['ready','needs_adjustment','unsupported']},'checks':obj({k:{'type':'boolean'} for k in ('identity','scale','arm_position','leg_alignment','hair_silhouette','costume_layers')},('identity','scale','arm_position','leg_alignment','hair_silhouette','costume_layers')),'reason':STRING},('id','job_id','phase','verdict','checks','reason'))),
    tool('spine_review_parts','limb-orientation-v1任务另传orientation_checks，逐侧映射和掌/鞋方向；字段见平台workflow-instructions。先看候选部件图或原部件图，再记录身份、轮廓、分离程度和肩肘腕/髋膝踝连接；生成候选提供job_id，原部件图省略。ready须全部合格；可裁切修复的缺陷记录needs_adjustment和具体计划，再apply布局。asset-integrity-v1 须 material_checks={connection_overlap,garment_continuity,layer_separation,silhouette_valid,edge_clean} 五项布尔值；失败可 needs_adjustment，ready须全部true。组装后再次检查。',obj({'id':STRING,'job_id':STRING,'verdict':{'type':'string','enum':['ready','needs_adjustment','unsupported']},'camera_view':CAMERA,'direction_consistent':{'type':'boolean'},'joint_review':JOINT_REVIEW,'identity_preserved':{'type':'boolean'},'silhouette_intact':{'type':'boolean'},'separated':{'type':'boolean'},'orientation_checks':ORIENTATION_CHECKS,'material_checks':MATERIAL_CHECKS,'reason':STRING},('id','verdict','camera_view','direction_consistent','joint_review','identity_preserved','silhouette_intact','separated','reason'))),
    tool('spine_context','读取需求、当前任务独立源副本和生成额度。先调用此工具。',obj({}),True),
    tool('spine_inspect','读取项目详情。rig 包含完整骨架与动作 JSON；character 包含关键点、部件、版本和生成任务。',obj(ENTITY,('kind','id')),True),
    tool('spine_images','一次读取1–8张检查图。每项支持与spine_image相同的原尺寸/单姿态/crop/zoom字段；推荐概览加关节放大批量看图。',obj({'images':{'type':'array','minItems':1,'maxItems':8,'items':DATA}},('images',)),True),
    tool('spine_image','原尺寸检查传resolution=original；摆开图pose=0..4返回单姿态原图；crop=[x,y,x1,y1]裁关节，zoom=1..4按需放大。裁图不抵全图验收。查看 character 原图或 assembly；part 查看任一项目的部件，指定 part 名称或编号；candidate + job_id 查看已生成候选图；rig的material + part=material_sources实际ID查看保留原图。返回图片。',obj({**ENTITY,'view':{'type':'string','enum':['image','assembly','reconstruction-image','layer-overlap-image','edge-review-image','prebind-parts','prebind-assembly','prebind-spread','stress-image','part','candidate','material']},'part':STRING,'job_id':STRING,'resolution':{'type':'string','enum':['preview','original']},'pose':{'type':'integer','minimum':0,'maximum':4},'crop':{'type':'array','items':{'type':'integer'},'minItems':4,'maxItems':4},'zoom':{'type':'number','minimum':1,'maximum':4}},('kind','id')),True),
    tool('spine_character','固定肩带已在torso但额外前景跨袖时，prepare-assembly可传accessory_occlusion=[{mode:fixed-to-costume,source:torso,near_part,source_sha256,remove_foregrounds,reason}]，仅去除匹配的重复派生层，保留源图与动作；必须实看后使用。repair-connections批量恢复同部件历史像素、局部修整、配准和遮挡：data={revision,restore_parts:[{part,expected_sha256,source_sha256,box,reason}],edits,assembly_plan}；一次事务与一次证据重建，失败不改原版，之后重验压力。正式绑定前执行stress-test得到9张实际骨骼驱动压力图；spine_image view=stress-image,part=p0..p8逐张查看，再review-stress提交revision/digest/checks={arm_extremes,alternating_legs,hip_rotation,hip_contour,strap_integrity,occlusion_stable}/verdict=ready或needs_repair/reason。静态prebind通过不能代替压力验收；压力通过才自动绑定。服装模板 garment-template：data={revision,template:{type,openings,shared_sources,missing_regions}}，类型 trousers/shorts/skirt/split-robe/armor；省略template查询。导入默认执行实测连接，同源腰腿图面优先；generate-parts completion_only=true只补明确缺失区域，字段见assembly-execution.md。默认完整导入自动登记同坐标并保留无关部件校准/遮挡。已校准来源的replacements只需{name,character_id,part}，平台自动推导锚点；back_accessory_part=torso/back声明合并/独立背包，自动加入8/32帧复检；导入/修复整批成功才提交一版。prepare-assembly 执行已有完整素材配准补全与前景遮挡拆层，data={revision,replacements:[{name,character_id,part,source_pair,target_pair,pins}],overlays:[{name,source,subject,source_sha256,polygon,reason}]}；源像素/目标母版坐标，源character须属于本任务；导入可带assembly_plan一次执行。详见workflow-instructions。edge-review 返回当前源像素的边缘候选与坐标；spine_image view=edge-review-image 查看4倍深浅底局部，候选须实际检查、不自动删高光。绑定前修整finish-parts data={revision,edits:[{part,reason,operations}]}使用material-finish同样的定域去色、邻点取色和alpha操作；原图保留并清除旧验收，不套用固定人物范围。新完整部件流程先prebind，再查看prebind-parts/prebind-assembly/prebind-spread三张图，review-prebind提交digest、checks={complete_parts,assembly_matches,spread_intact}、verdict=ready或needs_repair及reason；三项未通过禁止build（包括draft）。完整重叠图层：layer-overlap/review-overlap，spine_image view=layer-overlap-image；partition-canvas的completed_image为同尺寸完整RGBA，保留隐藏重叠。 同坐标流程：partition-canvas/install-canvas-layers/reconstruction/review-reconstruction，字段见workflow-instructions；查看spine_image view=reconstruction-image。自动角色处理：generate/generate-view/generate-parts/generation-plan/apply-ai/apply-part-sheet/apply-part-layout/apply-source-layout/guide/review/build/foot-layers/restore/reset-part/refresh-jobs。修改带当前 revision。prepare-assembly 的 connections 可执行连接端局部宽度拟合、同衣料调色或 shared-pixels 母版纹理补接；字段见 skills/spine-animation/references/assembly-execution.md，不能把同坐标配准当作纹理接缝修复。1.9.4新asset-sheet任务默认 generate-parts sheets=[turnaround,character-assets] 同批生成三视图参考板与完整角色素材图，分别计费共2张，共用基础图额度，另有8张补图／修订额度。先spine_review_turnaround phase=reference再裁片，assembly比对通过才build。turnaround仅作参考，不可导入atlas。可单独sheets=[turnaround]。部件图，requires_reviewed_layout=true；generation-plan（mode=complete-parts）和 generate-parts 的 body_layout 可选 combined/separate，asset-sheet 工具默认补 combined，可显式 separate，网页、直接 API 与后台默认 combined，显式 separate 保留兼容。combined 上排 head、整衣装 torso、back 或空、空，下排四完整臂腿链，共6或7块；看实际候选图后用 apply-part-layout 接入，不能 apply-part-sheet。原图已是素材图时，用 apply-source-layout data={revision,parts} 显式裁片接入，不生成。apply-part-layout data={revision,job_id,parts}：box 为实际像素裁片；完整臂/腿用 l/r-upper-arm/leg、chain=arm/leg、四关节 pins；整衣装可设 group=costume，仅 name=torso、不能设 chain，必须显式 start_pin=hip/end_pin=neck；覆盖 torso+pelvis，建连续 hip/torso weighted mesh，不能同时接入独立 pelvis；canvas+pieces 支持组合已有裁片。每个 part 可传 crop_polygon 和 exclude_polygons 为裁切/组合后的sprite内归一化多边形，先仅裁除外部像素再旋转，不补画；rotation 数值[-180,180]度，正数逆时针；先裁切／组合，再旋转并 expand 画布。start_pin/end_pin/pins 全部是最终旋转后 sprite 内0–1坐标。接入后实际查看 assembly，再以 parts_reviewed=true 复查当前 revision 才能 build。auto 可按缺项选 sheets=[body,arms,legs] 或显式 limbs 四完整肢体；limbs 也须 reviewed layout，且不与 arms/legs 重复。include_back=false 无背包布局，instructions_by_sheet 每组<=400字，勿混共享 instructions。缺连续衣料可在原预算内 generate-parts sheets=[costume]，只补完整主衣装，必须 reviewed layout；这不是更改审核拒绝后的重试。导入back前guide须含实际back定位点。平台自动发布参考图；生成和 build 使用稳定 nonce。guide 须完整 landmarks/settings。',obj({'id':STRING,'action':STRING,'data':DATA},('id','action','data'))),
    tool('spine_create_rig','由 project 源副本建立可编辑 rig。',obj({'project_id':STRING,'revision':INT},('project_id','revision'))),
    tool('spine_edit_rig','occlusion-overlay 拆分已观察的前景遮挡：part/source_slot/subject_slot/name/polygon/reason；polygon 为源贴图归一坐标，继承源网格权重，顺序源层→主体→前景；含源附件动画或动态 drawOrder 时拒绝。变更后重新动态验收。独立副本上事务性调整骨骼、贴图、替换骨架和动作。局部texture-repair/material-finish先查看当前spine_closeup；material-finish提供part/reason/operations，操作含归一polygon和kind=erase或edge-cleanup(radius .1–2)或opacity(top/bottom/start/end/reference_note)，仅改局部alpha保留RGB，透明区域必须有原图依据。part=目标ID,reason必填，white_regions是最多8个sprite内归一多边形，只清其中中性白底；copies=[{source_part或source_material,box:实际源像素,dest:目标像素,crop_polygon:可选裁片内归一轮廓}]复用当前rig的实际原像素。最多改变25%贴图；不补画未知区域，不全局删白。bone 必须给 bone=现有骨骼名，name 仅用于重命名。animation 提供 name 和完整 animation JSON；copy-animation 仅支持同版本骨骼局部曲线，不支持约束或 deform 重定向。参数错误可修正重试，未扣生图额度。',obj({'source_rig_id':STRING,'revision':INT,'nonce':NONCE,'operations':{'type':'array','items':OPERATION}},('source_rig_id','revision','nonce','operations'))),
    tool('spine_reskin','已有纹理换皮，action=plan/submit/status。data=project_id/style/mode/region_ids/reference_urls/revision/nonce；status 仅需 nonce。reference 必须各部件公共 HTTPS 图，material 仅表面材质。submit 会占用预算。',obj({'action':{'type':'string','enum':['plan','submit','status']},'data':DATA},('action','data'))),
    tool('spine_validate','校验 rig 或 project 的结构、贴图引用与打包。完成后的最终交付另外执行官方 runtime 校验。',obj(ENTITY,('kind','id')),True),
    tool('spine_closeup','1.9.9：长衣装腿部行走默认32帧，其它默认8帧；frame_count可选4–32。isolated=true仅渲染指定链，用于区分网格畸形和衣裙遮挡，不记验收，最终须查看组合图。按真实anchor骨骼渲染8帧局部图，默认head。检查白底、水平硬切、肤色/发丝连接、遮挡和alpha；真实图面为准。1.9.4新人物每个动作检查head、torso、l/r-upper-arm、l/r-upper-leg和__hair__，按真实存在骨骼选择。每个anchor单独spine_review_closeup(anchor=相同名称)。',obj({**ENTITY,'animation':STRING,'anchor':STRING,'frame_count':{'type':'integer','minimum':4,'maximum':32},'isolated':{'type':'boolean','default':False}},('kind','id','animation')),True),
    tool('spine_review_closeup','assembly-connections-v3另需rear_collar_behind_neck/front_collar_in_front/runtime_pose_consistent；颈口行走32帧、待机8帧。保留head颈口、__hip__腰胯、__back__配件检查及connection_checks，具体字段见workflow-instructions。limb-orientation-v1任务另传orientation_checks，逐侧映射和掌/鞋方向；字段见平台workflow-instructions。garment-occlusion-v1长衣装腿部须garment_checks逐项布尔：hip_coverage腿根覆盖、thigh_contour轮廓、knee_readable符合参考的膝部遮挡、garment_clearance衣料无穿插。行走32帧、待机8帧，ready须全部通过。anchor默认head，可指定torso、l/r-upper-arm、l/r-upper-leg、__hair__；看过当前对应帧数的局部图后记录ready/needs_repair/unsupported与具体原因。贴图、骨骼或动作变化使旧记录失效。asset-integrity-v1 必传 material_checks：torso和腿用connection_overlap/garment_continuity/layer_separation/silhouette_valid/edge_clean；head和臂去掉garment_continuity；__hair__用silhouette_valid/edge_clean/occlusion_valid。ready须全部true；没有相关衣料时该项表示无相关缺陷并在reason说明。检查整段动画而非单帧。',obj({**ENTITY,'animation':STRING,'anchor':STRING,'verdict':{'type':'string','enum':['ready','needs_repair','unsupported']},'garment_checks':obj({k:{'type':'boolean'} for k in ('hip_coverage','thigh_contour','knee_readable','garment_clearance')},('hip_coverage','thigh_contour','knee_readable','garment_clearance')),'connection_checks':obj({k:{'type':'boolean'} for k in ('neck_pin_aligned','collar_occlusion','waist_continuity','hip_overlap','root_stable','worn_camera','strap_face_inward','attachment_stable','texture_continuity','upper_edge_covered','complete_silhouette','occluded_artwork_complete','rear_collar_behind_neck','front_collar_in_front','runtime_pose_consistent')}),'orientation_checks':ORIENTATION_CHECKS,'material_checks':MATERIAL_CHECKS,'reason':STRING},('kind','id','animation','verdict','reason'))),
    tool('spine_motion','返回官方 runtime 动作联系图与真实运动、父骨相对运动、循环诊断。frame_count 默认8、范围4–32；明确行走需求的 walk/run 交付至少16帧，最终步态建议32帧。静态pose可合法，不把加载成功当作自然步态通过。交付前查看每个动作；无动作使用 __setup__。',obj({**ENTITY,'animation':STRING,'frame_count':FRAMES},('kind','id')),True),
    tool('spine_gait','32帧测量同侧手臂和腿运动的相关性、相位偏移和首尾衔接；pairs 必须按真实蒙皮对应关系填写，anchor 为身体参考骨骼。数值仅辅助视觉检查。',obj({**ENTITY,**GAIT},('kind','id','animation','anchor','pairs')),True),
    tool('spine_contact','对当前任务副本进行只读真实 alpha 鞋底诊断：接地漂移、统一场景速度下滑步、循环、归一化速度跳变。已知 sole-contact-v1 模板自动映射，任意骨架缺显式条件返回 not_assessed。需结合32帧看图；不认证自然步态、侧视或遮挡。',obj({**ENTITY,**CONTACT},('kind','id')),True),
    tool('spine_wait','等待已有亿创结果，每次 1–20 秒；随后 inspect 或 reskin status 查询。',obj({'seconds':INT}),True),
]

review_fields=next(t for t in WORKER_TOOLS if t['name']=='spine_review_closeup')['inputSchema']['properties']
WORKER_TOOLS.append(tool('spine_review_regions','看过当前资源的对应区域图后，一次记录多区域验收。逐项填写真实material_checks/garment_checks和原因；任一无效则整批不写入。准备复检包不代表已经查看。',obj({**ENTITY,'reviews':{'type':'array','minItems':1,'maxItems':64,'items':obj({k:v for k,v in review_fields.items() if k not in ('kind','id')},('animation','verdict','reason'))}},('kind','id','reviews'))))

for tools in (PUBLIC_TOOLS,WORKER_TOOLS):
    tools.append(tool('spine_review_bundle','平台一次准备最终资源的所有动作全身/七区域放大图、接地和alpha残点候选。默认返回清单，按panel读取缓存原图；支持animations子集。准备不记视觉通过，实际看图后仍用spine_review_closeup记录结论。资源或渲染器变化自动重新计算，不提交生图。',obj({'kind':{'type':'string','enum':['rig','project']},'id':STRING,'animations':{'type':'array','minItems':1,'maxItems':8,'items':STRING},'panel':STRING},('kind','id')),True))

next(t for t in PUBLIC_TOOLS if t['name']=='spine_contact')['inputSchema']['oneOf']=[
    {'required':['task_id'],'not':{'anyOf':[{'required':['kind']},{'required':['id']}]}},
    {'required':['kind','id'],'not':{'anyOf':[{'required':['task_id']},{'required':['artifact_index']}]}}
]
next(t for t in PUBLIC_TOOLS if t['name']=='spine_closeup')['inputSchema']['oneOf']=next(t for t in PUBLIC_TOOLS if t['name']=='spine_contact')['inputSchema']['oneOf']
next(t for t in WORKER_TOOLS if t['name']=='spine_contact')['inputSchema']['properties']['kind']={'type':'string','enum':['rig','project']}


class Bridge:
    def __init__(self,base,token='',task=None):
        from urllib.parse import urlparse
        p=urlparse(base)
        if p.scheme not in ('http','https') or not p.hostname or p.username or p.password or p.query or p.fragment:raise ValueError('base 需为服务 HTTP(S) 地址')
        self.base=base.rstrip('/');self.token=token;self.task=task

    def api(self,path,data=None):
        headers={'X-Spine-Local':'1','Content-Type':'application/json'}
        if self.token:headers['Authorization']='Bearer '+self.token
        req=urllib.request.Request(self.base+path,data=None if data is None else json.dumps(data,ensure_ascii=False,allow_nan=False).encode(),headers=headers)
        try:
            with urllib.request.urlopen(req,timeout=120) as response:return json.load(response)
        except urllib.error.HTTPError as e:
            try:message=json.load(e).get('error','Agent 请求失败')
            except Exception:message='Agent 请求失败'
            raise ValueError(message) from None
        except urllib.error.URLError:raise ValueError('无法连接 Spine Agent 服务') from None

    def submission(self,args):
        args=dict(args);image=args.pop('image_path',None)
        if image is not None:
            if args.get('sources'):raise ValueError('图片与已有 sources 只能选择一种')
            path=Path(image).expanduser()
            if not path.is_absolute() or not path.is_file():raise ValueError('image_path 须为调用电脑上的现有图片绝对路径')
            if path.stat().st_size>12*1024*1024:raise ValueError('上传图片最多 12 MB')
            args['image']=base64.b64encode(path.read_bytes()).decode();args.setdefault('image_preparation','asset-sheet');args.setdefault('max_generations',0 if args['image_preparation']=='preserve' else 8)
        return args

    def call(self,name,args):
        import re
        if name=='spine_reference':
            if args.get('kind')!='character' or not re.fullmatch('[a-f0-9]{32}',args.get('id','')):
                raise ValueError('参考图关联须指定有效 character id')
            if args.get('action') not in ('ensure','prepare','register'):raise ValueError('参考图操作无效')
            if args.get('action')=='ensure' and type(args.get('revision'))!=int:raise ValueError('自动参考图须携带当前 revision')
            if self.task:
                result=self.api('/api/automation/agent/tasks/'+self.task+'/tools',{'name':name,'arguments':args})
            elif args.get('action')=='ensure':
                result=self.api('/api/characters/'+args['id']+'/reference',{'action':'ensure','revision':args['revision']})
            elif args.get('action')=='register':
                result=self.api('/api/characters/'+args['id']+'/reference',{'url':args.get('url'),'revision':args.get('revision')})
            elif args.get('action')=='prepare':result=self.api('/api/characters/'+args['id']+'/reference')
            else:raise ValueError('参考图操作无效')
            if args.get('action')=='prepare' and not result.get('cached'):
                encoded=result.pop('image_data',None)
                if encoded:blob=base64.b64decode(encoded,validate=True)
                else:
                    req=urllib.request.Request(self.base+result['image_url'],headers={'Authorization':'Bearer '+self.token} if self.token else {})
                    with urllib.request.urlopen(req,timeout=60) as response:blob=response.read(12*1024*1024+1)
                if len(blob)>12*1024*1024 or hashlib.sha256(blob).hexdigest()!=result['image_id']:
                    raise ValueError('准备上传的原图已变化或超过 12 MB，请重新 prepare')
                import tempfile
                folder=Path(tempfile.gettempdir())/'spine-matrix-references';folder.mkdir(exist_ok=True)
                target=folder/(result['image_id']+'.png');temp=target.with_suffix('.tmp')
                temp.write_bytes(blob);temp.replace(target);result['image_path']=str(target.resolve())
            return result
        if self.task:return self.api('/api/automation/agent/tasks/'+self.task+'/tools',{'name':name,'arguments':args})
        if name=='spine_review_bundle':return self.api('/api/automation/agent/review-bundle',args)
        if name=='spine_sources':return self.api('/api/automation/agent/sources')
        if name=='spine_submit':return self.api('/api/automation/agent/tasks',self.submission(args))
        if name=='spine_batch':
            requests=args.get('requests')
            if not isinstance(requests,list) or not 1<=len(requests)<=32:raise ValueError('requests 须为 1–32 个任务')
            return self.api('/api/automation/agent/batch',{'requests':[self.submission(r) for r in requests]})
        if name=='spine_contact':
            task_id=args.get('task_id');direct='kind' in args or 'id' in args
            if direct:
                if task_id is not None or 'artifact_index' in args:raise ValueError('task_id 与 kind/id 只能选择一种')
                if args.get('kind') not in ('rig','project') or not re.fullmatch('[a-f0-9]{32}',args.get('id','')):raise ValueError('须指定有效的 rig/project id')
                return self.api('/api/automation/agent/contact',args)
            if not isinstance(task_id,str) or not re.fullmatch('[a-f0-9-]{16,64}',task_id):raise ValueError('须指定 task_id 或 kind/id')
            return self.api('/api/automation/agent/tasks/'+task_id+'/result-view',{'view':'contact',**{k:v for k,v in args.items() if k!='task_id'}})
        if name=='spine_closeup':
            if args.get('task_id'):
                if args.get('kind') or args.get('id'):raise ValueError('task_id与kind/id二选一')
                return self.api('/api/automation/agent/tasks/'+args['task_id']+'/result-view',{'view':'closeup',**{k:v for k,v in args.items() if k!='task_id'}})
            if 'artifact_index' in args:raise ValueError('artifact_index仅用于task_id')
            if args.get('kind') not in ('rig','project') or not re.fullmatch('[a-f0-9]{32}',args.get('id','')):raise ValueError('请提供task_id或有效kind/id')
            return self.api('/api/automation/agent/closeup',args)
        id=args.get('task_id','')
        if not re.fullmatch('[a-f0-9-]{16,64}',id):raise ValueError('task_id 无效')
        if name=='spine_status':return self.api('/api/automation/agent/tasks/'+id)
        if name=='spine_retry_stage':return self.api('/api/automation/agent/tasks/'+id+'/retry-stage',{'stage':args['stage']})
        if name=='spine_resume':return self.api('/api/automation/agent/tasks/'+id+'/resume',{'nonce':args['nonce']})
        if name=='spine_cancel':return self.api('/api/automation/agent/tasks/'+id+'/cancel',{})
        if name=='spine_refine':
            payload=dict(args)
            if 'part_images' in args:
                items=args['part_images']
                if not isinstance(items,list) or len(items)>8:raise ValueError('补全部件最多 8 张')
                prepared=[];total=0
                for item in items:
                    if not isinstance(item,dict) or not isinstance(item.get('image_path'),str) or not isinstance(item.get('name'),str):raise ValueError('补全部件须包含 name 和 image_path')
                    path=Path(item['image_path']).expanduser()
                    if not path.is_absolute() or not path.is_file():raise ValueError('补全部件 image_path 须为现有图片绝对路径')
                    if path.stat().st_size>6*1024*1024:raise ValueError('单张补全部件最多 6 MB')
                    blob=path.read_bytes();total+=len(blob)
                    if total>12*1024*1024:raise ValueError('补全部件总计最多 12 MB')
                    prepared.append({'name':item['name'],'image':base64.b64encode(blob).decode()})
                payload['part_images']=prepared
            return self.api('/api/automation/agent/refine',payload)
        if name in ('spine_result','spine_preview','spine_gait'):
            return self.api('/api/automation/agent/tasks/'+id+'/result-view',{'view':{'spine_result':'inspect','spine_preview':'motion','spine_gait':'gait'}[name],**{k:v for k,v in args.items() if k!='task_id'}})
        if name=='spine_download':
            result=self.api('/api/automation/agent/tasks/'+id)
            if result['state']!='completed':raise ValueError('任务尚未完成')
            output=Path(args['output_path']).expanduser()
            if not output.is_absolute():raise ValueError('output_path 须为绝对路径')
            req=urllib.request.Request(self.base+'/api/automation/agent/tasks/'+id+'/download',headers={'Authorization':'Bearer '+self.token} if self.token else {})
            with urllib.request.urlopen(req,timeout=90) as r:content=r.read()
            if hashlib.sha256(content).hexdigest()!=result['result_sha256']:raise ValueError('下载摘要不一致')
            if output.exists() and hashlib.sha256(output.read_bytes()).hexdigest()!=result['result_sha256']:raise ValueError('目标文件已存在，请选择新文件名')
            output.parent.mkdir(parents=True,exist_ok=True);tmp=output.with_suffix(output.suffix+'.tmp');tmp.write_bytes(content);tmp.replace(output)
            return {'task_id':id,'output_file':str(output),'sha256':result['result_sha256'],'preview_urls':[self.base+a['preview_url'] for a in result['artifacts']]}
        raise ValueError('未知工具')

    def handle(self,request):
        method=request.get('method');params=request.get('params',{})
        if method=='initialize':
            return {'protocolVersion':'2024-11-05','capabilities':{'tools':{}},'serverInfo':{'name':'spine-agent','version':'1.11.2'},'instructions':'Use spine_submit for new work, spine_status to follow it, and spine_refine for the latest completed task. Resume interrupted work with spine_resume rather than duplicate submissions. Preserve originals and stable nonces. Public callers provide images and requirements; the platform handles Matrix credentials and HTTPS reference publication. New images use Matrix GPT Image 2.5 Flare only. Default asset-sheet flow prepares a suitable side-walk view, separate turnaround reference and full character assets, then reviewed crops and assembly. The first turnaround and character-assets each have one baseline slot; eight additional images cover preparation/refinement, and zero forbids all generation. Read and follow spine-animation skill when available; worker mode starts with spine_context and the platform workflow instructions. For final verification, spine_review_bundle prepares all full-body, closeup, contact and alpha evidence on the server. Read its panels; preparation is not visual acceptance. Workers can batch actual observations through spine_review_regions. Review exact final resources: idle8, walk32, head/torso/both arms/both legs/hair, plus actual sole contact. Numeric checks and isolated diagnostics do not certify artwork, occlusion or natural gait. Repair scoped confirmed defects without altering protected pixels or other animations. A changed resource invalidates prior reviews. Content policy rejection stops generation; unknown submissions are queried, never duplicated. Only a visually inspected valid image with a quality defect can receive one targeted revision within the budget. Use result.html for simple previews and spine_download for ZIP. Output is JSON/atlas/PNG, not native .spine. Do not present failed or unreviewed artifacts as completed.'}
        if method=='ping':return {}
        if method=='tools/list':return {'tools':WORKER_TOOLS if self.task else PUBLIC_TOOLS}
        if method=='tools/call':
            name=params['name'];args=params.get('arguments',{})
            tools=WORKER_TOOLS if self.task else PUBLIC_TOOLS
            spec=next((t for t in tools if t['name']==name),None)
            if not spec:return {'isError':True,'content':[{'type':'text','text':'未知工具'}]}
            try:
                if not isinstance(args,dict) or set(args)-set(spec['inputSchema']['properties']) or any(k not in args for k in spec['inputSchema']['required']):raise ValueError('工具参数缺失或包含未知字段')
                result=self.call(name,args)
                if isinstance(result,dict) and 'content' in result:return result
                return {'content':[{'type':'text','text':json.dumps(result,ensure_ascii=False)}]}
            except (ValueError,KeyError,TypeError,OSError) as e:
                return {'isError':True,'content':[{'type':'text','text':str(e)}]}
        raise ValueError('Method not found')


def main():
    for stream in (sys.stdin,sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8')
    config=settings()
    p=argparse.ArgumentParser();p.add_argument('--base',default=os.getenv('SPINE_AGENT_BASE',config.get('base','http://127.0.0.1:8780')));p.add_argument('--task');p.add_argument('--token-file',type=Path,default=Path(config['token_file']) if config.get('token_file') and not os.getenv('SPINE_AGENT_TOKEN') else None);args=p.parse_args()
    token=args.token_file.read_text(encoding='utf-8-sig').strip() if args.token_file else os.getenv('SPINE_AGENT_TOKEN','')
    bridge=Bridge(args.base,token,args.task)
    for line in sys.stdin:
        request=None
        try:
            request=json.loads(line)
            if not isinstance(request,dict):raise ValueError('Invalid request')
            if 'id' not in request:continue
            result=bridge.handle(request);response={'jsonrpc':'2.0','id':request['id'],'result':result}
        except Exception:
            response={'jsonrpc':'2.0','id':request.get('id') if isinstance(request,dict) else None,'error':{'code':-32600,'message':'Invalid request or unsupported method'}}
        print(json.dumps(response,ensure_ascii=False),flush=True)


if __name__=='__main__':
    try:main()
    except (ValueError,OSError) as e:print(str(e),file=sys.stderr);sys.exit(1)
