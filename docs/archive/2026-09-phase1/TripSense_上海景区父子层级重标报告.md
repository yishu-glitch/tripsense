# TripSense 上海景区父子层级重标报告

## 目标

把原始 `parent_site` 从不可靠的文本标签，升级为可用于景区内部路线规划的实体层级。原始 `poi_shanghai_clean.csv` 的 7,509 条记录保持不变；上海 MVP 只对上海市16个行政区内的记录发布推荐服务数据。

## 当前全量结果

- 原始记录：7,509 条。
- 上海市内推荐数据：5,634 条。
- 二期周边城市数据：1,875 条。
- 上海市内发现父子候选关系：835 条。
- 自动通过：252 条关系，涉及99个父景区/场馆。
- 待人工或联网复核：554 条。
- 因空间冲突等原因拒绝：29 条。
- 推荐服务发布248个直接父子关系，其余地点按独立 POI 参与检索。

自动通过不等于“人工或官网核验”。联网和人工复核状态只保留在质检文件；推荐服务只读取当前发布的精简父子关系。

## 已发现的典型问题

### 误标

- 原“豫园”父级下混入昆山、太仓、川沙、嘉定等地的同名城隍庙。
- 原“东方明珠”父级下混入启东同名景点和距离数公里的杨浦打卡点。
- “上海自然博物馆”存在自己指向自己的关系。
- “外滩公园”实际位于上海影视乐园内，不能因名称含“外滩”挂到外滩风景区。

### 漏标

- 上海植物园至少发现18个可自动确认的内部园区或设施。
- 上海动物园至少发现14个内部场馆或动物展示节点。
- 上海野生动物园、辰山植物园、朱家角、周庄、西塘等均发现大量未标注关系。
- 朱家角的放生桥原先没有父级，现通过“景区内”地址、空间一致性和官网资料进入层级。

## 判定原则

1. 完整父景点名称出现在子景点名称前缀。
2. 地址明确出现“某景区内”。
3. 父子坐标必须在该景区允许范围内。
4. 仅空间接近不能自动形成父子关系，只进入 `review`。
5. 商铺、餐饮、服务设施即使位于景区内，也默认需要复核其产品角色。
6. 原始字段始终保存在 `parent_site_raw`，新字段写入 `parent_*_v2`。

## 已登记的权威来源

- 豫园官网：https://www.yugarden.com.cn/page/articleview/introduce-01.html
- 朱家角古镇官网：https://www.zhujiajiao.com/cn/scenic/bridge/57_1120_666.html
- 上海市文化和旅游局朱家角资料：https://whlyj.sh.gov.cn/cysc/20240418/eeb7bb78799e4f1293ed326d1c5a5666.html
- 上海博物馆场馆介绍：https://www.shanghaimuseum.net/mu/frontend/pg/infomation/about
- 上海博物馆东馆导览：https://www.shanghaimuseum.net/mu/upload/202508/e303e216-d767-4bec-bc72-39d26c05f899.pdf
- 上海市人民政府外滩风景区资料：https://www.shanghai.gov.cn/citywalk/20260625/9dfb6f86b9664e018607b5cc9ec9a53c.html
- 上海迪士尼度假区官网：https://www.shanghaidisneyresort.com/zh-cn/experience?group=disneytown

## 产出文件

- `data/processed/poi_shanghai_recommendation.csv`：推荐服务使用的精简层级表，仅包含父级、包含关系、节点角色和层级。
- `data/processed/poi_shanghai_city_only.csv`：上海16区内的结构化基础数据。
- `data/processed/poi_shanghai_nearby_phase2.csv`：昆山、太仓、嘉善等二期周边城市数据。
- `data/processed/shanghai_site_hierarchy_product.json`：规划器使用的精简父子图，不含算法证据标签。
- `data/processed/shanghai_site_hierarchy.json`：自动通过的层级图。
- `data/processed/shanghai_site_candidates.jsonl`：所有候选及证据、距离、状态和错误原因。
- `data/processed/shanghai_site_verification_queue.csv`：按影响范围排序的联网核验队列。
- `data/knowledge/shanghai_site_registry.json`：父景区实体、别名、空间范围和已登记来源。

推荐服务表的层级字段固定为：

- `site_parent_id`：父景点 ID。
- `site_parent_name`：父景点名称。
- `site_relation`：当前仅使用 `inside`，表示物理上属于景区内部。
- `site_role`：`site`、`attraction`、`exhibit`、`entrance`、`service`、`transport`、`viewpoint` 或 `commercial`。
- `site_level`：层级深度，父景区为0，直接子景点为1。

`full_parent_name_in_address`、`explicit_inside_address`、`geographically_consistent` 等内容只保留在候选质检文件，不再进入产品表或面向用户的标签。

`review`、`rejected`、置信度和原始 `parent_site` 同样只存在于离线质检文件。推荐服务表只发布已经通过当前校验的父子关系；没有发布父级的 POI 按独立地点参与检索。

## 下一步

联网核验按“上海市内优先、子节点多优先、路线价值高优先”推进。每个关系应记录支持它的具体网页，而不只是父景区官网首页。经核验后，规划器可在城市路线中把父景区视为一个站点，在进入该站点后再根据时间和兴趣展开内部子路线。
