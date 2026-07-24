# Supplemental Skill Markdown 生成规范

标记区内模板由生成器直接读取。四反引号围栏与本页说明不属于生成结果。

启用 `workflow.autonomy` 时，common skill 追加全部 workflow 执行合同；角色 skill 的“角色工作流”
改为该角色实际参与阶段的生效自主度、执行器、标准和验收。声明的 skill script 必须在资源导航和
执行合同中同时可追溯，不得让 Agent 在运行时临时重写替代实现。旧 manifest 保持原模板行为。

## 目录

1. [通用技能](#通用技能)
2. [角色专属技能](#角色专属技能)

## 通用技能

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

## 角色专属技能

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
