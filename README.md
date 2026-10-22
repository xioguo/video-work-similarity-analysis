# 视频作品相似度分析技能

面向视频作品审核的中文技能：对照两条或多条作品，结合完整口播、叙述结构、文字含义和抽帧画面，生成可回放、可复核的证据报告。

## 分析内容

| 维度 | 默认权重 | 比较内容 |
| --- | ---: | --- |
| 叙述节拍 | 30% | 完整叙述动作及其顺序、语义对应 |
| 表达话术 | 30% | 参与比较的表达动作复现数量 |
| 表达文字 | 20% | 语义相同的文字篇幅覆盖；换词但含义基本相同也计入 |
| 素材像素 | 20% | 抽帧后统一尺寸，比较像素并核查候选片段 |
| 独创叙事设定 | 不加权 | 单独核查概念映射及更早来源 |

综合公式采用“四项加权v2.1”。默认综合分超过60%触发内部审核认定条件；这是可配置的业务规则，并非经过统计校准的抄袭概率。结论仍需核查来源、公共素材、统一脚本及具体证据。

通用话术、推广行动、必然相同的操作步骤按规则标注或剔除。纯字符重合率另列辅助，不能代替含义比较。相同壁纸或软件白窗不等于直接搬运录屏。

## 安装技能

把本仓库克隆到你的 Codex 技能目录，文件夹名称使用 `video-plagiarism-check`：

```bash
git clone https://github.com/xioguo/video-work-similarity-analysis.git ~/.codex/skills/video-plagiarism-check
```

若该目录已经存在，请先检查并选择合并方式，不要直接覆盖已有自定义内容。其他支持 `SKILL.md` 的工具可按各自技能目录规则安装。

安装后，在新对话中提供两条作品链接或本地视频，明确希望比较的范围，并调用 `video-plagiarism-check`。

示例：

> 按视频作品相似度分析技能，对比下面两条视频。完整核查口播，生成带时间码的文字与素材对照，并给出四项分值、权重和综合结果。

## 文件结构

```text
SKILL.md
references/
  material-analysis.md
  semantic-text-coverage.md
scripts/
  frame_similarity.py
```

- `SKILL.md`：取证、分段、语义比较、素材核查和报告规范。
- `references/material-analysis.md`：抽帧、像素计算与连续素材核查。
- `references/semantic-text-coverage.md`：同义改写覆盖标注与去重方法。
- `scripts/frame_similarity.py`：对已有抽帧进行像素与外观筛查，并聚合外部已核实的语义分项。

## 运行抽帧比较脚本

需要 Python 3.9 或更新版本及 Pillow 图像库：

```bash
python3 -m pip install -r requirements.txt
python3 scripts/frame_similarity.py \
  --a-frames /path/to/A/frames \
  --b-frames /path/to/B/frames \
  --output /path/to/result.json \
  --interval-a 5 --interval-b 5
```

输入目录只放对应视频的采样帧，不混入封面、网格或对照图。脚本默认全图统一为320×180 RGB，使用 Lanczos 重采样；对每张B帧匹配最佳A帧，再汇总像素分。匹配方向不可省略，B→A与A→B不等价。

需要合成综合分时，将经过证据核实的节拍、话术、语义文字三项百分比传入，像素项由脚本现场计算：

```bash
python3 scripts/frame_similarity.py \
  --a-frames /path/to/A/frames \
  --b-frames /path/to/B/frames \
  --output /path/to/result.json \
  --weights 30,30,20,20 \
  --component-scores 40,60,55
```

以上分数仅为命令格式示例，不代表任何真实作品。脚本不自动判断语义，不下载视频，不执行语音转写；完整分析还需要可用的视频获取、音频转写和人工证据复核能力。

## 交付结果

- 结论报告：开场综合分、各项分值与权重、判定理由和待核实事项。
- HTML证据页：带时间码的原话对照、标色说明、素材并列图与回放定位。
- 量化记录：人工标注、复算脚本及JSON结果。

## 计算限制

人工分段和豁免分类可能影响分母，应公开口径并在临界案件展示替代口径的影响。抽帧可能漏掉短片段；颜色相近不证明素材身份，字幕不同也不能证明独立录制。没有证据支持的原创归属或复用时长应保留为待核实。

本仓库仅包含技能方法和通用脚本，不包含案例视频、账号凭据、实际审核材料或本机配置。
