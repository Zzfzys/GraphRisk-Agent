# Stage 04 路线 2：时序图产物验收与学习复盘

> 日期：2026-09-23
> 结果：PASS；路线 2 的小 fixture 和 IEEE-CIS 50k 里程碑完成，Stage 04 整体仍为 In Progress

## 实施结果

已实现 DuckDB 列投影与源文件交叉核对、按时间/ID 选取目标、交易—实体倒排索引、严格历史 ego graph、训练目标专用特征编码、节点/边表、可序列化的小图样例和完整性 manifest。产物规范见[图产物契约](../docs/data/stage-04-graph-artifacts.md)，路线 1 的逐边规则见[图 schema](../docs/data/stage-04-graph-schema.md)。

真实 IEEE-CIS 当前配置 `configs/graph_ieee_cis.yaml` 的验收结果：

| 项目 | 值 |
|---|---:|
| 源交易总数 | 590,540 |
| 建索引交易数（截至所选最后测试目标时间） | 511,959 |
| 目标数 train / validation / test | 50,000 / 10,000 / 10,000 |
| 全局节点数 | 528,187 |
| 全局交易—实体事实边数 | 1,530,667 |
| train 零历史目标 | 30 / 50,000 |
| validation/test 零历史目标 | 0 / 10,000；0 / 10,000 |
| train / validation / test 局部图逻辑边总数 | 2,377,053 / 448,176 / 434,907 |
| manifest 逻辑内容 SHA-256 | `8ace6710d2e2fdc95c5a8524f68a376b6b2b8e096feba1e06e0abd754f301a80` |

构建程序内部计时首次 22.51 秒、重复 23.69 秒；包含 Python 进程启动的第二次墙钟计时为 35.15 秒。约 50 毫秒间隔抽样的 Python 进程树峰值 RSS 为 **1,366.2 MiB**；未采到的瞬时峰值可能更高。第二次构建后 manifest 与第一次**逐字节一致**，其文件 SHA-256 为 `d1a9d57661873194afa60df97055f2039f3b29379f41585b890085e4f3da99a9`。已从真实产物重新加载 511,959 笔索引交易，并构建一个验证目标的 39 节点、47 条逻辑边局部图；加载器校验源与产物哈希。

## 自动验证

```powershell
uv run --offline pytest --basetemp=reports\generated\stage04-route2-final-focused tests\unit\test_temporal_entity_index.py tests\unit\test_temporal_graph_schema.py tests\integration\test_graph_artifacts.py -q
uv run --offline pytest --basetemp=reports\generated\stage04-route2-final-all -q
uv run --offline python scripts\build_graph_artifacts.py --config configs\graph_ieee_cis.yaml
```

| 检查 | 结果 |
|---|---|
| 聚焦单元及集成测试 | 19 passed in 10.82s |
| 全仓库测试 | 146 passed in 19.13s |
| Python compileall、wheel/sdist 构建 | PASS |
| 十笔 fixture 的所有目标与多个历史上限，对比扫描式参考构建器 | PASS，节点、边、哈希一致 |
| 小型真实管线式 fixture 两次构建，比较 manifest 字节 | PASS |
| 缺失/变化的源、分割特征漂移、损坏 encoder、错误数据源 | 产生明确失败 |
| 真实 IEEE-CIS 50k/10k/10k 受控构建和重新加载 | PASS |

代码还逐个选中目标构建 ego graph，以统计局部节点、边、零历史和截断；严格时间断言由参考构建器及 fixture 测试验证。全局边表保存事实，但模型必须查询逐目标截止图，不能把整表直接作为训练邻接关系。没有使用标签或未来关系构造节点特征。

## 学习复盘和后续边界

倒排索引回答的是“某实体在目标时间之前出现在哪些交易”，二分查找先排除同时间和未来记录，再按时间倒序、ID 升序取有界历史。`fanout: [10, 5]` 在当前 schema 中保留全部四类根实体，对每个实体选最多五笔历史交易。单笔历史交易可能经多个实体路径命中，但在局部图里只创建一次。全局节点映射的固定索引用于产物追溯，局部图的根索引始终为 0，两者用途不同。

本轮没有开展 100k 目标实验；路线 3 先使用已验收的 50k/10k/10k 集合做同 ID 的 GraphSAGE 与 XGBoost 受控比较。模型训练、风险分数、分数校准和邻居工具接入仍待后续路线。当前构建进程采样峰值约 1.34 GiB，后续 mini-batch 训练应避免每个 worker 各自复制一份索引。
