# Eval 失败/弱例归因摘要

- 生成时间：2026-09-26 18:07:05
- 用例数：1；通过：1；pass_rate=1.0000
- 失败码计数：{}

## 归因分类（primary）

| 类别 | hard 失败次数 | soft 弱例次数 |
|------|--------------|--------------|
| `prompt` | 0 | 0 |
| `algorithm` | 0 | 0 |
| `knowledge_data` | 0 | 0 |
| `rag_architecture` | 0 | 0 |
| `case_design` | 0 | 0 |
| `other` | 0 | 0 |

## 各类示例

## 说明

- `prompt`：LLM 提示词 / 阶段契约 / 软先验 / PlanOps 措辞
- `algorithm`：CCP / beam / dwell / geo / scoring / planner / replan
- `knowledge_data`：知识库内容、上海 CSV 标签、类型覆盖、POI 元数据
- `rag_architecture`：检索分层与证据注入方式
- `case_design`：评测用例本身不公/过时/期望不合理
- `other`：基础设施、API 抖动、接线缺陷等

离线默认（无 `--llm`）几乎不会把问题归到 `prompt`；开启 `--llm` 后 PlanOps/软先验相关失败才可能标为 prompt。
