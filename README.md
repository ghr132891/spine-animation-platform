# Spine AI 动画工作台

当前发布：**1.11.1**。[本版遮挡修复与GitHub研究](docs/GITHUB_OCCLUSION_RESEARCH.md) · [1.11能力与升级说明](docs/RELEASE_1.11.0.md)。网页页脚显示实际运行版本和提交号；更新代码后须重启服务。

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

先安装 [Python 3.10 或以上](https://www.python.org/downloads/) 和 [Node.js LTS](https://nodejs.org/)。自动制作还需要服务端可用的 Codex 账号，以及所选生图服务的 API Key 与额度。

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

在服务器本机打开 **[连接设置](http://127.0.0.1:8780/settings.html)**，下面两种方式**任选一种**，不用修改代码。此设置由管理员统一配置，同事无需各自填写密钥。

### 方式 A：使用其他生图 API

适用于支持 **OpenAI Images 兼容接口**的服务，包括提供 GPT Image / image2 等模型的供应商。选择服务时，确认它同时支持文字生图和参考图编辑；只有聊天接口的服务暂不能直接使用。

1. 在「接口类型」选择 **OpenAI Images 兼容 API**。
2. 按下表填写；尺寸保持「模型自动选择」，「兼容选项」先不改。

| 页面字段 | 怎么填 |
| --- | --- |
| API 根地址 | 服务商提供的 HTTPS API 地址。例如 OpenAI 官方为 `https://api.openai.com/v1`；第三方填它自己的地址，不能填网站首页或完整的 `/images/edits` 地址。 |
| API Key / 登录 Token | 上面同一家服务商提供的 API Key，账号需要有可用额度。 |
| 模型 ID | 服务商文档中的实际模型 ID，例如 `gpt-image-2`；网页显示的「image2」不一定就是模型 ID。 |

3. 点击 **保存 API 配置（不生图）**，返回「图片做动画」，上传角色图并输入动作要求。

**此方式无需配置图片存储或临时 HTTPS 通道**，平台直接把参考图文件上传到所选 API。保存只检查配置格式；首次生成成功才说明服务实际可用，生成按供应商规则计费。其他协议的接入范围和常见错误见 [生图 API 配置与排查](docs/IMAGE_PROVIDERS.md)。

### 方式 B：使用亿创

1. 在「接口类型」选择 **亿创（原有服务）**，填写亿创 Key，点击「保存亿创配置」，默认使用 GPT Image 2.5 Flare。
2. 配置 HTTPS 参考图片通道：简单试用可安装 `cloudflared`，再在设置页展开「无需存储配置：临时 HTTPS 图片通道」，点击「启用临时图片通道」。安装命令及长期存储方案见 [参考图片通道](docs/DEPLOYMENT.md#参考图片通道)。
3. 按同一说明为新服务器配置图片发布授权。临时链接最长有效 1 小时，持有链接者可读取对应图片；原作者的授权不会随仓库转移。

两种方式配置完成后，日常使用步骤完全相同。以后需要换服务，回到「连接设置」重新选择并保存；更换 API 地址时需重新填写对应密钥，已提交的生图请求仍使用原服务。

**网页能打开，只代表服务已启动。** 自动制作还需要 Codex 登录和生图服务可用；采用 HTTPS 参考图时还需通道及授权就绪。生成会使用管理员的账号额度；运行期间服务器需保持联网、开启且不休眠。

## 4. 日常使用

1. 打开 `http://服务器IP:8780/auto.html`，上传完整角色图片（PNG / JPG / WebP，最大 12 MB）。本机可用上面的图片做动画链接。
2. 输入需求，例如：**「制作待机和自然侧向行走，保留原角色外观，步幅小一些。」** 点击「生成动画」。
3. 在页面查看进度、阶段图片与动画。可以关闭网页，稍后从「最近制作」继续查看。
4. 想修改时，在「想怎么修改？」中输入：**「减小腿部弯曲，修复肩部接缝，其他保持不变。」** 点击「按要求修改」。当前网页微调复用已有素材，不额外生图，保留旧版。
5. 满意后点击「下载动画文件」。需要手动调骨骼时点击「调整细节」。

生成时会准备视角与部件、组装绑定、制作并检查待机／行走。**草稿可先预览下载，但不代表已通过美术验收**；请查看页面的问题说明。固定阶段失败时先在「阶段耗时与失败续作」中仅重试该阶段；模型处理才需要点击「继续这次制作（复用已有素材）」，不要反复新建任务。

同事只需与服务器处于可互通的局域网或 VPN。请把 `服务器IP` 换成部署机器的真实地址；`127.0.0.1` 只代表当前电脑，`0.0.0.0` 不是访问地址。平台默认免令牌，仅用于可信网络，勿直接开放到公网。

## 可选：在自己的 Codex 里使用

纯网页使用无需此步骤。想在 Codex 对话中发图、制作和微调的人，在本机安装 Python 与 Codex CLI 后：

1. 打开 `http://服务器IP:8780/downloads/spine-animation-skill.zip`，下载并解压。
2. 在解压目录运行下列命令，把地址换成实际平台地址：

```sh
python3 spine-animation/scripts/install.py --base http://服务器IP:8780 --non-interactive
```

3. 重新打开 Codex 对话，附上图片并输入：**「$spine-animation 生成这张角色的待机和侧向行走动画。」** 安装器会注册 Skill 和 MCP；客户端不需要生图服务密钥。

## 常见问题

| 情况 | 先检查 |
| --- | --- |
| 网页打不开 | 启动命令是否仍运行；同事访问需检查服务器 IP、防火墙的 8780 端口和网络互通。 |
| 页面有了，但自动制作失败 | 服务端运行 `codex login status`；确认 `node`、`codex` 可用，检查页面错误和 [排查说明](docs/DEPLOYMENT.md#排查与维护)。 |
| 参考图上传失败 | 亿创：检查图片通道和发布授权。兼容 API：确认供应商支持参考图编辑 `/images/edits`，查看 [API 排查](docs/IMAGE_PROVIDERS.md#常见问题)。 |
| 生图失败或额度不足 | 在设置页检查接口类型、地址、模型 ID、Key 和供应商返回原因；不要反复点生成。 |
| 下载后没有原来的角色和历史 | 正常。仓库只有代码，自己的素材与任务保存在 `storage/`，请备份该目录。 |

[详细部署与维护](docs/DEPLOYMENT.md) · [平台架构](docs/ARCHITECTURE.md) · [功能与开发记录](docs/DEVELOPMENT_HISTORY.md)

仓库包含后端、网页、Skill/MCP、测试和打包工具，不包含账号、用户素材或生成成果。导出主要为 Spine JSON、atlas 和 PNG，不生成原生 `.spine` 工程；预览使用 Spine 3.8 / 4.2 runtime，使用和分发须遵循 `web/runtime/` 中的许可。

### 并发与耗时

服务端默认最多2个后台助手同时工作，可用 `SPINE_AGENT_CONCURRENCY=1` 降为串行，或设置为最多4。每项任务的素材准备、视觉工具、绑定、压力测试和动态复检分别记录耗时与错误；不要仅凭缓存命中耗时判断首次生成速度。先观察服务器与模型额度，再调整并发。
