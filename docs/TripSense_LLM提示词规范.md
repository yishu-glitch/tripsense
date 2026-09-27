# TripSense LLM 提示词规范

> 代码入口：`src/tripsense/llm/prompts.py`（产品运行时）  
> 评测入口：`src/tripsense/eval/prompts.py`（UserSim / Judge）  
> 校验仍由 `provider.py` / `plan_ops.py` 白名单负责；提示词只约束语义与输出形态，**不代替算法判定可行性**。

本文列出所有会调用大模型的环节、各自职责边界、输入输出与编写原则。后续改文案优先改上述两个 `prompts.py`，并同步更新本表。

---

## 1. 总原则

1. **搭档关系，而非自我阉割**：模型与算法分工协作。提示词应写清产品主基调、语言风格与本环节优先产出；避免「你只负责…」这类绝对句，以免压缩模型对隐含意图的理解与泛化。契约用「优先产出 / 输出边界」表达，用「算法会承接可行性」说明协作，而不是禁止思考。
2. **算法硬约束、模型软智能**：时间预算、CCP、地理成团、锁定前缀由规划器保证；LLM 侧重意图、软先验、PlanOps 建议、文案与主观评测。
3. **约定 JSON 可解析**：禁止 Markdown 围栏；字段须落在现有校验白名单内。
4. **事实克制**：不编造 POI、营业、天气、票务、交通；不确定就说未知或邀请确认。
5. **最小改动（重规划）**：多轮优先 lock + 局部 replan，无故不重排全日。
6. **产品口吻**：凡 `assistant_reply` / 理由 / 日记，对齐「懂路线、能商量的同行伙伴」——温暖克制、留弹性、不施压打卡。

各产品侧提示词均内嵌同一段 **TripSense 产品上下文**（定位、主基调、语言方式、与算法的协作关系），再写本阶段角色背景。

---

## 2. 产品运行时提示词一览

| ID | 函数 | 调用时机 | 职责 | 禁止越权 |
|----|------|----------|------|----------|
| P1 | `prompt_intent` | 旧路径 / 兼容解释 | 把用户话解析成意图字段 | 不生成路线、不声称实时事实 |
| P2 | `prompt_soft_prior` | 首轮 `chat` Phase B | 软先验：intent_patch（含 want_places）+ 偏好权重 + 标签 | 不编 poi_id、不改可行性；点名景点用中文短名 |
| P3 | `prompt_choose_alternative` | 算法给出多候选后 | 选一条 profile | 不改地点列表 |
| P4 | `prompt_plan_ops` | 有 `current_plan` 时 Phase C | 产出 PlanOps + intent_patch | 不得发明 poi_id；新点名写入 want_places；库中没有也写中文名 |
| P4b | `prompt_supplement_place` | 点名解析失败后 | 联网检索补 name/district/category/**优先真实坐标** | 不编 poi_id、开放时间、票价；无精确坐标可标 unresolved / 只给区划，由算法软钉，不伪装成实测 |
| P5 | `prompt_reasons` | 路线确定后 | 路线/站点短理由措辞 | 不编造入选原因之外的事实 |
| P6 | `prompt_diary` | 日记润色 | 改写 title/narrative | 不增删地点与记录条目 |

### 2.1 场景分类（与 `intent.py` 一致）

`scene` ∈ 综合观光游 / 历史文化游 / 自然公园游 / 城市漫游 / 休闲购物游 / 亲子研学游  

`mode` ∈ balanced / relaxed / full / deep / photo  

`pace` ∈ slow / normal / fast  

`categories` ⊆ attraction / heritage / park / museum / culture / leisure  

`want_places`：用户点名的景点短中文名（最多 6 个）。与 `prefer_near`（固定事项区域）分开。主题词（公园/老建筑）不要写入。算法对照景点库解析并软锁；库中没有时优先高德检索坐标，再由 LLM 结构化补全，写入 `ext:` 候选并落盘 `data/pending/poi_supplements.jsonl`（不编正式 `poi_id` / 营业 / 票价，**有无坐标都落盘**）。质量阶梯：(a) 真实 lat/lng → 旅行图近似注入；(b) 仅区划 → 区县质心或同区同类目录 POI；(c) 仅名字 → 词面软加成 / 邻近已确认站 / 市中心先验软钉并抬高旅行不确定性。公开检索无精确坐标时，回复诚实说明「先按区划/邻近已确认地点排一版，入库后再校正」，不静默丢弃点名站。

### 2.2 PlanOps 允许集合

`lock` / `remove` / `prefer_categories` / `avoid_outdoor` / `replan(scope=all|tail)`  

详见 `src/tripsense/core/plan_ops.py`。

### 2.3 与产品文案规范的关系

推荐理由长度与语气对齐 `docs/TripSense_轻量推荐理由交互规范.md`：路线级一句、地点级短句，先贴用户状态，避免「必去/完美」。

---

## 3. 评测多 Agent 提示词一览

| ID | 函数 | 角色 | 何时使用 |
|----|------|------|----------|
| E1 | `prompt_usersim_open` | 用户模拟（开放生成下一轮） | `--multi-turn` 且启用 LLM UserSim |
| E2 | `prompt_usersim_behavior_*` | 按 ops 标签约束用户话术风格 | add_stop / shorten / replace / state_change |
| E3 | `prompt_judge` | 评测员 | 规则分之外的主观项与失败归因补充 |

规则 Judge（`RuleJudgeAgent`）不依赖 LLM；E3 仅补：理由是否答非所问、亲子适宜主观判断、调整是否「看起来」同向。

失败归因字段与 `FailureAttribution` 对齐，见 `docs/TripSense_评测与指标体系_v2.1.md` 附录 A.4。

---

## 4. 编写 / 迭代检查清单

改某一层提示词后：

1. 用 3 条手工话术打对应 API（或 `TripSenseService.chat`），确认 JSON 可解析、字段在白名单内。  
2. 跑 `python -m tripsense.eval --smoke`（无 Key）确认回归不依赖 LLM 的路径仍绿。  
3. 若改 P4/P5，抽 5 条 Eval-200 多轮 case 人工看 PlanOps 是否最小改动、理由是否空话。  
4. 更新本文件「修订记录」。

---

## 5. 修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1 | 2026-09-26 | 首版：补齐产品六层 + 评测三层职责表 |
| 0.2 | 2026-09-26 | 产品侧六层改为高可用详版：schema、映射、few-shot、禁止项；提高 max_tokens |
| 0.3 | 2026-09-26 | 各阶段补全产品主基调与角色背景；弱化「只负责」绝对表述，改为「优先产出 / 输出边界 / 协作关系」，保留泛化空间 |
| 0.4 | 2026-09-26 | Phase B/C：固定时段事项缺地点或时间则追问（needs_clarification），齐了才写 prefer_near + busy_* 并重排 |
| 0.5 | 2026-09-26 | Phase B/C/intent：用户点名景点写入 `want_places`，交给算法解析入线；禁止只改场景、丢掉专名 |
| 0.6 | 2026-09-26 | 点名可能不在本地库仍写 want_places；解析失败走 P4b 补候选。多轮未改时间则继承上一轮预算 |
| 0.7 | 2026-09-26 | P4b：无精确坐标时不再 fail-closed；按区划/邻近锚点软纳入并落盘 pending，回复诚实说明近似 |
| 0.8 | 2026-09-26 | 标志性/经典/打卡/古建筑/玩一天：软先验签名地标 + scoring tier=core；同 parent_site 折叠；模糊换站澄清；加室内保留户外；禁止描述性占位 want_places；同城 geo 护栏 |
| 0.9 | 2026-09-26 | Phase C：换成室内须 ops+重排；胡同/民国=ADD 不删大公园；文艺人少主题切换不追问换哪站；咖啡馆区域锚点 |

### 0.2 产品侧结构（每层统一包含）

1. 角色与边界（相对算法）  
2. 输入字段说明  
3. 输出 JSON schema（与校验白名单一致）  
4. 场景/操作映射与决策顺序  
5. 文风与示例  
6. 硬禁止项  

实现文件：`src/tripsense/llm/prompts.py`。
