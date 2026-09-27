# 提示词升级后推荐质量复测与归因（2026-09-26）

## 1. 复测做了什么

| 项 | 说明 |
|----|------|
| 提示词状态 | 已确认 `src/tripsense/llm/prompts.py` 含产品上下文 `_PRODUCT_CONTEXT`、阶段角色、软化绝对表述；与 `docs/TripSense_LLM提示词规范.md` v0.3 一致 |
| 离线主跑 | `python -m tripsense.eval --multi-turn --judge stub`（**LLM 关闭**） |
| 离线 N | **200**（Eval-200 全量多轮） |
| **产品 LLM 全量** | `python -m tripsense.eval --multi-turn --llm --judge stub`（**DeepSeek 开启**，`.env`：`TRIPSENSE_LLM_*`） |
| LLM N | **200**；约 **28.6 min**；`llm_enabled=true` |
| 离线报告 | `data/eval/reports/eval_report_20260926_113159.json` |
| LLM 报告 | `data/eval/reports/eval_report_llm_full.json`（同源 `eval_report_20260926_173157.json`） |
| LLM 归因摘要 | `data/eval/reports/attribution_notes_llm_full.md` |
| 辅跑（早先） | `--llm --limit 12` → `eval_report_llm_sample_12.json` |

## 2. 离线 vs 产品 LLM 路径对比（同 Eval-200 多轮）

| 指标 | 离线（无 `--llm`） | LLM 全量（DeepSeek） | 变化 |
|------|-------------------|----------------------|------|
| pass_rate | **1.000**（200/200） | **0.975**（195/200） | ↓ 5 hard |
| ccp | 1.000 | 0.995 | ↓ |
| adjust | 1.000 | 1.000 | 持平 |
| geo | 1.000 | 1.000 | 持平 |
| density | 0.965 | 0.955 | ↓ |
| retain | 0.995 | 1.000 | ↑ |
| theme | 0.960 | 0.935 | ↓ |
| failure_codes | theme×8, sparse×6, drop×1, crowd×1 | theme×13, sparse×9, ccp×1 | 主题/稀疏增多；出现 CCP hard |
| hard（primary） | 全 0 | **algorithm×5** | LLM 路径新暴露 |
| soft（primary） | algorithm×16 | algorithm×18 | 略增 |
| **prompt** | 0 / 0 | **0 / 0** | **未向 prompt 偏移** |

### Soft/Hard 集合变化

| 变化 | 用例 |
|------|------|
| 离线 soft 被 LLM 修掉（10） | `dlg-0077,0141,0315,0330,0463,0534,0638,0647,0674,0743` |
| 仍 soft（6） | `dlg-0245,0334,0382,0509,0602,0609` |
| 新 soft（12） | `dlg-0092,0120,0266,0288,0325,0367,0493,0514,0516,0724,syn-002,syn-007` |
| 新 hard（5） | `dlg-0082,0240,0362,0450,0468`（theme×4 + ccp×1；均非离线 soft 升级） |

解读：开启产品 LLM 后路线结构会变（软先验 / PlanOps / 理由链），密度与主题弱例集合**重组**而非整体变好；`adjust` 仍满分，规则 Judge **未**把失败标成 `prompt`（无 `op_noop` / `adjust_opposite` / `intent_parse_miss`）。

## 3. 失败/弱例归因（LLM 全量 200）

| 类别 | hard | soft | 说明 |
|------|------|------|------|
| **algorithm** | **5** | **18** | 主因。hard：`dlg-0468/0082/0450/0362` theme hit=0；`dlg-0240` CCP 越界。soft：过稀 + 主题不足 |
| **knowledge_data** | 0（primary） | 0（primary） | theme 类 **secondary**=`knowledge_data` 共 13 次 |
| **prompt** | 0 | 0 | 未出现 PlanOps 反向调整 / 意图漏解析类码 |
| **rag_architecture** | 0 | 0 | 规则分未覆盖检索注入 |
| **case_design** | 0 | 0 | 未见明显用例不公 |
| **other** | 0 | 0 | 无 API 抖动导致的整案失败 |

## 4. Top 可行动修复（按影响，LLM 路径证据）

1. **[algorithm + knowledge_data]** 主题命中：都市观光/自然公园等 hit_ratio=0 的 hard（`dlg-0468,0082,0450,0362`）及 soft；检查场景标签与打分，覆盖约 13 条 theme。
2. **[algorithm]** 单站过稀：`stops=1 expected~3/5`（含 `syn-002/007`、`dlg-0325` 等），约 9 条 sparse。
3. **[algorithm]** CCP 边界：`dlg-0240` planned 略超 safe（192.3 vs 188.7）——LLM 路径下预算收紧或 dwell 膨胀需护栏。

**仍不优先改 prompt**：全量 DeepSeek 路径 soft/hard 的 primary 均为 `algorithm`；prompt 计数保持 0。若后续出现 `adjust_opposite`/`op_noop`/`intent_parse_miss`，再开 prompt 专项。

## 5. 命令备忘

```bash
# 离线全量（基线）
python -m tripsense.eval --multi-turn --judge stub

# 产品 LLM 全量（本轮）
python -m tripsense.eval --multi-turn --llm --judge stub

# 失败邻域回归
python -m tripsense.eval --multi-turn --llm --judge stub --ids dlg-0468,dlg-0082,dlg-0240,dlg-0325,dlg-0334
```
