# 部署与交付

[返回首页简明教程](../README.md)。本文供管理员配置和排查，普通使用者只需网页地址。

## 环境与启动

- Python 3.10+，依赖见根目录 requirements.txt。
- Node.js：用于官方 Spine runtime 采样、接地诊断和导出检查；安装后确保 `node` 在 PATH 中，或设置 `SPINE_AGENT_NODE`。
- Codex CLI：启用后台自动制作时，在服务端安装并完成账号登录，确保 `codex` 在 PATH 中，或设置 `SPINE_AGENT_CODEX`。网页使用者不需要安装 Codex。

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python tools/package_spine_skills.py
python app/server.py --host 0.0.0.0 --port 8780
```

浏览器打开 `http://127.0.0.1:8780/auto.html`。局域网使用服务器实际 IP。macOS 可双击 Start.command（优先使用 .venv）；前台启动需要保持进程运行。可选 macOS 登录服务：使用目标 Python 执行 `agent/install_service.py --install`，会生成本机路径的 launchd 配置。

## 服务端配置

在服务器本机打开 `http://127.0.0.1:8780/settings.html` 选择生图服务。首次配置直接按 [首页的两种配置方式](../README.md#3-首次配置管理员只需一次) 操作，无需修改代码。默认保留亿创 GPT Image 2.5 Flare；也可配置 OpenAI Images 兼容 API 的地址、密钥与模型（例如 `gpt-image-2`），详见 [生图服务配置](IMAGE_PROVIDERS.md)。凭据保存在被 Git 排除的 storage 目录，不写入源代码。已有素材微调可以使用零生图额度。

亿创图生图需要可读取的 HTTPS 参考图片；OpenAI Images 兼容 API 直接上传参考 PNG，可跳过下文的图片通道和公开链接授权。平台支持配置对象存储，或启用隔离的 Cloudflare 临时图片通道。参考发布配置见 `skills/spine-animation/references/image-preparation.md`；macOS ARM 可用 `tools/install_cloudflared.py` 安装固定版本客户端，其他系统使用对应的官方客户端。

使用亿创的新部署必须自行配置参考图发布方式及其授权；兼容 API 直接上传文件，无需配置公开链接。本仓库不携带原服务器的图片公开授权、登录会话或密钥。

## 参考图片通道

**仅使用亿创时需要本节。选择 OpenAI Images 兼容 API 可整节跳过。**

在项目目录打开另一个终端。Apple Silicon Mac 可运行：

```sh
.venv/bin/python tools/install_cloudflared.py
```

安装器下载并校验固定版本的官方客户端，保存到 `tools/bin/cloudflared`。然后在**服务器本机**打开 `http://127.0.0.1:8780/settings.html`，展开「无需存储配置：临时 HTTPS 图片通道」，点击「启用临时图片通道」。

其他系统从 [Cloudflare 官方下载页](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/) 安装对应客户端；在启动平台的终端设置 `SPINE_CLOUDFLARED_BINARY` 为其可执行文件的绝对路径，再启动平台。长期使用也可以在设置页配置 S3 兼容存储，填写端点、桶、区域和密钥并验证保存。两种方式任选一种。

临时通道只发布本次任务的参考 PNG，不发布整个工作台；链接最长有效 1 小时，持有链接者在有效期内可读取图片。它依赖服务器与外网连接，不提供长期在线保证。

**新部署的管理员需自行取得任务图片的发布授权。** 若明确同意后续提交的任务图片及必要派生参考图按上述范围提供给亿创，可在本机新建 `storage/reference-publication-policy.json`，内容如下；未授权时不要创建此文件：

```json
{
  "authorized": true,
  "scope": "submitted-task-images",
  "provider": "quick-tunnel",
  "purpose": "matrix-reference-generation",
  "max_url_ttl": 3600
}
```

此记录仅适用于该部署的临时通道，不授权其他文件、永久公开或额外付费生成。撤销时把 `authorized` 改为 `false`，并在设置页关闭通道。未记录授权时后台可能无法自动完成需要公开参考图的步骤；请先配置再提交。S3 的图片共享范围需由管理员另行确认。

## 网络访问

平台默认面向可信局域网，Agent API 免令牌。需要访问控制时，配置 `SPINE_AGENT_REQUIRE_TOKEN=1`、`SPINE_WEB_REQUIRE_CODE=1`；不要把默认免令牌服务直接暴露到公网。更改环境变量后重启服务。

## Windows 命令差异

本项目完整生产流程目前在 macOS 验证，尚未完成 Windows 服务端验收。Windows Python 安装完成后可在项目目录的 PowerShell 使用以下命令；无需修改脚本执行策略或激活虚拟环境：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe tools/package_spine_skills.py
npm install -g @openai/codex
codex login
codex login status
.\.venv\Scripts\python.exe app/server.py --host 0.0.0.0 --port 8780
```

仍需按首页选择并配置一种生图服务；仅亿创需要额外的图片通道和公开链接授权。`Start.command` 与 launchd 安装器仅适用于 macOS。Windows 客户端安装 Skill 时，将首页安装命令的 `python3` 改为 `py -3`。

## Skill/MCP 与日常使用

`python tools/package_spine_skills.py` 从源码构建安装包到 `web/downloads/`。Skill 源码位于 `skills/spine-animation/` 与 `skills/spine-ai-reskin/`，MCP 桥接位于 `agent/spine_mcp.py`。安装方式见各 Skill 及 `agent/spine-animation-distribution/安装说明.txt`。

网页上传图片和需求后，后台自动创建独立副本。网页微调默认不生图，自动修复默认4轮（首次编辑另计），检查和交付不计编辑轮数。可用草稿自动挂回原记录；未验收通过不能标成完成。同一次中断使用继续制作，避免重复任务。

## 排查与维护

先打开 `http://127.0.0.1:8780/api/health`，返回 `ok: true` 说明 HTTP 服务可用；它不代表生图或后台 Codex 已验证。

- 后台找不到程序：在启动服务的同一个终端检查 `node --version`、`codex --version`、`codex login status`。必要时设置 `SPINE_AGENT_NODE`、`SPINE_AGENT_CODEX` 为绝对路径后重启服务。
- CLI 参数报错：当前后台使用 `codex exec --ignore-user-config`、`--output-schema` 等参数。通过 `codex exec --help` 检查兼容性；原环境使用 `0.162.0-alpha.17.2`，其他发行版需验证，不能以登录成功替代执行兼容性检查。
- 服务启动错误：前台运行看终端输出；`Start.command` 后台运行看 `storage/server.log`。制作失败可结合任务对应的 `storage/agent-tasks/<任务ID>/stderr.log` 排查，不要公开日志中的敏感内容。
- 更新代码：等任务结束，备份 `storage/`，停止旧服务；Git 安装执行 `git pull --ff-only`，再重新安装 requirements、运行 `tools/package_spine_skills.py` 并重启。ZIP 安装建议解压到新目录，停止旧服务后迁移自己的 `storage/`，按首页重新安装启动。

可选离线检查（不会提交新生图）：

```sh
python -m unittest discover -s tests -p 'test_*.py'
```

上述测试包含需要 Node 的运行时检查；浏览器脚本见 tools/verify_*.cjs，部分历史实测脚本有原作者机器路径，不作为通用部署步骤。不要运行生图 smoke 脚本来代替离线测试，它们可能调用付费服务。

`storage/` 是运行数据根目录，应由管理员另行备份和控制访问。Git 仅管理源码，拉取仓库不会恢复原服务器的素材、任务、生成历史和账号。官方 Spine runtime 的许可保留在 `web/runtime/*/LICENSE`，部署与分发应遵循其许可。
