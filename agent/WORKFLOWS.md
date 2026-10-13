# 程序批量调用 Spine 自动工作流

程序提交自然语言需求，平台排队启动 Codex CLI，通过 MCP 调用现有换皮、单图行走、骨骼编辑和动作工具。在独立副本上处理，自动查看多帧预览并校验，最后提供 ZIP。调用者不必打开网页拖骨骼。1.7.1 起，管理员可启用临时 HTTPS 参考图通道，无需配置对象存储；已有可用 S3 配置优先。使用者在 Codex 中只发送角色图片即可默认制作待机和自然行走；可另说动作要求，不需登录亿创、复制链接或填写亿创／存储密钥。

## 同事直接使用网页

不需要安装 Python 或配置 API。打开服务机器的 `/auto.html`，上传全身角色图，填写动作要求，点击“生成动画”。后台排队完成后，页面可播放动作并下载 Spine ZIP。1.9.1 的独立结果页为 `/result.html?rig=实际骨架编号&animation=实际动作名`，主要展示最终样子；“调整细节”才进入 `/rig.html?rig=实际骨架编号&advanced=1`。普通用户不用处理骨骼、部件格位或接地数值，Codex 负责看图、检查和按后续描述微调。当前人形角色以待机、行走为基础，其他动作先判断是否可适配；失败会显示原因，不等于任何图片、动作都能成功。

网页与程序调用默认均无需访问码或令牌。同事打开页面即可自动连接，分享时只发送局域网／VPN页面地址。远程浏览器仍使用签名、HttpOnly Cookie 保存30天的独立会话，只显示自己的制作记录；清除 Cookie 会失去原会话的记录入口，开放访问不会把远程访客提升为管理员。网页没有亿创密钥。管理员可显式设置 `SPINE_WEB_REQUIRE_CODE=1` 并重启，恢复网页访问码；访问码模式下才需提供页面地址与访问码。未设置此项时，网页跟随 `SPINE_AGENT_REQUIRE_TOKEN` 的开放／保护设置。此入口不代替外网的 HTTPS 和整站鉴权。

“我有图片”默认勾选“先准备完整角色部件图”。Codex先检查目标动作的视角，必要时补视角，再调用亿创生成一张完整部件图，审查实际裁片、连续臂与腿鞋链、组装和接缝后才绑定。已有完整部件图直接复用，不重复生图。取消选项后只用原图，严重遮挡或复杂背景仍可能失败。立绘、视角、部件与一次质量修订共用4张预算，原图保留。“按描述生图”在最多4张预算内按需调用亿创生成立绘或部件图，显示积分提示后由用户点击提交。请求 nonce 自动保存在浏览器，网络失败重试复用同一任务，避免重复排队或生成。

编辑工作台中“自动制作”可带入当前角色，在独立副本中制作，原图、已校准关节、部件和版本均保留。结果放在简易页的“最近制作”中。程序批量调用仍使用下述 API。

## 接入条件

- 服务机器已安装并登录 Codex CLI（`codex login status`）。后台使用保存的登录，会消耗对应 Codex 额度；亿创生图另计。
- 亿创凭据仍由平台保管，远程程序只需平台地址，默认无需 Agent 令牌。
- 需要补视角／部件时由服务端发布器准备 HTTPS 参考图。管理员在服务机器 `http://127.0.0.1:8780/settings.html` 可启用临时 HTTPS 通道，官方客户端位于 `tools/bin/cloudflared`；无需 Cloudflare 账户、域名或存储密钥。已有 ready 的 S3 配置优先，S3 仍可由本机配置 endpoint、region、bucket、access_key_id、secret_access_key、prefix 与签名链接有效期，密钥不返回调用者。`GET /api/automation/agent/sources` 的 `reference_upload.ready` 表示发布器是否就绪，就绪不等于已完成真实发布或补图验收。
- 通道未启用、客户端缺失、网络不通且无匹配有效缓存时，需要管理员处理；平台保留任务、报告具体原因，不要求普通使用者提供链接或凭据，也不声称补图自动化链路已实际验证。
- Agent API 默认允许所有可连接平台的客户端提交、查询及下载任务。服务端仍保留内部会话签名文件；无需交给调用者。管理员可选设置 `SPINE_AGENT_REQUIRE_TOKEN=1` 并重启服务，恢复 Bearer 认证，客户端兼容可选的 `--token-file`。
- 本机地址 `http://127.0.0.1:8780`；另一台机器使用服务机器的局域网／VPN IP。`0.0.0.0` 是监听地址。
- 当前远程方式为局域网或 VPN。外网部署需要 HTTPS 和对整个平台的鉴权，不能仅开放 8780 端口；原工作台其他接口仍采用已有局域网访问方式。

## HTTP API

所有 Agent 接口默认无需 `Authorization`；POST 仍需 `X-Spine-Local: 1` 和 `Content-Type: application/json`。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/automation/agent/sources` | 查询源项目编号和实际能力 |
| POST | `/api/automation/agent/tasks` | 提交一个需求，返回 202 和任务编号 |
| POST | `/api/automation/agent/batch` | 一次最多 32 项，每项独立返回任务或错误 |
| GET | `/api/automation/agent/tasks` | 查询任务列表 |
| GET | `/api/automation/agent/tasks/{id}` | 查询状态、阶段、生成额度和成果 |
| POST | `/api/automation/agent/tasks/{id}/cancel` | 停止后续处理 |
| GET | `/api/automation/agent/tasks/{id}/download` | 下载完成任务的 ZIP 合集 |

请求示例（项目编号用 sources 接口查得的真实编号替换）：

```json
{
  "nonce": "4d3e05ce7cbf4a10b795b31b06dbe079",
  "requirements": "保留全部贴图和已有动作，头部放大 2%，新增慢速行走动作，自动检查并导出。",
  "sources": [{"kind": "rig", "id": "真实的32位项目编号", "revision": 0}],
  "max_generations": 0,
  "max_repairs": 2,
  "timeout_seconds": 1800
}
```

`sources.kind` 支持 `project`（原 Spine 资源）、`rig`（骨骼项目）、`character`（单图角色）。每个任务最多 16 个源项目。空 sources 会创建单图角色，从需求生成立绘、完整部件图和动作。新单图任务默认 `image_preparation="asset-sheet"`，部件计划为一张 `character-assets`，不固定生成三组旧部件。`auto` 保留仅补实际缺项的旧流程，`preserve` 使用现有图面。默认生图上限4张，已提供完整部件图或已接入的合格完整部件会先复用。已有有效图片的质量补做最多一次且仍占原预算；预算用尽不自动加额。

批量请求格式为 `{"requests": [上述请求, 另一个请求]}`，每项有自己的稳定 nonce 和生成上限。额度逐项限制，不是所有批次共享一个上限。任务串行运行，避免无限并发消耗额度。

相同 nonce + 相同请求会复用任务；相同 nonce + 不同请求会被拒绝。超时、未知提交状态或重启打断不会自动重新付费提交。状态为 `queued/running/completed/failed/cancelled/interrupted`；终态后查询记录仍可保留生成任务 ID。失败不是完成，调用方不能将所有 202 当作生成成功。

## Python 客户端

`spine_workflow.py` 和同目录的 `spine_mcp.py` 可复制到调用机器，无第三方 Python 依赖。配置 `SPINE_AGENT_BASE=http://服务器IP:8780` 后：

```sh
python3 spine_workflow.py submit --request request.json
python3 spine_workflow.py batch --request batch.json
python3 spine_workflow.py status --task <任务编号>
python3 spine_workflow.py download --task <任务编号> --output result.zip
```

默认不用配置令牌。客户端核对下载 SHA-256 后才保存结果。

## Codex 接入

交互式 Codex 用本地 stdio MCP 桥接远程平台 HTTP API。按 OpenAI Docs 支持的方式配置：

```toml
[mcp_servers.spine]
command = "python3"
args = ["/调用机器的绝对路径/spine_mcp.py", "--base", "http://服务机器IP:8780"]
```

公开工具是 `spine_sources/spine_submit/spine_batch/spine_status/spine_cancel/spine_result/spine_preview/spine_gait/spine_contact/spine_refine/spine_download/spine_reference`。`spine_submit.image_path` 上传调用电脑上的图片，默认 asset-sheet：先检查、准备完整部件图并审查组装，再绑定；已有完整部件图不重复生成；`spine_preview` 默认返回8帧图片，frame_count 可选4–32；`spine_refine` 基于上一版完成的 task_id、artifact_index 冻结实际交付骨架继续调整，默认不重新生图。每次微调产生新任务，带 parent_task_id，保留旧版；后续改动继续最新完成任务。旧版被外部修改时拒绝用其冒充原交付版。

安装 `skills/spine-animation` 后，用户在 Codex 里给图片说“做行走动画”，后续直接说“步幅小一点”或“头放大2%”。Skill 管理真实编号、nonce、轮询、预览和下载；当前会话未加载 MCP 时，可使用同包客户端。安装程序 `scripts/install.py --base 服务地址 --non-interactive` 复制 Skill 到 Codex 目录并注册 `spine_animation`。远端只需一次配置服务地址，不需要令牌文件或亿创 Key；简易网页也默认无需访问码。

提交后平台后台启动独立 `codex exec`，挂载当前任务专用工具；调用者不用一直保持 Codex 会话在线。后台不加载用户的其他 MCP 配置，使用只读 shell 沙箱，平台写操作由任务范围内的 MCP 工具执行。

## 验收与能力边界

- 原项目保持不变，输入会先冻结为独立副本。骨骼修改支持事务性多操作、替换同版本完整骨架、替换完整动作，以及同版本骨骼局部曲线的明确映射。
- 导出前执行结构与图集校验，官方 Spine 3.8／4.2 runtime 加载所有皮肤、每个动作采样 33 帧；无效坐标或加载失败阻止交付。
- 后台 AI 必须查看每个动作的联系图，普通检查默认8帧，明确行走／奔跑门禁至少16帧，最终步态及复杂接合建议32帧，供其判断接合、遮挡、穿插和步态。资源改动后必须重新看预览；文件容器时间戳不会误判为内容改变。
- 多帧视觉判断仍可能漏检；没有保证任意图片都能拆出正确部件，或任意骨架都能自动转换动作。不同局部坐标轴、父子层级、IK、deform 需要实际适配，不能只改骨骼名称。SKEL 二进制不能编辑骨骼；交付为 runtime JSON/atlas/PNG，非原生 `.spine` 工程。
- 修正次数、生成额度或执行时间用尽后返回失败原因和已有任务记录，不要求人工拖骨骼，也不伪报完成。失败后增加预算重新提交属于新任务，调用方须明确允许。

## 后台常驻

macOS 可用 `install_service.py` 生成／安装用户 LaunchAgent。它在用户登录后启动并在进程退出后重启；机器睡眠、关机或退出用户登录期间不能执行。服务重启会将运行中的 Codex 任务标记为 interrupted，保留候选图和生成编号；排队任务继续执行。

官方依据：[MCP 接入](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)、[Codex 无交互执行](https://learn.chatgpt.com/docs/non-interactive-mode)。

## 1.4.0：补图与步态检查

spine_refine.part_images 上传调用电脑的补全部件，导入上一版独立副本，保留原部件和版本链，由后台完成绑定；spine_gait 测量所选同侧手脚的运动相关性、相位偏移和循环误差。骨骼映射需来自真实蒙皮，数值不替代视觉验收。完整流程见 Skill 的 references/rig-refinement.md。客户端 preview --frames 16/32 可检查更多关键姿态。

## 1.5.0：动作图片适配检查

图片提交默认 image_preparation=auto，由 Codex 检查目标动作的视角、遮挡和缺失部件。需要时在 max_generations 内使用亿创参考图生成合适视角或选择性的部件组，复查当前图片与部件后才允许建骨架。原图保留。MCP 上传图片默认额外预算8；严格原图模式用 preserve 和预算0。HTTP 调用者需自行明确预算；所有生成共用它。视角合格不等于任意动作均可实现，复杂转身与非人形仍需具体判断。


## 1.6.0：GPT Image 2.5 Flare

所有新生图统一通过亿创 gpt-image-2.5-flare，high / 1K / PNG，每次一张。参考图字段 input_images，须为 HTTPS URL。generate-view 和 generate-parts 接受 data.reference_url，并自动复用匹配当前角色的参考图缓存或已应用候选图链接。每份任务在提交前记录 model，旧任务无 model 时按 nano-banana-2 查询，不重复提交。

## 1.7.1：服务端临时 HTTPS 参考图，无需配置存储或亿创网页登录

普通调用直接使用 `spine_submit.image_path` 和自然语言 requirements。服务器保存当前图片，后台 Codex 判断目标动作所需视角与部件；合适时不生图，需要补图时 `generate-view`／`generate-parts` 自动确保原图 HTTPS 链接。优先复用与当前原图完全一致的有效缓存或已应用候选图；缺失时使用 ready 的 S3 存储，或管理员启用的 Quick Tunnel 发布服务器正规化后的原图，回读校验像素，再按图片哈希缓存。S3 签名链接过期会刷新；临时 PNG 链接有效期1小时，服务重启旧链接失效，平台再次使用时自动刷新。发布和链接准备不占生图张数，不提交额外生图。

管理员配置亿创 Key 并启用参考图发布器，之后其他使用者不登录亿创、不操作网页、不复制 URL、不持有这些密钥。临时通道只需服务机的官方 `tools/bin/cloudflared` 客户端；不需要对象存储账户、Cloudflare 账户或域名。它连接独立 loopback PNG 服务器，只允许随机 token 对应的 PNG GET/HEAD，不能通过通道访问工作台、目录或其他接口；拿到链接者在有效期内可读取对应原图。生图仍由亿创 GPT Image 2.5 Flare 完成。服务机本地设置入口为 `http://127.0.0.1:8780/settings.html`，远端无法更改发布配置。S3 存储仍可选，已有可用配置优先；密钥不会出现在 sources、MCP 结果或客户端安装包中。

[Cloudflare 官方文档](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)将 Quick Tunnel 用于测试与开发，没有 uptime 保证；每次启动主机名会变化。服务器负责链接有效期和缓存刷新，不能据此承诺永久链接或生产稳定性。服务机与网络需保持运行；此通道只提供亿创读取参考图的出口，不替代使用者连接平台所需的局域网／VPN。

诊断时可调用 `spine_reference {action:"ensure", kind:"character", id:"角色编号", revision:当前修订号}`。worker 仅可操作任务自己的副本，revision 冲突拒绝发布。HTTP 等价入口为 `POST /api/characters/{id}/reference`，JSON 包含 `action:"ensure"` 和 `revision`，附 `X-Spine-Local: 1`。`GET` 同路径只查询缓存，不触发上传；旧 `prepare`／`register` 保留兼容，但网页上传不再作为主流程前提。

换图后按新哈希处理，不误用旧参考；只改骨骼或部件参数且原图未变时可继续复用。未知生图提交仍保留同一 nonce 和生成编号，不用新任务重试。缓存检查与上传都不能替代实际补图的角色身份、完整部件和动作画面验收。

发布器未就绪或网络失败时，明确报告平台侧原因并由管理员处理，保留已有任务。不能要求消费者配置存储。只有实际发布、远端像素校验、Flare 生成结果复查及动作多帧检查完成后，才给出各阶段的验收结论；不能把代码接入或 ready 状态当作全流程成功。已有 Spine 的 `reference` 换皮仍需每个部件的真实 512×512 画布链接；本轮只接入单图动画，不自动代填 AutoReskin 的 `reference_urls`。

## 1.8.0：部件复用与真实动作交付

`generate-parts` 可指定 `instructions_by_sheet={body,arms,legs}`，每组最多400字，不与非空 `instructions` 混用。只有确认原角色没有背包／背部道具时才用 `include_back=false`：身体图是头、躯干、腰胯三列一行，3:2。提示词与持久任务布局一致，但模型遵循程度仍需实际看图。

后台 Codex 查看现有候选后，可用 `spine_character action=apply-part-layout` 传当前 `revision/job_id/parts`，按实际语义给裁片像素坐标与裁片内的关节 pins。完整臂／连鞋腿可以直接绑定到同侧三骨的连续网格；`canvas+pieces` 可组合同一候选中的裤腿和鞋。接口不新增生图、不推断关节、不补缺失像素；对照组装图再评估当前修订并 build。完整参数见 OpenAPI 的 PartLayoutRequest 与 Skill 的 image-preparation.md。

最终官方 runtime 校验会报告真实可见运动、相对父骨运动和循环差异，提前拒绝 Spine 4.2 非零 rotate.angle 缺少 value 的错误写法。明确行走／奔跑任务的对应 walk/run 动画交付至少需16帧已查看且各 skin 有可见与关节运动；最终建议32帧复查。静态 pose 和非循环动作仍可合法，数值检查不能证明自然步态或所有遮挡都正确。继续检查肩髋遮挡、鞋底刚性、支撑期与摆动期。


### 1.8.1 接地与完整肢体

新建人形行走默认抬脚7%、60%支撑期，双腿共用较短链长确定的步幅与场景速度，配合平顺摆腿目标和按步长缩放的摆臂；完整刚性鞋依据完整alpha下轮廓估计鞋底方向与踝目标，并检查双骨可达范围。缺少完整鞋时明确使用ankle-fallback；数学目标不能替代最终不透明鞋底验收，也不能把正面素材转换为侧视图。可显式选择 `generate-parts.sheets=["limbs"]` 一张四完整肢体候选，必须经Codex实际裁片与关节审核后用 `apply-part-layout` 接入；默认body/arms/legs保持兼容。


### 1.8.2 通用步态与真实鞋底诊断

新建人形行走使用256采样的Spine4.2 Hermite Bezier角度曲线，减小关键帧速度变化；保留60%支撑期、两腿统一步幅与场景速度。既有骨架与动作不自动改写，曲线平滑并不等于严格世界空间C1、自然步态或已有正面图变成侧视。

`spine_contact` 只读诊断真实alpha轮廓在官方runtime中的接地漂移、统一场景速度下滑步、循环与归一化速度变化。公共调用用 `task_id/artifact_index` 或 `kind="rig",id="实际交付骨架"` 二选一；worker只能检查任务持有副本。默认128、最多512采样。已知sole-contact-v1模板可自动读取映射，其他骨架必须按真实骨链/蒙皮给options.pairs、phase、scene_speed、stance等；缺条件是not_assessed，不能说通过。仍需32帧检查遮挡、关节、角色身份和视角。

CLI例：`spine_workflow.py contact --rig 实际骨架ID --animation walk --samples 128`；显式条件用`--request options.json`。工作台“检查鞋底接地”读取当前动作与皮肤，不改资源、不上传、不生图。预览链接自动选中已交付walk。

### 1.9.0 简洁结果页与提示词失败处理

用户只看最终角色动画：独立结果页提供动作、播放／暂停、左右翻转及下载；速度、外观和历史成果放在折叠选项中。骨骼、部件、动作编辑与接地诊断保留在“调整细节”按钮后的高级页面。已保存角色版本的预览使用该版本真实 rig，旧制作记录的结果入口也解析到简洁预览；不要求使用者手动操作骨骼才能完成制作。

提示词 `animation-parts-v7` 按需求主语言使用简洁正向美术描述，保留必要角色、服装／配件、相机、连接和格位语义；英文产品名、文件或动作标识可以保留。只生成实际缺项，按组选 body／arms／legs 或显式 limbs，减少无关要求与布局复杂度，不将改词或分组作为规避审核方法。Codex 仍检查实图的角色身份、完整部件、关节和鞋口连续性，模板本身不能保证模型接受或产出正确图片。

失败按原因处理：提交未知／超时保留同 nonce 和记录，只查原任务；查询或下载网络失败只读重试；审核拒绝、IMAGE_RECITATION、IMAGE_SAFETY 明确停止，不自动改词、拆组、裁图／换图、换模型或供应商规避。生成结束但原因不明／没有图片时如实保留原因，不冒充可补做的视觉缺陷。

仅对已收到并查看的有效图片，发现具体视觉／布局质量问题后，可先裁切或复用现有合格部分；确需新图时在剩余预算内最多一轮精确改写或按缺项分组重做。新生成使用新 nonce，原 job／gen_id、候选和改动原因保留；每张仍占原总预算，不因失败退还已预留额度。再不合格则停止新增生图并说明限制，不声称平台会自动重生成所有失败。提示词和失败分类的模拟测试与实际生图验收分别说明。

## 完整部件图流程（1.9.1）

新立绘先检查视角和身份；需要时准备同角色视角图，再以亿创 GPT Image 2.5 Flare 生成 `character-assets`。它建议4列2行、3:2：头／躯干／腰胯／可选背部配件，完整双臂连掌／完整双腿连鞋。没有背部配件留空。平台不按格子猜部件：Codex查看实际候选、标注裁片和关节，通过 `apply-part-layout` 接入连续加权链。收到图片不等于验收通过。

原有部件图先记录 `source_kind="asset_sheet"`，用虚拟组装姿态校准 guide，再通过 `apply-source-layout` 的 `{revision,parts}` 直接裁切原图；旋转头部可用 `rotation`（正角逆时针），pins属于旋转后展开的裁片。此操作没有生图任务，也不改原图。

新策略绑定门禁要求头、躯干、腰胯和四条完整链已接入、当前revision实际查看assembly，并记录 `ready, parts_reviewed=true`。生成后检查32帧行走与真实鞋底接地，不能将完整部件图或运行时校验当作自然步态保证。更换视角仍可能有角色漂移，复杂发型、长衣摆与道具遮挡仍需图面和权重调整。

参考制作规范使用 `compact-weighted-character-v1`：默认 `body_layout="combined"` 将上衣、腰带、腰胯／长衣摆保留在一块主衣装中，减少腰线接缝。上排头／完整衣装／可选背部道具／空，下排四条完整链；无背部道具时6块，有道具7块。`group="costume"` 用显式hip/neck pins覆盖torso+pelvis，当前绑定仅hip/torso连续权重，不实现独立衣摆物理。实际服装需要时选separate。`spine_context.production_reference` 返回该规范及边界；不可照抄原3.8骨骼编号或把原run当自然walk。新完整素材策略交付行走要求当前导出资源32帧与status=assessed的鞋底诊断。


1.9.2 新人物默认先准备侧向行走视角（正面参考先转换），再生成完整部件图；Codex 先 spine_review_parts 检查身份、轮廓、分离和肩肘腕／髋膝踝连接，裁切修整后组装、绑定并检查待机8帧/行走32帧和鞋底，定向优化后复检。多边形裁片只遮去外部像素，不补画。旧任务合同及已有rig微调不变。reference_unavailable 是生图提交前的参考图失败，不等于已生成。

spine_review_parts 是任务内后台工具，外部使用者仍通过 spine_submit/status/preview/refine/download 操作；不用学习关节检查JSON。


## 1.9.3头颈接缝自动复检

后台每个最终动作使用spine_closeup渲染8帧局部放大图，再以spine_review_closeup记录实际结论。白底组件只定位疑似问题；头颈矩形硬切和遮挡由Codex查看实图。新任务带neck-seam-v1，当前资源的任一动作未完成局部检查不能交付；修改后全部旧结论失效。texture-repair只清指定多边形中的中性白底、复制当前rig实际部件或保留material_sources中的原像素，单片最多25%，不凭空补画、不全局删白、不自动新增付费生图。新角色保留最多4张不可变准备图，rig副本继续可访问。严格不改贴图的请求只诊断并说明问题。

公开只读工具spine_closeup支持task_id/artifact_index或kind/id二选一；默认anchor=head，零长度骨通过实际头附件范围定位，无法可靠定位时按真实头颈骨指定anchor。CLI：`python3 spine_workflow.py closeup --rig 实际编号 --animation walk --output /绝对路径/neck.png`。生成接口和原有动作/接地检查保持不变。


当前1.9.4以Skill三视图流程为准：asset-sheet新人物同批生成turnaround与character-assets，先reference后assembly比对。初次两张基础图另计；默认max_generations=8为补图/修订额度，重抽基础图也占8张。0禁止全部生成。每个最终动作逐区域放大并spine_review_closeup(anchor=对应区域)，先通过当前digest验收再导出。

1.9.5 局部材质修整与深浅底复检：参见 Skill references/seam-review.md。material-finish 与 texture-repair 均须当前资源 closeup；只改已查看区域的 alpha，不改 RGB、骨架和动作。透明度依据原材质，禁止给实色服装自动透明化。

1.9.6：新建膝下连续衣装自动添加保守衣摆随动，生成图保留自然垂落。不可直接追随大腿绝对偏转或两腿最大值；Codex在32帧行走及躯干/双腿放大图中检查前翘、折角、穿插和循环。接地正常不代表衣摆自然，复杂衣装仍需定向调整。


1.9.7 新任务启用 asset-integrity-v1：部件、组装与每动作局部图记录 material_checks，失败不交付；数字接地不代替视觉验收。缺衣料可用 sheets=[costume] 单独补完整主衣装，仍受原额度和质量修订限制。导入 back 先标定位点，缺定位点明确报错。详细流程见 spine-animation Skill 的 seam-review.md。


## 1.9.9 长衣装与腿形诊断、复检提速

读取 spine_context.garment_contract。garment-occlusion-v1 对平台识别的长衣装双腿：行走32帧、待机8帧组合closeup，并由spine_review_closeup记录garment_checks四项：hip_coverage、thigh_contour、knee_readable、garment_clearance。ready须全部true；原设计膝部遮挡允许，异常挤尖/穿插不允许。不能把接地数值通过当作美术通过。

腿形可疑才调用相同摘要/动作/帧数的 spine_closeup(isolated=true) 对照。单腿本体异常查定位和短安装区权重；本体正常查衣装开衩/层级。隔离图不计最终审查。不得照抄蓝裙坐标或用扩大髋部固定区、缩细大腿、全局透明衣裙掩盖问题。编辑后摘要改变须复检。

集中相关编辑再做最终检查，重复相同请求复用PNG及runtime采样缓存；不同只读请求有两路计算上限。诊断只查看疑点，不对所有区域额外生成隔离图。继续同一任务复用素材、额度和nonce，不新建重复生成。新图片仍为亿创GPT Image 2.5 Flare，禁止未经请求切换模型。缓存和并发缩短本地检查时间，不保证亿创排队时长。
