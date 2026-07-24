# 协作规范

本仓库由 A、B、C 三组共同维护。每组插件必须独立安装、运行和验收；共享区域变更需要说明对其他组
和现有用户的影响。

## 路径所有权

| 范围 | 主要责任方 |
|---|---|
| `plugins/group-a-expert-eval/**` | A 组 |
| `plugins/group-b-expert-eval/**` | B 组 |
| `plugins/group-c-expert-eval/**` | C 组 |
| `plugins/mobilework-expert-manager/**` | 公共维护，导师复核 |
| `.claude-plugin/**`、根文档、CI | 公共维护，导师复核 |

路径所有权用于明确主要责任，不禁止跨组评审、测试或修复。跨组修改必须在 Pull Request 中说明原因，
并邀请被影响组参与评审。

## 分支与提交

- A 组使用 `group-a/<topic>`。
- B 组使用 `group-b/<topic>`。
- C 组使用 `group-c/<topic>`。
- 共享改动使用 `shared/<topic>`，提交前先说明影响范围。
- 后续开发通过 Pull Request 合并，不直接向 `main` 推送功能改动。
- 一次 Pull Request 聚焦一个可验收目标，不混入无关重构或生成物。
- 可发布的插件变更必须同步更新对应 `.claude-plugin/plugin.json` 的 SemVer；只改 Git commit
  而不升级版本不会触发已安装插件更新。

## 必须保留的证据

- 需求或 case 版本。
- 插件、专家、模型和运行环境版本。
- 可复现命令与配置。
- 原始输出、结构化指标和失败日志。
- 优化副本标识及变更说明。
- 优化前后在相同条件下的对比结果。
- 明确列出未执行或未通过的验证。

不得提交 API Key、Token、Cookie、真实 `.env`、个人配置或其他敏感信息。

## 提交前验证

```bash
claude plugin validate . --strict
python3 -m unittest discover \
  -s plugins/mobilework-expert-manager/skills/mobilework-expert-manager/tests \
  -p 'test_*.py'
```

涉及某组插件时，还应在隔离 Claude Code 配置中完成本地安装和组件发现验证。真实 Promptfoo、
OpenCode 和本地 Web 链路只有在真实运行且保留证据后才能标记通过。

## Pull Request 最低说明

- 改了什么，为什么改。
- 影响哪个组、插件和公开命令。
- 使用了哪些测试或真实运行验证。
- 已知限制、回滚方式和待完成事项。
