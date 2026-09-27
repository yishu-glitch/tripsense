# TripSense 多轮评测摘要报告

> 生成时间：2026-09-26 11:46  
> 数据集：`data/eval/dialogue_eval_200.jsonl`（200 例）  
> 模式：规则 UserSim 回放 + `TripSenseService.chat` + 规则 Judge（`--judge stub`）  
> 原始 JSON：`eval_report_20260926_113228.json`

## 1. 总览

| 跑次 | Case Pass | CCP | density | theme | adjust | retain | geo |
|------|-----------|-----|---------|-------|--------|--------|-----|
| 多轮（本轮） | 200/200（100.0%） | 100.0% | 96.5% | 96.0% | 100.0% | 99.5% | 100.0% |
| 多轮（修复前） | 191/200（95.5%） | 100.0% | 92.5% | 93.0% | 100.0% | 99.5% | 100.0% |
| 首轮（本轮） | 200/200（100.0%） | 100.0% | 99.0% | 100.0% | — | 98.5% | 100.0% |
| 首轮（修复前） | 200/200（100.0%） | 100.0% | 91.5% | 100.0% | — | 98.5% | 100.0% |

说明：Case Pass 要求 CCP 硬通过且 overall 达难度阈值；density/theme 等软失败仍写入 `failures`（`[soft]`）以便归因。字段释义见文末 [附录：指标说明](#附录指标说明) 与文档 §8。

## 2. 按操作类型（多标签）

| ops tag | n | Case Pass |
|---------|---|-----------|
| `(none)` | 13 | 100.0% |
| `add_stop` | 125 | 100.0% |
| `replace` | 82 | 100.0% |
| `shorten` | 114 | 100.0% |
| `state_change` | 139 | 100.0% |

## 3. 按难度 / 场景

### 难度

| difficulty | n | Case Pass |
|------------|---|-----------|
| high | 65 | 100.0% |
| low | 28 | 100.0% |
| medium | 107 | 100.0% |

### 场景

| style_scene | n | Case Pass |
|-------------|---|-----------|
| 亲子研学游 | 32 | 100.0% |
| 休闲购物游 | 31 | 100.0% |
| 历史文化游 | 57 | 100.0% |
| 城市漫游 | 41 | 100.0% |
| 综合观光游 | 12 | 100.0% |
| 自然公园游 | 27 | 100.0% |

## 4. 失败归因聚类（含 soft）

| failure_code | 次数 | 示例 case id |
|--------------|------|-------------|
| `theme_mismatch` | 8 | dlg-0534, dlg-0334, dlg-0509, dlg-0609, dlg-0638, dlg-0382, dlg-0245, dlg-0743 |
| `too_sparse` | 6 | dlg-0647, dlg-0463, dlg-0330, dlg-0674, dlg-0315, dlg-0602 |
| `constraint_dropped` | 1 | dlg-0141 |
| `overcrowded` | 1 | dlg-0077 |

### 硬失败

本轮 **无硬失败**（200/200 Case Pass）。

### 主要 soft 簇解读

1. **`theme_mismatch`**：多轮末态停靠与首轮主题命中率偏低（常见于休闲/亲子末轮偏商场或弱相关点）。主题分已改为对照首轮话术 + `must_scene`。
2. **`too_sparse`**：半天/公园/单点长停留等仍偏稀；密度选种修复已消除原先北京郊区「抗战纪念馆+卢沟桥」全日仅 2 站主簇（首轮 density 91.5%→99.0%）。
3. **`constraint_dropped` / `overcrowded`**：个位数 soft，建议人工抽检。

## 5. 本轮代码修复

| 改动 | 说明 |
|------|------|
| `planner._density_aware_seed_id` | 避开 hop 孤立高分种子，缓解全日 `too_sparse` |
| `metrics.score_density` 归因 | 稀少不再误标为 `overcrowded` |
| `metrics.score_theme` | 多轮按首轮 opener / `must_scene` 打主题分 |
| `runner` 汇总 | 增加 `by_op`；文档与筛选报告中文化 |

## 6. 复跑命令

```bash
python -m tripsense.eval --smoke
python -m tripsense.eval --first-turn-only
python -m tripsense.eval --multi-turn --judge stub
```

报告目录：`data/eval/reports/`。

## 附录：指标说明

完整说明见 [`docs/TripSense_评测框架_对话集200.md`](../../../docs/TripSense_评测框架_对话集200.md) **§8**（阈值以 `src/tripsense/eval/metrics.py` 为准）。

### 首轮 vs 多轮

- **首轮**：只评 `dialogue[0]` 的初始 `plan()`；表中 `adjust` 为 `—`（无 ops，恒满分）。
- **多轮**：按对话回放 `chat()`，对**末态**路线打分；可暴露增点/缩短/换点后的回归。
- 「本轮 / 修复前」是两次独立跑批的对比标签，不是同一次执行里的两个阶段。

### Case Pass（硬门禁）vs soft

- **Case Pass** = `ccp` 硬通过 **且** `overall` ≥ 难度阈值（low **0.70** / medium **0.75** / high **0.80**）。
- 表中 CCP/density/theme/… 是各量纲**单项 pass 率**，不等于 Case Pass。
- **soft**：Case 已过门禁，但单项未达 floor；写入 `failures` 且带 `[soft]`，用于归因，不算 Case 失败。

### 各指标（单项 floor）

| 指标 | 含义 | floor |
|------|------|-------|
| ccp | 机会约束满足且计划时长 ≤ 安全预算 | **1.0**（硬） |
| adjust | 多轮修改是否与用户 ops 同向 | **0.6** |
| theme | 停靠贴合主题（对照首轮话术 / must_scene） | **0.6**（文史/亲子 **0.7**） |
| geo | 相邻段旅行时间不过度异常（>90min 记异常） | **0.6** |
| density | 停靠数相对预算不过稀/过密 | **0.5** |
| retain | 硬约束（半天/亲子/低行动力）仍成立 | **0.7** |
| overall | `0.25·ccp+0.20·adjust+0.20·theme+0.15·geo+0.10·density+0.10·retain` | 见上难度线 |

### 常见 failure_code

| 码 | 含义 |
|----|------|
| `theme_mismatch` | 主题命中偏低 |
| `too_sparse` / `overcrowded` | 停靠过稀 / 过密 |
| `constraint_dropped` | 硬约束未保留 |
| `ccp_violation` | CCP/预算未满足（硬） |
| `geo_jump` | 地理跳跃异常 |
| `op_noop` / `adjust_opposite` | 调整未生效 / 方向不合理 |
