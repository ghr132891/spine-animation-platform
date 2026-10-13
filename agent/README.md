# AI 自动换皮调用说明

服务默认监听 `0.0.0.0:8780`，支持本机与同一局域网的 AI Agent 调用。本机地址为 `http://127.0.0.1:8780`；其他设备使用 `http://服务器局域网IP:8780`。输入原有 Spine 和目标风格，输出新的 Spine 资源 ZIP。原项目保持不变，结果是独立项目。

机器可读接口：`GET /api/automation/openapi.json`，本地文件为 [openapi.json](openapi.json)。POST 请求必须附带 `Content-Type: application/json` 和 `X-Spine-Local: 1`。亿创使用工作台已有凭据，调用方无需传 Key。Agent 制作／微调接口默认也无需令牌，只配置平台地址（1.4.1及以上）。

已封装为 `spine-ai-reskin` Skill，源目录为 [skills/spine-ai-reskin](../skills/spine-ai-reskin/SKILL.md)，含独立 Python 客户端、HTTP/恢复说明及 OpenAPI。可将整个文件夹加入 Agent 的 Skills 目录，并通过 `$spine-ai-reskin` 使用。当前电脑已安装到 `~/.codex/skills/spine-ai-reskin`；可分享的 ZIP 为 [spine-ai-reskin-skill.zip](spine-ai-reskin-skill.zip)，平台下载路由为 `/downloads/spine-ai-reskin-skill.zip`。

## 最短调用

在 `spine-reskin` 目录执行：

```sh
# 先生成计划，免费查看部件编号、自动提示词和生成张数。
python3 tools/spine_agent.py plan \
  --input /path/to/original-spine.zip \
  --style '冰雪蓝白装甲，银色接缝，简洁卡通风格'

# 自动生图、按原轮廓替换、验证并下载。--output 会自动等待。
python3 tools/spine_agent.py run \
  --input /path/to/original-spine.zip \
  --style '冰雪蓝白装甲，银色接缝，简洁卡通风格' \
  --state /path/to/ice-task.json \
  --output /path/to/ice-spine.zip
```

`--input` 也支持含 JSON/SKEL、atlas、全部 PNG 的目录；支持相对路径、裁切、正交旋转、多页和显式 PMA。已有工作台项目可用 `--project PROJECT_ID` 代替 `--input`。

在其他局域网设备执行 CLI 时，所有命令附加 `--base http://服务器局域网IP:8780`。服务与任务记录仍保存在服务器上，输入文件由调用方发送，下载结果写到调用方设备。

默认选择全部 atlas 部件，一张部件对应一次生成，默认上限 32 张。可指定 `--regions r0 r1` 只处理部分部件；较大的资源需显式设置 `--max-generations 64` 等上限。计划只计算张数，不查询实时价格。实际生图按亿创账户计费。

同一风格任务重复执行时保留同一个 `--state` 文件，它记录提交前创建的任务编号；精确重复请求会返回已有任务。新风格使用新的 state 文件。等待默认最多 900 秒，超时不取消服务端任务，也不会重新生图：

```sh
python3 tools/spine_agent.py status --state /path/to/ice-task.json
python3 tools/spine_agent.py wait --state /path/to/ice-task.json --timeout 900
python3 tools/spine_agent.py download --state /path/to/ice-task.json --output /path/to/ice-spine.zip
```

## HTTP 调用

先调用 `POST /api/automation/reskin/plan`：

```json
{
  "project_id": "工作台已有项目的32位编号",
  "style": "深蓝银色科技装甲，少量琥珀色细节",
  "mode": "material",
  "max_generations": 32
}
```

响应包含 `parts[].id`、`parts[].prompt`、实际送给亿创的 `parts[].provider_input`、`generation_count`、`within_budget`、骨架版本和动作列表。提示词自动结合目标风格、部件名称、尺寸和原色统计；此版本采用规则模板，不包含另一次 LLM 或视觉语义识别调用。

调用 `POST /api/automation/reskin/tasks`，在同一请求中增加稳定的 `nonce`（推荐 UUID 的 32 位十六进制字符串）：

```json
{
  "nonce": "a3b7d084e12043c7942880abd728a4da",
  "project_id": "工作台已有项目的32位编号",
  "style": "深蓝银色科技装甲，少量琥珀色细节",
  "mode": "material",
  "region_ids": ["r0", "r1"],
  "max_generations": 2
}
```

也可将 `project_id` 替换为 `files`，无需预先导入：

```json
{
  "nonce": "a3b7d084e12043c7942880abd728a4da",
  "style": "深蓝银色科技装甲",
  "files": [{"name": "source.zip", "data": "ZIP文件的Base64，无data URI前缀"}]
}
```

服务返回 HTTP 202 和任务。按 `status_url` 每隔 5–10 秒查询，完成后下载 `download_url`；`preview_url` 可以打开新项目。URL 为本机服务内的相对地址。可用 `GET /api/automation/reskin/tasks` 找回任务；`POST .../{id}/cancel` 停止之后的提交，已交给亿创的任务仍可能完成。

| 状态 | 调用方操作 |
|---|---|
| `queued` / `generating` / `assembling` | 等待并查询同一任务 |
| `completed` | 下载 ZIP；校验响应中的 `result_sha256` |
| `needs_attention` | 读取 error 和 jobs；恢复只查询已有任务，不重复提交 |
| `failed` | 保留失败信息及已完成候选；不返回半套成品 |
| `cancelled` | 已停止后续自动处理 |

网络超时导致 `submission_unknown` 时，先在亿创核对，再通过 `POST .../{id}/resume` 绑定已存在的任务：

```json
{"generation_ids": {"本地job_id": "亿创已存在的gen_id"}}
```

查询达到上限时可以 `POST .../{id}/resume`，请求体 `{}`。不可把提交超时当作失败，换新 nonce 重新发送可能重复计费。服务重启会继续收集已有任务；已提交任务永不自动重新提交。未提交的 prepared 部件会继续执行。请只运行一个服务实例管理同一 storage。

## 两种生成路线

- `material` 为默认：亿创生成表面材质，结合原图明暗贴回部件，保留原暗色线条、alpha 和几何。因此擅长颜色、材质和表面风格改变；不能增加帽子、改变人物轮廓或完整重画面部。
- `reference`：每个部件提供公共 HTTPS 的 512×512 参考画布 URL，亿创按该画布重绘。下载画布：`GET /api/projects/{project_id}/reference?id={region_id}`。调用方自行上传到可访问的图片地址，再传 `reference_urls: {"r0": "https://..."}`；CLI 使用 `--mode reference --references urls.json`。本机地址、普通原图 URL 和 Base64 不能替代对应部件的参考画布。此版本不自动上传源素材。

新资源保持骨架 JSON/SKEL 与 atlas 字节一致，贴图尺寸和 alpha 不变，沿用原来的 mesh、权重、deform、IK 与动作。输出含原文件名资源、`_reskin-validation.json` 及 `_reskin-automation.json`（目标风格、自动提示词、任务编号和来源摘要）。导出不包含工作台亿创凭据。下载的文件是运行时资源包，不是新建的原生 `.spine` 工程。

## 验证

`tests/test_automation.py` 覆盖批量处理、幂等、重启、提交结果不明、取消、失败、源快照、手工编辑保护和原文件不变量。`tools/verify_automation_http.py` 用独立临时服务与模拟 provider 验证 HTTP/OpenAPI/CLI，不产生真实生图费用。真实亿创的单部件端到端样例及报告在 `evidence/automation/`；整套角色效果仍需逐动作检查。
