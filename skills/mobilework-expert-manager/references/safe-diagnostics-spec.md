# 安全静态诊断与证据等级

检查外部 ZIP、附件或未知目录时读取本文件。输入文件不因用户上传而自动可信。

## 默认无执行

`scripts/diagnose_expert.py`、`scripts/diagnose_skill.py` 和 validator 默认 static-only，禁止：

- 执行包内 Python、Shell、JavaScript 或 TypeScript；
- 调用包脚本的 `--help`；
- import 包内 Python 模块；
- 启动 Plugin、Custom Tool 或 MCP；
- 执行包管理安装、prepare/postinstall 等 lifecycle；
- 把未知包安装到真实 workspace。

允许 JSON/JSONC/YAML/Markdown 解析、Python `ast.parse`、ZIP CRC/路径/根目录检查、Office Open XML
读取、secret 和便携性扫描、manifest/投影/hash 对比。Python 语法检查只读取文本并调用
`ast.parse`，不得 import。

`import_reference.py` 同样遵守零执行：本地目录中的 Python、Shell、JS/TS 只会作为 UTF-8 文本
读取，绝不运行。DOCX 先做 ZIP metadata、路径、大小、压缩比和 CRC 检查，再只读
`word/document.xml`；存在宏、嵌入对象或外部链接时拒绝。PDF、图片和其他无法由当前依赖安全
转换的二进制即使伪装成文本扩展名，也会按文件 magic 返回 `conversion-required`，不会原样塞进
Reference。本地文件和目录复用 `manager-contract.json` 的条目数、单项大小、总大小、路径长度和
路径深度限制；symlink、FIFO 等非普通文件直接拒绝。只有用户确认来源、使用角色和复制动作并
显式传 `--confirm` 后，才原子重建专家包。

```bash
python scripts/diagnose_expert.py <package-dir-or-zip> --format human
python scripts/diagnose_expert.py <package-dir-or-zip> --format json
```

`--runtime` 只表达请求并以退出码 4 阻止宿主执行，不是放行开关。

技能诊断额外要求：输入是一个完整技能目录或单根 ZIP；目录名与 `SKILL.md` frontmatter `name`
必须是相同 kebab-case；frontmatter 必须通过 Agent Skills 官方字段、长度和类型规则，未知字段、
超过 500 字符的 `compatibility`、非 string → string 的 `metadata`、非字符串
`allowed-tools` 都失败。扫描全部文件的 symlink、缓存、secret-like 内容、不可移植路径和静态
Python 语法；计算逐文件 SHA-256 与确定性 tree hash。诊断不强制转换 YAML 类型、不改写原技能；
通过也不代表它已在 OpenCode Runtime 加载。

## 信任等级

| 来源 | 允许验证 |
|---|---|
| 外部 ZIP、附件、未知目录 | 仅静态诊断 |
| 当前 manager 从受控输入刚生成的包 | 临时安装和 config 读回 |
| 用户明确声明可信的外部包 | 仅真实容器或 VM 沙箱内运行 |
| 未知包且没有沙箱 | Runtime 阻止，不回退到宿主执行 |

## Finding

每个 finding 固定包含：`code`、`severity`、`phase`、`path`、`location`、`message`、
`rootCause`、`remediation`、`evidence`。code 和 rootCause 必须稳定；evidence 简短且不得回显 secret。
保留所有 raw findings，再按 rootCause 分组；同时报告 rawFindingCount 与 rootCauseCount。

JSON 顶层包含 `schemaVersion`、`ok`、`status`、计数、groups、findings 和 execution。execution 默认：

```json
{"policy":"static-only","attempted":false,"reason":"untrusted-package"}
```

## 退出码与证据等级

- 0：请求的静态阶段通过；
- 1：发现包合同问题；
- 2：命令调用或环境合同错误；
- 3：manager 内部错误；
- 4：请求 Runtime，但信任或沙箱策略阻止。

状态含义：

- `invalid`：静态合同失败，安装器阻止；
- `installable`：可信临时安装及 receipt 读回通过；
- `config-loadable`：可信安装通过 caller 显式提供且另行核验来源的 sidecar pure config 解析；
  sidecar 从安全打开的同一 descriptor 复制为私有可执行副本，记录的 hash/version 绑定该实际
  执行副本；
- `runtime-not-tested`：因信任、沙箱或环境没有运行 Runtime；
- `runtime-verified`：来源 Electron 新会话发现 Agent。

静态通过、安装成功或 config 可解析都不能表述为 Runtime 已加载。来源 Electron 不在当前验收范围
时，最高只报告已有证据等级。
