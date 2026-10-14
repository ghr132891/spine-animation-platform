# 标准部件生成对比

本机管理员可通过 `tools/parts_lab.py` 或 `/api/parts-lab/` 执行两条真实素材路线。接口及门禁详见 [技能参考](../skills/spine-animation/references/parts-comparison.md)。普通用户默认生成流程暂不切换到实验模型。

模板生成复用当前生图服务与参考图可用性检查；输入人物母版和模板板，保存唯一供应商回执。See-through 使用官方 Hugging Face 演示的公开 Gradio API，上传只包含选定任务图片，输入同一人物母版，输出实际 PSD；不需要把平台密钥交给第三方。演示返回可能受可用性或 GPU 配额限制，不能当作自建生产推理服务。

安装可选读取依赖：`python -m pip install -r requirements-parts-lab.txt`，或用 `--target storage/parts-lab-deps --no-deps` 独立安装 psd-tools、attrs、typing_extensions（复用平台已有 Pillow/numpy）。模板试拼需要 Node.js。自建 See-through GPU 服务尚未配置，当前适配器固定官方演示地址，不能宣称支持任意远程部署。

幂等与恢复：模板 POST 之前先保存 submission_unknown；只有取得回执才改 pending。中断只读原任务，失败不自动换模型。跨进程文件锁阻止 CLI 和网页同时重复提交。参考通道失败发生在生成前，可使用同一试验重试。See-through 同样保存事件编号，重连只读取原事件。

现阶段限制：模板参考图是软约束，没有 ControlNet/轮廓锁定。硬套现成网格可能把生成的新鞋裁坏。See-through 按语义分层，可能保留合并双腿与合并双鞋；透明图层数多不代表完整肢体已齐全。

检查包读取 PSD 的原绘制顺序；模板检查包使用官方 Spine runtime 的静态几何。五项视觉结论绑定贴图、模板几何和证据摘要；任何阻断项阻止 ready，素材通过不等于动画通过。

上游参考：
- https://github.com/shitagaki-lab/see-through
- https://github.com/QwenLM/Qwen-Image-Layered
- https://github.com/EsotericSoftware/spine-scripts

当前未内嵌上游模型代码或权重，未将用户角色图片加入发行包。

## 合腿结果的续修

保留原始试验结果，另建角色副本：复用 PSD 中的头脸、颈部、衣装背包和双臂，只针对缺失的独立腿鞋补图。`app/semantic_completion.py` 负责等比锚点定位、按原顺序组合、独立腿重复检查和恢复已有可见纹理；它不是自动补全模型。补图仍由获授权的生图工具提供，本次使用内置 imagegen，不能将这部分算作 See-through 原始输出。

可见纹理恢复只覆盖新部件内部已对齐的像素，完整隐藏轮廓保持不裁切；不带入另一条腿的悬空细条，不叠加位置不同的旧裤脚。保留真实裤腰作为覆盖层、完整颈片作为领口后的插接层。经 `install-canvas-layers` 导入并通过 `complete-assets-v1` 后再 build，新动作按最终资源重新检查。执行细节见 skill 的 See-through 局部续修章节。
