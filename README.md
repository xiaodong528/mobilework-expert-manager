# MobileWork Expert Manager

用于创建、转换、修改、诊断、校验、安装、打包和版本发布 MobileWork 专家与专家团的独立
Claude Code 插件。

本仓库只发布 `mobilework-expert-manager` 插件，不提供面向实习分组的 marketplace。各组组长应维护
自己的 marketplace GitHub 仓库，并从本仓库引用公共专家管理插件。

## 插件接口

| 项目 | 值 |
|---|---|
| 插件名 | `mobilework-expert-manager` |
| 当前版本 | `0.1.0` |
| Skill | `mobilework-expert-manager` |
| Skill 调用 | `/mobilework-expert-manager:mobilework-expert-manager` |

## 在 Marketplace 中引用

组长在本组 `.claude-plugin/marketplace.json` 的 `plugins` 数组中加入：

```json
{
  "name": "mobilework-expert-manager",
  "source": {
    "source": "github",
    "repo": "xiaodong528/mobilework-expert-manager"
  }
}
```

用户添加本组 marketplace 后，使用本组市场名安装：

```text
/plugin install mobilework-expert-manager@<marketplace-name>
/reload-plugins
```

本仓库不是 marketplace，因此不要执行
`/plugin marketplace add xiaodong528/mobilework-expert-manager`。

开发者也可以直接在仓库根目录加载插件：

```bash
claude --plugin-dir .
```

## 核心能力

- 以 `expert.json` 作为专家包结构与资源所有权的唯一事实源。
- 支持单专家、专家团、角色拓扑、Workflow、Skills、MCP、custom tools、plugins、references、
  instructions 与 LSP。
- 支持 `scripted`、`fixed`、`bounded`、`guided`、`adaptive` 五档 Workflow 自主度和相应权限边界。
- 支持外部 ZIP、附件和未知目录的无执行静态诊断。
- 支持结构化 findings、root cause、证据 gate、可信 sidecar 和 OpenCode pure config 验证。
- 支持旧包迁移规划、供应链审计、Bundle manifest 和 Bundle 校验。
- 支持专家包本地 Git 初始化、SemVer 建议与用户确认后的版本发布。

## 仓库结构

```text
.
├── .claude-plugin/
│   └── plugin.json
├── skills/
│   └── mobilework-expert-manager/
│       ├── SKILL.md
│       ├── scripts/
│       ├── references/
│       ├── evals/
│       └── tests/
└── .github/workflows/
    └── validate-plugin.yml
```

`skills/mobilework-expert-manager/agents/openai.yaml` 是该 Skill 的既有资源，不是 Claude Code
插件根目录下的 subagent。

## 安全边界

- 外部或未知输入默认只允许静态诊断，不执行其中的 Python、Shell、JavaScript/TypeScript、
  Plugin、custom tool、MCP 或包管理脚本。
- 不把静态校验、安装成功或 pure config 加载成功描述为 Runtime 已验证。
- 未经明确确认，不自动 commit、tag、配置 remote 或发布专家包版本。
- `.git`、真实 `.env`、`node_modules`、lockfile、缓存、日志、密钥和个人配置不得进入分发包。

## 本地验证

```bash
claude plugin validate . --strict
python3 -m unittest discover \
  -s skills/mobilework-expert-manager/tests \
  -p 'test_*.py'
```

CI 使用 Node.js 22、`@anthropic-ai/claude-code@2.1.218`、Python 3.11 和
`PyYAML==6.0.3` 执行相同校验。发布新能力或修复时必须同步升级
`.claude-plugin/plugin.json` 的 SemVer。

完整工作流、权限矩阵和验收要求请阅读
[`skills/mobilework-expert-manager/SKILL.md`](skills/mobilework-expert-manager/SKILL.md)。

## 资料

- [Claude Code 插件开发](https://code.claude.com/docs/en/plugins)
- [Claude Code 插件技术参考](https://code.claude.com/docs/en/plugins-reference)
- [Claude Code 插件市场](https://code.claude.com/docs/en/plugin-marketplaces)
- [OpenCode 官方文档](https://opencode.ai/docs/)

## License

Apache License 2.0，详见 [LICENSE.txt](LICENSE.txt)。
