# 专家源目录 Git 与 SemVer

## 目录

1. [信任与仓库边界](#信任与仓库边界)
2. [`.gitignore` 与分发](#gitignore-与分发)
3. [修改后的发布询问](#修改后的发布询问)
4. [SemVer 建议](#semver-建议)
5. [确认发布](#确认发布)
6. [失败与恢复](#失败与恢复)

## 信任与仓库边界

新专家成功生成并校验后由 `create_expert.py` 初始化本地 Git。可信既有专家第一次真实修改成功
后也补充初始化。repo root 必须精确等于专家目录；外部 ZIP、附件、未知目录、只读诊断、安装到
workspace 和临时测试都不得初始化。

本合同只允许本地 commit/tag。不得配置 remote，不得 fetch、pull、push、stash、reset、checkout、
clean 或切换分支。所有 Git 子进程都以专家目录为 `-C`，禁用 hooks、fsmonitor、pager、自动
commit/tag signing 与外部 attributes。不要读取或执行 alias、filter、hook 或 remote 操作。

## `.gitignore` 与分发

根 `.gitignore` 是包拥有文件，管理器维护下列 required block，用户可在 block 外添加规则：

```gitignore
.DS_Store
__MACOSX/
._*
__pycache__/
*.py[cod]
.pytest_cache/
.mypy_cache/
node_modules/
.env
.env.*
!.env.example
dist/
*.zip
```

validator 必须拒绝会忽略 `expert.json`、派生必需文件或 manifest 声明资源的用户规则。发布时仍只
显式暂存包拥有文件，不使用 `git add .`。

可信源目录可以含根 `.git/`，validator 不遍历其内容。嵌套 `.git/` 非法；ZIP 中任何 `.git/`
都是 error。`.git/**` 永远不进入 ZIP、bundle、安装投影、revision 或 package hash，不泄露历史、
remote、身份和对象。

## 修改后的发布询问

每次新建或真实修改成功后必须：

1. 重生成派生物并完整校验；
2. 用 `version_expert.py --package-dir <package>` 读取最后 release tag 到当前状态的累计 diff；
3. 展示 diff、建议版本、分类原因和验证证据；
4. 明确询问用户是否发布该版本；
5. 只有用户确认后才追加 `--confirm`，可用 `--version` 传入用户修订值。

用户拒绝或暂缓时不修改 `expert.json.version`，不 commit、不 tag；报告 `versionPending`，下次真实
修改后继续询问。只读操作、失败修改、安装和测试不询问。release 自身不递归触发发布询问。

## SemVer 建议

- major：删除或重命名角色、workflow、command，改变 slug/type 或输出合同等破坏兼容变化；
- minor：新增兼容角色、workflow、Skill、command、custom tool、权限或可选能力；
- patch：兼容修复、文档/派生同步、依赖精确化；
- 无法可靠分类：建议 minor 并要求用户判断；
- 首次完整 release：`v1.0.0`。

tag 使用 `vX.Y.Z`，`expert.json.version` 保存 `X.Y.Z`。建议基线是当前分支最后一个可达、符合
`vX.Y.Z` 的 release tag；没有 tag 时按首次 release 处理。

## 确认发布

确认后 `version_expert.py` 依次检查：精确 repo root、Git 身份、分支和 tag 唯一性、空 index、
包所有权及完整 validator。身份优先专家仓库本地配置，其次用户全局配置；缺失时暂停，不写全局
配置、不伪造邮箱。

工作树允许已有包拥有文件变化；非忽略且无所有权的文件会阻断发布。工具更新 version、重验、只
暂存拥有文件，创建 `chore(release): vX.Y.Z` commit 和 annotated tag，再读回 tag 指向、manifest
version、commit 内容和工作树。tag 注释只包含 slug、version、`expert.json` hash、合同版本和
验证等级。

## 失败与恢复

更新 version、重生成或校验在 commit 前失败时，只恢复 release 流程自身改动，不覆盖用户原有
变化。发布前若 index 已含 staged 变化则停止，不擅自清理。commit 成功而 tag 失败时保留 commit，
返回 `release-incomplete`、commit SHA 和可重试状态；不得为了制造原子假象删除提交。
