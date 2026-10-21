---
name: spine-ai-reskin
description: "使用 Spine 换皮工作台，根据原有 Spine 资源和目标风格自动生成部件提示词、调用已配置的生图服务、替换贴图并导出新资源包。用于整套或指定部件换皮、提示词计划、任务查询恢复及结果下载，支持本机和局域网服务。不用于单张人物图生成行走或更换骨架。"
metadata:
  version: "1.11.1"
---

# Spine AI 自动换皮

生图使用平台当前配置的服务和模型（`GET /api/provider`），默认亿创 Flare，也支持 OpenAI Images 兼容 API。每次1张，历史任务按提交时的配置恢复，不自动切换服务或模型。亿创通过 HTTPS `input_images` 引用图片；兼容 API 下载对应参考画布后作为 multipart 文件上传。`reference` 换皮仍需每个部件真实参考画布的 HTTPS 链接映射；此入口尚未自动准备整套换皮画布，不能把单图人物的自动上传能力当作整套换皮已实现。缺少画布时报告具体前提，不丢弃参考图改成文生图。


使用已有工作台服务完成换皮，结果另存为独立项目。保留原 JSON/SKEL、atlas、骨骼、动作、mesh 权重、贴图尺寸和透明轮廓。交付可下载的运行时 ZIP、结果预览和校验结论。

## 选择输入与生成路线

- 输入是含 JSON 或 SKEL、atlas、全部 PNG 的 ZIP/目录，或工作台的 `project_id`。原生 `.spine` 工程需先导出这些资源。
- 使用用户给定的目标风格、部件范围和生成数量上限。未限定部件时，处理全部 atlas 部件；默认最多 32 次生成，每个部件一次。缺少源资源或目标风格时询问缺项。
- 默认 `material`：生成表面材质，按原轮廓和明暗贴回，保留暗色线条。适合改色、材质与表面风格，不能增加轮廓外配饰或重画整个人物。
- 用户需要原部件细节重绘时，使用 `reference`；先读 [参考图与恢复说明](references/api.md)，核对平台已准备对应的 HTTPS 参考画布映射。不要把普通整个人物图或 Base64 当作部件参考画布。不要把单图动画助手的免登录上传能力声明为整套换皮的已实现功能。
- material 的提示词只描述表面材质、颜色、织物纹理与均匀光照，避免加入人物、五官、肢体、游戏名和“竞品复刻”等无关内容。查看计划中的实际 `provider_input`，原部件信息属于计划元数据，不代表传入了生图提示词。不要宣称已运行视觉语义识别或另一次 LLM 分析。
- 人物肤色和五官需保留时，先查看部件实际像素；脸与衣服混在同一部件时，仅按部件名称过滤不够。优先限定完整的衣服/道具部件或明确保护分区；没有经过视觉检查的分区不能宣称身份已保护。

## 服务与工具

优先采用用户提供的服务地址，其次使用明确配置的 `SPINE_RESKIN_BASE`；在工作台所在电脑默认 `http://127.0.0.1:8780`。在其他电脑上需要服务器的局域网 IP，不能使用 `0.0.0.0` 作为远端访问地址。

先请求 `GET /api/health` 确认服务可用。服务源码不是此 Skill 的组成部分；不要在远端调用失败时另建一个空工作台。当前本机已有服务器源码位于 `/Users/tu/Auto Creating/spine-reskin`，需要启动时可使用其 `Start.command --no-open`；已运行时复用现有实例。

优先运行 Skill 自带的 [scripts/spine_agent.py](scripts/spine_agent.py)。此客户端只需 Python 3 标准库；亿创凭据由服务器读取，调用方不传 Key、不复制凭据文件。

下面 `SPINE_SKILL_DIR` 指此 Skill 的真实目录，`SPINE_BASE` 指选定服务地址；将源路径、目标风格和输出路径替换为当前任务值。

```sh
python3 "$SPINE_SKILL_DIR/scripts/spine_agent.py" plan \
  --base "$SPINE_BASE" --input "/path/to/original-spine.zip" \
  --style "冰雪蓝白装甲，银色接缝，简洁卡通风格"
```

已有项目使用 `--project PROJECT_ID` 代替 `--input`；指定部件使用 `--regions r0 r1`。不要凭示例猜测部件编号，使用 plan 或项目摘要返回的真实编号。

## 执行与交付

1. 运行 `plan`。它不会提交亿创生图。检查部件、提示词、`generation_count`、`within_budget` 和能力边界。若超过上限，按用户授权的范围调整部件或数量；不要只为绕过错误提高上限。
2. 用户已要求实际换皮时，直接推进任务；只要求查看提示词、计划或接口时，到计划为止，不提交生图。沿用已有授权，不额外添加确认步骤。
3. 为本次风格使用独立的 state 文件，在任务输出目录保存；同一任务始终复用它。先 `run` 返回任务编号，再分段查询或等待，避免一次长等待阻止进度更新。

```sh
python3 "$SPINE_SKILL_DIR/scripts/spine_agent.py" run \
  --base "$SPINE_BASE" --input "/path/to/original-spine.zip" \
  --style "冰雪蓝白装甲，银色接缝，简洁卡通风格" \
  --state "/path/to/output/ice-task.json"

python3 "$SPINE_SKILL_DIR/scripts/spine_agent.py" wait \
  --base "$SPINE_BASE" --state "/path/to/output/ice-task.json" --timeout 45
```

4. 读取 JSON 中的 `state` 和 `progress`。`queued/generating/assembling` 继续查询同一任务；客户端等待超时不会取消任务，也不意味着生成失败。需要时继续 `status` 或 `wait`。
5. `completed` 后下载，并检查 `validation.passed`、`skeleton_unchanged`、`atlas_unchanged` 和 `result_sha256`。客户端会核对 ZIP 摘要，包内包含 `_reskin-validation.json` 和 `_reskin-automation.json`。

```sh
python3 "$SPINE_SKILL_DIR/scripts/spine_agent.py" download \
  --base "$SPINE_BASE" --state "/path/to/output/ice-task.json" \
  --output "/path/to/output/ice-spine.zip"
```

6. 将响应中的 `preview_url` 与服务地址组合成可访问的完整 URL。视觉检查与文件校验分别说明：只通过不变量校验时，不宣称整套动作的视觉效果已验收。最终提供 ZIP、预览链接和有意义的限制。

## 任务恢复

- 生图会计费。请求超时或 `submission_unknown` 时保留同一 nonce/state，先查询已有任务；不能换新编号盲目重复提交。
- `needs_attention` 读取 `error` 和 `jobs`；查询达到上限可恢复轮询。提交结果不明时先核实亿创已有 `gen_id`，再绑定；具体参数见 [references/api.md](references/api.md)。不要编造编号。
- `failed` 不返回半套成品；保留已有候选和失败原因。只有用户授权重做时才建立新的任务。
- 亿创返回“涉嫌抄袭”或“违反安全策略”等文字时，只能报告该次上游生成失败，不能据此断言源素材确实抄袭。核对真实提交提示词与图片输入；按已有授权和预算定向修正，不只更换 nonce 重复抽卡。普通改色方案与 AI 材质/细节重绘成果分别说明，不能把调色包装成重绘成功。
- `cancelled` 停止继续处理。用户要求取消时使用 `cancel`，不声称已取消亿创服务器上已经提交的生成。
- 服务重启会继续收集已提交任务，并继续未提交部件。结果项目被手动修改后不能用自动任务覆盖，源项目保持不变。

需要直接 HTTP 集成、参考图模式或恢复参数时读取 [references/api.md](references/api.md)。完整请求字段见 [references/openapi.json](references/openapi.json)，以选定服务器的 `GET /api/automation/openapi.json` 为当前版本依据。
