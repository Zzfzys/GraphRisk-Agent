# 当前项目入口

更新时间：2026-11-18（Asia/Hong_Kong）。GitHub现有默认分支`codex/stage09-version-closeout`采用当前核心与工作台源码，取代此前旧版项目目录。

## 源码位置

- `core/`来自核心提交`8ec3dad06c211dddd589d011a4e07b7378d1a82b`。
- `workbench/`来自工作台及回归修复提交`73de022ea7b2a43e00eb3b47acbd50ffd239da31`。
- [SOURCE_MAP.json](SOURCE_MAP.json)记录逐项来源和哈希；原源码内容不因公开目录分组而改写。
- 当前网页在[工作台扩展](workbench/extensions/transaction_workbench/web.py)，问答服务在[对应runtime](workbench/extensions/transaction_workbench/runtime.py)。

这些目录对应原本地两个源码来源，发布布局不表示本地目录或部署已迁移。子目录中的绝对路径、旧状态、报告链接和启动命令应按具体运行环境核验，不自动覆盖本文或用户最新要求。

## 当前采用与验收

日终XGBoost 216+49为当前主模型候选／试用分档；即时216+49及原x3保留。报告采用已认可的v12呈现。工作台整合风险、证据、报告与DeepSeek同案问答。

工作台相关离线验证为18项通过，另有2项旧UI检查明确被替代；详见[回归测试](workbench/tests/unit/test_transaction_workbench_regression.py)。这不代表真实回答写作和多轮质量已全部通过。全文评审器和完整案例评估仍有待验收，后续应分别审查。

## 密钥、数据与运行材料

真实API密钥、交易／评估记录、模型权重、生成报告、正式授权、会话及费用账本、环境与缓存不公开。配置示例为空值；恢复运行需要本地保留材料及相应授权，不能用新会话或新库重置消耗。

原GitHub历史继续保留；本地未公开的实验提交不作为此次更新的父提交，因此不会连同旧报告上传。源码备份标记`source-snapshot-20261118`仍保留，可追溯更新前保存的当前源码内容。

## 修改入口

先读根AGENTS.md及相关子目录规则，再按任务定位源码。改动前确认来源和现有运行绑定；有意义的接口测试直接驱动API，页面保留AppTest冒烟。未经具体授权不调用付费模型，也不恢复暂停的本地评估。
