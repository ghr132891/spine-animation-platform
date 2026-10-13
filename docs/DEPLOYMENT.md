# 部署与交付

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

在 `/settings.html` 配置亿创凭据，默认新生图模型为 GPT Image 2.5 Flare。凭据保存在被 Git 排除的 storage 目录，不写入源代码。已有素材微调可以使用零生图额度。

图生图需要亿创可读取的 HTTPS 参考图片。平台支持配置对象存储，或启用隔离的 Cloudflare 临时图片通道。参考发布配置见 `skills/spine-animation/references/image-preparation.md`；macOS ARM 可用 `tools/install_cloudflared.py` 安装固定版本客户端，其他系统使用对应的官方客户端。

每个新部署必须自行配置参考图发布方式及其授权。本仓库不携带原服务器的图片公开授权、登录会话或密钥。

平台默认面向可信局域网，Agent API 免令牌。需要访问控制时，配置 `SPINE_AGENT_REQUIRE_TOKEN=1`、`SPINE_WEB_REQUIRE_CODE=1`；不要把默认免令牌服务直接暴露到公网。更改环境变量后重启服务。

## Skill/MCP 与日常使用

`python tools/package_spine_skills.py` 从源码构建安装包到 `web/downloads/`。Skill 源码位于 `skills/spine-animation/` 与 `skills/spine-ai-reskin/`，MCP 桥接位于 `agent/spine_mcp.py`。安装方式见各 Skill 及 `agent/spine-animation-distribution/安装说明.txt`。

网页上传图片和需求后，后台自动创建独立副本。网页微调默认不生图，自动修复默认4轮（首次编辑另计），检查和交付不计编辑轮数。可用草稿自动挂回原记录；未验收通过不能标成完成。同一次中断使用继续制作，避免重复任务。

## 检查与备份

```sh
python -m unittest discover -s tests -p 'test_*.py'
```

上述测试包含需要 Node 的运行时检查；浏览器脚本见 tools/verify_*.cjs，部分历史实测脚本有原作者机器路径，不作为通用部署步骤。不要运行生图 smoke 脚本来代替离线测试，它们可能调用付费服务。

`storage/` 是运行数据根目录，应由管理员另行备份和控制访问。Git 仅管理源码，拉取仓库不会恢复原服务器的素材、任务、生成历史和账号。官方 Spine runtime 的许可保留在 `web/runtime/*/LICENSE`，部署与分发应遵循其许可。
