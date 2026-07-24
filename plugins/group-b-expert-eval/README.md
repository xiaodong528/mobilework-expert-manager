# Group B Expert Evaluation

状态：**起步骨架**。插件结构和共同评测合同可用，但尚未实现 Promptfoo、OpenCode、评测结果 Web
或真实业务链路。

## 调用

```text
/plugin install group-b-expert-eval@mobilework-expert-eval
/reload-plugins
/group-b-expert-eval:expert-evaluation
```

## B 组交付范围

- 独立选择并评估 1 个单专家和 1 个专家团。
- 设计结构化、混合式和开放式评测 case。
- 实现 Claude Code → 插件 → OpenCode → Promptfoo → 本地 Web 的完整链路。
- 保持原始专家包只读；优化产物写入带版本标识的新副本。
- 用相同输入、模型和运行环境对比优化前后结果。

具体实现、依赖和验收证据由 B 组在本目录持续补充。
