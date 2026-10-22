# 视频作品相似度分析技能

对比两条视频的完整口播、叙述结构和画面素材，生成带时间、逐句引文和并列图的中文报告。

**当前技能v2.2；内容算法content-v3；执行协议workflow-v1。** 固定综合计算、抽帧复核与报告版式；页头显示双方完整标题和本次实际模型ID。相同已核标注得到相同计算结果与报告；模型对语义和来源的初判仍需要复核。

## 安装

下载整个仓库，将其放入目标工具的技能目录，并将文件夹命名为`video-plagiarism-check`。Codex通常使用`~/.codex/skills/video-plagiarism-check`；其他工具使用自己的技能目录。不要仅复制SKILL.md：scripts、references、templates、examples都要一起安装。重启/重新加载目标工具使其发现技能。不同工具副本应核对v2.2，不能继续调用旧四维自动裁定版。

图像/视频依赖：`python3 -m pip install -r requirements.txt`。内容计算与报告结构使用Python标准库；画面需要Pillow和ffmpeg，imageio-ffmpeg可提供二进制。平台视频获取及全量语音转写使用当前环境可用的工具，本技能不含凭据、不捆绑付费服务、不假定某个转写服务一定存在。

## 如何使用

给模型两条视频链接或本地文件，说：“使用video-plagiarism-check完整分析，按workflow-v1交付”。模型必须读取[技能入口](SKILL.md)，实际执行取证、标注、画面复核及统一报告生成。

```bash
python3 scripts/run_case.py init --case-id SP-YYYYMMDD-001 --output /案件目录/案件.json
# 补齐真实证据和标注；字段见references/execution-contract.md
python3 scripts/run_case.py build --case /案件目录/案件.json
```

统一入口校验完整转写/引文、视频和帧文件指纹、全长采样、固定候选连续复核、来源记录，再从实际输入计算，生成：

- score.json：双向内容分及固定权重；缺证据保留N/A。
- 交付核验.json：complete/partial/blocked、缺失项、证据及工具指纹。
- 分析摘要_案件号.md：摘要和指标。
- 证据对照_案件号.html：固定七段报告，含逐项原话、结构链、命中区间和画面对照。

退出0才表示必交项完成；退出2表示部分/受阻交付，不能删掉缺失项后宣告完成。协议/输入错误则修正后重跑。

## 评分与展示

每方向内容分＝结构37.5%＋语义动作37.5%＋语义文字25%；首页综合为两个方向的均值。含义相同但字符不同也可计入，按实际证据标注；纯字符序列分不替代语义。

全部有效口播层包括表达、推广、步骤、事实；个性表达与来源归属另核。像素和图像哈希只用于筛查画面候选，不加入内容综合，未确认素材复用不能写0%。

60%保留为用户认可的内部参考阈值，写入交付核验配置。报告仅展示综合相似度、分项和证据对照，不输出是否抄袭、是否侵权或复制证据等级，也不展示跨线判定。不得自行构造40/30/20/10或50/50两套主/严口径。旧案旧数字保留当时定义，不换标签冒充新口径。

## 模型与作品标题

案件登记双方完整原始title，以及analysis_model的真实ID和来源。来源runtime须来自实际运行环境；user_declared为用户提供、尚未核实；没有真实ID时显示未获取，不猜测。多模型协作另外记录ID与角色。模型和标题同时出现在HTML、Markdown和交付核验中。

可选preview_video绑定用户允许展示的本地预览，回放按钮按证据时点定位；不自动把原视频当可公开预览。

## 文件与验证

- SKILL.md：唯一任务入口与必交约定。
- references/execution-contract.md：固定字段、分段/同义规则、候选选择、展示规则。
- references/evidence-acquisition.md：证据获取、缺工具处理与来源核验。
- references/semantic-text-coverage.md、material-analysis.md：语义位置和素材方法。
- scripts/run_case.py：统一计算、交付检查和报告生成。
- scripts/extract_frames.py：本地视频全长/连续窗口抽帧、时间清单和全部网格页。
- scripts/score_content.py、frame_similarity.py：确定性内容计算与图像筛查。
- templates/report.html：从易读案例提炼的深蓝页头、综合分概览、左右高亮原话卡片、素材对照、回放定位和折叠附录。
- examples/：可直接运行的明确构造样例。
- tests/：完整本地视频流程、缺证据、空标注、自选权重、篡改帧、重跑一致性测试。

```bash
python3 -m unittest discover -s tests -v
python3 scripts/score_content.py --self-test
python3 scripts/run_case.py build --case examples/案件.json --output /tmp/视频技能构造示例
```

最后一个构造示例预期退出2：内容可计算、视频未提供，不能宣告完整完成。测试覆盖机械行为，不代表已经跨所有模型验证语义一致性或法律判定准确率。

案件视频、报告、个人路径、账号凭据及真实标注不放进公共技能包。对外公开需要用户另行授权。
