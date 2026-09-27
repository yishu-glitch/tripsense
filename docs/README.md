# TripSense 文档索引

> 校招 / 面试优先读 **[作品集导读](TripSense_作品集导读.md)**（约 10 分钟路径）。  
> 工程启动与 Demo 命令见根目录 [`README.md`](../README.md)。

历史诊断与阶段数据报告在 [`archive/2026-09-phase1/`](archive/2026-09-phase1/)，**不作为当前实现依据**。

---

## 产品 / 设计

| 文档 | 内容 |
|------|------|
| [`TripSense_作品集导读.md`](TripSense_作品集导读.md) | **招聘向入口**：阅读路径与面试可讲决策 |
| [`TripSense_综合产品设计文档_v2.0.md`](TripSense_综合产品设计文档_v2.0.md) | **产品主文档**：机会、用户、原则、指标与上海 MVP 范围 |
| [`TripSense_产品原型设计依据与框架_v2.5.md`](TripSense_产品原型设计依据与框架_v2.5.md) | 手机端交互原型依据、状态字段与验收清单 |
| [`TripSense_核心交互设计说明_v2.0.md`](TripSense_核心交互设计说明_v2.0.md) | 冷启动、规划、对话与路线、时间调整、手账 |
| [`TripSense_城市旅行风格设计规范.md`](TripSense_城市旅行风格设计规范.md) | 同行人 / 天数入口、城市灵感与跨日叙事 |
| [`TripSense_轻量推荐理由交互规范.md`](TripSense_轻量推荐理由交互规范.md) | 路线级 / 地点级理由的长度、语气与生成约束 |

`TripSense_市场与竞品研究.md` 待补充；研究范围已记在产品主文档 §1.5。

根目录 [`design-qa.md`](../design-qa.md)：地点详情原型 Design QA（实现对照记录）。

---

## AI / 架构与实现对齐

| 文档 | 内容 |
|------|------|
| [`TripSense_AI与技术架构说明_v2.0.md`](TripSense_AI与技术架构说明_v2.0.md) | LLM、路线算法、规则、RAG、实时工具与隐私边界 |
| [`TripSense_第一阶段知识架构.md`](TripSense_第一阶段知识架构.md) | 稳定知识 · 结构化 POI · 实时工具三层 |
| [`TripSense_产品研究实现对齐说明.md`](TripSense_产品研究实现对齐说明.md) | 产品北极星 ↔ 研究模型（CCP / 卡尔曼等）边界与已落地规格 |

---

## LLM 提示词

| 文档 | 内容 |
|------|------|
| [`TripSense_LLM提示词规范.md`](TripSense_LLM提示词规范.md) | 产品六层（B soft prior → A choose / C replan 等）与评测 UserSim / Judge；实现见 `src/tripsense/llm/prompts.py`、`src/tripsense/eval/prompts.py` |

---

## 评测

| 文档 / 路径 | 内容 |
|-------------|------|
| [`TripSense_评测与指标体系_v2.1.md`](TripSense_评测与指标体系_v2.1.md) | **评测主文档**：采用指标、质量护栏、Eval-200 怎么读 |
| [`TripSense_评测与指标体系_v2.0.md`](TripSense_评测与指标体系_v2.0.md)、[`TripSense_评测框架_对话集200.md`](TripSense_评测框架_对话集200.md) | 已合并进 v2.1，仅保留跳转 stub |
| [`../data/eval/SELECTION_REPORT.md`](../data/eval/SELECTION_REPORT.md) | Eval-200 子集筛选报告 |
| [`../data/eval/reports/`](../data/eval/reports/) | 过程报告（归因、多轮摘要、badcase 复跑等）；面试不必通读 |

---

## 部署

| 文档 | 内容 |
|------|------|
| [`TripSense_MVP_运行与部署.md`](TripSense_MVP_运行与部署.md) | 本地 MVP、`cloudflared` 临时公网、Render / Docker、密钥与边界 |

上海服务数据重建命令仍以根目录 README / 部署文档为准；POI 复核证据见 `data/audit/`（含表格类审计材料）。

---

## 建议阅读顺序（校招 / 面试）

1. [作品集导读](TripSense_作品集导读.md) → 根目录 README（问题 → 混合架构 → 关键决策 → Demo）
2. 综合产品设计文档 v2.0
3. LLM 提示词规范（讲清 B → Planner → A/C）
4. 评测与指标体系 v2.1（讲清护栏，不堆数字）
5. 需要深挖时：产品研究对齐、交互说明、部署文档
