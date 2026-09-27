# TripSense 地点详情原型 — Design QA

- source visual truth path: `C:\Users\18325\Desktop\个人资料\Tripsense-app\prototype.html`（Screen 5：文化介绍卡片）以及用户确认的“显式了解地点入口 + 出发前了解预告”产品说明
- implementation URL: `http://127.0.0.1:8123/?demo_weather=rain`
- implementation file: `D:\Codex\tripsense\web\TripSense_产品原型_v2.0.html`
- browser-rendered evidence: Codex in-app browser captures（路线卡片、上海博物馆详情首屏、文化卡片展开、返回路线）
- viewport: 默认浏览器视口 701 × 871 px；窄屏复核 360 × 800 CSS px
- source dimensions: 黄山参考原型为可交互 HTML，非单张固定像素稿
- implementation dimensions: 页面容器最大宽度 440 CSS px；以 deviceScaleFactor 1 的浏览器视口检查
- density normalization: 均按 CSS 像素检查，无 @2x 位图对比
- state: 未登录可用；上海；朋友；3 天；雨天提醒演示；D1 上海博物馆；文化卡片展开

## Full-view comparison evidence

- 路线卡片保留原有停留时间、推荐理由、标签和预约提醒。
- “了解地点”使用文字按钮而非单独箭头，并与停留时间形成同一操作组。
- “出发前了解”问题预告直接出现在卡片正文中，降低功能发现成本。
- 详情页沿用 TripSense 现有紫、薄荷绿、珊瑚色和纸张卡片语言，没有引入新的视觉体系。

## Focused region comparison evidence

- 路线卡片操作区：检查 440px 主视图和 360px 极窄视图；360px 时操作区自动移到标题下方，避免文字竖排。
- 地点详情首屏：检查真实地点图片、标题、标签、个性化推荐理由、地点介绍和实用提醒。
- 文化卡片：检查展开/收起、现场观察提示和官方资料入口。
- 返回行为：从详情返回后恢复路线浏览位置。
- 浏览器控制台：0 条 warning/error。

## Findings

- [P3] 南京路步行街目前复用了通用上海街景图片。
  - Location: `placeDetails['南京路步行街'].image`
  - Evidence: 内容与地点相关，但不是南京路的专属取景。
  - Impact: 不影响交互验证；正式内容接入时会降低地点辨识度。
  - Fix: 内容库接入后替换为有授权信息的南京路专属图片并记录来源。

## Comparison history

1. First render — [P1] 文化预告被放入卡片标题行，上海博物馆标题被挤成竖排。
   - Fix: 将文化预告移到正文标签下方，标题行仅保留停留时间与“了解地点”。
   - Post-fix evidence: 440px 路线截图中地点名恢复横排，文化问题占据独立整行。
2. Narrow render — [P2] 360px 下停留时间被两个右上角控件压窄并竖排。
   - Fix: `@media(max-width:360px)` 下将操作区移动至标题下一行，两枚按钮保持横排。
   - Post-fix evidence: 360 × 800 路线截图中“约 90 分钟”和“了解地点”均完整显示。

## Open Questions

- 黄山参考原型的本地 `file://` 页面被浏览器安全策略阻止，无法生成同视口的持久化源截图并进行严格的同图对照；当前比较基于其源结构、用户确认的产品说明与浏览器渲染结果。

## Implementation checklist

- [x] 显式“了解地点”入口
- [x] 文化内容预告
- [x] 个性化推荐理由
- [x] 地点介绍与真实图片
- [x] 出发前实用提醒
- [x] 可展开文化卡片与官方来源
- [x] 返回路线并保留位置
- [x] 440px 与 360px 响应式检查
- [x] 键盘可达的语义按钮与图片替代文本
- [x] 浏览器控制台检查

final result: blocked

Blocker: 无法在浏览器中打开并捕获本地黄山参考原型，因此不具备 Product Design QA 所要求的持久化、同视口源视觉证据。实现本身的浏览器交互与响应式检查已通过。
