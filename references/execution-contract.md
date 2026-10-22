# 案件执行协议 workflow-v1

## 固定版本与输入

skill v2.2仅升级执行一致性，内容算法仍为content-v3，不以某个既往案件分数作预设答案。`run_case.py init`生成完整案件骨架；逐字段补真实材料。JSON路径相对案件文件所在目录解析，允许合法绝对路径。不能把示例数据当真实分析。以下对象是唯一输入，不另外手填综合分、权重或阈值。

| 字段 | 要求 |
|---|---|
| schema_version/workflow_version/method_version | case-v1 / workflow-v1 / content-v3 |
| case_id/dataset_type | 安全案件号；real真实或synthetic构造 |
| works.A、works.B | 完整title/url/published_at/video/preview_video/duration_seconds/transcript/transcript_complete/frames_manifest/cover/cover_unavailable_reason；preview_video可空 |
| analysis_model | id/source/contributors；实际分析模型及来源，见下节 |
| annotations | 人工审计标注JSON；使用下面的规范，方法版本content-v3 |
| sampling | interval_seconds默认5；改变时override_reason说明真实用户要求或证据约束 |
| visual_reviews | 逐候选实际读图与连续窗口检查，见下一节 |
| cover_review | 双方封面齐备时填写status=reviewed、same/different/judgment |
| source_review | status=reviewed、notes写时间线/公共来源/未决授权、evidence_files列本地来源记录；reviewed表示已查并记录结果，不保证所有来源已解决 |
| findings | 需要强调的局部证据链；可以为空，不用为低分案例硬凑重证 |
| blocked_steps | 未完成步骤：step/reason，禁止空泛“受限”而不说缺什么 |
| limitations | 漏检范围、听辨争议、来源限制等 |

`material_screening`可登记单独筛查结果作为留痕；正式入口始终从清单中的原帧重算，不信任手填/过期均值。

## 报告身份信息

两个作品标题必须来自原始页面元数据或用户明确提供的完整标题，不用“昆明A/成都B”等标记代替。页头并列A标题×B标题，作品卡片和Markdown摘要再次完整展示。未获取时保留“标题未获取”，执行状态不能是complete。

analysis_model示例（仅演示字段，必须换成实际ID）：

```json
{"id": "provider/actual-model-id", "source": "runtime", "contributors": []}
```

id应来自本次实际执行的响应元数据或运行环境；保留提供方给出的精确模型ID（包括版本/日期后缀），不将“Astra”等昵称、用户偏好或技能声明当实际模型。source=runtime表示实际环境记录；用户提供但无法核实的ID用user_declared，报告明确注明。环境未暴露ID时用id=null、source=unavailable，显示“未获取（运行环境未提供）”，不影响已可核数值。

如实际用了不同分析/复核模型，contributors每项记录id、role、source；不用把提取帧、公式计算等无模型脚本列成模型。报告页头、摘要、交付核验均保留这些字段。

preview_video是明确选择的本地可展示视频，可与脱敏预览路径对应；只登记video不自动嵌入播放器，以免原画面含配置或凭据。回放按钮从已核时间区间取起点，绑定A/B播放器；未提供预览时展示时间和原作品链接，不出现无效回放按钮。

## 分段与同义判定一致性

1. **先切分后匹配。**用同一份完整校正转写，按有独立目的的命题或动作分段，遇到对象/目标/目的变化切分；不按字幕行、句号或固定秒数切分。一个句子只修饰同一动作时不拆；并列动作有各自完成条件时拆。重复观点指定代表单元，重复项记录`exclude_reason`而不是悄悄删除。
2. 需要保留的混合单元在匹配前列出独立`requirements`，要件是“对象＋动作/属性＋关键条件/目的”；礼貌词、语气词、同义修饰不单列要件。不要把一个事实拆成许多有利的要件来调权。
3. 匹配双方具体要件，不只看大类。相同对象、动作和目标条件同义时计；主体相同但操作对象/目的不同，保留未命中部分。只有“都是教程”“都是安装软件”等泛化类别不作完整命中。
4. 全部有效口播包含E表达、P推广、S步骤、T事实；G只排除可解释的问候、互动模板等。推广/步骤可以相似，但不能由此宣告独创。个性候选只取具体E子表达，不把混合句整段算原创。
5. 每方向等价命中动作数 = 各单元“已核命中要件数÷总要件数”之和；再除以该方向全部有效动作数。不用0.3、0.6等主观权重。文字是实际同义覆盖位置的并集，同一个位置不重复计。
6. 对齐边保存具体`shared_action`和`boundary`。只用共同动作的保序链计算结构，不把重排后的相同动作当全部同序。句子篇幅和动作数量不是独立证据，也不是原创概率。
7. 同一数据复算：复用已核标注及其文件指纹。独立重审：记录转写、分段、排除、要件和对齐的差异；在原文上解决争议后形成共享基线。**不要平均各模型分数。**暂时无法核实的匹配不写matched=true，同时明确“保守已核覆盖，存在待核项”，不能把尚未核实冒充已证实不同。

## 标注规范

顶层：method_version、A_units、B_units、structure_pairs，可选candidate_E_only。每侧单元按实际时间排列，不在匹配后重排。

```json
{
  "id": "a01", "time": "00:00–00:05", "text": "无需花钱",
  "attribute": "E", "group": "a01", "exclude_reason": null,
  "requirements": [{"key": "free", "meaning": "使用无需付费", "matched": true,
    "evidence": [{"id": "b01", "time": "00:02–00:06", "text": "不用付费"}]}],
  "text_coverage": {"total_chars": 4, "matched_chars": 4, "spans": [
    {"original_span": "无需花钱", "normalized_start": 0, "normalized_end_exclusive": 4,
     "supporting_other_units": ["b01"]}]}
}
```

示例仅解释字段；真实内容必须来自实际转写。B侧也单独标注，不能直接复制A侧命中范围。所有单元id全局唯一，time必须存在；命中证据的time与目标单元time一致。structure_pairs每项含a_id/b_id/shared_action/boundary。文字区间是归一后位置，左闭右开；归一=去标点/空白、字母小写、保留字母数字；变量屏蔽在校正转写与双方标注中一致执行并另列清单。

个性候选池candidate_E_only只统计独立审计的文字位置，未知来源保持候选，不冒充已核F2。详见[语义文字方法](semantic-text-coverage.md)。脚本可校验位置/引文/公式，不会替模型判断同义；它也不能发现所有遗漏的转写单元、虚假的工具执行声明或虚假的来源记录。

## 画面复核输入

先把extract_frames生成的两侧`抽帧清单.json`写入works.A/B.frames_manifest。入口根据全长帧自动确定必核对：全局哈希最高、像素最高、B首/中/尾各自最佳A；帧对取并集。可先运行build得到`交付核验.json.required_visual_pairs`；退出2是正常的“候选未复核”状态，随后读图并补齐。不要只挑对自己结论有利的对照图。

每个visual_reviews项：

```json
{
  "id": "V01", "status": "reviewed",
  "a_frame": "f000000.jpg", "b_frame": "f000000.jpg",
  "same": "具体相同点或未见有区分力的相同点",
  "different": "具体不同点或未见明显差异",
  "dense_A": "加密/A-V01/抽帧清单.json", "dense_B": "加密/B-V01/抽帧清单.json",
  "continuous_observation": "连续窗口内操作/鼠标/内容/运动如何变化；哪些对应、哪些不对应",
  "judgment": "本对支持什么、不能证明什么",
  "reuse_status": "unknown", "exclusive_details": ""
}
```

status=reviewed只能在模型实际看过对应帧及连续窗口后填写。reuse_status只接受unknown、public_asset、confirmed_specific；最后一种须写exclusive_details，且归属/授权仍须另核。每侧窗口包含本侧锚点，长度至少min(5秒,片长)，间隔1秒，边界处向片内平移。允许多个候选共用覆盖相应锚点的同一连续清单，节约抽帧，但仍需逐对观察记录。无法读图就记阻塞，不编造轨迹。

## 局部事实与仅展示相似度

60%为用户认可的内部参考阈值。统一入口固定保存reference_threshold_percent=60与report_policy=similarity-only-v1；无需案件手填，不改变content-v3公式。

报告首页展示综合相似度、双向分数和执行状态，随后展示分项及话术/素材事实。不输出“抄袭/不抄袭”“侵权/不侵权”“较强复制证据”或高/低风险等级；不按是否越过60%生成通过/不通过、红黄绿或类似判断。complete/partial/blocked只表示工作完成程度，不表示作品性质。

findings用于局部相同点与不同点，每项必含summary、kind（expression或continuous_footage）、evidence_files。表达项关联a_id/b_id；画面项关联visual_review_id。summary只写原话对应、细节变化、时间与来源事实，不写是否抄袭结论。旧输入中遗留的specific_correspondence_verified等归因标记不再驱动等级生成；当前入口不输出evidence_level或strong_chain。

素材身份分析、公共来源核验与未知项照常保留，不因只展示分数而省略证据获取。输入不足时保留N/A、具体缺口与partial/blocked，不以“证据不足”作为作品定性。

## 固定生成与校验边界

报告版式固定为evidence-editorial-v1：深蓝渐变页头和锚点导航、蓝色综合分概览、深色表头、白底卡片、左右原话与暖色高亮、配对素材图、可定位的预览视频、折叠计算附录。移动端左右栏按A后B堆叠。高亮只能来自已核文字位置或实际对应引文，不由模型随意涂色；语义标注未通过检查时不展示“已核”卡片。

build始终生成score.json（缺失时N/A对象）、交付核验.json、固定七段HTML与Markdown摘要。标准入口是必需步骤，score_content.py单独成功不代表整个案件完成。HTML包含所有单元、要件证据、保序链、文字命中位置、双向分项、个性候选、来源、并列帧和未完成项；报告不靠模型再次自由改写数值。

退出0=必交项完成；退出2=受阻或部分完成，保留可核结果；输入版本错误则报错停止。重跑覆盖当前HTML，旧的数值缓存不能拿来补缺。证据哈希（文件指纹）、脚本和模板哈希存入交付核验，便于对比版本。指纹只能确认文件一致，不能证明图像语义判断正确。

跨模型承诺限于：**同一输入/标注/软件版本→相同数值、相同执行状态、相同报告结构与措辞**。初次判断的分歧通过共享标注和差异记录收敛，不能声称已实现所有模型对原视频自动一致。

本次显示策略更新不回改旧案标注与算法。重新生成同案报告时覆盖原HTML，并生成分析摘要；如输出目录已有旧名“判定结论_案件号.md”，其内容同步改为同一分析摘要，避免旧结论残留。
