# 舒适度数据核验矩阵 — 逐家深调

核验日期 2026-10-28（22:00–23:30 JST 复测）。范围 = `ooep/delay` 运行时实际会尝试的
运营方全集：`realtime_operators`（`data/lines.json`）+ `feed_jobs` 的 77 条自有源 +
`ODPT_KEY` 存在时追加的 13 个 ODPT 运营方（工作流已配置该 secret），另加 10 个
不在延迟源里的主要都市铁道/新交通，共 70+ 家。逐家一行，不再用「同上」合并。

核验方法（每家都走同一套）：

1. 先读延迟/位置源的真实 payload，确认里面没有藏着的舒适度字段；
2. 再查官方 App / 官网 / PDF / 帮助页，找到就抓端点验证字段与刷新节奏；
3. **只接入公开、无鉴权、可机器读取的实时数据**。App-only、预报、周报、PDF 一律不接，
   避免把预报/平均值冒充实时值。宁漏不误报。

图例：○ 有 · △ 部分/仅预报 · — 无公开实时数据

## 一、已接入（6 家）

| 运营方 | 列车拥挤 | 逐车拥挤 | 车内温度 | 无障碍/车内设备 | 公开渠道（已实测） | 备注 |
|---|---|---|---|---|---|---|
| JR东日本 | ○ 等级+百分比（绿色车厢另给） | — | — | — | cloudrail（官方 App 后端，匿名注册即可）；`train_*.json` 实测只有 `organizationBasicInfo[].congestionRate` / `greenCarCongestionRate` + 编组数 | 首都圈通勤线；新干线/特急无读数。App 里的逐车/温度不在公开文档中，取不到 |
| JR西日本 | ○ 百分比 | ○ 每车百分比 | ○ 每车 ℃ | ○ 轮椅/婴儿车/Wi-Fi/厕所/行李架 | `https://www.train-guide.westjr.co.jp/api/v3/trainmonitorinfo.json`（与位置 feed 同源，按列车号 join） | 近畿/北陆/冈山/广岛/山阴；`-1`=该车无传感器，`status!=1` 整车置空 |
| 大阪 Metro | ○ 百分比 | ○ 每车百分比 | ○ 每车 ℃ | — | `https://api.mobility-operation-info.emetro-app.osakametro.co.jp/app/api/v1/trainlocation?route_code=N`（公开 App API key）；payload 实测仅 `trainCarCongestionList` / `trainCarTemperatureList` | 9 条线；无列车号，用位置+时刻锚定；直通覆盖北急/近鉄けいはんな |
| 东京 Metro | ○ 1–6 级 | — | — | — | CrowdNavi `https://tmcawebapp1.tokyometro.jp/v1/index2.html` 起头的「列車別混雑グラフ」静态页 | 9 条线 + 直通（东武/东急/西武/相鉄/东叶/埼玉高速/みなとみらい/JR East/小田急）；今日折线 + 5 日平均柱，未发布今日值时打 `est` |
| 都营地下铁 | ○ 1–6 级（仅三田線共用段） | — | — | — | 同上：南北線白金台页会列出目黒—白金高輪共用段上的三田線列车 | **不是全线**：浅草線/新宿線/大江戸線/三田線其余段没有公开实时读数；都营自家 App 是 NAVITIME 预报 |
| 小田急 | ○ 百分比（列车当前值） | ○ 每车百分比 | ○ 每车 ℃ | — | 官方列车位置后端 `https://d6oynijiy33tb.cloudfront.net/trains/schedules/depart?station_code=NNN&up_down=01\|10`（公开 x-api-key，与 delay 同一把，仅存于 `delay/baker/odakyu.py`）；`car_infos[]` 实测 35 s 内变化 | 小田原線/江ノ島線/多摩線；逐车传感器只装在部分编组，无传感器车为 0/缺省（已置空）。班次身份用 `/trains/{code}?version=v2` 的完整停站序列 + 全线时刻钉死 |

产物字段见 README：
`congestion{class,rate|level,est}`、`congestion_cars[]`、`temperature_cars[]`、
`facilities[]`（wheelchair/stroller/wifi/restroom/luggage）。

## 二、直通运行带来的额外覆盖

不新增数据源，但舒适度已经落到下面这些公司的列车上。数字是 2026-10-28 19:18
快照里「有舒适度读数且钉住」的列车数（一列车可同时经过多家公司）：

| 公司 | 有读数的列车 | 经由的线路（key 上的线名） | 数据来源 |
|---|---|---|---|
| 東急 | 424 | 東横線 262 / 田園都市線 162 / 目黒線 147 / 東急新横浜線 90 | Metro 副都心線・半蔵門線・南北線页，三田線共用段 |
| 東武 | 229 | 伊勢崎線 169 / 東上本線 60 / 東武日光線 37 | Metro 日比谷線・半蔵門線・有楽町線页 |
| 埼玉高速鉄道 | 83 | 埼玉高速鉄道線 83 | Metro 南北線页 |
| みなとみらい線 | 82 | みなとみらい21線 82 | Metro 副都心線・東横線直通页 |
| 東葉高速鉄道 | 78 | 東葉高速線 78 | Metro 東西線页 |
| 相鉄 | 61 | 相鉄新横浜線 61 / 相鉄本線 59 / いずみ野線 28 | 東急目黒線・東横線直通页 |
| 小田急 | 52 | 小田原線 52（其中 31 条走千代田線直通） | 自家源 + Metro 千代田線页 |
| 西武 | 41 | 池袋線 41 / 西武有楽町線 41 | Metro 有楽町線・副都心線页 |
| りんかい線 (TWR) | 13 | りんかい線 13 | JR East 埼京線直通（JR East 源） |
| 北大阪急行 | 8 | 北大阪急行線 8 | 大阪 Metro 御堂筋線 |
| 近鉄 | 8 | けいはんな線 8 | 大阪 Metro 中央線 |
| 京成 / 京急 / 北総 / 阪急 / 南海 | 0 | — | 这些公司不跑进 Metro/都营/大阪 Metro 的读数区间，延迟源里也没有舒适度 |

19:18 产物的 2018 条里曾有 1 条 JR 関空快速被误钉到南海 key（なんば→関西空港），
是 `dest_scan` 的跨运营方误配，已修复（见第六节），南海因此没有读数。

## 三、逐家核验

### 3.1 JR 四家（JR东日本/JR西日本已接入，见第一节）

| 运营方 | 官方实时舒适度 | 公开匿名接口 | 端点/字段（实测） | 判定 | 证据 |
|---|---|---|---|---|---|
| JR東海 | 仅 App/会员：smartEX 的「混雑状況」与「シートマップで座席選択」并列，是空席/座席表，不是车内拥挤 | — | `traininfo.jr-central.co.jp/shinkansen/pc/ja/index.html` 200 HTML；测试 JSON 路径返回首页 HTML；smartEX 登录墙 | 仅App（空席文脉，非车内舒适度） | https://traininfo.jr-central.co.jp/shinkansen/pc/ja/index.html |
| JR九州 | — | — | `IDS2Web.xml` 200 XML：`time/info/aif/nm/sts/map/eif/lin/txt`，只有运行信息；どれどれ App 字段仅位置/のりば/両数/行先/遅延/運休 | 无 | https://www.jrkyushu.co.jp/trains/info/data/IDS2Web.xml |
| JR北海道 | — | — | `express_now.json` 200 JSON：`cbango/runStatus/yokuStatus/yokuDetail/status/statusDetail/chien/runRosen`；`rosen_now.json` 顶层 `time/lines`；无舒适度 | 无 | https://www3.jrhokkaido.co.jp/trainlocation/json/express/now/express_now.json |
| JR四国 | — | — | `train.jr-shikoku.co.jp` 200 HTML/JS；2026-10-22 官方 X 称停止走行位置配信（页面仍 200，实时性未验证）；delay 源 20:0x 仍一次返回 100 班，字段 `TrainNum/Pos/PosNum/delay/Direction/Type/Line` | 无 | https://www.jr-shikoku.co.jp/info/ |

### 3.2 関東私鉄

| 运营方 | 官方实时舒适度 | 公开匿名接口 | 端点/字段（实测） | 判定 | 证据 |
|---|---|---|---|---|---|
| 東急 | 部分线有车辆级拥挤，App 内；官网帮助把「車両別混雑度/改札混雑度」列为已结束 | — | `tid-tokyu.jp` NXDOMAIN；`api.railway.dp.tokyu.co.jp` 403；位置 API 无舒适度字段 | 仅App | https://www.tokyu.co.jp/ |
| 京王 | 拥挤预报（第三方 NAVITIME） | — | `transfer-train.navitime.biz/keio/...` 200 text/html，SPA 壳，无 JSON/混雑字段 | 仅预报 | https://www.navitime.co.jp/congestion/prediction/railroad |
| 西武 | 列车 5 级拥挤，App 内（2022-03-12 起） | — | `trainfo-api` 的 `vacancyStatus/vacancyStatusText` 全为 null；`carNo/carCondition` 是编组/车号，不是拥挤度；`/congestion`、`/train?trainNo=`、根路径均 404 | 仅App | https://train.seibuapp.jp/ |
| 東武 | 预报（東上線/アーバンパークライン 平日统计） | — | 位置页 SSR 无舒适度；官网/bundle.js 搜「混雑」0 处 | 仅预报 | https://www.tobu.co.jp/ |
| 京成 | — | — | `traffic_info.json` 200 JSON：`UP/TS/EK`、车 `no/bs/sy/ik/dl/hk/sr`，只有位置/延误 | 无 | https://zaisen.tid-keisei.jp/data/traffic_info.json |
| 京急 | 站内闸口拥挤（駅視-vision），非车内 | — | 官网 403 Cloudflare；位置源无舒适度 | 站内 only | https://www.keikyu.co.jp/ |
| 相鉄 | 站内闸口拥挤（25 站），非车内 | — | 位置源无舒适度 | 站内 only | https://www.sotetsu.co.jp/ |
| つくばエクスプレス | 逐车 6 级（车内摄像头 AI，2026-10-07 起），App 内 | — | `tid/trains.json` 无舒适度字段；`/assets_rti/` 403；NAVITIME TX 组件是 SPA 壳 | 仅App | https://www.mir.co.jp/ |

### 3.3 関東ローカル

| 运营方 | 官方实时舒适度 | 公开匿名接口 | 端点/字段（实测） | 判定 | 证据 |
|---|---|---|---|---|---|
| 関東鉄道 | — | — | 官网只发运行信息；TG 镜像对象字段仅 `id/l/n/r/g/d/s` | 无 | https://www.kantetsu.co.jp/train |
| 小湊鉄道 | — | — | 官方 App 只有運行状況/時刻表/運賃/観光；高速巴士 Wi-Fi 不算车内 | 无 | https://apps.apple.com/jp/app/id6446678117 |
| 流鉄 | — | — | 官网只公告 30 分以上延误；TG 同上 | 无 | http://ryutetsu.jp/ |
| 山万（ユーカリが丘線） | — | — | 官网只有运行信息；ユーカリPASS/エキタグ均无舒适度 | 无 | https://town.yukarigaoka.jp/yukariline/ |
| 上信電鉄 | — | — | 仅静态车内 Wi-Fi（クモハ503/504 等，2019-11-16 起）；TG 无舒适度 | 无 | https://www.joshin-dentetsu.co.jp/info/1504/ |
| 上毛電気鉄道 | — | — | 官方无；NAVITIME 有第三方混雑予報 | 无（第三方预报） | https://jomorailway.com/ |
| 真岡鐵道 | — | — | SL 预约限流是静态措施；TG 无舒适度 | 无 | https://www.moka-railway.co.jp/11112/ |
| わたらせ渓谷鐵道 | — | — | トロッコ「空席状況」是余座，不是拥挤；大間々站 Wi-Fi 不在车内 | 无 | https://www.watetsu.com/ |
| 富士急行 | 仅预报：時間帯別混雑状況 PDF（2020-09 生成） | — | `200 application/pdf`，1 页，按时间带/上下行，非机器可读 | 仅PDF（预报） | https://www.fujikyu-railway.jp/upload/file/%E6%B7%B7%E9%9B%91%E7%8A%B6%E6%B3%81.pdf |
| 伊豆箱根（駿豆線） | — | — | 仅 2015 年车内 Wi-Fi 实证；运行页无舒适度 | 无 | https://www.izuhakone.co.jp/sunzudaiyu/ |
| 伊豆急行 | — | — | 官方无；NAVITIME 有第三方预报 | 无（第三方预报） | https://www.izukyu.co.jp/ |
| 箱根登山鉄道 | 设施级等待信息（30 分以上待ち），非车内/非逐车 | — | `hakonenavi.jp/transportation/congestion/` 200 HTML，`.operation_text`；车站 Wi-Fi 不在车内 | 站内设施 only | https://www.hakonenavi.jp/transportation/congestion/ |
| 秩父鉄道 | — | — | SL パレオエクスプレス 12 系全车静态 Wi-Fi；TG 无舒适度 | 无 | https://www.chichibu-railway.co.jp/slpaleo/service.html |
| 鹿島臨海鉄道 | — | — | 官网只发运行信息；TG 同上 | 无 | https://www.rintetsu.co.jp/ |
| ひたちなか海浜鉄道 | — | — | 官网/X 只发运行信息 | 无 | http://www.hitachinaka-rail.co.jp/ |
| いすみ鉄道 | — | — | 全车静态 Wi-Fi（2020-10 起，现状未验证）；TG 无舒适度 | 无 | https://isumirail.co.jp/ |

### 3.4 関東 Metro / AGT / 新交通

| 运营方 | 官方实时舒适度 | 公开匿名接口 | 端点/字段（实测） | 判定 | 证据 |
|---|---|---|---|---|---|
| 都営地下鉄（全线） | 仅预报：App 号车混雑是 NAVITIME 预测；官网只有 PDF（Incapsula 拦截） | — | 官网 `subway/kanren/congestion/train.html`；三田線共用段由 Metro CrowdNavi 提供 | 仅预报（共用段已接入） | https://www.kotsu.metro.tokyo.jp/subway/kanren/congestion/train.html |
| 横浜市営地下鉄 | — | 位置 JSON | `yokohama.train-location.transfer.navitime.biz/blue-line/locations` 200 JSON：`alerts/trains/update`；车 `car_composition/delay_minutes/destination/direction/position_id/train_id/train_type`，无舒适度 | 无 | https://yokohama.train-location.transfer.navitime.biz/blue-line/locations?lang=ja&format=json |
| 東京モノレール | — | — | `news/offpeak.html` 200 HTML；PDF 为 2023-04 浜松町 6–10 时利用状况 | 仅PDF | https://www.tokyo-monorail.co.jp/news/offpeak.html |
| ゆりかもめ | — | — | 周次 PNG（2025/4/21–25 乗車効率），回顾统计 | 无 | https://www.yurikamome.co.jp/company/news/20250428_3592.html |
| りんかい線 (TWR) | — | — | 2021 年「朝通勤時間帯の混雑状況」PDF，已停更 | 仅PDF | https://www.twr.co.jp/tabid/101/pno_860/24/Default.aspx |
| みなとみらい線 | — | — | 2020 年平日朝 7:30–9:30 混雑率 PDF | 仅PDF | https://www.mm21railway.co.jp/info/news/2020/06/post-187.html |
| 湘南モノレール | 站内混雑（1 小时/15 分钟，青=平时红=今日），非车内 | — | `congestion/ofuna.html` 200 HTML，无 JSON | 站内 only | https://www.shonan-monorail.co.jp/congestion/ |
| 多摩都市モノレール | — | — | `konzatsu_graph.pdf`（站点超时）；`tamamonorail_congestion.pdf` 为 2026-09-30 时点混雑率 | 仅PDF | https://www.tama-monorail.co.jp/konzatsu_graph.pdf |
| 埼玉高速鉄道 | — | — | `news/2026/504.html` 只有「混雑が予想」文字与临时列车通知 | 无 | https://www.s-rail.co.jp/news/2026/504.html |
| 埼玉新都市交通 | — | — | `topics/2026/04/post_348.html`；PDF「混雑時間帯時刻表」（2026-01-11） | 仅PDF | https://www.new-shuttle.jp/topics/2026/04/post_348.html |
| 北総鉄道 | — | 位置 JSON（京成系） | `traffic_info.json` 200 JSON：`UP/TS/EK`，无舒适度；2023 年早间 PDF | 无 | https://zaisen.tid-keisei.jp/data/traffic_info.json |
| 東葉高速鉄道 | — | — | `rush-hour_20220907.pdf` 乘车率调查示意 | 仅PDF | https://www.toyokosoku.co.jp/wp/images/rush-hour_20220907.pdf |
| 千葉都市モノレール | — | — | 混雑状況页 PDF 明确写「時間帯別の平均混雑率」「各列車・各車両の混雑率ではございません」 | 仅PDF | https://www.chiba-monorail.co.jp/index.php/konzatsu-joukyou/ |
| 宇都宮ライトレール | — | — | `info/4049`；PDF 为 2026/4/8–14 日别混雑周报 | 仅PDF | https://www.miyarail.co.jp/info/4049 |
| 横浜シーサイドライン | — | — | PDF「朝のラッシュ時間帯利用状況（新杉田駅）」2022-08-10，站别统计 | 仅PDF | https://www.seasideline.co.jp/ |
| 芝山鉄道 | — | 位置 JSON（京成系） | 官网无混雑入口；京成 `traffic_info.json` 无舒适度 | 无 | https://www.sibatetu.co.jp/ |
| 成田高速鉄道アクセス (NRA) | — | — | 第三种设施保有公司，Sky Access 实际运行由京成负责；无舒适度 | 无 | http://www.nra36.co.jp/ |

### 3.5 中部・関西

| 运营方 | 官方实时舒适度 | 公开匿名接口 | 端点/字段（实测） | 判定 | 证据 |
|---|---|---|---|---|---|
| 名鉄 | — | 位置 HTML | `top.meitetsu.co.jp/trainlocation/TrainRunningPositionHM.aspx` 200 HTML：種別/位置/遅延；无舒适度 | 无 | https://top.meitetsu.co.jp/trainlocation/ |
| 近鉄 | — | 位置 HTML | `tid.kintetsu.co.jp/LocationWeb/trainlocationinfoweb01.html?eki=3` 200 HTML：行先/遅延/位置/種別；无舒适度。特急空席案内是余席 | 无 | https://www.kintetsu.co.jp/gyoumu/app/ |
| 京阪 | — | 位置 JSON | `www.keihan.co.jp/zaisen-up/trainPositionList.json` 200 JSON：`fileCreatedTime/locationObjects`；车 `trainNumber/carsOfTrain/delayMinutes/destStationNameJp/trainTypeJp`；22:42→22:47 有更新，无舒适度 | 无 | https://www.keihan.co.jp/zaisen/ |
| 阪神 | — | — | 阪神アプリ「列車走行位置」只有車両数/停車駅/遅延；`rail.hanshin.co.jp` 匿名 GET 超时（未验证） | 无 | https://www.hanshin.co.jp/hanshin_app/ |
| 阪急 | 仅预报：「混雑傾向」6 段階、列車/車両，仅 4 个最混雑区间，AI 予想（2024-03 起） | — | 官网/沿線アプリ展示；无公开 JSON；官方称「今后研究实时」 | 仅预报 | https://www.hankyu.co.jp/story/report/post-16.html |
| 南海 | — | — | `traffic.nankai.co.jp/railinfo/01.html` 200 HTML，仅延误文句；App 有实时位置；车站厕所空位不是车内 | 无 | https://www.nankai.co.jp/traffic/nankai_app.html |
| あおなみ線 | — | 未验证（超时） | `trainlocation-aonamiline.jp` GET timeout；官网 `/railinfo` 200 HTML 仅「平常通り」 | 无 | https://www.aonamiline.co.jp/railinfo |
| 愛知環状鉄道 | — | 位置 HTML | `AikanJsp/ZaisenInfo3.jsp` 200 HTML(Shift_JIS)：列車番号/位置/遅延；无舒适度 | 无 | https://www.aikanrailway.co.jp/train/ |

### 3.6 地方（九州・中国四国・北陸・东北）

| 运营方 | 官方实时舒适度 | 公开匿名接口 | 端点/字段（实测） | 判定 | 证据 |
|---|---|---|---|---|---|
| 西日本鉄道 | 滚动保留最近 5 个工作日、逐日平均 PDF（10/21–10/27，每份约 1.8 MB） | — | `200 application/pdf`；无实时接口 | 仅PDF | https://www.nishitetsu.jp/train/trainkonzatsu/ |
| 福岡市交通局 | — | 运行 JSON | `subway.city.fukuoka.lg.jp/running.json` 200 JSON，当时为 `[]`；ちかまるナビ仅位置 | 无 | https://subway.city.fukuoka.lg.jp/status/ |
| 熊本市交通局 | — | — | 市電ナビ走行位置，无舒适度 | 无 | https://www.kumamoto-city-tram.jp/ |
| 長崎電気軌道 | — | — | 位置图；官方只发运行/暂停信息 | 无 | https://www.naga-den-spmap.com/ |
| 広島電鉄 | 2022-02 IC 卡计数静态调查 PDF | — | `crowded_train-0214.pdf` 200 PDF，非现行数据 | 仅PDF | https://www.hiroden.co.jp/information/covid19/pdf/crowded_train-0214.pdf |
| 岡山電気軌道 | — | — | 「電車どこ？」位置，无舒适度 | 无 | https://okdweb.jcld.jp/okadensite/map.html |
| 水島臨海鉄道 | — | — | Bus-Vision 位置/到站，无舒适度 | 无 | https://loc.bus-vision.jp/mizurin/view/searchStop.html |
| 万葉線 | — | — | とやまロケーション位置；官网静态バリアフリー介绍 | 无 | https://www.manyosen.co.jp/ |
| 富山地方鉄道 | — | — | 市内電車運行状況/とやまロケ位置，无舒适度 | 无 | https://www.chitetsu.co.jp/?page_id=38294 |
| とさでん交通 | — | — | ロケマップ位置；实测 747 行 `d[8]` 全 `-`、`d[13]` 全 -1 | 无 | https://tosaden-rb.kochi-mobility.net/location/ |
| 江ノ島電鉄 | — | — | 列車位置サイト，无舒适度 | 无 | https://www.enoden-location.jp/ |
| 水間鉄道 | — | — | 官网无混雑/位置页 | 无 | https://www.suitetsu.com/ |
| 函館市企業局交通部 | — | — | イカすロケ约 9 s 刷新；实测 480 行 `d[13]` 全 -1 | 无 | https://www.city.hakodate.hokkaido.jp/docs/2024031200084/ |
| 豊橋鉄道 | — | — | のってみりん+位置，无舒适度 | 无 | https://www.toyotetsu.com/notte-ikomai/ |
| 北越急行 | — | — | `unkou/pcGuide.html` 200 HTML：種別/行先/列車番号/遅れ/現在位置，1 分更新 | 无 | https://hokuhoku.co.jp/unkou/pcGuide.html |
| あいの風とやま鉄道 | — | — | あいトレ `/json/train.json` 返回 200 text/html SPA，未取到 JSON | 无 | https://trafficinfo.ainokaze.co.jp/ |
| ハピラインふくい | — | 未验证 | `trainlocation.hapi-line.co.jp` 443 超时（HTTP 000） | 无（未验证） | https://www.hapi-line.co.jp/traffic/ |
| IRいしかわ鉄道 | 前一周平日平均 PDF（周二更新，仅见 2023-05-02，疑停更） | ODPT 静态定义 | `/api/v1/definitions` 200 JSON：`@type`=Railway/Station/TrainType，无舒适度 | 仅PDF | https://www.ishikawa-railway.jp/news/453 |
| IGRいわて銀河鉄道 | — | — | `igr.jorudan.biz` 200 HTML：列車位置/遅延 | 无 | https://igr.jorudan.biz/ |
| 山形鉄道 | — | 未验证（HTML/admin-ajax） | 列車現在位置，无舒适度 | 无 | https://flower-liner.jp/tracking/ |

### 3.7 都市地下鉄・新交通（延迟源之外）

| 运营方 | 官方实时舒适度 | 公开匿名接口 | 端点/字段（实测） | 判定 | 证据 |
|---|---|---|---|---|---|
| 札幌市交通局 | 仅静态：早高峰周均 PDF（每周三更新） | 无（只有 2018–2023 历史 CSV） | 官网 200 HTML；CKAN CSV 字段 `date,time,code,name,status,ja-jp,en…`，无拥挤/温度 | 无 | https://www.city.sapporo.jp/st/konzatsu_jokyo2020.html |
| 仙台市交通局 | 无：2026-03 已标「取組終了」（原为静态调查） | 无 | `/unkou/` 200 HTML；WP REST 仅页面索引 | 无 | https://www.kotsu.city.sendai.jp/subway/subway_konzatsu/ |
| 名古屋市交通局 | 仅静态：周均 PDF（改札通过人数，每周三更新） | 有（仅线级运行状态） | `/datas/latest_traffic.json` 200 JSON：`list[6]`，`rosen_id/traffic_title/traffic_message`；无拥挤/温度/列车 ID | 无（状态可接，拥挤不可接） | https://www.kotsu.city.nagoya.jp/rp/subway/TRP0004057.htm |
| 京都市交通局 | 无（地铁；旧静态页 404） | 无 | 地下鉄運行情報页仅新闻，无 XHR/JSON；实时混雑只在市バス「ポケロケplus+」 | 无 | https://www.city.kyoto.lg.jp/kotsu/page/0000270686.html |
| 神戸市交通局 | 仅静态：mieruka 3 个月平均（巴士车内/地铁闸机） | 无 | `/operation/train_operation/` 200 HTML，服务端渲染，无数据 JSON | 仅静态统计 | https://kotsu.city.kobe.lg.jp/company/service/mieruka/ |
| 沖縄都市モノレール（ゆいレール） | 仅静态：2025-08 早高峰调查公告 | 未验证 | 本环境全站 403（CloudFront）；`/realtime-timetable/` 未验证 | 无（未验证） | https://www.yui-rail.co.jp/news/10918/ |
| 大阪モノレール | 仅静态：2022-07 PDF（改札人数平均） | 无 | `/delay/` 200 HTML；PDF 200 PDF；无 JSON | 仅PDF | https://www.osaka-monorail.co.jp/upload/info/pdf/CR0708.pdf |
| 神戸新交通（ポートライナー/六甲ライナー） | 仅静态：2026-07 调查结果（令和8年5–6月） | 无 | `/portlinerinfo/` 200 HTML，服务端渲染，无数据 XHR | 仅PDF | https://www.knt-liner.co.jp/news/7778/ |
| 広島高速交通（アストラムライン） | 无 | 无 | `delay.html` 200 HTML；无 JSON/XML | 无 | https://www.astramline.co.jp/ride-guidance/delay.html |
| 北九州高速鉄道（モノレール） | 无（未验证） | 无 | 官网 200 HTML，2.7 KB 维护页；无 JSON | 无（未验证） | https://www.kitakyushu-monorail.co.jp/ |

10 家都没有「实时列车身份 + 拥挤」的公开机读接口，也没有仅 App 的实时拥挤功能。
札幌/名古屋是周均 PDF，神戸 mieruka 是 3 个月平均，大阪/神戸新交通是调查均值；
NAVITIME 的「混雑予報」覆盖这 10 家，属第三方统计预报，只记录功能存在，不计接入。

### 3.8 ODPT 追加组

ODPT 标准数据集（`odpt:Train` / `odpt:TrainInformation` / `odpt:Station`）只提供运行与
位置；本仓 `delay/baker/odpt.py` 也只取 `odpt:delay`、`odpt:fromStation`、`odpt:toStation`、
`odpt:trainType`、`odpt:carComposition`，没有拥挤/温度/逐车设备字段。追加的 13 家
（JR-East、TokyoMetro、Tokyu、KantoRailway、SaitamaRailway、SaitamaTransit、TWR、
TokyoMonorail、Rinkai、Yurikamome、Minatomirai、Shibayama、ToyoRapid）与无 key 的
3 家（Toei、Tobu、YokohamaMunicipal）都不因 ODPT 多出舒适度数据。

### 3.9 四国逐家复核（2026-10-28 20:0x JST）

JR四国官方位置 feed（`train.jr-shikoku.co.jp/g?arg1=train&arg2=train`）一次返回 100 班，
字段只有 `TrainNum/Pos/PosNum/delay/Direction/Type/Line`；ことでん与伊予鉄只发布运行信息
（延误/停运）；土佐电 `d[8]` 拥挤位、`d[13]` 乘客数与函馆市电 `d[13]` 实测全空/全 -1。
四国岛上没有可机器读取的实时车内舒适度源。

## 四、Wi-Fi 能抓到什么（2026-10-28 复核）

| 运营方 | 车内 Wi-Fi 现状 | 处理 |
|---|---|---|
| JR西日本 | `trainmonitorinfo` 逐车 `wifi` 设备位（部分车） | 唯一来自实时 payload 的 Wi-Fi 数据，已输出 `facilities.wifi` |
| JR四国 | 官方「JR四国の無料Wi-Fiサービス」：8600/8000 系特急（しおかぜ・いしづち等）、2700/2600 系特急（南風・うずしお等）、快速マリンライナー（グリーン席・指定席のみ） | 按车型公布的静态信息，无逐车/实时字段；不写入 comfort |
| JR东日本 | 新干线/特急有付费 Wi-Fi；cloudrail 只覆盖通勤线，无对应列车记录 | 不标注 |
| 东京 Metro | 访日客免费 Wi-Fi「Metro_Free_Wi-Fi」车内服务于 2022-06-30 结束 | 不标注 |
| 都営地下鉄 | Toei_Subway_Free_Wi-Fi / TRAVEL JAPAN Wi-Fi 于 2023-03-31 结束；现行仅部分车站 OpenRoaming | 不标注 |
| 大阪 Metro | 运营商 Wi-Fi 2022-12 / 2023-03 结束，Osaka Free Wi-Fi 2023-03 结束 | 不标注 |
| 上信電鉄 | クモハ503/504 等部分编成（2019-11-16 起） | 静态，不写入 comfort |
| 秩父鉄道 | SL パレオエクスプレス 12 系全车 | 静态，不写入 comfort |
| いすみ鉄道 | 全车（2020-10 起，现状未验证） | 静态，不写入 comfort |
| 富士急行 | 富士山ビュー特急/フジサン特急按车种 | 静态，不写入 comfort |
| 関東鉄道・小湊鉄道 | 仅高速巴士有 Wi-Fi，铁路没有 | 不标注 |
| 箱根登山・伊豆箱根 | 箱根是车站/巴士 Wi-Fi；伊豆箱根只有 2015 年实证 | 不标注 |

结论：实时 Wi-Fi 只能保留 JR西日本 payload 里的逐车字段；JR四国可按车型确认有
Wi-Fi，但没有逐车/实时字段，不写入 comfort。给其他线路写静态「有 Wi-Fi」
会与现状相反，宁可不写。

- https://www.kotsu.metro.tokyo.jp/subway/kanren/wi-fi.html
- https://subway.osakametro.co.jp/guide/usage/wireless_lan/wireless_lan.php
- https://news.yahoo.co.jp/expert/articles/877fa90414aef6a58129bb76bf627262baf34477

## 五、结论

- 公开可实时抓到的舒适度数据已全部接入：JR东日本（列车级）、JR西日本（逐车+温度+设备）、
  大阪 Metro（逐车+温度）、小田急（逐车+温度，实时传感器）、东京 Metro & 都営三田線共用段（1–6 级，含 5 日均标注）。
- 小田急的逐车读数来自官方列车位置后端（与 delay 同一个 host），不是 App 里的 6 级预报：
  `car_infos[]` 的 `passenger_rate` 在 35 s 内就会变化，属实时传感器；身份用 v2 班次详情的
  完整停站序列钉死后，才允许落到某个 suki 班次上。
- JR 东日本的逐车拥挤与车内温度、其余私铁的逐车数据只在官方 App 内（東急/TX/西武），
  或只有预报（京王/東武/阪急/都営），无公开接口；继续逆向 App 的收益低、稳定性差，
  且容易把预报值当实时值。宁缺毋滥。
- 新增运营方时按本表第三节的同一流程核验：先看延迟源 payload，再查官方 App/网页/PDF，
  只有公开可机器读取的实时数据才进 `comfort/sources/`。

## 六、映射覆盖率与钉班次规则

有舒适度读数的候选列车 → 成功钉到 jr.suki.ing 运行线的比例：

| 运营方 | 候选 | 已映射 | 命中率 |
| --- | --- | --- | --- |
| JR东日本（cloudrail） | 424 | 379 | 89% |
| JR西日本（train-guide） | 201 | 148 | 74% |
| 东京 Metro / 都营（CrowdNavi） | 1468 | 1468 | 100% |
| 大阪 Metro | 25 | 23 | 92% |
| 合计 | 2118 | 2018 | 95% |

以上是 2026-10-28 19:18 JST 晚高峰快照；23:2x 深夜复测 1485 候选 / 1427 已映射，
`comfort.verify` 1427/1427（深夜班次少，分母不同，规则没有退化）。18:5x 的对照是
1983→1891（95%）；19:18 这轮东京 Metro 的 `_deps` 停站索引修复（见下）把 CrowdNavi
候选从 1401 抬到 1468 且全部命中。

未映射的 100 条 = 找不到唯一班次 74 条 + 争用同一 slot 时被证据更强的一方取代 15 条
+ key 冲突 11 条。

`comfort.verify` 对全部 2018 条逐条复核：suki key 存在、该分钟确有班次、上报站在路线上、
停站序列一致，0 例外。产物里 15 条 `ev:["contested"]`（争用后胜出）与 2 条
`ev:["dest_scan"]`（靠终点全局反查命中）单独标出，便于事后抽查；这 2 条 dest_scan
里有 1 条是跨运营方误钉，已按下文规则修复。

### 跨运营方误钉：JR 関空快速 → 南海 key（已修复）

19:18 产物里 JR-West `1051M`（新今宮 → 関西空港）被 `dest_scan` 钉到了
**南海** key 7507（なんば(南海線) → 関西空港(鉄道)，中间是天下茶屋/泉佐野…），
把 JR 的逐车读数挂到了南海的班次上。原因：JR-West 的 record 没有 `official`
停站表，`dest_scan` 只按「终点相等 + 上报站在站序里 + 窗口内唯一」匹配，
而那个时刻窗口里只有南海这一条同终点运行线。

修复：`dest_scan` 的候选 key 必须含有该运营方本次 bake 自报的至少一个线路名
（`op_lines`，按 `rec["op"]` 聚合）。这条约束只作用于最后一档 `dest_scan`，
不影响前三档正常匹配；八高線/中央本線这类「同公司、不同运行线」的救援仍然成立。

回放验证（2026-10-28）：

- 1051M 在 19:14–19:16 旧逻辑钉到 7507（南海），新逻辑不再产生南海 key；
  19:06–19:12 新逻辑改钉到 JR-West 自己的 11769（天王寺→関西空港，新今宮 19:12）。
- 801T（御着→網干）在 19:15–19:23 新逻辑仍钉到 14041（野洲 16:17 発），未被误伤。
- 23:2x 全量 bake：1485 候选 / 1427 已映射，`comfort.verify` 1427/1427；
  该时刻 `dest_scan` 为 0，修复后没有新增误钉。

### 钉班次的先后次序与同 slot 争用

一个 suki key 是「停站序列」而不是某一趟车，同一天几十趟共用一个 key，所以真正的身份是
(key, 始発発車分)。四档解析按证据强弱依次尝试，前一台命中即返回：

1. `stopTime` 绝对时钟 ±3 分（JR西日本 position feed 自带列车停靠上报站的时刻）；
2. 同上 ±1 分（同一分钟窗口里挤进两条路线时用更紧的窗口分开）；
3. `[表定発車A, 表定到着B]` 物理区间（上报了下一站但 `stopTime` 缺失时）；
4. 墙钟减延误的严格解析 `resolve_direct_by_time`（运营方自报发车分钟为 tier 0，
   只有墙钟可用为 tier 2）。

同一 (key, 发車分) 被两辆实况车声称时，只保留证据严格更强的一方（tier 小者胜，
同 tier 比与时刻表的偏差）；打成平手就两条都不发 —— 谁胜谁负不能取决于源返回的顺序。
这类记录在产物里打 `ev:["contested"]`，本轮 13 条，逐条核对起点、终点、上报站与
运行线一致。

**终点全局反查**（`_resolve_destscan`，最后一档）：线路自报的候选池只覆盖服务名称
对应的那条线，而列车实际跑在别的路径上 —— 八高線 高麗川→川越 的车在 suki 里挂在别的 key 下，
中央本線 大月→小淵沢 属于更长的 高尾→小淵沢 运行线。这一档不看线路池，直接用终点索引
（每张运行线的最后一站 → key，兼容 `関西空港(鉄道)` 这类带括号的站名）反查，
条件更硬：终点必须逐字相等、上报站必须在站序里且不是最后一站（否则「前方还剩什么」这条
判据失去区分力）、下一站仍向前、有官方剩余停站时必须通过 `_stops_consistent` 站序比对、
窗口内 `(key, 班次)` 必须唯一，且候选 key 必须属于同一运营方（见上）。它只在前三档
全部失败后才跑，本轮多救回 2 条。

**试过并否决的放宽**：只要求「打印的终点仍在前方」而不是等于运行线最后一站
（`_dest_ahead`）。它只多钉 2 条，而这 2 条恰恰是「运行线比车次显示的终点更远」——
即该车在半途收车，是另一趟服务；同一时刻另有 11 条阪和線/山陽本線车次停在
终点更短的独立 key 上，严格匹配本来就命中。故整条放宽已删除，`dest_mode` 相关代码
一并撤掉。

### 线路是否抓全

- **JR东日本**：对 cloudrail 的 `train_<id>.json` 命名空间做了 464 个 id 的探测
  （1..200 / 901..920 / 1001..1011 / 7801..7811 / 各线路 `-1..-4` 子文件）。除已抓的 63 个外，
  只多出 `42`（小海线）、`65`（八高线）、`66`（水户线）、`103`（仙石东北线/石卷线）四个真实
  文件，且其中每一列车的 `congestionRate` 都是 `{"valid": false, "isTarget": false}` ——
  JR东日本根本不发布这些线路的拥挤度，抓了也没有数据。`902` 已在 curated 列表里但服务端
  返回 404（死文件，无害）。
- **JR西日本**：5 个区域 master 的全部 45 个 slug 都抓（近畿 27 / 北陆 1 / 冈山 7 /
  广岛 6 / 山阴 4），逐车 `carNo` 全局排序后输出。

### 方向是否抓全

按映射结果统计「运行线起点→终点」组合：JR东日本、JR西日本、东京 Metro 9 条线、
大阪 Metro 的 n≥4 线路都同时出现多个起点与多个终点，没有单向线路。
`_direction()` 的奇偶方向过滤只作用于纯数字车次，JR西日本的车次带字母后缀（3489M、4664S），
不会被误过滤。

2026-10-28 18:2x 复查发现一处真单向：**都営三田線 31 班全部开往西高島平**。
三田線与南北線共用目黒—白金高輪区间，此前只抓每个方向的终点站页（目黒 / 赤羽岩淵），
目黒页只列北行三田線，南行（往目黒・日吉）一班都看不到。已补抓白金台 B 向页
（`FILL_PAGES`）：该页是南行列车离开共享段前最后一个共同站，补读结果只填空缺 slot，
不覆盖锚点页读数。同时刻实测 1283→1319 条，都営 32→64 条，双向齐全，lost 0，
1319/1319 通过 `comfort.verify`。

18:4x 又发现一处真单向：**丸ノ内線 B 向（池袋方面）的锚点被 `ANCHOR_OVERRIDES`
覆盖成了支线终点方南町**，只读到支线 49 班，主线「荻窪→池袋」~70 班整向缺失。
改为锚点 荻窪 + 方南町补抓页（仍只填空缺 slot）：固定 17:00 窗口 A/B 实测 1246→1310，
+64 条全部是荻窪→池袋，lost 0，新增 64 条逐条通过 key/始発分/上报站校验；全量 bake
1973 条，`comfort.verify` 1973/1973。同刻丸ノ内線四个方向组合齐全：
池袋→荻窪 83 / 荻窪→池袋 74 / 方南町→池袋 28 / 池袋→方南町 25。

18:30 班次（1887 条）再按「同一条线是否存在终点互换的两趟车」逐线复核：
39 个线路标签里唯一看似单向的是**副都心線**——72 条全部 `from=渋谷` 开往和光市方向。
实测其和光市 A 向页（303 行/日，元町・中華街 111、湘南台 28）本地映射 83/83，南行
一班不少；只是和光市—小竹向原与有楽町線共用，同一 slot 被两页各读一次，最终去重留下
先到的有楽町線副本（该副本的 `line` 标为有楽町線）。站点按 (suki key, 始発分) 注入，
与 `line` 字段无关，所以方向数据是齐的，只是 comfort.json 的行名沿用了页面名。
同理，4 条「東京メトロ 山手線」是東西線直通三鷹车次，key 的 lines 里没有 Metro 名时
`_pick_line` 回退到 lines[0]，属标签问题。

19:1x 修掉一处影响**所有** CrowdNavi 页的时刻索引错误：`_deps` 在算出锚点发车之后
又多加了一站停站（`s = 始発分, run1, dwell1, …`，循环 `Σ(run_i+dwell_i), i<anchor`
本身已经是该站发车，末尾再加 `dwells[anchor]` 等于把每一行都推迟一个停站，通常 1 分）。
3 分钟间隔时 ±1 窗口还能吸收这 1 分；银座线晚高峰 2 分钟间隔时，行时刻正好落在
两班之间（两组间隔都=1），被当成不可证明整行丢弃。修复后冻结 18:48 对照：
1401→1460（+59），**银座线 B 页 74→115（+41）**；逐行核对银座线渋谷页，
113 条记录的始発分与页面行完全相等（113/113）。9 个旧 slot 不再命中（含 4 条直通车
原先挂在 山手線/中央線 型 key 上），这些行在正确时刻上无法唯一确定，按宁漏不误报丢弃。
19:18 全量 bake 2018 条、`verify` 2018/2018；银座线 178→219、東西線 148→161、
日比谷線 167→171、半蔵門線 160→162、都営三田線 64→67、丸ノ内線 230→235。

八高線/桜島線/赤穂線/関西空港線 在同一时刻各只有 1 条读数，样本不足，不构成单向证据；
大阪 Metro 中央線同刻同时有往夢洲与往生駒/コスモスクエア两个方向。
另有 11 条 JR西日本记录的停站序列在 key 的 `s_jas` 里不是单调递增：全部是大阪環状線/
阪和線的环线型 key（`s_jas` 把环线写了两遍），并非钉到反方向；`verify` 的前缀一致性
检查全部通过。

### 仍未映射的部分（JR西日本 53 条）

- 真因是 feed 的 `pos` 报的是**物理位置**而不是「最后一个停车站」：`杉本町_浅香` 这样的
  一对相邻站，对特急/新快速来说两站都不停车，所以 `from` 根本不在任何停站序列里。
  逐条查过 11 条这类记录：以它们终点为最后一站的运行线确实存在（14/60/75 条不等），
  但没有一条的站序包含上报站。快车位置只能靠「上一停站→下一停站之间」来锚定，
  suki 侧没有这种区间时刻表，接不了。
- 19 条：同一分钟有 2 条以上候选路线，无法唯一确定，按「宁漏不误报」放弃。
- 15 条：feed 的 `stopTime` 缺失，只能退回墙钟窗口。
- 做过「忽略终点」的放宽实验：能多钉 13 条，但其中 `3178M` 这类被钉到了反向的路线上
  （feed 的 `pos` 说车在离开终点方向），属于误报，**已否决**；只要求终点仍在前方的
  半放宽也试过，只多 2 条且都是「运行线比车次终点更长」的另一种车，同样删掉。
- JR西日本 feed 的 `direction` 字段与车辆实际行驶方向无稳定对应（同一取值下顺/逆序都有，
  92:16 与 106:16），不能用作方向过滤。
