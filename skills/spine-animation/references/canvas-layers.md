# 完整部件、同坐标和重叠验收

先准备完整衣装、成对完整肢体及配件，再按同一母版坐标组装。可见像素归属不是完整部件的裁切边界。衣领/裤腰图案只归主衣装，但袖根、裤装插接段可在隐藏区域与主衣装重叠。背包先补完整袖后包体，不能把可见残片当完整背包。

## 导入

已有完整PNG：install-canvas-layers，保留母版整数offset、关节pins、owns和draw_order，不按格子尺寸逐片缩放。

母版加完整补图：partition-canvas 每层传 polygon（或同尺寸mask PNG Base64）定义可见归属，另传 completed_image（同母版尺寸完整RGBA PNG Base64）定义真实完整轮廓。平台保留该层可见原像素，其余完整轮廓保留，不再被归属蒙版裁掉。不能同时传旧fill_image。旧fill_image仅用于局部遮挡补片，不是完整部件的替代。缺真实补图时complete=false并交付草稿，不能依靠复制手掌轮廓或拉伸裤纹假补全。

绘制顺序由实际遮挡确定：通常背包、远侧肢体、主衣装、近侧肢体、头/前发。前肩带属于前层，不能和后包体一起压到背后。先查看各独立部件，再看合成。

## 两道验收

1. reconstruction / reconstruction-image：检查静态拼回与母版；review-reconstruction 记录当前digest与四项checks。
2. layer-overlap / layer-overlap-image：平台生成原位、双向摆臂、双向分腿五个诊断姿态，并报告四处关节局部重叠像素。review-overlap 提交当前 revision、digest、verdict=ready/needs_repair、reason，以及 checks={complete_silhouettes,joint_overlap,draw_order,spread_no_holes} 四项布尔结论。

正式build要求两道验收均通过、部件complete、关节无零重叠；draft=true保留问题草稿。摆开图是绑定前的刚性旋转诊断，不是最终动画：之后仍要用实际骨架检查待机8帧、行走32帧及局部极值。任何数字不能自动证明自然步态、合理遮挡或隐藏画面完整。图层、坐标、关节点和顺序变化后旧digest失效。

## 已知反例

绿马甲静态拼回误差为零，摆开仍暴露腿根截断与袖后缺图，双肩局部重叠为零。新检查应阻止这种素材正式构建，而不是把拼回分数当成合格。肤色阈值遗漏抗锯齿像素、沿手形填裤料、反复挪骨骼，都不能代替连续裤腰—大腿图面。

平台尚未安装自动语义分割/遮挡补全模型。生图仍走配置服务，按真实候选图审查。参考实现与边界见仓库 docs/LAYER_COMPLETION.md。
