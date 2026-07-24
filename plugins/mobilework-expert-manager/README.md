# MobileWork Expert Manager

用于创建、转换、修改、诊断、校验、安装、打包和版本发布 MobileWork 专家与专家团的 Claude Code
插件。插件内保留完整 Agent Skill、脚本、规范、评测集和测试。

技能源码位于 [`skills/mobilework-expert-manager/`](skills/mobilework-expert-manager/)；入口文件为
[`SKILL.md`](skills/mobilework-expert-manager/SKILL.md)。

## 核心能力

- 以 `expert.json` 作为专家包结构与资源所有权的唯一事实源。
- 支持单专家、专家团、角色拓扑、Workflow、Skills、MCP、custom tools、plugins、references、
  instructions 与 LSP。
- 支持 `scripted`、`fixed`、`bounded`、`guided`、`adaptive` 五档 Workflow 自主度和相应权限边界。
- 支持外部 ZIP、附件和未知目录的无执行静态诊断。
- 支持结构化 findings、root cause、证据 gate、可信 sidecar 和 OpenCode pure config 验证。
- 支持旧包迁移规划、供应链审计、Bundle manifest 和 Bundle 校验。
- 支持专家包本地 Git 初始化、SemVer 建议与用户确认后的版本发布。

## 安全边界

- 外部或未知输入默认只允许静态诊断，不执行其中的 Python、Shell、JavaScript/TypeScript、
  Plugin、custom tool、MCP 或包管理脚本。
- 不把静态校验、安装成功或 pure config 加载成功描述为 Runtime 已验证。
- 未经明确确认，不自动 commit、tag、配置 remote 或发布专家包版本。
- `.git`、真实 `.env`、`node_modules`、lockfile、缓存、日志、密钥和个人配置不得进入分发包。

## 主要命令

```bash
python skills/mobilework-expert-manager/scripts/check_environment.py --feature core
python skills/mobilework-expert-manager/scripts/create_expert.py --manifest <expert.json>
python skills/mobilework-expert-manager/scripts/validate_expert.py <package-dir> --format json
python skills/mobilework-expert-manager/scripts/diagnose_expert.py <unknown-dir-or-zip> --format json
python skills/mobilework-expert-manager/scripts/install_expert.py \
  --package-dir <package-dir> --workspace-dir <workspace>
python skills/mobilework-expert-manager/scripts/package_expert.py \
  --package-dir <package-dir> --output-dir <dist-dir>
python skills/mobilework-expert-manager/scripts/validate_expert_bundle.py <bundle>
```

完整工作流、权限矩阵和验收要求请阅读
[`skills/mobilework-expert-manager/SKILL.md`](skills/mobilework-expert-manager/SKILL.md) 及其按需引用的
[`references/`](skills/mobilework-expert-manager/references/)。

## 验证

```bash
python /path/to/skill-creator/scripts/quick_validate.py skills/mobilework-expert-manager
python -m unittest discover \
  -s skills/mobilework-expert-manager/tests \
  -p 'test_*.py'
```

本次同步快照通过技能快速校验及 230 项单元测试。

## License

Apache License 2.0，详见 [LICENSE.txt](LICENSE.txt)。
