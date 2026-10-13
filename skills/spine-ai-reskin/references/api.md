# HTTP、参考图及恢复

所有路由相对于选定的工作台地址。本机默认 `http://127.0.0.1:8780`，局域网使用服务器 IP。服务默认监听 `0.0.0.0:8780`。POST 发送 JSON，附 `Content-Type: application/json` 和 `X-Spine-Local: 1`；该固定头是操作保护，不是身份凭据。亿创 Key 由服务器管理。

## 计划与提交

`POST /api/automation/reskin/plan` 不提交图片；返回 `parts[].id/name/prompt/provider_input`、骨架版本和动作列表、生成张数与上限。

```json
{
  "project_id": "已有项目的32位十六进制编号",
  "style": "深蓝银色科技装甲，少量琥珀色细节",
  "mode": "material",
  "region_ids": ["r0", "r1"],
  "max_generations": 2
}
```

编号从实际项目或 plan 获取。`GET /api/projects` 查项目，`GET /api/projects/{id}` 查摘要和 `regions`。`region_ids` 省略时选择全部部件，必须非空且无重复。

`POST /api/automation/reskin/tasks` 使用同一类请求，增加调用方持久保存的 `nonce`。推荐 `uuid.uuid4().hex`。相同 nonce 和精确相同请求复用已有任务；同一 nonce 的参数变化会被拒绝。

也可不用已有项目，传资源文件：

```json
{
  "nonce": "调用方生成并持久保存的32位十六进制编号",
  "style": "深蓝银色科技装甲",
  "files": [{"name": "source.zip", "data": "ZIP字节的Base64，无data URI前缀"}]
}
```

`project_id` 与 `files` 二选一。可选 `revision` 锁定已有项目修订。`max_generations` 默认 32，可配置 1–128；超过所选上限时提交被拒绝，不产生生图。提示词不是额外的 LLM 调用，按部件与风格模板构造。

提交返回 HTTP 202，包括任务 `id`、`state`、`progress`、`status_url`、`output_project_id`、`preview_url`。完整字段见 [openapi.json](openapi.json)。

## 查询、下载与取消

| 请求 | 行为 |
|---|---|
| `GET /api/automation/reskin/tasks` | 找回已有任务 |
| `GET /api/automation/reskin/tasks/{id}` | 查询任务与部件状态 |
| `GET /api/automation/reskin/tasks/{id}/download` | 仅 completed 时下载 ZIP |
| `POST /api/automation/reskin/tasks/{id}/cancel`，body `{}` | 停止后续自动处理，已提交的远端生成可能仍完成 |
| `POST /api/automation/reskin/tasks/{id}/resume` | 查询恢复或绑定已存在生图编号 |

按 5–10 秒查询一次，遵循执行环境的单次等待限制，并设置总等待预算。达到预算时交付持久任务编号、当前进度和恢复方法；不要把它当作生成失败而再次提交。

`completed` 响应有 `download_url`、`result_sha256` 和 `validation`。URL 是服务内相对路径，使用正常 URL 合并得到完整地址，不能把局域网调用链接强制改回 localhost。下载后校验 SHA-256。

CLI 退出码：`0` 表示调用成功（任务可能还在运行），`2` 表示 failed/needs_attention/cancelled，`1` 表示调用或本地写入失败。不能仅凭退出码 0 宣称任务完成，须检查 JSON `state`。

state 文件记录任务 ID、请求摘要和服务地址。同一请求始终复用同一文件；其他请求或服务不能覆盖它。服务器 IP 变更时，先以原任务 `--task ID --base 新地址` 查询，不创建新任务。

## 参考图模式

默认材质模式不要求上传原图。`reference` 要求每个所选部件提供对应的公共 HTTPS 参考画布：

1. 从 `GET /api/projects/{id}/reference?id={region_id}` 下载服务生成的 512×512 画布。
2. 优先复用本次任务保存且与原画布匹配的 HTTPS 链接。缺失时由平台管理员或已有素材发布流程准备这份画布的链接与对应映射；普通使用者不需登录亿创，也不应被要求手工复制链接。`blob:`、本机 URL、Base64 和单张整人物图不能替代部件画布。
3. 将真实部件与链接映射存为 JSON：`{"r0":"https://example.com/r0-reference.png"}`，使用 `mode: reference` 与 `reference_urls`；CLI 为 `--mode reference --references /path/to/urls.json`。源贴图／修订变化后重新核对画布，不能复用不匹配的旧链接。

1.7.0 的 S3 兼容存储发布器自动服务于单图角色的补视角／部件，尚未接入 AutoReskin 的各部件画布。此处的 `reference_urls` 前提仍然存在；缺失时明确说明需要管理员准备部件链接，不提交缺少参考图的生图，不换其他生成服务，也不重复付费生成图片来获取链接。角色的 `spine_reference(kind="character", action="ensure")` 缓存不能替代这里每个部件的参考画布。不能宣称仅发一套 Spine 就已实现所有 reference 换皮前处理自动化。

使用原画布布局才能正确裁回原部件。此路线仍保持原 alpha 与几何，不能通过换皮增加新轮廓或骨架。只有原生 `.spine` 工程时，先导出运行时 JSON/SKEL、atlas、PNG。

## needs_attention

读取 `error` 及每个 job 的 `status/gen_id/poll_error`。

- 查询达到上限：`POST .../{id}/resume`，body `{}`，只查询已有任务。
- `submission_unknown`：在亿创核对是否已有生成记录，取得真实 `gen_id`；未核实前不重发。绑定的是本地 job_id，不能用 region_id 或任务 id 替代。

```json
{"generation_ids": {"本地job_id": "已存在的亿创gen_id"}}
```

CLI 保存这份映射，再运行：

```sh
python3 "$SPINE_SKILL_DIR/scripts/spine_agent.py" resume \
  --base "$SPINE_BASE" --state "/path/to/output/ice-task.json" \
  --generation-ids "/path/to/existing-generations.json"
```

`failed` 任务不会自动重新生成；已完成候选保留在结果项目中。输出项目被手工编辑时停止自动装配，不覆盖手工修改。需要重做时遵循用户授权范围建立新任务。一个 storage 由一个服务实例管理。

## 结果与边界

ZIP 保留源文件名、骨架 JSON/SKEL 与 atlas 的字节，PNG 尺寸和 alpha 不变；附 `_reskin-validation.json`、`_reskin-automation.json`（风格、提示词、生成编号及来源摘要）。有同名附加报告时服务选择其他报告文件名，不能覆盖原资源。导出不包含服务器亿创凭据。

这是一套运行时资源，不是原生 `.spine` 工程。不能把文件不变量校验等同于视觉验收：材质、参考重绘的细节与整体一致性仍需播放动作检查。工作台预览提供 3.8/4.2 runtime；其他版本在匹配的引擎检查。
