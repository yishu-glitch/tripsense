# TripSense

**AI 旅行规划与记录助手** · *Hybrid AI travel planner & trip journal*

> 把「想去哪儿、能走多久、现场怎么变」收成一份可商量、可保存、可继续记的行程。  
> 上海 MVP 可本地一键演示；无大模型 Key 也能稳定跑通规划闭环。

仓库：[github.com/yishu-glitch/tripsense](https://github.com/yishu-glitch/tripsense)

---

## 这是什么

TripSense 面向一个真实痛点：用户并不缺景点信息，缺的是把模糊偏好、有限时间和不断变化的现场条件，组织成**适合自己的可执行方案**，并在旅行中/后继续调整与记录。

产品闭环是三段连续能力，而不是一次性生成攻略：

| 阶段 | 用户得到什么 |
|------|----------------|
| **规划** | 从城市灵感 / 自然语言出发，生成有取舍、有理由的单日或多日路线 |
| **调整** | 继承当前行程上下文，局部加点、删点、换点或放慢节奏，不轻易推倒重来 |
| **记录** | 在计划内或计划外地点留下文字、心情与照片引用，让攻略长成手账 |

产品原则（可写进面试口述）：先理解当下状态再出线；时间是建议不是任务；每次调整基于当前路线；事实不确定时说未知，不让模型猜营业/天气。

---

## 为什么是「LLM + 算法」混合

纯 LLM 行程看起来聪明，但容易丢点名站、超时、编造营业事实。TripSense 的产品选择是 **可靠性优先**：大模型负责语义与取舍表达，算法负责可行性与可复现。

```text
用户话术
  → B  Soft Prior（LLM）   意图补丁 / want_places / 偏好软先验
  →    Planner（CCP 等）   时间机会约束 + 选点排序，保证硬可行
  → A  Choose（LLM，可选） 多候选路线中择一表达
  → C  Replan（LLM+Ops）   多轮：PlanOps + 局部重排，默认最小改动
```

面试可讲清的边界：

- **算法硬约束**：时间预算（机会约束 CCP）、地理成团、锁定前缀、营业/未知态降级。
- **模型软智能**：理解「慢慢走 / 想去外滩 / 雨天换室内」、软先验、推荐理由与日记润色。
- **无 Key 仍可演示**：确定性本地解析 + 规划器完整可跑；有 Key 时语义更准，不改变可行性判定。

更多规格见 [`docs/TripSense_产品研究实现对齐说明.md`](docs/TripSense_产品研究实现对齐说明.md)、[`docs/TripSense_LLM提示词规范.md`](docs/TripSense_LLM提示词规范.md)。

---

## 关键产品决策（面试可展开）

这些不是「功能清单」，而是做过取舍的设计点：

1. **`want_places` 点名站** — 用户说「想去外滩」时，专名写入意图并由算法解析入线；禁止只改场景标签却丢掉专名。
2. **Session 继承** — 多轮未改时间则继承上一轮预算与行程上下文；调整默认 lock + 局部 replan，而不是每轮重开一张空白表。
3. **Place supplement** — 本地库没有的点名站仍保留中文名；检索补全坐标/区划后写入 `ext:` 候选与 `data/pending/poi_supplements.jsonl`，质量不足时诚实说明「先按区划/邻近排一版」，不静默丢弃。
4. **模糊换站澄清** — 「换一个更轻松的」类请求在信息不够时先澄清，避免胡乱替换整条线。
5. **Signature landmarks** — 「经典 / 打卡 / 玩一天」等表述用签名地标软先验 + core 分层，同 `parent_site` 折叠，减少父子景点重复入线。

交互与理由展示见 [`docs/TripSense_核心交互设计说明_v2.0.md`](docs/TripSense_核心交互设计说明_v2.0.md)、[`docs/TripSense_轻量推荐理由交互规范.md`](docs/TripSense_轻量推荐理由交互规范.md)。

---

## Demo

- **本地 MVP（推荐）**：`python -m tripsense.mvp` → 浏览器打开 `http://127.0.0.1:8000/`
- **手机临时公网**：本机已跑 MVP 时，另开 `cloudflared tunnel --url http://127.0.0.1:8000`（地址会变，适合当天演示）
- **长期托管**：仓库含 `Dockerfile` / `render.yaml`；密钥用环境变量配置，勿提交进 git。详见 [`docs/TripSense_MVP_运行与部署.md`](docs/TripSense_MVP_运行与部署.md)
- **静态原型**：`web/TripSense_产品原型_v2.0.html`（GitHub Pages 只能托管静态页，**不能**跑规划 API）

当前无稳定 7×24 公网 Demo URL 时，以本地 / Cloudflare 临时隧道为准。

---

## 技术栈（简述）

| 层 | 选型 |
|----|------|
| 规划内核 | Python：意图解析、卡尔曼偏好、CCP 时间预算、束搜索 + 2-opt |
| 服务 | FastAPI + SQLite（行程 / 记录 / 日记） |
| 前端原型 | 单页 HTML 产品原型（规划 / 对话 / 记录同屏） |
| 可选增强 | LLM（意图软先验 / 择优 / 理由 / 日记）、高德 Web 服务（天气等） |
| 知识分层 | 稳定 RAG · 结构化 POI · 实时工具（未知则明确 `unknown`） |

---

## 快速运行 MVP

```powershell
pip install -e ".[api]"
python -m tripsense.mvp
```

- Demo：`http://127.0.0.1:8000/`
- OpenAPI：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/api/v1/health`

无需 API Key 即可规划、保存行程、写记录、生成本地模板日记。可选：

```powershell
$env:AMAP_WEB_SERVICE_KEY = "你的高德 Web 服务 Key"   # 天气
$env:TRIPSENSE_LLM_API_KEY = "你的大模型 Key"          # 语义增强；详见部署文档
```

无 Key 回归 / 离线 demo：

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
python -m tripsense.demo --city shanghai --text "今天想慢慢走，看看老建筑，大概四小时"
```

完整部署与手机联调见 [`docs/TripSense_MVP_运行与部署.md`](docs/TripSense_MVP_运行与部署.md)。

---

## 评测与指标（入口，不堆报告）

产品侧主文档：[`docs/TripSense_评测与指标体系_v2.1.md`](docs/TripSense_评测与指标体系_v2.1.md)

- 采用指标：规划保存转化、调整后保存、回访与记录转化等（MVP 不以「计划完成率」为北极星）。
- 质量护栏：Eval-200 对话回归；子集筛选见 [`data/eval/SELECTION_REPORT.md`](data/eval/SELECTION_REPORT.md)。
- 跑法概览：`python -m tripsense.eval --smoke`（无 Key）；详细口径与 Case Pass 定义见指标文档附录。
- 过程报告在 `data/eval/reports/`（含归因笔记、多轮摘要、badcase 复跑等），面试讲框架与 1–2 个 case 即可，不必通读全部 dump。

---

## 文档导航

校招快读：[`docs/TripSense_作品集导读.md`](docs/TripSense_作品集导读.md)（约 10 分钟）  
完整分组索引：[`docs/README.md`](docs/README.md)

| 分组 | 从这里读 |
|------|----------|
| 产品 / 设计 | 作品集导读、综合产品设计、原型依据、交互、旅行风格、推荐理由 |
| AI / 架构 | AI 与技术架构、知识分层、产品–研究对齐、LLM 提示词 |
| 评测 | 指标体系 v2.1、Eval-200 筛选报告 |
| 部署 | MVP 运行与部署 |

根目录 `design-qa.md` 为原型 Design QA 记录，非产品主文档。

---

## 仓库结构（精简）

```text
src/tripsense/core/      意图 · 约束 · 规划器（可离线）
src/tripsense/llm/       提示词与 LLM 协作层
src/tripsense/api/       FastAPI
src/tripsense/eval/      评测与指标实现
web/                     产品交互原型
data/knowledge/          稳定知识种子
data/processed/          上海等 POI 服务表
data/eval/               Eval-200 与报告
docs/                    产品 · 评测 · 提示词 · 部署
tests/                   行为与公式回归
```

---

## 作者说明

本仓库为个人项目（校招作品向）：覆盖问题定义、关键交互、LLM×算法边界、可演示 MVP 与离线评测框架。建议阅读顺序：作品集导读 → README → 本地 Demo → 产品主文档；技术细节以代码与专题文档为准。
