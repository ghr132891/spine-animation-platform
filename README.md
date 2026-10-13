# Spine AI 动画工作台

上传角色图片，输入动作要求，生成可预览、可下载的 Spine 动画；生成后可直接用文字继续修改。

**管理员部署一次，同事打开网页即可使用。** 网页使用者无需安装 Python、Codex 或登录亿创。已有平台地址的同事直接看下面的「日常使用」。

## 1. 下载代码

点击本页绿色 **Code → Download ZIP**，解压；在解压后的项目目录打开终端（应能看到 `app`、`web`、`requirements.txt`）。

也可以使用 Git：

```sh
git clone https://github.com/ghr132891/spine-animation-platform.git
cd spine-animation-platform
```

## 2. 安装并启动（管理员）

先安装 [Python 3.10 或以上](https://www.python.org/downloads/) 和 [Node.js LTS](https://nodejs.org/)。自动制作还需要服务端可用的 Codex 账号、亿创 API Key 与生图额度。

以下命令适用于 macOS / Linux；本项目完整流程目前在 macOS 验证。Windows 服务端部署尚未完整验收，命令差异见 [部署说明](docs/DEPLOYMENT.md)。

**首次安装：**

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python tools/package_spine_skills.py
npm install -g @openai/codex
codex login
codex login status
```

按登录提示完成授权。Codex 必须安装、登录在**运行平台的同一个系统用户**下；只登录浏览器不代表后台 CLI 已登录。安装与登录参考 [官方说明](https://learn.chatgpt.com/docs/codex/cli) 和 [认证说明](https://learn.chatgpt.com/docs/auth)。

**启动平台：**

```sh
.venv/bin/python app/server.py --host 0.0.0.0 --port 8780
```

打开 **[图片做动画](http://127.0.0.1:8780/auto.html)**。用上述命令运行时保持终端开启；按 `Ctrl+C` 停止。macOS 首次安装完成后，也可双击 `Start.command` 后台启动。

## 3. 首次配置（管理员，只需一次）

在服务器本机打开 **[连接设置](http://127.0.0.1:8780/settings.html)**：

1. 填写自己的亿创 API Key，点击「验证并保存」。新生图使用 **GPT Image 2.5 Flare**。
2. 配置参考图片通道，供亿创读取原图。简单试用可安装 `cloudflared` 后，在设置页展开「无需存储配置：临时 HTTPS 图片通道」，点击「启用临时图片通道」。安装命令、其他系统和长期存储配置见 [部署说明](docs/DEPLOYMENT.md#参考图片通道)。
3. 为这台新服务器确认图片发布授权。临时链接最长有效 1 小时，持有链接者可以读取对应图片；原作者的授权不会随仓库转移，具体设置见同一部署说明。

**网页能打开，只代表服务已启动。** 自动制作还需要 Codex 登录、亿创连接、参考图通道及授权均就绪。生成会使用管理员的账号额度；运行期间服务器需保持联网、开启且不休眠。

## 4. 日常使用

1. 打开 `http://服务器IP:8780/auto.html`，上传完整角色图片（PNG / JPG / WebP，最大 12 MB）。本机可用上面的图片做动画链接。
2. 输入需求，例如：**「制作待机和自然侧向行走，保留原角色外观，步幅小一些。」** 点击「生成动画」。
3. 在页面查看进度、阶段图片与动画。可以关闭网页，稍后从「最近制作」继续查看。
4. 想修改时，在「想怎么修改？」中输入：**「减小腿部弯曲，修复肩部接缝，其他保持不变。」** 点击「按要求修改」。当前网页微调复用已有素材，不额外生图，保留旧版。
5. 满意后点击「下载动画文件」。需要手动调骨骼时点击「调整细节」。

生成时会准备视角与部件、组装绑定、制作并检查待机／行走。**草稿可先预览下载，但不代表已通过美术验收**；请查看页面的问题说明。中断时优先点击「继续这次制作（复用已有素材）」，不要反复新建任务。

同事只需与服务器处于可互通的局域网或 VPN。请把 `服务器IP` 换成部署机器的真实地址；`127.0.0.1` 只代表当前电脑，`0.0.0.0` 不是访问地址。平台默认免令牌，仅用于可信网络，勿直接开放到公网。

## 可选：在自己的 Codex 里使用

纯网页使用无需此步骤。想在 Codex 对话中发图、制作和微调的人，在本机安装 Python 与 Codex CLI 后：

1. 打开 `http://服务器IP:8780/downloads/spine-animation-skill.zip`，下载并解压。
2. 在解压目录运行下列命令，把地址换成实际平台地址：

```sh
python3 spine-animation/scripts/install.py --base http://服务器IP:8780 --non-interactive
```

3. 重新打开 Codex 对话，附上图片并输入：**「$spine-animation 生成这张角色的待机和侧向行走动画。」** 安装器会注册 Skill 和 MCP；客户端不需要亿创密钥。

## 常见问题

| 情况 | 先检查 |
| --- | --- |
| 网页打不开 | 启动命令是否仍运行；同事访问需检查服务器 IP、防火墙的 8780 端口和网络互通。 |
| 页面有了，但自动制作失败 | 服务端运行 `codex login status`；确认 `node`、`codex` 可用，检查页面错误和 [排查说明](docs/DEPLOYMENT.md#排查与维护)。 |
| 参考图上传失败 | 检查图片通道是否已启用、cloudflared 是否可用、服务器外网和发布授权。 |
| 亿创失败或额度不足 | 在设置页检查 Key、账户额度及供应商返回原因；不要反复点生成。 |
| 下载后没有原来的角色和历史 | 正常。仓库只有代码，自己的素材与任务保存在 `storage/`，请备份该目录。 |

[详细部署与维护](docs/DEPLOYMENT.md) · [平台架构](docs/ARCHITECTURE.md) · [功能与开发记录](docs/DEVELOPMENT_HISTORY.md)

仓库包含后端、网页、Skill/MCP、测试和打包工具，不包含账号、用户素材或生成成果。导出主要为 Spine JSON、atlas 和 PNG，不生成原生 `.spine` 工程；预览使用 Spine 3.8 / 4.2 runtime，使用和分发须遵循 `web/runtime/` 中的许可。
