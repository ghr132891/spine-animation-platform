# 默认生成流程：续作与边缘处理

2026-10-16。适用于新任务，已交付角色和动画保持原版本。

| 本次问题 | 实际执行方式 |
|---|---|
| 缩略图漏看浅边和实心白点 | 完整导入和任务准备自动生成 edge-evidence-v1：源图摘要、整数像素候选、4倍深浅底局部。确认污染后使用定域去色或带 expected_rgba 的邻点取色。 |
| 白衣料和鞋面高光可能误删 | 候选只供判断，不自动去白；去色保持 alpha，不透明残点须逐点确认，不套用其他角色坐标。 |
| 已验收素材转动画仍要手工抄写参数 | 网页 animate-materials 复用当前素材摘要及会话归属；视角和连接确认后，平台自动绑定并发布草稿。对话提交已验收完整素材也采用此默认。 |
| 重试多建版本、旧素材覆盖新成果 | 网页使用稳定 nonce；绑定编号由任务、角色、素材摘要决定。重复确认复用同一 rig，旧阶段不能覆盖更新成果，修复次数沿用原额度。 |
| 零生图复用任务漏完整动态检查 | 新动画复用完整角色或生成骨架时，仍开启 detail、orientation、integrity、connection 合同。自然行走同时要求接地与膝弯数值通过，仍须实际视觉检查。 |
| 描述生成尚无关节点时中断 | 按 needs_source_image 等待原图，不把尚未产生的关节点当执行异常。 |

网页入口：`POST /api/workflows/tasks/{已完成素材任务}/animate-materials`，JSON 传 `nonce`。只续作当前单套、已验收且摘要未变的素材；固定零生图预算，保留原制作记录和版本。

确认视角与连接后，`spine_assess_image` 返回 `automatic_animation.rig_id`，后续直接检查该草稿，不重复 `build`。自动生成不等于正式通过，须检查最终相同资源的待机8帧、行走32帧和全部必要局部。

边缘证据使用 `spine_character action=edge-review` 与 `spine_image view=edge-review-image`。源贴图或诊断代码变化会生成新证据；历史快照保留，`not_assessed` 不作为合格结论。

`tools/verify_material_animation.py` 在临时目录执行真实素材交付、网页续作、自动绑定和官方 runtime 校验，验证像素、位置、前领及请求去重。没有新生图，不把回归草稿作为新的美术成果。`tools/verify_material_handoff_dom.cjs` 验证按钮、网络失败后的 nonce 复用与页面状态更新。
