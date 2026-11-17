# 智能运营决策分析平台

面向用户行为序列分析、RFM分层、流失预警、智能推荐、归因建模、活动效果评估与实时运营决策的数据分析工程平台。

这是一个前后端分离的管理平台：前端 React + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 React + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 行为序列 | `sequence` | 序列模式 | 模式编号、用户分群、事件序列 |
| RFM分层 | `rfm` | 分层规则 | 规则编号、规则版本、最近消费天界、消费频次界 |
| 流失预警 | `churn` | 预警模型 | 模型编号、模型版本、模型名称、特征变量 |
| 联合发布准入 | `joint_release` | 发布批次/流失去向名单 | 规则版本、模型版本、名单编号、发布批次 |
| 智能推荐 | `recommend` | 推荐策略 | 策略编号、推荐场景、召回算法 |
| 归因建模 | `attrmodel` | 归因模型 | 模型编号、模型名称、归因逻辑 |
| 活动评估 | `activity` | 活动方案 | 方案编号、活动名称、活动类型 |
| 实时决策 | `realtime` | 决策规则 | 规则编号、触发事件、决策条件 |
| 预算分配 | `budget` | 预算方案 | 方案编号、预算总额、渠道分配比例 |
| LTV估算 | `lifetime` | LTV模型 | 模型编号、模型名称、估算方法 |
| 实验变体 | `abvariant` | 变体方案 | 变体编号、关联实验、变体名称 |
| 运营看板 | `dashboard` | 看板视图 | 视图编号、视图名称、指标组合 |
| 评分卡 | `scorecard` | 评分卡 | 评分编号、评分对象、特征变量组 |
| 聚类分群 | `cluster` | 聚类任务 | 任务编号、聚类算法、特征维度 |
| 对比分析 | `comparison` | 对比组 | 对比编号、对比名称、实验组筛选 |
| 数据漂移 | `datadrift` | 漂移检测 | 检测编号、监控数据集、基线版本 |
| 推送计划 | `pushplan` | 推送方案 | 方案编号、推送内容、目标人群 |
| 指标归因 | `metricdrill` | 归因分析 | 分析编号、目标指标、波动方向 |
| 策略库 | `tacticlib` | 运营策略 | 策略编号、策略名称、策略类型 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## RFM分层 × 流失预警 联合发布准入（业务核验码 04d4803e）

把启用的 RFM 分层规则版本与已部署的流失预警模型联合成榜，生成唯一一份
「流失去向名单明细数据集」并原子投递到「流失预警待处理明细」。

- **共同满足**：规则处于「已启用」且模型处于「已部署」，且规则启用回执与模型部署
  回执齐备，才允许发布；未部署模型或未启用规则一律不新增正式名单。
- **唯一名单**：同一规则×模型（同一发布批次）只留存一份已发布数据集；并发成榜由
  进程锁 + 已发布幂等拦截，竞态下也只会有一份。
- **原子写入**：名单、待处理明细、业务凭证在一次事务里落库，任一步失败整体回滚，
  不留下单侧状态。
- **每用户一条当前记录**：投递到待处理明细时旧记录置为「已被新名单替换」，同一用户
  始终只有一条 `是否当前=true` 的记录，并携带规则版本与模型版本。
- **RFM 结果驱动**：成榜成员 = RFM-0003 口径下最近消费低档（流失候选）与模型判定
  流失的交集；分层阈值、允许范围和上限统一走 `app/rfm_spec.py`（核验码 04d29b9b），
  RFM-0003 为核对基准记录。
- **凭证链路**：业务凭证可从上游 `user_segment` / `churn_dataset` 按记录追到同一份
  名单，核对用户价值分层、模型准确率、预警推送频率和覆盖人数。

接口前缀 `/api/joint-release`：`spec`（规范口径）、`versions`（数据版本核对）、
`gate`（准入核对）、`receipts/*`（回执登记）、`batches`、`publish`（联合发布）、
`datasets`（名单数据集）、`pending`（待处理明细）、`vouchers` 与
`vouchers/{凭证编号}/lineage`（凭证追溯）。前端页面在
`frontend/src/views/jointrelease/`。

领域：数据工程
子方向：数据分析与数据应用
