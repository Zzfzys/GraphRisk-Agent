# Stage 08 路线 0：前置门禁、产品范围与实验合同

日期：2026-09-29。结果：**路线 0 工程门禁通过**；Stage 08 仍 In Progress，路线 1～8 未完成，L0 个人学习待自测。
依据：[实验合同](../docs/data/stage-08-experiment-contract.md)、[设计 YAML](../configs/stage08_experiment.yaml)、[独立项目](../projects/enterprise-graphrag/README.md)。

## 实测环境与旧基线

| 检查 | 实际结果 |
|---|---|
| uv / Python | 0.12.18 / 3.11.16；仓库 .venv、.uv-cache、.uv-python |
| 同步 | `uv sync --locked --offline --group demo --group dev` 成功 |
| 核心版本 | langgraph 1.2.12；checkpoint-postgres 3.1.2；psycopg 3.3.6；streamlit 1.64.0；torch 2.9.1+cu128 |
| GPU | NVIDIA RTX 2060，6144MiB，检查时占用 12MiB，驱动 610.74 |
| RAM | 系统可见约 15.37GiB，检查时空闲约 3.75GiB；重任务串行 |
| 磁盘空闲 | C 11.55GiB；D 23.02GiB；E 55.40GiB |
| Stage07 Compose | API、工作台、数据库容器 healthy |
| ready | `http://127.0.0.1:8000/api/v1/ready` 为 ready，六项依赖 true |
| 全量测试 | **339 passed，1 个已有 Starlette/AnyIO 弃用 warning**，56.47 秒 |
| 旧 Agent v1 | **50/50** fake/系统回归通过 |
| 本地真实回归 | 3 个固定同交易场景：verified / insufficient_evidence / verified；其中无政策场景不调用生成 |
| Agent v2 | 新鲜隔离库 **15/15**，0 failed，0 not_run；另 3 个开发案仍 not_run，不计入 |
| 托管 | 当前进程 DASHSCOPE_API_KEY 未设置；endpoint 未配置；调用 0 次，费用 0 元 |

本地 qwen2.5:0.5b：Q4_K_M、494.03M、约 398MB；digest `a8b0c51577010a279d933d14c2a8ab4b268079d44c5c8830c0a93900f1827c67`。
qwen3-embedding:0.6b：Q8_0、595.78M、约 639MB、1024 维；digest `ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d`。
这些证明当前模型可用；未测试新候选，也没有新 provider 胜负结论。

## 冻结哈希

| 文件 | SHA256 |
|---|---|
| 根 uv.lock | `e31ddd7a212d34dc12baebf5568d2c28a583f1ca998f2a8ed02bcd8200c79d6f` |
| stage05_agent_cases_v1.yaml | `d4442875bd170bc020fbba26c150dd8fa9d2181bf60d936eb8ae00891ddb1ae1` |
| stage06_agent_cases_v1.yaml | `f91200b170e0665628312bc24ecfd01b928d6a74d861ce8e9bb9eac31caaf684` |
| stage07_agent_frozen_v2.yaml | `60fcad25b9dda57ecd243a226b7d41ce2bda88d28dd89ce88fd460784091f1d1` |
| 子项目 uv.lock | `ea4d79b994cffea963492cc2d244845cb740c598f2e4f4f32e6ce7e766a836d9` |

根锁与三个旧评估集均未改变。设计冻结与具体案例冻结分开；20/30 案目前尚未编写，不能宣称已跑。

## 数据库与失败记录

在既有隔离测试实例 127.0.0.1:15432 上分别新建 `graphrisk_stage08_pytest_c1c20b2371` 和 `graphrisk_stage08_eval_c1c20b2371`。迁移、导入固定政策索引、授予既有 app 角色权限后执行；测试库使用迁移角色运行 fixture，评估库使用受限 app 角色。没有在 Compose 业务库注入故障。

首次 v2 命令缺 `GRAPHRISK_STAGE07_EVAL_ISOLATED=1`，保护检查拒绝执行（exit 1）。确认目标为刚建隔离库后设置标记重跑，exit 0、15/15。不修改保护代码，不把首次失败删除。每次再次跑 v2 应准备**新隔离库**，防止固定幂等键命中旧过期待审数据；迁移/导入方式见 [Stage07 数据库说明](../docs/data/stage-07-postgres-service.md)。

首次子项目 cache-dir 使用父目录相对路径，在根目录调用 uv 时解析到了仓库外而失败；改为仓库内 cache 配置，并显式设置 UV_CACHE_DIR/UV_PYTHON_INSTALL_DIR 后通过。初次读取 JSON 未指定 UTF-8 导致 GBK 解码失败，改显式 UTF-8；只影响检查脚本。未修改系统权限。

原始结果均放在忽略目录 `reports/generated/stage08/`：`pytest-baseline.log`、`agent-v1-c1c20b2371.json`、`agent-v2-c1c20b2371.json`、`agent-v2.log`（首次失败）、`agent-v2-retry.log`。连接配置仅保存在忽略的 .env.stage08-*，本文不含连接串。

## 独立安装验证与复现

子项目独立锁只解析自身一个包；没有运行依赖。已完成源码包→wheel→新的干净虚拟环境安装。用 `python -I` 验证安装位置在新环境 site-packages、版本 0.1.0，`find_spec('graphrisk')` 和 `find_spec('torch')` 均为 None。这只验收打包边界，问答和演示功能属于路线 4～8。

仓库根目录 PowerShell：

```powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $PWD '.uv-python'
uv sync --locked --offline --project projects/enterprise-graphrag
uv build --offline --project projects/enterprise-graphrag --out-dir reports/generated/stage08/dist
uv venv reports/generated/stage08/package-check/.venv --python 3.11 --offline
uv pip install --offline --python reports/generated/stage08/package-check/.venv/Scripts/python.exe reports/generated/stage08/dist/enterprise_graphrag-0.1.0-py3-none-any.whl
& reports/generated/stage08/package-check/.venv/Scripts/python.exe -I -c "import enterprise_graphrag, importlib.util; assert importlib.util.find_spec('graphrisk') is None; assert importlib.util.find_spec('torch') is None; print(enterprise_graphrag.__version__)"
```

测试/评估入口（先按上述说明配置各自隔离库的环境变量；不把密钥放命令行）：

```powershell
uv run --offline pytest -q
uv run --offline python scripts/evaluate_stage06_agent.py --real --output reports/generated/stage08/agent-v1-repeat.json
# 以下仅针对新准备的隔离评估库
$env:GRAPHRISK_STAGE07_EVAL_ISOLATED = '1'
uv run --offline python scripts/evaluate_stage07_contract.py --probe-stage07-v2-all --require-complete --output reports/generated/stage08/agent-v2-repeat.json
```

## 选型、时长与后续

选择千问北京固定快照；100 元授权上限、80 元停止、20 元余量，单实验 5 元，单请求预留 0.10 元。价格、Token/时间限制、输入白名单与未来执行要求见实验合同；当前只是合同，没有已上线费用拦截器。

路线 0 计划 3h（学习 0.5h）；阶段仍按 44h、每周 20～24h推进。路线 1 下步实现适配与 fake HTTP 测试。路线 3 才做真实付费 smoke/20 案；路线 4 冻结原创语料/30 案。未配置千问密钥不会阻止路线 1，但会阻止路线 3 真实验收。

L0 自测：解释为何 50/50 +15/15 不能比较模型；用合同手算一次请求费用；说明为什么独立 wheel 无 graphrisk 模块仍不代表 GraphRAG 功能完成。