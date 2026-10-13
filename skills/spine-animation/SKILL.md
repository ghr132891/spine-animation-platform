---
name: spine-animation
description: "通过 Spine 工作台在 Codex 对话中从角色图片或描述制作 Spine 动画，查看动作帧图，并根据后续自然语言要求连续微调骨骼、贴图位置和动画，另存版本与下载 ZIP。支持局域网调用和批量任务；用于将人物参考图准备为侧向行走部件图、检查修整连接、组装后制作并复检动画，以及已有 Spine 的骨骼、动作调整。"
---

# Spine 动画：平台制作，Codex 验收与定向微调

只收到角色图片时，默认保留外观，制作待机和自然侧向行走。用户只需给图与需求，不要求其填写 JSON、拖骨骼、登录亿创或准备参考图链接。优先沿用已配置的平台，不能另起空服务。输出 JSON/atlas/PNG ZIP，不是原生 `.spine` 工程。

## 调用与续作

优先 `spine_animation` MCP。`spine_sources` 查看实际能力和编号，`spine_submit` 提交，保存 task_id/原始请求/稳定的32位十六进制 nonce，`spine_status` 跟进。同一提交超时复用 nonce；中断续作用 `spine_resume`，不另开重复制作记录。

- 首次人物图片：`image_preparation="asset-sheet", max_generations=8`。先检查视角，再准备三视图和完整部件图。首次三视图、部件图各有1张独立基础额度，额外8张用于改视角、补图及修订；0额度禁止所有生图。
- 明确不生图／保留原图：`preserve, max_generations=0`；`auto` 是仅按缺项补图的兼容策略，不静默替代 asset-sheet。
- 图片与已有角色相同：先核对原图像素和高分辨率资产，匹配后复用，避免从截图重拆或重复抽图。
- 自动修复默认 `max_repairs=4`（首次编辑后的4轮修复），图片额度另计；检查与交付不计修复轮数。显式传入的额度优先。
- 连续修改：从本对话最新已交付的版本 `spine_refine`，默认不生图。直接另存的 `direct_delivery` rig 没有新任务号，使用其真实编号作为 `spine_submit sources=[{kind:"rig",id:...}]` 的源；不要回退旧 worker 成果或虚构 task_id。
- 操作只在独立副本上完成。纯材质修改保留骨架和动作；修改单个动作保留其他动作完整 JSON。多个合理候选无法辨别时才询问对象。

## 平台主导的快速复检（1.10.0）

完成一轮关联修改后，优先 `spine_review_bundle(kind,id)`。平台一次冻结最终资源、决定帧数与区域、两路渲染、测量接地、扫描 alpha 残点并持久保存复检包。省略 animations 检查全部动作；行走全身和双腿32帧，待机和其他局部8帧。

返回清单后用同一工具的 `panel` 读取各张原尺寸图。平台缓存跨客户端、跨进程复用；图片/骨架/动作/渲染器改变即失效。不要在每次单像素调整后重做全套，也不要为读取复检包新建制作任务。后台用 `spine_review_regions` 一次提交多区域真实结论，任一无效则整批不写入。工具缺失时才回退 `spine_preview/spine_closeup/spine_contact`。

**准备完成不等于验收通过。** 仍须实际看全身和全部必要局部图，不能看到数字正常就标记 ready。Codex 负责判断身份、自然步态和遮挡等语义问题；平台负责确定性的执行、采样、缓存、清残点及验收约束。不能声称任意人物已脱离视觉判断自动合格。

局部边缘问题先区分：独立孤点、连通细发丝、抗锯齿边、重复衣料、缺失连接。平台只对新生成部件自动清理极弱独立插值尘点（alpha≤3、面积≤4像素）；不自动侵蚀主体、不处理用户原素材。其他修复查看实际部件后选定范围，使用 `material-finish`，无需重画或自行编写 Python。详见 [平台复检与边缘修复](references/platform-review.md)。

## 图片准备与绑定

行走必须是侧面或可读的三分之四侧面，头、衣装和双鞋朝向一致。正面图先用亿创改视角；骨旋转和整体横移不能把正面图变为侧走。只做正面待机或明确保留原图时遵守用户需求。

新角色同批 `generate-parts sheets=["turnaround","character-assets"], body_layout="combined"`，三视图单独作为比对参考，不导入图集。完整衣装从衣领到裙底连续，完整手臂带掌、腿连鞋，长后发独立；不靠固定格位猜测部件。看候选后 `spine_review_parts`，按真实像素和关节 pins 用 `apply-part-layout`；用户已给部件图用 `apply-source-layout`，不重复生图。再看当前 revision 的 assembly，六项连接和图面检查通过后才 ready/build。

生成策略、布局、参考图发布与字段详见 [图片与部件准备](references/image-preparation.md)；三视图比对见 [三视图验收](references/turnaround-review.md)。需要改骨骼、补臂、换动作时读 [骨架与动作微调](references/rig-refinement.md)。长衣装、肩缘、颈部白块、腿形和透明裙摆问题读 [接缝与分层修复](references/seam-review.md)。不套用其他人物坐标、权重或本次蓝裙的修复范围。

## 图像服务与失败边界

新图统一使用亿创 **GPT Image 2.5 Flare**（`gpt-image-2.5-flare`，high/1K/PNG，每次1张），不自动换模型。历史任务按原模型查询。平台自动将当前参考图发布为 HTTPS（已配置存储或启用的临时图片通道）、回读验证并缓存；调用者不登录亿创、不复制链接、不提供服务端凭据。发布失败报告平台原因，不能丢弃参考图改文生图。

参考图持续授权以所连接平台的 `spine_sources.reference_upload.publication_authorization` 或本机已记录的用户授权为准。`authorized=true` 且范围、用途和有效期匹配时，自动发布任务提交图片及必要派生参考图，无需逐图重复询问；遵守撤销或缩小范围的后续指示。该授权不增加生图预算，不授权上传其他本地文件，也不随安装包转移到另一独立平台。

简洁、单一主要语言、正向美术描述。收到并看过有效图片后，具体视觉/布局缺陷最多一次预算内质量修订，先复用和裁切合格素材。内容审核拒绝或 IMAGE_RECITATION 明确停止，不改词、拆组、裁图或换 provider 规避。提交未知只查原任务；查询/下载失败只重试读取；reference_unavailable 则是尚未提交生图，修好参考发布后继续原请求。预算耗尽继续处理已有合格素材，不自动增加预算。

## 交付标准

后台制作调用 `spine_deliver_draft(id,summary)` 即可把本任务可加载的新骨架交付到原网页记录，summary 写清待修问题。最终 `outcome=failed` 也应保留实际 rig artifacts，平台自动关联；无须后台申请网页管理员权限。正式验收与草稿交付分开，不耗修复次数。

直接交付修复草稿时，不能只发独立页面而漏掉首页记录。本机管理员可先 GET /api/workflows/session（保留 Cookie），再 POST /api/workflows/tasks/{原任务id}/delivery，提交 rig_id、summary（含待修问题）、process_url（已存在的 /downloads/目录/index.html）。平台校验可加载资源，单独保存草稿关联与版本；最近制作显示“已有修复草稿”，原 worker 状态不变。每次修复后重新关联当前 revision，不新建重复制作任务。远程普通会话没有关联权限，不扩大其权限。

用户要求全过程可见或允许草稿交付时，每个有产物的阶段给出实际图/预览与当前问题，沿用同一过程入口。视觉未通过但结构可加载的产物也可交付为明确标注缺陷的草稿；修复后更新当前链接并保留版本。草稿交付与正式验收通过分开记录，不伪造 ready 或把失败 worker 改为成功。

- 对最终相同资源摘要检查全部动作：待机8帧、行走32帧；头颈、躯干、双臂、双腿、完整头发，含深浅底与极值姿态。缺少、旧版或隔离诊断图不能替代最终组合验收。
- 长裙重点检查髋部覆盖、大腿连续轮廓、符合原服装的膝部遮挡及裙片间距。不为露膝破坏裙装，不靠缩步、细腿、扩大固定区或透明度掩盖问题。疑似畸形时再额外比较 `isolated=true`。
- 接地必须 assessed 且通过；not_assessed 不是通过。数值不认证美术正确。查看实际可见关节运动、鞋底、同侧手脚反相、循环、接缝及遮挡，不能以 JSON 可加载冒充动作完成。
- 修复后新摘要全部复检。保留源资产；未解决的具体缺陷如实记录。额外手动补修的直接交付不得伪装为原失败任务完成。
- 下载 ZIP，返回 `result.html?rig=真实编号&animation=真实动作名` 的完整预览链接；高级骨骼页只作可选调整入口。简短报告结果、验证范围与实质限制。

## 网页直接操作

用户也可以在制作页或动画预览页的“想怎么修改？”输入自然语言，平台复用当前最新成果另存修改。网页通过 /api/workflows/refine 提交，保留 nonce、当前版本与会话归属，同一制作记录显示进度，处理期间保留上版预览。该入口默认零生图额度；需要新图时明确说明缺项，不静默增加费用。用户无需在 Codex 再转述同一要求。

## 安装与无 MCP 调用

服务默认局域网免令牌；用户指定地址优先，不用 `0.0.0.0` 作请求地址。仅管理员启用 `SPINE_AGENT_REQUIRE_TOKEN=1` 时才用可选 token-file，不能输出令牌。settings.json 只保存平台地址等客户端设置，服务端生图凭据不打包。

安装：`python3 scripts/install.py --base http://服务器IP:8780 --non-interactive`。无 MCP 时由 Codex 执行 [scripts/spine_workflow.py](scripts/spine_workflow.py) 的 submit/status/refine/preview/download；每个版本保存独立 state 文件，重复提交复用原 nonce。批量用 `spine_batch`（最多32项），跟进每项终态。不要把这些命令变成普通用户必须完成的操作。
