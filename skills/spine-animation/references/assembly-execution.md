# 默认装配执行路径

完整部件导入 apply-part-layout / apply-source-layout 后，平台默认执行 assembly-execution-v2：按已审查关节点配准 → 保存整数 canvas_offset 与 canvas_pins → 保留独立完整 alpha 和隐藏重叠 → 供试拼、摆开和骨架导出共享。格子图仍只是裁片来源。导入尚不齐全时继续补齐，不对可见碎片冒称补全。修改 guide 会从保存的原始完整裁片重新配准，不在反复缩放后的图上累计变换。

需要补全时优先找本任务已有完整裁片，调用 spine_character action=prepare-assembly；后台来源必须是任务拥有的 character，不能读取任意其他用户角色。管理员接口为 POST /api/characters/{id}/prepare-assembly，X-Spine-Local: 1。

参数：
- revision：当前版本。
- replacements（可选）：[{name,character_id,part,source_pair:[[x,y],[x,y]],target_pair:[[x,y],[x,y]],pins:{...}}]。source_pair 和 pins 是源 part-images PNG 的像素坐标，target_pair 是母版像素坐标。连续臂 pins 为 shoulder/elbow/wrist/hand；连续腿为 hip/knee/ankle/toe。平台调用真实源图执行配准、保存来源摘要和变换，保留补全 alpha；不会把轮廓剪回原图可见区域。不得把不完整来源标成完整。
- overlays（可选）：[{name,source,subject,source_sha256,polygon,reason}]。从现有 source 贴图的归一化 polygon 提取前景，source 在 subject 后，前景紧邻 subject 前；source_sha256 是当前实际装配 PNG 摘要，防止套用别的素材轮廓。例如后领 → 头颈 → 前领。平台从同一数据生成静态预览和导出附件，复制源部件的骨骼及权重。不得用固定人物坐标。传 [] 可清除遮挡方案。

导入还可带 assembly_plan={replacements,connections,overlays}，在同一次平台操作执行后续步骤，返回最终 revision。修复必须落实为真实像素/锚点/前后层级操作，不能只写检查规则后再次停在原缺陷。

完成后查看 prebind-parts、prebind-assembly、prebind-spread，再提交当前 digest 的三项真实结论；随后 build。补全部件、前领轮廓、位置或源图变化会使旧验收失效。finish-parts 改动前景来源时会移除旧遮挡轮廓，应重新标注；只改不相关部件时保留轮廓。完整且固定穿戴的背包可与主衣装合并，按模板保留身后包体和身前肩带；需要独立摆动的背包必须另层。

平台负责确定性执行，视觉判断仍负责选对素材和关节。缺少隐藏图面时应复用或生成实际补图，不能宣称同坐标转换自动画出了缺失部分。全程记录实际素材来源、未通过中间图及最终版本，保留上一版合格成果。

动态摆腿暴露的重复裤腰/口袋端片须回到素材修复：先按实际范围修外露端角，保留髋部中心重叠；若多次修整仍有断面或半透明残片，复用已验证的完整同部件再配准，不继续侵蚀。明确列出本次素材哪些保留、哪些被替换，旧动画不可冒充新建动作。

## v2：修复不回退的默认执行

对已校准的完整来源，优先使用 replacements=[{name,character_id,part}]，同时省略 source_pair、target_pair 和 pins。平台从源部件关节与目标母版关节推导配准，保留隐藏像素；不靠格位猜坐标。来源仍须属于任务且需视觉确认适合相同角色、相机和侧别。只有关节本身需重标时才显式提供完整锚点和 pins。

局部重新导入和补全只重置被替换部件，保留其余像素、校准与前景层。前景来源变了才使该轮廓失效，并返回 assembly_invalidations；重新标注其真实 source_sha256，不整套清空已修好的领口。导入含 assembly_plan 以及 prepare-assembly 均整批提交一次 revision；失败保持原版本，修正参数后使用原 revision 续作。

背包合并在完整衣装时传 back_accessory_part="torso"；独立背包传 "back"。该声明随导出保存，平台复检包自动覆盖待机8帧、行走32帧的背包组合区域。不能因为没有独立 back slot 而漏检；无背包角色不填，移除声明用 null。复用不是美术验收，仍查看三项素材图和最终动画。

## 验收图复用与遮挡顺序维护

部件、试拼、摆开三个接口默认共用同一份持久证据。连续读取和仅填写验收结论不重复渲染；贴图、关节、校准、遮挡、背景设置或渲染代码变化时自动重建。无需为了刷新图片重新导入部件或重新生图。缓存损坏只重算证据，不消耗生图额度；缓存命中不代表美术通过。

保存关节点保留完整部件的实际绘制顺序。多个前景层按所有 source → subject → foreground 关系统一排序；冲突方案整批拒绝并保留原版本。遇到冲突应修正真实前后关系，不能靠反复提交不同顺序掩盖。

## 默认生成任务自动执行

网页、直接生成 API 和 worker 省略 sheets 时统一为 turnaround + character-assets，后者默认 combined；显式指定旧分组仍可用。新导入完整素材会自动配准、求解前后遮挡并生成三项 prebind 证据，不能将“等助手调用 prepare”作为默认执行条件。

任务启动时平台先检查任务副本：已有完整素材就直接准备，缺失则记录缺项；不跨用户找素材、不把残片标成补全。各次导入、位置修整和验收后更新 material_preparation，网页原任务直接展示三张图和历史版本，失败状态也不隐藏素材。状态 awaiting_visual_review 只代表证据就绪，仍须实际查看后提交验收。无需再手写独立下载页面才能让用户看到阶段结果。

## 素材到动画的默认续作

已验收完整素材作为动画来源时，对话提交也默认启用下述自动绑定；完成单套素材交付后，网页提供“用此素材生成待机和行走”。平台验证当前素材与已交付摘要一致，按同一制作记录建立独立副本，预算固定为零生图；重复请求沿用 nonce。视角、关节和组装实际检查完成后，spine_assess_image 返回 automatic_animation，平台已自动绑定并发布草稿，后续直接验收其中的 rig_id，不再重复 build。未确认侧向适配时不会仅凭素材完整就生成侧走。新内容摘要允许在原修复额度内重新绑定。

复用完整素材或已生成骨架的动画任务，即使 preserve/max_generations=0，也启用完整局部、朝向、材质与连接检查；省略生图不省略动态验收。旧任务按原合同保留，已交付成果不重写。

## 连接区拟合与同源裤料补接

同坐标只对齐关节，无法保证相邻裁片的开口宽度、裤缝与纹理连续。先看实际连接区，判断是几何不匹配、色差还是裤缝/褶皱错位；优先复用同一衣料母版，不能把整腿等比缩小、缩短步幅或透明淡化当作修复。

平台 `prepare-assembly` 和导入的 `assembly_plan` 支持 `connections`（1–4项），真实修改像素，整批成功才提交 revision。每项：

```json
{
  "part": "l-upper-leg", "donor": "torso",
  "source_sha256": "当前腿片摘要", "donor_sha256": "当前衣装摘要",
  "join_y": 连接截面高度, "release_y": 膝或肘之前的过渡终点,
  "target_span": [开口左边界, 开口右边界],
  "reason": "实际观察与测量依据"
}
```

上述数值均为当前母版像素坐标，必须逐角色测量。只支持已校准、向下延伸的完整臂腿链；根关节不移动，过渡终点以下像素与原图完全一致。目标开口必须包含原关节；宽度变化超过安全范围会拒绝。每项保存前后摘要和修复记录；同批 donor 不可同时修改。

可选 `texture_join`：

- **仅色差**：`{mode:"color",donor_y,target_y,release_y,material_note}`。从实色同材质截面估计稳健色偏，渐变回原颜色；保留原褶皱、alpha，不处理错位裤缝。
- **共享真实纹理**：`{mode:"shared-pixels",start_y,solid_until_y,release_y,material_note}`。将母版同坐标实际 RGB 复制到连接段；在 solid_until_y 以下逐渐回到原腿片纹理。仅改 RGB，不淡化 alpha；母版须完整覆盖请求的可见区域，内部透明缺口会拒绝；仅允许最多3列且不超过5%的外缘抗锯齿区域保留原色。不能跨衣料、肤色或配件取纹理，不能宣称渐变自动保持所有图案语义。

若当前衣装已裁短，可选 `donor_layout={job_id,sha256,layout}` 复用**本角色**已有完整衣装候选。sha256 为 jobs PNG 的摘要；layout 与 apply-part-layout 单个完整衣装规格相同，须 name=torso、group=costume、真实 box、start_pin/end_pin。平台从原候选提取并按同一 hip/neck 配准，作为临时纹理来源，不替换当前躯干或改变其前景遮挡。无网络下载、无生图调用、不可传任意文件路径。

修改后旧验收失效，平台立即重建完整部件、试拼和摆开证据。实际检查裤腰横线、口袋重复、褶皱续接、胯宽与摆开漏缝；几何数值合格不代表美术通过。已有动画只需替换同尺寸目标贴图，骨架/动作逐字段保留；再检查待机8帧、行走32帧的连接局部。缺少真实同源图面则补图，禁止把色差降低包装成纹理连续。

## 服装模板执行

`spine_character action=garment-template`，data 为 `{revision, template:{type,openings,shared_sources,missing_regions}}`。省略 template 只返回当前角色与 garment_plan；显式保存会使旧素材验收失效。可在高级角色页“部件 → 部件生图要求”选择类型。不要把生成图格位当母版坐标。

| type | 图面与归属 |
|---|---|
| trousers | 上衣＋长裤。腰线、口袋归主体；腰胯、裆部和大腿根共享连续裤装母版。|
| shorts | 上衣＋短裤。腰胯与短裤管同源；裸腿皮肤、裤口与鞋分材质。|
| skirt | 连续腰裙与内衬归主体，腿位于裙下；透明外纱和实色内衬分别保留。|
| split-robe | 外袍前后片连续，内穿裤腿独立，保留开衩与后侧覆盖。|
| armor | 胸腰甲、肩甲、腿甲按搭接分层；缺口补内衬，不能拉伸硬甲。|

平台由当前原图与关节点计算 `shoulder_span/hip_span`，这是母版投影的关节中心间距，不是人体解剖宽度。每个角色每侧的实际袖口/裤口仍须看图测量。模板的默认插接深度仅是按实测开口宽度得到的初值，可以明确指定；不是所有角色统一固定像素。母版图片或肩髋定位改变会拒绝旧模板，须重新确认测量。

`openings` 按 l/r-upper-arm、l/r-upper-leg 命名，每项 `{span:[左边界,右边界],join_y,release_y,insert_depth,evidence,fit_required}`。全部为母版像素；span 覆盖原关节，release_y 在膝肘之前，插接深度不得超过开口宽度的一半。evidence 记录实图依据。已匹配的袖根可设 fit_required=false，保留规格但不重复改形。禁止把近侧宽度复制给透视不同的远侧。

裤装 `shared_sources` 每项为 `{donor_layout,texture_join}`，用上一节已支持的真实本角色候选与 shared-pixels 范围。配置时就验证候选摘要；未选同源图面会返回精确范围的 completion_requests，而不会偷偷调色。裙装/外袍/甲片不得沿用裤装的外衣纹理补腿。

`prepare-assembly data={revision,garment_template:上述配置}` 可在一次事务中保存并执行。已保存模板在完整部件导入后默认执行；手动重新准备用 `apply_template:true`。平台检查关节中心至插接深度范围有真实不透明衣料，然后调用局部宽度拟合与共享纹理补接，保留膝肘以下像素、动作和无关素材。相同模板＋相同结果摘要不再次形变；新导入目标部件会重新适配。失败整批回滚。显式手动修复不被模板暗中覆盖。

`missing_regions=[{part,box:[x0,y0,x1,y1],reason}]` 只登记实际确认缺图的母版范围。生成调用 `generate-parts completion_only=true` 后，平台只选择所需 costume/limbs 组，将范围和保留要求写入实际 provider 提示词，沿用原额度与 nonce；没有缺失区则拒绝重复生图。当前服务接口是参考图指导的部件生成，不承诺所有供应商具有硬遮罩 inpaint 能力；补图完成仍要显式裁片导入和检验。只缺来源选择时先检查已有候选，不应把 completion_requests 自动当作付费补图授权。

生成预览、提交提示词和任务保存的是同一份模板。每个任务副本沿用自身模板，不跨用户找来源；未经实图测量的模板显示 needs_opening_measurements，不能伪称已自动校准。完成后仍验收完整部件、试拼、摆开与动态连接。

## 后生成素材的自动动画状态转换

单角色 animation 任务根据当前完整素材验收、视角／六组连接／材质／逐侧朝向确认，动态启用自动绑定。任务创建时尚无图、无部件或 material_animation=false/缺省，不再阻断后续续作。显式 `_material_animation=false` 会保存独立的禁用策略；materials-only 与多来源任务不自动绑定。

`review-prebind`、`spine_assess_image` 和需要时的三视图 assembly 比对均触发同一状态转换。可以先确认视角再提交素材验收，也可以相反；填写验收导致的元数据 revision 增加不使未变图面失效，贴图、关节、比例、遮挡或动作设置改变则按内容摘要使旧证据失效。仍须真实读取当前 assembly，不能只填写 ready。

任务 `animation_transitions` 保存 awaiting_materials → awaiting_view/awaiting_turnaround → binding → draft_pending → review_pending/review_preparing → review_ready；错误保存具体阶段与原因。`automatic_animation` 返回真实 rig_id。相同素材使用稳定版本号，重复事件和草稿交付重试不新建骨架、不重复消耗绑定修复次数。网页的直接素材提交通过持久事件交给相同协调器；对话工具直接触发同一实现，不能各自维护另一套绑定条件。

动态复检有独立平台队列，不阻塞新任务事件处理，不需要 Codex 再发起一次准备命令。服务重启后可继续已有草稿的复检；临时失败有限重试，保留草稿和原失败记录，不重新生图或启动模型。模型工具保存旧任务快照不能覆盖后台已经更新的阶段。取消任务不继续交付；资源改变不发布为当前复检结果。网页即使模型回合已结束，也继续轮询尚未完成的平台复检，并显示其独立状态。

review_ready 只表示复检包已准备，仍为 not_reviewed。使用 spine_review_bundle 读取对应图片，继续按全部动作和局部验收；不得把准备完成写成动画美术合格或自动 finish。

## 实际骨骼压力测试

静态 prebind 的三项仍须通过，但不是正式绑定的最后门禁。新单角色 animation 任务记录 runtime-assembly-stress-v1；网页直接 build 同样要求当前压力测试通过。历史已交付 rig 不回写或下架。

平台使用与正式导出相同的 make_skeleton、连续链网格权重、前领／肩带层级和鞋口覆盖，构造内存诊断 ZIP，由官方 Spine runtime 实际求骨骼世界坐标与加权顶点，再渲染压力帧。诊断不会创建正式 rig／version，不扣生图或正式绑定修复次数。

三组每段2秒，8帧均包含正反极值：
- stress-arms：双向摆臂±35°，检查袖根与肩带遮挡。
- stress-legs：左右大腿反相±22°，检查腿根尖角、凹口与裤料覆盖。
- stress-hip：髋部±10°、躯干反向补偿并交替摆腿，检查腰胯相对扭动。这里是二维画面内旋转，不是三维转身。

每组输出全身、__hip__、__straps__，共9张图；局部带深浅底对照，肩带区域按实际肩骨与躯干长度计算，不依赖独立back插槽。没有背包的角色同样检查肩袖覆盖。

自动流程为静态＋视角通过 → stress_pending → awaiting_stress_review → 正式绑定。压力不通过则 stress_needs_repair。使用 `spine_character action=stress-test data={}` 返回清单（准备完成只表示not_reviewed）；使用 `spine_image kind=character,id,view=stress-image,part=真实panel.key` 逐张读取。提交 `review-stress data={revision,digest,checks:{arm_extremes,alternating_legs,hip_rotation,hip_contour,strap_integrity,occlusion_stable},verdict:"ready"或"needs_repair",reason}`，checks均为布尔。后台未看全图、旧摘要、假true或缺项均不得通过；诊断图已准备不能冒充验收。

材质、关节、设置、遮挡或渲染代码变化均使旧证据失效，失败和修复图都保留。相同资源缓存复用；服务重启按平台队列恢复。验收通过触发同一个自动绑定流程，无需再手动build。压力姿态不是自然行走，不检查接地；最终idle/walk必须继续原有8/32帧与接地复检。不要缩小压力角度、收窄大腿或扩大固定区来隐藏原缺陷。

## 压力失败后的批量修复

先用原尺寸逐姿态与关节裁切区分“被擦除缺图”和“前景层夹带衣料”。恢复必须来自本角色同一部件的 part_finish_history；不能拿另一侧镜像替代。肩带应分清固定在主体上的包带与跨肩覆盖层，不要用截短前景制造新硬边。

`repair-connections data={revision,restore_parts:[{part,expected_sha256,source_sha256,box,reason}],edits,assembly_plan}`：box为当前部件PNG内像素矩形，历史来源必须同尺寸且出现在本部件历史。恢复、finish-parts式edits、prepare-assembly式assembly_plan一次事务提交，只有最外层重建证据。恢复目标随后connections使用平台计算的确切新摘要，无需模型再往返读取一次。其余部件与来源仍校验显式摘要；失败不替换原角色。旧压力验收失效，复检前不准宣布合格。

## 固定穿戴配件的遮挡归属

肩带悬浮或跨袖时，先对照主衣装原PNG：若完整肩带已经在主衣装，额外复制的整条前景可能才是问题。实际检查后使用 `prepare-assembly data={revision,accessory_occlusion:[{mode:"fixed-to-costume",source:"torso",near_part:"l-upper-arm"或"r-upper-arm",source_sha256,remove_foregrounds:[真实重复前景名],reason}]}`。

平台只移除当前衣装派生、且subject对应所选近臂的重复前景，要求完整衣装、正确源摘要和真实前后顺序；保留所有base贴图、关节点与动作。该操作不得同时指定新增overlays，避免重新加入被删除的层。不能用于没有同源完整图面的独立配件，不能套给所有人物。

不要通过截短肩带形成新的硬截边，也不要只用角度切换drawOrder掩盖归属错误。Spine共享网格和drawOrder是可用机制，但需要匹配真实深度关系。修复后重新做完整部件、摆开、压力与idle/walk复检。具体GitHub一手来源及试验见平台仓库 docs/GITHUB_OCCLUSION_RESEARCH.md。
