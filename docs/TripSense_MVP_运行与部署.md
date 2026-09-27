# TripSense MVP 运行与部署

## 1. 当前可交付边界

本地 MVP 已贯通以下路径：

1. 根据自然语言生成上海路线；
2. 保存并列出多份行程，供产品侧切换；
3. 为计划内或计划外地点添加天数、文字、自定义心情和照片引用；
4. 使用路线与旅途记录生成、保存并重新读取 AI 旅行日记；
5. 同一 FastAPI 进程提供原型页面、OpenAPI 与业务接口。

该闭环不依赖大模型 Key、地图 Key、云数据库或用户账号，因此适合产品评审、交互演示和自动化回归。配置高德 Web 服务 Key 后可启用当前城市天气；未配置时仍明确返回未知，不由模型猜测。

## 2. 本地运行

```powershell
cd D:\Codex\tripsense
pip install -e ".[api]"
python -m tripsense.mvp
```

访问地址（本机）：

- 产品 demo：`http://127.0.0.1:8000/`
- OpenAPI：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/api/v1/health`

默认绑定 `0.0.0.0:8000`，本机仍可用 `127.0.0.1`；手机需用电脑的局域网 IP（同一 Wi‑Fi）。在 PowerShell 运行 `ipconfig`，查看 WLAN 的 IPv4，例如：

```text
http://192.168.x.x:8000/?v=route-card
```

不要在手机上打开 `http://127.0.0.1:8000/`——那是手机自己，不是电脑。若手机仍打不开：

1. 用 `netstat -ano | findstr :8000` 确认监听是 `0.0.0.0:8000`（若是 `127.0.0.1:8000` 则手机永远进不来，请重启：`python -m tripsense.mvp --host 0.0.0.0 --port 8000`）；
2. 确认手机与电脑同一 Wi‑Fi；
3. Windows 防火墙已放行 Python 入站时一般足够；仅本机调试可改回 `--host 127.0.0.1`。

### 可选：临时公网 HTTPS（手机点链接）

本机 MVP 已在跑时，另开终端：

```powershell
cloudflared tunnel --url http://127.0.0.1:8000
```

终端会打印 `https://xxxx.trycloudflare.com`，手机浏览器直接打开即可（电脑需保持 MVP + tunnel 运行）。该地址会随重启变化，适合当天演示，不适合长期分享。

默认数据保存于 `data/tripsense-demo.db`。也可运行：

```powershell
python -m tripsense.mvp --host 127.0.0.1 --port 8123 --db data/review.db
```

### 可选：启用高德天气

高德控制台中创建服务平台为“Web 服务”的 Key，然后在启动服务的同一个 PowerShell
窗口中设置：

```powershell
$env:AMAP_WEB_SERVICE_KEY = "你的高德 Web 服务 Key"
python -m tripsense.mvp
```

健康检查中的 `realtime` 为 `amap-weather` 表示配置生效。当前支持的城市会根据用户
所选城市自动切换编码：上海 `310000`、北京 `110000`。Key 缺失、城市不支持、超时或
高德返回错误时均降级为 `unavailable`，路线仍可生成，且响应和日志不会包含 Key。

实时信息遵循“先提醒、后确认”的边界：默认 `apply_realtime=false`，天气、交通、开放、
预约、拥挤和活动变化不得自动删点、改序或调分，只写入 `realtime_notices`。用户明确接受
提醒后，客户端才以 `apply_realtime=true` 重新请求路线。

## 3. 自动化验证

```powershell
python -m pytest -q -p no:cacheprovider
```

测试覆盖路线约束、上海知识服务层、行程持久化、计划内/计划外记录、自定义心情、日记生成、HTTP 错误语义、浏览器 API 客户端以及产品原型关键行为。

## 4. MVP 数据模型

- `journeys`：城市、模式、场景、状态、路线快照与更新时间；
- `journey_stops`：路线地点、顺序、完成状态、实际停留时间和备注；
- `journey_records`：实际发生的天数、地点、心情、文字与照片引用；
- `journey_diaries`：生成版本、来源记录数、结构化日记内容和生成时间。

旅行记录允许不绑定原计划地点。这样用户临时走进的书店、咖啡馆或街区仍能进入旅行日记，而不会被“必须完成路线”的产品逻辑排除。

## 5. 算法与生成策略

当前路线由本地意图解析、分层知识检索、机会约束时间预算、卡尔曼偏好权重、束搜索构造与 2-opt 排序共同生成；算法层不依赖大模型，无 Key 时完整可跑。配置 Key 后，大模型负责意图软先验、多候选择优、推荐理由与日记润色，不参与可行性判定。公式与依据见 `TripSense_产品研究实现对齐说明.md` 的“当前实现的算法规格”。旅行日记在无模型凭证时由 `local-template-v1` 按天组织，保证输出可复现。

下一阶段模型增强应采用适配器，而不是把供应商 SDK写进业务层：

```text
DiaryGenerator
  ├─ LocalTemplateDiaryGenerator（当前默认）
  └─ StructuredLlmDiaryGenerator（后续可选）
```

模型只能重写结构化日记中的标题、摘要与叙事，不能虚构地点、照片、心情或实时事实。生成失败时回退到本地模板。

## 6. 公网托管（手机长期可点链接）

GitHub Pages **不能**跑 FastAPI/聊天 API。完整 MVP 请用容器托管（推荐 Render 免费档）。

仓库已含：

- `Dockerfile`：`python -m tripsense.mvp --host 0.0.0.0 --port $PORT`
- `render.yaml`：一键 Web Service 蓝图

步骤：

1. 将本仓库推到 GitHub（公开仓库便于分享 demo）；
2. 打开 [Render](https://render.com) → New → Blueprint，选中该仓库；或 New Web Service → 选仓库 → Runtime=Docker；
3. 在 Dashboard 配置密钥（**不要**写进 git）：
   - `TRIPSENSE_LLM_API_KEY`（可选，无 Key 仍可规划/演示，聊天语义增强降级）
   - `AMAP_WEB_SERVICE_KEY`（可选，天气）
   - 以及 `.env.example` 中的 `TRIPSENSE_LLM_*` 如需覆盖默认值；
4. Deploy 完成后用 Render 提供的 `https://xxxx.onrender.com/?v=route-card` 在手机打开。

本地复现容器：

```powershell
docker build -t tripsense .
docker run --rm -p 8000:8000 --env-file .env tripsense
```

免费档冷启动可能要等几十秒；SQLite 在免费实例上不持久，仅适合演示。

## 7. 生产部署建议

生产版建议拆为：静态前端/CDN、FastAPI 容器、PostgreSQL、私有对象存储、后台任务队列、模型网关和实时工具适配器。SQLite 仅用于单机 demo，不用于多实例部署。

迁移顺序：

1. 保持现有 HTTP 协议，增加 PostgreSQL 存储适配器；
2. 引入匿名会话和正式账号之间的数据归属迁移；
3. 照片改为预签名直传，对象元数据继续写业务库；
4. 日记生成改为异步任务，并提供生成中、成功、失败状态；
5. 接入模型网关与评测日志；
6. 在已经接入的高德天气适配器后继续补充预约、开放、活动和交通工具。

## 8. 在哪里切换和对比大模型

大模型已接入，但**只负责语义与表达**：意图软先验、多候选择优、推荐理由和日记润色。
选点、排序、时间与距离仍由 `src/tripsense/core/planner.py` 的算法决定，模型不能直接产出
最终地点列表，也不能改写机会约束。未配置 Key 时全部回退本地：意图走确定性解析，日记走
`src/tripsense/core/journey.py` 的 `local-template-v1`，因此未登录、无 Key、断网时仍可完整演示。

`/api/v1/chat/respond` 的 `llm` 字段会区分 `intent_fallback`、`ops_fallback`、
`alternatives_fallback`、`reasons_fallback` 并带上 `error`，便于判断某一层是否真的走了模型。

模型入口统一放在项目根目录的 `.env`。复制 `.env.example` 后，只改这四项：

```text
TRIPSENSE_LLM_PROVIDER=openai-compatible
TRIPSENSE_LLM_BASE_URL=供应商的兼容接口地址
TRIPSENSE_LLM_MODEL=要测试的模型名
TRIPSENSE_LLM_API_KEY=对应密钥
```

密钥只放本机 `.env`，不写进 HTML、源代码、提交记录或测试日志。模型适配代码应集中在
`src/tripsense/llm/`（下一阶段创建），业务层只调用统一接口。更换供应商时不修改路线、
记录、日记存储和 API 路由。

模型对比使用同一份匿名化输入和同一份结构化输出约束，至少记录：模型名、提示词版本、
耗时、输入/输出 token、估算成本、JSON 合规率、地点事实一致性和人工偏好。模型生成失败、
超时或结构不合规时，必须自动回退到 `local-template-v1`。

## 8. 尚需产品或外部授权的事项

以下事项本轮未擅自决定，也不阻塞本地 demo：

- 登录方式与匿名数据合并规则；
- 云厂商、部署地域、域名与预算；
- 对象存储与照片保留期限；
- 大模型供应商、成本上限和内容审核策略；
- 地图、天气、预约和交通数据的商业授权；
- 分享日记的默认可见范围与撤回机制。

这些选择确定后，可以在现有接口和存储边界后替换实现，无需重做产品原型。
