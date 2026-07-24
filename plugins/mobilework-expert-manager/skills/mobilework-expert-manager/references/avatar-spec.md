# MobileWork 专家头像规范

本文件定义 `avatar_url` 的来源、生成、复制和安全合同。头像是专家包的一部分，必须在生成、
校验、`--force` 重建和打包后保持可解析。

## 1. 覆盖范围

生成器为以下对象补齐头像：

- 顶层单专家或专家团；
- `type: expert` 的单专家 agent；
- `type: team` 的团长；
- 每一个团员。

每个本地相对 `avatar_url` 都必须解析到包内真实文件；agent Markdown frontmatter 的
`avatar_url` 必须与 `expert.json` 中对应角色一致。

## 2. 支持的来源

### HTTPS 远程头像

- `https://` URL 原样保留，不下载、不复制。
- 即使全部头像都是远程 URL，生成包仍保留 `avatars/` 目录。
- `http://` URL 非法，避免在分发和运行时降级安全性。

### 本地相对头像

- 路径必须位于包内 `avatars/`。
- 禁止绝对路径、`~`、`..` 和 symlink。
- 输入 manifest 旁存在有效相对头像时，复制真实字节到生成包。
- 复制后更新生成态 `expert.json`，使路径指向包内文件。

### 缺失或断链头像

未提供头像，或声明的本地相对文件不存在时，生成确定性 SVG placeholder：

- 顶层：`avatars/<slug>.svg`；
- agent：`avatars/<agent-id>.svg`。

placeholder 由稳定标识和展示名生成；同一输入重复生成时内容应稳定。

## 3. 文件限制

支持以下格式，扩展名大小写不敏感：

| 格式 | 扩展名 | 校验 |
|---|---|---|
| PNG | `.png` | 检查 PNG magic bytes。 |
| JPEG | `.jpg` / `.jpeg` | 检查 JPEG magic bytes。 |
| WebP | `.webp` | 检查 RIFF/WEBP magic bytes。 |
| GIF | `.gif` | 检查 GIF87a/GIF89a。 |
| SVG | `.svg` | 解析安全 XML 结构。 |

单个头像最大 2 MiB。只有扩展名正确但内容 magic bytes 不匹配的 raster 文件必须失败。

## 4. SVG 安全

SVG 可以包含静态矢量图形与内联样式，但禁止：

- `<script>`；
- `onload`、`onclick` 等事件属性；
- `<foreignObject>`；
- 外部 HTTP/HTTPS、`file:` 或其他外部资源引用；
- 可执行 URL、数据外带或实体扩展；
- 试图逃离包边界的路径。

生成器自己的 placeholder SVG 也必须通过同一校验。

## 5. 命名冲突

顶层头像与 agent 头像可能来自同名文件。处理时：

1. 比较目标路径和真实字节；
2. 同路径同字节可以复用；
3. 同路径不同字节必须分配稳定、不冲突的目标名；
4. 更新各自 `avatar_url`，禁止静默覆盖另一对象的头像。

## 6. `--force` 重建

当 manifest 位于已有生成包内并使用 `--force` 时：

- 在替换旧目录前读取 manifest 引用的现有包内头像字节；
- 将这些字节带入 sibling staging；
- staging 完整校验通过后再原子替换；
- 失败时旧包头像和目录保持不变。

不要从未引用的 `avatars/` 文件猜测所有权。manifest 未引用的头像属于孤儿文件，validator 必须拒绝。

## 7. 验证清单

- `avatars/` 存在。
- 顶层和每个角色都有非空 `avatar_url`。
- HTTPS URL 不要求本地文件，HTTP URL 失败。
- 所有本地路径位于包内 `avatars/` 且真实存在。
- 文件大小、扩展名、magic bytes 或 SVG 安全结构通过。
- agent Markdown 与 manifest 的 `avatar_url` 一致。
- 没有未引用头像、symlink、路径逃逸或开发机绝对路径。
- 打包、解压和再次校验后头像字节与引用保持一致。
