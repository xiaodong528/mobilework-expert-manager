# Skill Markdown 与旧包生成兼容规范

新统一技能池不生成、重命名或改写技能内容。用户上传技能经 `diagnose_skill.py` 静态诊断后，
包括 `SKILL.md` 在内的所有文件按原字节复制到 `.opencode/skills/<name>/`；`name` 必须与目录名
一致。管理器自建的 `managed` 技能也使用完整自选名称，不套用专家或角色名称前缀。

## Agent Skills 官方格式门

所有新生成或成功导入的技能必须通过
[Agent Skills Specification](https://agentskills.io/specification) 的强制格式规则。MobileWork
可以因安全、路径、静态语法或便携性规则拒绝其他官方格式有效的技能，但不能接受官方格式无效
的技能。

`SKILL.md` 必须以 YAML frontmatter 开始，且只能使用以下顶层字段：

| 字段 | 规则 |
|---|---|
| `name` | 必填；1–64 个小写 ASCII 字母、数字或单连字符；不得首尾为连字符或包含连续连字符；必须与父目录名一致。 |
| `description` | 必填；非空字符串，最多 1024 字符；说明做什么以及何时使用。 |
| `license` | 可选；存在时为非空字符串，填写许可证名或随包许可证文件引用。 |
| `compatibility` | 可选；存在时为 1–500 字符的字符串；仅描述真实环境要求。 |
| `metadata` | 可选；必须是 string → string 映射，不对键和值做类型强制转换。 |
| `allowed-tools` | 可选实验字段；必须使用空格分隔的非空字符串，不能写成 YAML list 或 mapping。 |

未知顶层字段直接失败；自定义字符串信息放入 `metadata`。上传技能的校验失败只返回稳定 finding，
不补字段、不改类型、不重排 YAML。`SKILL.md` 正文没有强制章节；超过 500 行只报告渐进披露
warning，不作为官方格式错误。引用随包文件时使用相对 skill root 的路径，并避免深层引用链。
管理器要求 PyYAML 解析和生成 block-style YAML；缺少依赖时创建、诊断和校验失败关闭，不回退
为官方参考校验器不接受的 JSON flow mapping。

历史专家包只要包含非合规 Skill，就立即阻断 validate、package 和 install，不保留宽松兼容分支。
修复原始 Skill 后在干净包中重新导入或重建；管理器不会为解除阻断而原地改写 `preserved` 字节。

下方标记区仅供未修改旧 schema 包的兼容生成器读取。四反引号围栏与本页说明不属于生成结果。
旧包启用 `workflow.autonomy` 时继续投影历史模板；结构性修改前先迁移到统一技能池，此后不再
使用这些生成模板。

## 目录

1. [Agent Skills 官方格式门](#agent-skills-官方格式门)
2. [旧通用技能兼容模板](#旧通用技能兼容模板)
3. [旧角色技能兼容模板](#旧角色技能兼容模板)

## 旧通用技能兼容模板

<!-- mobilework-template:common-skill:start -->
````markdown
# 通用专家工作指引

这个 playbook 适用于 `$expert_name` 中的所有 agent。

包类型：`$expert_type`

描述：$description

## 何时加载

$when_to_use

## 工作节奏

1. 先澄清用户目标、输入材料和验收标准。
2. 判断当前任务是单专家直接执行，还是专家团团长委派团员执行。
3. 输出要有证据、可验收、可复查。
4. 优先交付最小但完整的有用产物，避免发散和臆测。
5. 报告完成前先验证，并说明验证证据。

## 资源导航

$resource_navigation

只读取当前任务需要的 reference，运行 script 前确认参数、输入输出和 workspace 边界。不要因为资源存在就一次性加载全部内容。

## 回传格式

向其他 agent 回传，或总结最终工作时，使用以下结构：

```markdown
## 结果
[完成了什么，或发现了什么。]

## 证据
[命令、文件、检查结果、来源引用或其他证据。]

## 验收状态
[逐条标记验收标准为通过、失败或阻塞。]

## 依赖关系
[使用了哪些上游输入，解除了哪些下游阻塞，以及该分支是否可并行。]

## 失败或阻塞项
[明确原因和下一步动作；没有则写 none。]

## 风险
[已知缺口；没有则写 none。]
```
````
<!-- mobilework-template:common-skill:end -->

## 旧角色技能兼容模板

<!-- mobilework-template:role-skill:start -->
````markdown
# $title 工作指引

这个角色 playbook 服务于 `$expert_name`。

## 何时加载

$when_to_use

## 职责边界

$responsibilities

## 角色方法

1. 用本角色语言复述被分派的任务。
2. 只收集完成本角色职责所需的上下文。
3. 在被委派的责任范围内工作；如果是单专家包，则在单专家责任范围内完成工作。
4. 产出必须能被明确验收标准验证。
5. 明确写出假设、依赖和剩余风险。

## 角色工作流

$workflow

## 资源导航

$resource_navigation

只加载当前任务需要的资料和工具；没有列出的资源不得凭空假设存在。

## 交付契约

$handoff_contract

回传内容必须包含完成结果、证据、验收标准状态、失败或阻塞项，以及未解决风险。若团长因为验收失败重新派发任务，先直接处理失败标准，再考虑新增范围。

如果你的任务属于并行批次，必须留在自己的分支边界内。回传时说明使用了哪些依赖、产出了哪些结果、是否发现共享状态冲突，方便团长判断是否可以进入下游阶段。

## 质量门控

$quality_gates
````
<!-- mobilework-template:role-skill:end -->
