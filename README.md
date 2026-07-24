# MobileWork Expert Evaluation Marketplace

面向“26 暑期实习－智能体评估优化方向”的 Claude Code 插件市场。仓库包含一个公共专家管理插件，
以及 A、B、C 三组独立开发的专家评估与优化插件。

> 当前状态：`mobilework-expert-manager` 为可用公共插件；A/B/C 插件为可安装、可继续开发的起步骨架，
> 尚未实现完整的 Promptfoo、OpenCode、评测结果 Web 或真实业务链路。

## 快速开始

在 Claude Code 中添加市场：

```text
/plugin marketplace add xiaodong528/mobilework-expert-eval-marketplace
```

按需安装插件：

```text
/plugin install mobilework-expert-manager@mobilework-expert-eval
/plugin install group-a-expert-eval@mobilework-expert-eval
/plugin install group-b-expert-eval@mobilework-expert-eval
/plugin install group-c-expert-eval@mobilework-expert-eval
/reload-plugins
```

也可以使用非交互 CLI：

```bash
claude plugin marketplace add xiaodong528/mobilework-expert-eval-marketplace
claude plugin install mobilework-expert-manager@mobilework-expert-eval
```

## 插件清单

| 用途 | 插件安装标识 | Skill 调用 |
|---|---|---|
| 公共专家管理 | `mobilework-expert-manager@mobilework-expert-eval` | `/mobilework-expert-manager:mobilework-expert-manager` |
| A 组 | `group-a-expert-eval@mobilework-expert-eval` | `/group-a-expert-eval:expert-evaluation` |
| B 组 | `group-b-expert-eval@mobilework-expert-eval` | `/group-b-expert-eval:expert-evaluation` |
| C 组 | `group-c-expert-eval@mobilework-expert-eval` | `/group-c-expert-eval:expert-evaluation` |

## 仓库结构

```text
.
├── .claude-plugin/
│   └── marketplace.json
├── plugins/
│   ├── mobilework-expert-manager/
│   ├── group-a-expert-eval/
│   ├── group-b-expert-eval/
│   └── group-c-expert-eval/
├── .github/workflows/
│   └── validate-marketplace.yml
└── CONTRIBUTING.md
```

每组只对本组插件目录负责。市场清单、根文档和公共插件属于共享区域，修改前需说明影响并由导师复核。
具体分支、评审和证据要求见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 共同评测合同

每组插件最终应独立跑通：

```text
Claude Code 对话
  → 本组插件
  → 真实 OpenCode 专家（团）
  → Promptfoo
  → 本地评测结果 Web
```

共同要求：

- 评估并优化 1 个单专家和 1 个专家团。
- 覆盖结构化、混合式和开放式任务 case。
- 比较不同模型、不同专家版本及“使用专家／不使用专家”。
- 原始专家包保持只读；优化生成带版本标识的新副本。
- 使用相同 case、模型和运行环境完成优化前后复测。
- 保留配置、版本、输入、原始输出、指标、日志和报告等可复现证据。

## 本地验证

```bash
claude plugin validate . --strict
python3 -m unittest discover \
  -s plugins/mobilework-expert-manager/skills/mobilework-expert-manager/tests \
  -p 'test_*.py'
```

CI 会在每次推送到 `main` 和每个 Pull Request 上执行同样的市场结构校验与公共插件测试。
插件使用显式 SemVer；发布新能力或修复时必须同步升级对应 `plugin.json` 的版本。

## 资料

- [课题任务书](https://docs.qq.com/doc/DRXVNTktmaFZ2Wmpy)
- [Promptfoo GitHub](https://github.com/promptfoo/promptfoo)
- [Promptfoo 入门文档](https://www.promptfoo.dev/docs/intro/)
- [Promptfoo OpenCode SDK Provider](https://www.promptfoo.dev/docs/providers/opencode-sdk/)
- [OpenCode 官方文档](https://opencode.ai/docs/)
- [Claude Code 插件开发](https://code.claude.com/docs/en/plugins)
- [Claude Code 插件技术参考](https://code.claude.com/docs/en/plugins-reference)
- [Claude Code 插件市场](https://code.claude.com/docs/en/plugin-marketplaces)

## License

Apache License 2.0，详见 [LICENSE.txt](LICENSE.txt)。
