# TripSense

TripSense 是一款灵活、智能、懂用户的旅行规划与记录助手。当前版本以上海为 MVP 主场景，完成可复现的规划内核和三层知识架构，并为 Web MVP 提供稳定 API 边界。

## 产品原则

- 先理解用户此刻的状态，再生成路线。
- 时间是可以调整的建议，不是必须完成的任务。
- 用户可以随时加点、删点、替换或放慢节奏。
- 每次调整都基于当前路线继续，而不是忘掉上一轮重新开始。
- 规划完成后继续记录实际游览过程，为后续建议提供依据。

## 当前结构

```text
src/tripsense/core/   离线可运行的意图、约束、场景和路线规划内核
src/tripsense/knowledge/ 稳定 RAG、结构化 POI 与实时工具编排
src/tripsense/api/    FastAPI 接口层
web/                  可独立运行的产品交互原型
data/knowledge/       上海稳定知识种子及数据约定
data/processed/poi_shanghai_recommendation.csv 上海后端直接读取的 POI 服务表
data/processed/shanghai_site_hierarchy_product.json 规划器使用的精简层级图
data/                 清洗后的北京/上海 POI、距离图、停留时间与对话数据
docs/                 产品诊断、路线图与知识架构说明
research/             旧实验结果与研究资产
tests/                核心公式和产品行为测试
```

## 本地验证

无需任何 API Key：

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
python -m tripsense.demo --city shanghai --text "今天想慢慢走，看看老建筑和真实的街区生活，大概四小时"
```

## 查看产品原型

`web/TripSense_产品原型_v2.0.html` 是当前上海 MVP 的交互基准，覆盖旅行风格选择、
AI 对话与路线同屏调整、轻量推荐理由、时间拖动和动态攻略保存。原型目前使用本地演示
数据；通过本地 API 运行并配置高德 Web 服务 Key 后，路线接口会读取当前城市天气。
天气、交通、开放和活动信息默认只生成提醒，只有用户确认后才允许影响路线。

可直接用浏览器打开 HTML，或在 `web` 目录启动本地静态服务：

```powershell
python -m http.server 8765
```

## 启动 API

安装 Web 依赖后，可用一个进程同时启动产品原型与 API：

```powershell
pip install -e ".[api]"
python -m tripsense.mvp
```

本机浏览器打开 `http://127.0.0.1:8000/`。默认绑定 `0.0.0.0`，手机请用电脑局域网 IP（`ipconfig` 查 WLAN IPv4），例如 `http://192.168.x.x:8000/`，不要用手机上的 `127.0.0.1`。若 `netstat` 显示只监听 `127.0.0.1:8000`，请重启并显式带上 `--host 0.0.0.0`。首次启动会自动创建
`data/tripsense-demo.db`，不需要 API Key、账号或外部数据库。原型在该地址运行时，
保存行程、添加旅途记录和生成 AI 旅行日记会写入真实 SQLite；直接双击 HTML 时则保持
纯前端演示模式。

### 手机公网演示

- **当天临时链接**：本机 MVP 运行时执行 `cloudflared tunnel --url http://127.0.0.1:8000`，用打印的 `https://*.trycloudflare.com` 在手机打开。
- **长期托管**：仓库含 `Dockerfile` / `render.yaml`。推到 GitHub 后用 [Render](https://render.com) Docker Web Service 部署；在面板配置 `TRIPSENSE_LLM_API_KEY`、`AMAP_WEB_SERVICE_KEY`（勿提交进 git）。详情见 `docs/TripSense_MVP_运行与部署.md`。
- **GitHub Pages**：只能托管 `web/` 静态原型，**不能**跑聊天/规划 API。

如需指定端口和数据库：

```powershell
python -m tripsense.mvp --port 8123 --db data/my-demo.db
```

也可以只启动 API 开发服务器：

```powershell
pip install -e ".[api]"
uvicorn tripsense.api.app:app --reload
```

核心规划器默认采用确定性本地解析，外部大模型只作为可选的语义增强层，因此断网或没有 Key 时仍可稳定演示。

### 启用高德天气

在高德开放平台创建服务平台为“Web 服务”的 Key。启动 MVP 前在同一个 PowerShell
窗口设置环境变量，随后重新启动服务：

```powershell
$env:AMAP_WEB_SERVICE_KEY = "你的高德 Web 服务 Key"
python -m tripsense.mvp
```

访问 `http://127.0.0.1:8000/api/v1/health`；返回
`"realtime":"amap-weather"` 表示天气服务已启用。未配置或请求失败时自动返回
`unavailable`，不会阻断路线生成。当前 MVP 会根据用户选择的上海或北京分别使用对应
城市编码。Key 仅由后端进程读取，不要写入 HTML 或提交到仓库。

`POST /api/v1/routes/plan` 默认 `apply_realtime=false`，只返回
`realtime_notices` 提醒。用户明确同意按提醒调整时，再以
`apply_realtime=true` 重新请求；这是天气、交通、开放与活动信息共同遵守的产品边界。

### MVP 接口

- `POST /api/v1/routes/plan`：生成可解释的本地路线；
- `GET/POST /api/v1/journeys`：列出或保存行程；
- `GET /api/v1/journeys/{id}`：读取行程与地点状态；
- `POST/GET /api/v1/journeys/{id}/records`：添加或读取照片引用、文字与自定义心情；
- `POST /api/v1/journeys/{id}/diaries/generate`：根据路线和记录生成并保存旅行日记；
- `GET /api/v1/journeys/{id}/diaries/latest`：读取最新旅行日记；
- `GET /docs`：查看交互式 OpenAPI 文档。

当前旅行日记使用可复现的本地模板生成器，确保无模型凭证也能完成 demo；后续可在不改变接口的前提下替换为受约束的大模型生成器。

## 上海服务层数据

后端通过 `src/tripsense/core/data.py` 固定读取
`data/processed/poi_shanghai_recommendation.csv`。该文件由高德增强表、POI
复核结论、景区父子层级和上海距离图共同生成，不应手工修改。

重新构建：

```powershell
$env:PYTHONPATH = "src"
python scripts/build_shanghai_serving_pois.py --root .
```

构建脚本会执行以下处理：

- 排除复核结论为“从服务库排除”“暂不入服务库”的记录；
- 普通门店不进入静态服务层，商场和商业街按场景保留；
- 使用有效的高德营业时间，拒绝把商圈名称等噪声当作开放时间；
- 景区入口和设施型子地点不作为旅行目的地；
- 只有顶层 POI、距离图节点且具备推荐资格的记录才标记为路线候选；
- 开放、预约、票务和拥挤度统一标记为实时核验项。

构建统计写入 `data/audit/shanghai_serving_build_summary.json`，机器可读的
复核输入为 `data/audit/shanghai_poi_review_decisions.csv`。

## 第一阶段知识架构

- 历史、文化、建筑、体验特征进入稳定知识 RAG。
- 坐标、类别、行政区、距离和经核验营业时间进入结构化库。
- 天气、开放、拥挤、预约和交通通过实时工具接口获取。

规划接口会随每个地点返回证据与实时状态。实时工具尚未连接时，系统明确返回 `unknown`，不会让语言模型猜测实时事实。详细设计见 `docs/TripSense_第一阶段知识架构.md`。

除路线接口外，`POST /api/v1/knowledge/search` 可查看进入路线优化前的候选地点、数据质量标记和分层证据。

## 高德详情小样本探针

探针默认从上海数据中选择 6 个代表性 POI，核验图片、今日/每周营业时间、父子 POI、入口和出口坐标等高级字段。Key 仅从环境变量读取，不写入文件或日志：

```powershell
$env:PYTHONPATH = "src"
$env:AMAP_WEB_SERVICE_KEY = "你的高德 Web 服务 Key"
python scripts/probe_amap_details.py --root .
```

结果会覆盖写入 `data/audit/amap_probe/`，包括原始响应、扁平明细和字段覆盖率摘要；探针数据属于核验依据，不作为临时缓存。

确认探针质量后，先运行 100 条可续跑回填：

```powershell
python scripts/backfill_amap_details.py --root . --limit 100
```

复核 `data/audit/amap_backfill_summary.json` 和 `data/processed/amap_child_candidates.csv` 后，再使用 `--all` 完成剩余数据。脚本会跳过已经写入 `data/source/amap_detail_backfill.jsonl` 的 POI；高德子 POI 只生成候选证据，停车场、售票处、游客中心等不会进入景点关系。
