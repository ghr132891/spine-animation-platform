# 三视图与细节验收

只对新人物asset-sheet生成启用three-view-v1，旧任务不追溯修改。默认同批generate-parts sheets=["turnaround","character-assets"]：两张单独候选、同一当前source_image_id；首次三视图1张、完整部件图1张单独计入基础额度，不占max_generations=8。立绘、改视角、补图、基础图重抽及质量修订计入8张，最多合计10张。max_generations=0仍禁止所有生图。未知提交或审核拒绝停止新提交，沿用原任务查询。

turnaround为3列单行：正面、沿facing的严格侧面、背面，等高、脚底对齐、自然站姿。它是生成的设计推断，要先对照原参考检查，不当作未经验证的真值。character-assets仍是单一目标相机下的可裁切部件图。严禁导入turnaround作为部件或用它的多个人物当动画帧。

后台先spine_image view=candidate job_id=实际三视图任务，再spine_review_turnaround，参数id/job_id/phase=reference/verdict=ready或needs_adjustment或unsupported，checks完整包含identity、scale、arm_position、leg_alignment、hair_silhouette、costume_layers布尔值及reason。合格再查看部件，spine_review_parts并按真实像素裁片与pins导入。

组装后spine_image view=assembly；同样工具phase=assembly检查当前revision：肩窝与手臂是否接于正确位置，髋膝踝是否匹配比例和曲线，长后发在背后且不粘臂、前发连续；长裙自由下摆与腿片独立，不能重复裙条随膝折。相机不同不能逐像素对齐，按等高比例和相应视角的轮廓判断。ready须六项全部通过；变更图或组装使旧记录失效。已审查三视图原像素随rig保存为material_sources id=turnaround，后续微调可用spine_image view=material part=turnaround对照。

组装合格后每个最终动作做整身预览，再spine_closeup每个实际存在的head、torso、l-upper-arm、r-upper-arm、l-upper-leg、r-upper-leg及__hair__；各返回8张真实runtime局部图。每处spine_review_closeup必须带相同anchor和具体reason，分别记录。__hair__包括完整头发图面；腿部包括髋至鞋端、手臂包括肩至手端。编辑任何资源后旧结论失效。

三视图和数值都不能保证自然步态：还需idle8帧、walk32帧，检查膝弯方向/幅度、交替支撑、鞋底和层级；不接受用几乎不抬腿的小步隐藏穿插。可在预算内修裁片、pins、骨骼、权重、图层和曲线，复检后导出。已有部件确实烘焙了重叠衣片且原像素无法分离时，标记待补图，不编造修好。明确不生图或原完整部件图复用遵守用户要求，不强制新三视图。
