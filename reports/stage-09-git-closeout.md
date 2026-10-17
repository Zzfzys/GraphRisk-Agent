# Stage 09 本地 Git 版本收口

> 2026-10-17后续状态：origin已配置为Zzfzys/GraphRisk-Agent，分支已推送并通过真实GitHub CI。下面2026-10-16记录是本地收口的历史快照；旧bundle不覆盖，后续提交与运行见[路线6验收](stage-09-route-6-ci.md)。

2026-10-16：本地工程收口完成。当前开发分支为 `codex/stage09-version-closeout`；Stage 05～09 积累源码已形成 Git 集成基线，并通过独立干净检出验证。远端尚未配置，未推送，GitHub Actions 未执行；原路线 6 整体仍待远程证据，Stage 09 仍为 In Progress。

## 版本与位置

| 项目 | 实际状态 |
|---|---|
| 原基点 | `bfbdc40e3466d397afa174b296b31cdd8ab94848`，2026-09-24 的 Stage 05 政策语料提交 |
| 当前源码 | `C:/Users/12138/.codex/worktrees/71df/GraphRisk-Agent` |
| 开发分支 | `codex/stage09-version-closeout`，从 detached HEAD 建立 |
| 集成提交 A | `cc1da7dc92779c5260ea64721b313c8a52d43120`，553 个已跟踪文件；417 个文件相对原基点发生变化 |
| 证据提交 B | 本报告、README 与总计划/阶段总纲/实现/学习/交接文档的最终同步；仅 Markdown，最终 HEAD 记在本地 `acceptance.json` |
| 运行与私有证据 | `E:/GraphRisk-Agent`，不混作当前源码 |
| 原 E 盘 main | HEAD 仍为 `bfbdc40`；原有 317 行工作区状态保持一致，未合并或重置 |

本轮使用一个当期集成提交收录历史积累，不虚构逐路线或过去日期的提交。Git 用户身份原未配置，提交使用命令级 `Codex <codex@local.invalid>`；未改变全局用户身份、safe.directory 或其他系统配置。

## 提交边界与审查

- 初次候选审查覆盖 552 份工程文件、4,158,093 字节；新增本报告后提交共 553 份。包含源码、配置、锁、测试、模拟政策/语料、公开工程文档及 CI workflow。
- 6 份 WorkBuddy 本地状态保留在磁盘并加入忽略。秘密、原始/处理中交易数据、真实预测/权重、私有逐案输出、缓存、环境和生成取证不入 Git。两个已有 CSV 均为三行汇总指标，不是原始交易。
- 19 个本地已知秘密值与候选文件逐字节交叉检查无匹配，高可信 token/私钥规则无真实命中；DSN 模板、无效端点和测试凭据已区分。此项不代替将来公开推送前的完整历史及发布边界复核。
- 已保存原索引、Git 状态、提交候选清单和各 staged blob 哈希。完整暂存源的 whitespace 检查报告旧文件末尾空行/纯缩进等警告，未为消除旧样式警告而改写冻结源码；不宣称全源 whitespace 零告警。

## 从提交独立检出的实际验证

通过 `git clone --no-local` 从提交 A 建立 E 盘独立检出；没有复制 C 盘未提交源文件充数。553 个索引 blob 全部匹配，47 个冻结政策/评测/fixture 文件与 Git 对象字节相同。Git 对普通源码按既有属性规范行尾，因此检出指纹为 `b1009e261a266b5853d074fa7784fc74a1ea98feb792a2f3567807c7df3c7958`；原 C 盘原始字节快照另外保留，不能将这两个字节指纹混为一谈。

| 验证层 | 结果与范围 |
|---|---|
| 静态 | 278 个 Python AST 解析、47 个冻结文件 Git 往返通过；未新增 lint 框架 |
| Windows CPU/fake | 815 passed / 1 skipped / 0 failed / 0 errors；跳过原因是 Windows 不允许创建符号链接 |
| 隔离 PostgreSQL/pgvector | 24 passed，零跳过；另外 15/15 机制探针通过，探针使用受限应用账号 |
| 独立 GraphRAG core | 91 passed |
| 独立 GraphRAG demo | 3 passed |
| wheel 打包/安装验证 | 主包 119 个成员、独立 GraphRAG 包 10 个成员，均通过且不借用源码导入 |

以上是各层分别执行的真实结果，不拼成一次全量运行。数据库机制探针使用模拟政策及确定性 1024 维嵌入，包含一个合成空邻居变体，不是新增真实 LLM 质量样本。复用 E 盘按现有锁准备的独立环境，实际源导入指向干净检出；这证明提交文件足够执行这些层，不宣称在全新电脑或空依赖缓存完成安装。最终提交 B 仅更新文档，运行代码树与已验证 A 相同，差异另存证据。

本次未重新构建镜像；原路线 6 的新镜像、Linux 回归等保留原验收范围，不能记成本次干净检出的新镜像结果。

## 保存与恢复

私有证据：`E:/GraphRisk-Agent/reports/generated/stage09/git-closeout/20261016-v1/`，含各层 `validation/*/public-summary.json`、日志、Git 清单、最终 HEAD/备份哈希与 `acceptance.json`。

备份：`E:/GraphRisk-Agent/artifacts/stage-09/git-closeout/20261016-v1/`：

- `GraphRisk-Agent-stage09.bundle`：分支最终提交及其祖先 Git 对象；经 bundle 校验与另一新目录恢复检查。
- `worktree-before-commit.zip`：553 份选定 C 盘原始工作文件字节，保留 Git 行尾规范化前的内容。
- `legacy-main-source-preserved.zip`：447 份选定 E 盘旧工作区源码，原工作区未修改。

Git bundle 不包含被忽略的数据、模型、秘密、运行环境或私有逐案证据；这些仍在原 E 盘。备份也在同一块 E 盘，不代表异机备份。恢复到一个尚不存在的新目录，不覆盖当前 E 盘项目，例如：

```powershell
. 'E:\GraphRisk-Agent\scripts\enter_project_env.ps1' | Out-Null
git clone --branch codex/stage09-version-closeout 'E:\GraphRisk-Agent\artifacts\stage-09\git-closeout\20261016-v1\GraphRisk-Agent-stage09.bundle' 'E:\GraphRisk-Agent\reports\generated\stage09\git-closeout\manual-restore'
```

## 保留的结论和下一步

两个训练/生成账本的字节与哈希未变；原路线 6 验收清单的 435 个文件重新核对通过。新增真实数据训练、真实模型生成和付费调用均为 0。增强 XGBoost 通过、GNN 增量失败/不晋级的决定未变；本人学习与人工复核独立 Pending。

本地代码现已提交到上述分支，可通过提交及 bundle 恢复。E 盘 main 仍是另一个保留的旧工作区，不能直接在那里默认执行新源码。下一步核对目标远端与发布权限，取得真实 GitHub Actions run URL 后补路线 6 远程证据；原路线 7/8 及后续 GNN 关系模式检索尚未启动。
