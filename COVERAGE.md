# 舒适度数据核验矩阵 — 延迟抓取里的全部运营方

核验日期 2026-10-28。范围 = `ooep/delay` 运行时实际会尝试的运营方全集：
`realtime_operators`（`data/lines.json`）+ `feed_jobs` 里的 77 条自有源 +
`ODPT_KEY` 存在时追加的 13 个 ODPT 运营方（工作流已配置该 secret）。

核验方法：
1. 逐家读延迟源的实际 payload（位置/延误字段）与官方 App 后端文档；
2. 有 App/网页/PDF 线索的，直接抓取验证字段（不依赖二手资料）；
3. **只接入公开、无鉴权、可机器读取的实时数据**；App-only、预报、PDF 一律不接，
   避免把预报/平均值冒充实时值。宁漏不误报。

图例：○ 有 · △ 部分/仅预报 · — 无公开实时数据

## 一、已接入（5 家）

| 运营方 | 列车拥挤 | 逐车拥挤 | 车内温度 | 无障碍/车内设备 | 公开渠道（已实测） | 备注 |
|---|---|---|---|---|---|---|
| JR东日本 | ○ 等级+百分比（绿色车厢另给） | — | — | — | cloudrail（官方 App 后端，匿名注册即可）；`train_*.json` 实测只有 `organizationBasicInfo[].congestionRate` / `greenCarCongestionRate` + 编组数 | 首都圈通勤线；新干线/特急无读数。App 里的逐车/温度不在公开文档中，取不到 |
| JR西日本 | ○ 百分比 | ○ 每车百分比 | ○ 每车 ℃ | ○ 轮椅/婴儿车/Wi-Fi/厕所/行李架 | `https://www.train-guide.westjr.co.jp/api/v3/trainmonitorinfo.json`（与位置feed同源，按列车号 join） | 近畿/北陆/冈山/广岛/山阴；`-1`=该车无传感器，`status!=1` 整车置空 |
| 大阪 Metro | ○ 百分比 | ○ 每车百分比 | ○ 每车 ℃ | — | `https://api.mobility-operation-info.emetro-app.osakametro.co.jp/app/api/v1/trainlocation?route_code=N`（公开 App API key）；payload 实测仅 `trainCarCongestionList` / `trainCarTemperatureList` | 9 条线；无列车号，用位置+时刻锚定 |
| 东京 Metro | ○ 1–6 级 | — | — | — | CrowdNavi `https://tmcawebapp1.tokyometro.jp/v1/index2.html` 起头的「列車別混雑グラフ」静态页 | 9 条线 + 直通（东武/东急/JR）；今日实测折线 + 5 日平均柱，未发布今日值时打 `est` |
| 都营地下铁 | ○ 1–6 级 | — | — | — | 同上（东京 Metro 站点的混雑图会列出都营直通列车，如目黑的叁田线） | 仅覆盖出现在东京 Metro 页面上的都营车次，非全线全车 |

产物字段见 README：
`congestion{class,rate|level,est}`、`congestion_cars[]`、`temperature_cars[]`、
`facilities[]`（wheelchair/stroller/wifi/restroom/luggage）。

## 二、有公开数据但不能实时接入（App / 预报 / PDF）

| 运营方 | 数据内容 | 渠道 | 判定 |
|---|---|---|---|
| つくばエクスプレス (TX) | 逐车 6 级拥挤 | 官方 App | 公开 `tid/trains.json` 无舒适度字段；拥挤接口 403，App-only |
| 东急 | 逐车拥挤（实时） | 官方 App | 位置 API（Sotetsu 系 AWS 后端）无舒适度字段，App-only |
| 东武 | 逐车拥挤 | 官方 App | 现行 SSR 位置页无舒适度字段，App-only |
| 西武 | 列车 5 级拥挤 + 逐车设备 | 官方 App | `trainfo-api` 里 `vacancyStatus` 全为 null；`carNo/carCondition` 是编组/车号，不是拥挤度 |
| 小田急 | 列车 6 级拥挤预报 | 官方 App | 后端无该字段；预报非实时 |
| 京王 | 拥挤预报 | NAVITIME 页面/App | 第三方预报页，无公开 API |
| 西铁 | 5 日平均拥挤 | 官网 PDF | 非实时、非机器可读 |
| 多摩都市单轨 | 早高峰列车级拥挤 | 官网 HTML | 非实时 |
| 宇都宫 LRT | 拥挤统计 | 官网 PDF | 非实时、非机器可读 |

## 三、无公开实时舒适度数据（延迟源只有位置/延误）

以下运营方的延迟 payload 都不含舒适度字段，官方页面/商店检索也未发现公开实时
车内拥挤、温度或逐车无障碍数据；未接入。

| 运营方 | 说明 |
|---|---|
| JR东海 / JR九州 / JR北海道 / JR四国 | 位置/延误源均无舒适度字段；无公开实时车内数据 |
| 京急 / 京成 / 京阪 / 阪急 / 阪神 / 近铁 / 南海 / 相铁 / 名铁 | 同上（App 内若有展示，均非公开数据源） |
| 横滨市营地下铁 | ODPT 仅运行信息 |
| 青森铁道、IR石川、IGR、山形、北越急行、爱之风、HapiLine | 位置/延误源 |
| 福冈市营地下铁、熊本市电、长崎、广岛电铁、冈山电铁、水岛临海、万叶线、富山地方、土佐电、江之岛、水间、函馆市电、丰桥 | 同上；土佐电/函馆市电的 payload 中拥挤标志实测恒为空值 |
| 关东铁道、小凑铁道、流铁、山万、上信、上毛、真冈、渡良瀬、富士急、伊豆箱根（骏豆）、伊豆急、箱根登山、临海线、秩父、鹿岛临海、常陆那珂、夷隅、湘南单轨、港未来、北总、东叶、千叶单轨、东京单轨、百合海鸥、多摩单轨、埼玉高速、埼玉新都市、宇都宫 LRT、横滨海岸线、芝山 | 东京 Glance 镜像源仅位置/延误 |
| ODPT 追加组：东急/关东/埼玉高速/埼玉新都市/TWR/东京单轨/临海/百合海鸥/港未来/芝山/东叶 | ODPT 标准数据集只有运行/位置，无舒适度 |

## 四、结论

- 公开可实时抓到的舒适度数据已全部接入：JR东日本（列车级）、JR西日本（逐车+温度+设备）、
  大阪 Metro（逐车+温度）、东京 Metro & 都营（1–6 级，含 5 日均标注）。
- JR 东日本的逐车拥挤与车内温度、其余私铁的逐车数据只在官方 App 内，无公开接口；
  继续逆向 App 的收益低、稳定性差，且容易把预报值当实时值。宁缺毋滥。
- 新增运营方时按本表第三节的同一流程核验：先看延迟源 payload，再查官方 App/网页/PDF，
  只有公开可机器读取的实时数据才进 `comfort/sources/`。
