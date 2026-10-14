# 默认装配执行路径

完整部件导入 apply-part-layout / apply-source-layout 后，平台默认执行 assembly-execution-v2：按已审查关节点配准 → 保存整数 canvas_offset 与 canvas_pins → 保留独立完整 alpha 和隐藏重叠 → 供试拼、摆开和骨架导出共享。格子图仍只是裁片来源。导入尚不齐全时继续补齐，不对可见碎片冒称补全。修改 guide 会从保存的原始完整裁片重新配准，不在反复缩放后的图上累计变换。

需要补全时优先找本任务已有完整裁片，调用 spine_character action=prepare-assembly；后台来源必须是任务拥有的 character，不能读取任意其他用户角色。管理员接口为 POST /api/characters/{id}/prepare-assembly，X-Spine-Local: 1。

参数：
- revision：当前版本。
- replacements（可选）：[{name,character_id,part,source_pair:[[x,y],[x,y]],target_pair:[[x,y],[x,y]],pins:{...}}]。source_pair 和 pins 是源 part-images PNG 的像素坐标，target_pair 是母版像素坐标。连续臂 pins 为 shoulder/elbow/wrist/hand；连续腿为 hip/knee/ankle/toe。平台调用真实源图执行配准、保存来源摘要和变换，保留补全 alpha；不会把轮廓剪回原图可见区域。不得把不完整来源标成完整。
- overlays（可选）：[{name,source,subject,source_sha256,polygon,reason}]。从现有 source 贴图的归一化 polygon 提取前景，source 在 subject 后，前景紧邻 subject 前；source_sha256 是当前实际装配 PNG 摘要，防止套用别的素材轮廓。例如后领 → 头颈 → 前领。平台从同一数据生成静态预览和导出附件，复制源部件的骨骼及权重。不得用固定人物坐标。传 [] 可清除遮挡方案。

导入还可带 assembly_plan={replacements,overlays}，在同一次平台操作执行后续步骤，返回最终 revision。修复必须落实为真实像素/锚点/前后层级操作，不能只写检查规则后再次停在原缺陷。

完成后查看 prebind-parts、prebind-assembly、prebind-spread，再提交当前 digest 的三项真实结论；随后 build。补全部件、前领轮廓、位置或源图变化会使旧验收失效。finish-parts 改动前景来源时会移除旧遮挡轮廓，应重新标注；只改不相关部件时保留轮廓。完整且固定穿戴的背包可与主衣装合并，按模板保留身后包体和身前肩带；需要独立摆动的背包必须另层。

平台负责确定性执行，视觉判断仍负责选对素材和关节。缺少隐藏图面时应复用或生成实际补图，不能宣称同坐标转换自动画出了缺失部分。全程记录实际素材来源、未通过中间图及最终版本，保留上一版合格成果。

动态摆腿暴露的重复裤腰/口袋端片须回到素材修复：先按实际范围修外露端角，保留髋部中心重叠；若多次修整仍有断面或半透明残片，复用已验证的完整同部件再配准，不继续侵蚀。明确列出本次素材哪些保留、哪些被替换，旧动画不可冒充新建动作。

## v2：修复不回退的默认执行

对已校准的完整来源，优先使用 replacements=[{name,character_id,part}]，同时省略 source_pair、target_pair 和 pins。平台从源部件关节与目标母版关节推导配准，保留隐藏像素；不靠格位猜坐标。来源仍须属于任务且需视觉确认适合相同角色、相机和侧别。只有关节本身需重标时才显式提供完整锚点和 pins。

局部重新导入和补全只重置被替换部件，保留其余像素、校准与前景层。前景来源变了才使该轮廓失效，并返回 assembly_invalidations；重新标注其真实 source_sha256，不整套清空已修好的领口。导入含 assembly_plan 以及 prepare-assembly 均整批提交一次 revision；失败保持原版本，修正参数后使用原 revision 续作。

背包合并在完整衣装时传 back_accessory_part="torso"；独立背包传 "back"。该声明随导出保存，平台复检包自动覆盖待机8帧、行走32帧的背包组合区域。不能因为没有独立 back slot 而漏检；无背包角色不填，移除声明用 null。复用不是美术验收，仍查看三项素材图和最终动画。
