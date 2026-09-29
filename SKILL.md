---
name: easyeda-sch-to-pcb
description: 嘉立创EDA专业版（立创EDA / LCEDA Pro）**从原理图程序化绘制到 PCB 生成的通用方法论与避坑指南**：经 run-api-gateway.eext + bridge 驱动 sch_Primitive*/pcb_Primitive*/lib_* 高层 API，完成「页面落图/改图（器件·导线·网名·文字·折线·NC）→ 自建封装/器件 → 器件落板 → 走线/过孔/铺铜/板框 → 几何验收与网表对账 → Gerber/BOM/3D 交付」。内含平台级硬事实与 80+ 条坑清单（症状→根因→对策）、验收体系（含"自证循环"盲区）、以及没有导出图片 API 时用截图做图像对账的方法。当任务涉及：把已有原理图（KiCad/PDF/其他）在立创里复刻；在立创里批量改原理图版式；自建封装几何；程序化布线/铺铜/导出制造文件；或遇到「API 说成功但重开就回退」「线条/文字/位置不对」「立创 ERC/DRC 不可用怎么验收」时使用。触发词：立创EDA、LCEDA、嘉立创EDA、easyeda、run-api-gateway、bridge、sch_Primitive、pcb_Primitive、lib_Footprint、openInEditor、getDocumentSource、setDocumentSource、页面落图、原理图复刻、自建封装、铺铜、缝合过孔、Gerber、ERC 不可用、DRC 不可用、立创页面回退、立创怎么验收。
agent_created: true
---

# 立创EDA专业版：从原理图绘制到 PCB 生成（通用方法论）

**用途**：把一份已有的原理图在立创EDA专业版里**程序化落图/改图**，并继续走到 **PCB 建板/布线/铺铜/交付**。
本 skill 是**总纲**：贯通全流程的铁律 + 两侧主流程 + 验收体系；分阶段细节与坑清单在 `references/`。

依赖技能：`easyeda-api`（**提供 bridge 服务端 + API 参考原文**，查函数签名一律去那里）。

---

## 0. 依赖清单与快速开始（**首次使用先看这一节**）

### 必须装（缺一样都跑不起来）

| # | 依赖 | 说明 / 获取 |
|---|---|---|
| 1 | **立创EDA专业版**（桌面客户端） | 平台本体 |
| 2 | **API Gateway 扩展** `run-api-gateway.eext` | 在 EDA 里安装并启用；它负责把 JS 执行请求转给客户端 |
| 3 | **bridge 服务** `bridge-server.mjs` | ★ **本技能不含它** —— 由技能 **`easyeda-api`** 提供（先安装那个技能），或自备一份实现同一协议的桥（HTTP + WebSocket，端口段 49620-49629） |
| 4 | **Node.js** | 用来跑 bridge 服务 |
| 5 | **Python ≥ 3.10** | 跑本技能脚本 |

Python 包：`numpy`（图像对账）、`Pillow`（图像对账）、`pymupdf`（解析 PDF 矢量）、`shapely`（PCB 铺铜计算）。
**只做"落图/改图/验收"的话，`numpy` + `Pillow` 就够；`pymupdf`/`shapely` 按需装。**

### 快速开始（三步确认链路）

```bash
# 0) ★ 必做：把本地地址从代理里排除，否则 127.0.0.1 会被拦成 502
export no_proxy="127.0.0.1,localhost" NO_PROXY="127.0.0.1,localhost"

# 1) 启动 bridge（★ 用"受管后台任务"常驻，别用 nohup &，否则会被回收）
node <easyeda-api 技能>/scripts/bridge-server.mjs

# 2) 在立创EDA里装好并启用 run-api-gateway.eext；若 /health 一直 edaConnected:false，
#    点菜单 API Gateway → 重新连接（扩展重试约 5 次就停，不会自动重连）

# 3) 自检 + 工程速览（**先跑这个**）
python <本技能>/scripts/smoke.py --expect-project "你的工程名"
```

`smoke.py` 会逐项报：bridge 是否在跑 / EDA 是否连上 / 当前工程与文档 / 原理图页与 PCB 板速览，
任何一步不过都给**可操作提示**。全绿 = 可以开工。

> ⚠ 本技能是**方法论 + 工具碎片**，不含"一键跑完全流程"的成品脚本 ——
> 因为每家的输入格式（CAD 导出 / PDF / 表格）和工程结构都不同。
> 落地时按 SKILL.md §3 / §4 的流程，把 `scripts/` 里的客户端与图像工具接进你自己的运行器即可
> （模块划分建议见文末 §6）。

---

> ★ 以下每条都来自**实盘踩坑**。遇到与"官方文档说法"冲突时，**以本文件为准** ——
> 文档与运行时不一致，是这个平台的常态（多处 API 是"文档里有、跑起来坏"）。

---

## 0. 开工前置（五条，缺一条必然白跑）

1. **bridge 用"受管后台任务"常驻**，不要 `nohup … &`（会随该次调用结束被回收）。
   起好后 `GET /health` 必须 `edaConnected:true` 且 `edaWindowCount ≥ 1`。
2. ★★★ **先清代理**：沙箱/CI 常注入 `http_proxy`，会把 `127.0.0.1:49620` 拦成 `502`。
   ```bash
   export no_proxy="127.0.0.1,localhost" NO_PROXY="127.0.0.1,localhost"
   ```
   任何 bridge 脚本之前都要做（裸 `urllib` 会读代理环境变量）。
3. **扩展不会自动重连**：bridge 重启过、或 EDA 先于 bridge 启动 ⇒ `/health` 长期 `edaConnected:false`。
   先做 `/health` 长轮询（≥100s）排掉"慢重连"，仍不通就请用户在 EDA 里点 **`API Gateway → 重新连接`**。
   ⚠ 重连后 `windowId` 会变；自写直连代码要认 **`disconnected` 与 `no longer connected` 两种措辞**并自动换窗重发。
4. **写操作前先过工程护栏**：核对 `dmt_Project.getCurrentProjectInfo().friendlyName` 是否等于目标工程。
   用户很可能**同时开着别的工程**（例如云端原稿与本地副本），护栏缺失就会写错地方。
5. **固定解释器与工作目录**：用装了 `numpy/shapely/pymupdf` 的解释器；工具、证据、报告各自有固定目录，
   并且**每次改动都要留回滚包**。

参考实现：`scripts/ee_bridge.py`（自动换窗重试 + 工程护栏 + 分批 + 轮询）；细节见 `references/bridge-and-env.md`。

---

## 1. 单位与坐标（官方 #1 mistake，错了差 10 倍）

| 域 | 单位 | 换算 | y |
|---|---|---|---|
| 原理图 / 符号 | **0.01 inch** | `mm × 3.937007874` | 页面内容在 **y 为负**的一侧（如 A4 横向约 `[−825, 0]`） |
| **PCB / 封装** | **mil** | `mm × 39.3701` | 立创 **+y 朝上**；KiCad **+y 朝下** ⇒ 落板要翻转 |

- ★★★ **`create` 的 y 符号：`页源 y = −(传入 y)`**（`Text.create` 与 `Polygon.create` **一致**）。
  页源/PDF 里的 y 是**正值（向下）**、`getState_*` 读回是**负值** ⇒ **一律传负 y**。
  传错的后果极隐蔽：create 报成功、`getAll()` 条数也对，**但整块内容被镜像到页面另一侧**。
- ★ `getState_Line()` 读回的是**负 y**；POLY/LINE 等记录里是正值。
- ★ **绝对坐标 vs 相对坐标**：`pcb_PrimitivePolyline` 用**绝对坐标**；
  封装内的局部几何只乘 `39.3701`，**不要再叠加一次器件 `at` 偏移**（叠加会整块偏离）。
- ★ 从 PDF 抄坐标时：**1 立创单位 = 0.254mm = 0.72pt**；480dpi 基准图 **1 单位 = 4.8px**（无偏移）。

---

## 2. ★★★★ 十条贯通铁律（比任何单个 API 都值钱）

1. **"读回成功"≠"改好了"。** 原理图侧 `sch_PrimitiveText` 的
   `delete / modify / setState_* + done()` **全部返回 true、会话内也生效，但关页签重开一律回退**
   （`sch_Document.save()` 返 true 也没用）；`Wire.modify(id,{line})` **返真却无效，还会凭空少线**。
   ⇒ **任何写操作之后必须：`save()` → `closeDocument` → 重新 `openDocument` → 再读回核对**。
   只有会话内 read-back 通过 = **未验证**。
2. **可靠写通路只有两条**：① **页源文本手术**（`getDocumentSource` → 按行改 → `setDocumentSource` → `save`）；
   ② **`delete` + `create`**（对象整体重建，分批）。
   ⚠ 写源时必须**在 Python（宿主）侧拼好整份字符串再发过去**；"在 JS 里 split/join 再写回"实测**不落盘**。
3. **单次 bridge 调用有 30s 上限** ⇒ 批量写/删**必须分批**（经验值：wire/poly/text 每批 **15 条** ≈ 3s）；
   慢接口（库搜索、`openDocument` 等）一律"**发射不等待 + 长轮询**"。
4. **`save()` 是异步的**：`await save()` 返 `true` 之后仍可能没落盘 ⇒ **存盘后等 4~6 秒再关页签**
   （实测立刻关只存了约一半；`save()` 还返过 `false`，需要重试）。
5. **验收的"自证循环"盲区**：对账脚本的期望值常来自**我们自己的记录** ⇒ **位置类错误永远查不出来**
   （实测：整层框线错位几厘米、整页缺几十条线，一路验收全绿）。
   ⇒ **位置必须与外部基准（原始 PDF 矢量 / 基准图 / 用户截图）量化对账**；
   并且**口径必须含"数量/条数"** —— 只查"期望能否找到"查不出**重复与缺失**（"以为删掉了其实没删"就是这么来的）。
6. **删除后必须独立复核残留**，且**要按内容删所有副本**：
   一旦用错 y 符号建过再改正，页上会**同时存在两份**（其中一份镜像在页面另一侧之外）。
7. **"某处放不放得下"必须用该项工艺自己的规则**：
   放置/布线查 **DRC 间距**（从现有版图反推，别猜）；只有**铺铜**才用**铺铜净空**。混用会差 1.6~2.5 倍。
8. **`create(...)` 报"参数不正确"时，先把实参逐个打出来** —— 先怀疑**字段名/漏参/该 API 分支坏了**，
   别归因"时序""上下文"。同理：**文档里有 ≠ 运行时能用**（见 `references/pitfalls.md` G 节坏 API 清单）。
9. **`verify` / `rollback` 这类"只读现状"的子命令，绝不能排在"重规划/可能失败"的逻辑之后**
   —— 否则"东西已经落地、正想回滚"的那一刻，回滚入口自己先退出了。
   配套：**任何"先删后建"的脚本，第一件事就是存回滚包**。
   ★★ **回滚/清场的最强手段 = "开工前备份整份页源，收工后整份写回"**
   —— 一次搞定所有图元类型（**包括那些"API 删不掉"的文字**、以及被平台**自动合并/吸附**过的几何），
   比"逐条删回"可靠得多。实测：用这招跑完示例后，页面逐类记录数与开工前**完全一致**。
   （注意：写回后平台内部的 `ticket`/`zIndex` 计数器会前进，**源长度会略变**，这不代表有残留 ✓）
10. **一切"看起来对"都要落到可复算的证据上**：条数、几何覆盖、Gerber 计数、坐标拟合残差、图像叠色差异 ——
    挑一条**独立数据链**交叉核对，不要信"API 说成功"。

---

## 3. 原理图侧主流程（落图 / 改图）

```
① 准备"结构化图元清单"（器件 / 导线（含网名）/ 电源符号 / 端口 / 文字 / 折线 / NC）
② 锚上下文：openDocument 一个已存在页 → 等当前文档 = 目标页
③ 建页或复用页（发射不等待 + 轮询页表拿 uuid）
④ ★ 钉页：每步执行前自检 getCurrentDocumentInfo()，漂移就 activateDocument 拉回；拉不回就**中止**
⑤ clean（只保留页框，按页框符号 uuid 判）
⑥ 注册本页所需的外部扩展库（每页一个 title；★ 每实例唯一 symbol/device uuid；★ 不带 footprint）
⑦ 落图顺序：器件 → 电源符号 → 端口 → 文字 → 折线 → 导线 → NC →（注记/框线）
     ⚠ 导线放最后：Wire.create 有"落在已指定网名的图元上则失败"规则，让并网冲突**响亮失败**
⑧ 等提交稳定（连续 3 次读数不变）→ 逐段对账 → 通过才 save → 关页签腾槽
```

**改版式 / 补内容的优先顺序**：
1. **页源文本手术**（源里有 `TEXT / POLY / LINE / ATTR` 等记录，按行改 ⇒ **零格式漂移**，且备份就是源文本）；
2. 需要整体重建时用 **`delete + create`**（POLY/WIRE 的 create/delete 能落盘）；
3. **器件位置**：`Component.modify(id,{x,y})` **有效**（少数可靠的 `modify`）；
4. **搬"器件块"** = 元件 + 挂在它引脚上的短线：**先确认这些短线远端什么都没挂** ⇒ 才可以整体平移；
5. **每次改完同步订正自己的台账**，否则下一轮验收立刻报 missing。

细节：`references/schematic.md`。

---

## 4. PCB 侧主流程（建板 / 布线 / 铺铜 / 交付）

```
① 自建封装几何：lib_Footprint.create → openInEditor(fpUuid, 库名) → ★硬护栏(当前文档 == 该封装)
     → pcb_PrimitivePad.create(...) / pcb_PrimitivePolyline.create('', 3, poly, w) 画丝印
     → save → 取出 .elibz2 **复读验证**（名字查得到 ≠ 里面有焊盘，小心空壳）
② 建器件：lib_Symbol.create + lib_Device.create({symbolType:2, symbolUuid, footprintUuid})
③ 落件：pcb_PrimitiveComponent.create({libraryType:'3', libraryUuid, uuid:devUuid}, layer, x, y, rot)
④ 板框：★ 层 11 上**一整条闭合 Polyline**（width 常用 6）
⑤ 走线：pcb_PrimitivePolyline.create(net, layer, poly, width)   ← 绝对坐标；poly 必须带 'L' 标记
⑥ 过孔：pcb_PrimitiveVia.create(net, x, y, holeDia, dia, 0)
⑦ 铺铜：★ 实心铜**只有 Fill**：Polyline.create(...) → 取回对象 → convertToFill()
     ❌ Pour / Poured / Region 在 Gerber 里都没有铜；`Fill` 只能表达**无孔单环** ⇒ 孔用**细缝(slit)**连到外边界
⑧ 交付：pcb_ManufactureData.*（Gerber / PnP / BOM / 3D / Netlist / IBom ✅；PDF / DXF / IPC2581 离线拿不到）
```

**四道独立验收（缺一不可）**：
① **连通性**（**只有走线/焊盘/过孔/`Fill` 是导体**；必须做真正的"点在多边形内"判定，禁止包围盒近似）；
② **铺铜间隙**（最小间距 vs 要求，违规 0）；
③ **交付物自洽**（**每层 `G36` 计数 == 该层 `Fill` 环数**；过孔数 == `Drill_*.DRL` 行数）；
④ **与基线/第三方统计对账**（坐标图按**位号**配对拟合，看残差）。
⚠ **`pcb_Drc.check()` 不能当判据**（离线恒 `false`，对故意违规也不敏感 = "没跑起来"，不是"无违规"）。

细节与映射表：`references/pcb.md`。诊断/DFM/补线方法：`references/verification.md`。

---

## 5. ★★ 图像对账（屏幕上的东西 API 读不回来）

`getAll()` 只给图元，给不出"渲染成什么样"；而**立创专业版没有导出图片/打印的 API**
（`SYS_*` / `DMT_*` / 制造数据接口都查过）⇒ "看着不对"类问题只能靠**截图 + 外部基准**做量化对账：

1. **定标**：把截图与我们**自己画的、绝对坐标已知的图元**（框线、标题）配对，解出 `px = s·u + b`
   （横竖应同比例；用 2 条框线 + 若干标题交叉验证）。
   ★ 用户截图一般落在 `~/.workbuddy/clipboard-images/` 之类的剪贴板目录 —— 排查"界面看着不对"**先去找截图**。
2. **反投影 + 叠色**：把截图映射回页坐标，与基准图叠色
   （**红 = 基准有我们没有（缺口）/ 青 = 我们有基准没有（多余）/ 黑 = 对齐**）。
3. **逐格墨迹对账**：分格统计"基准图有墨迹、我们空白"，定位**整块缺失**。
4. **合成渲染自证**：把"我们新落的图元"用绘图库画出来与基准并排 —— 既自证也给用户能看的结果。
5. **几何投影验证**：把元件的**本体矩形 + 全部引脚外端**投到基准图上，看是否重合
   —— 判断"器件摆错 vs 符号画错"的决定性手法。

工具：`scripts/img_overlay.py`（定标 / 叠色 / 逐格 / 并排）；方法细节见 `references/verification.md`。

---

## 6. 参考文件索引

| 文件 | 内容 |
|---|---|
| `references/pitfalls.md` | ★★★ **坑清单总表（80+ 条）**，按"连接 / 持久化 / 坐标 / 批量 / 页签 / 库 / 坏 API / 铺铜 / 导出 / 验收假绿 / 视觉"分类 —— **遇到怪现象先来这查** |
| `references/schematic.md` | 原理图侧：图元映射表、客户端 8 个隐藏机制、页源记录格式与常见手术、文字坐标约定、从 PDF 矢量补齐内容 |
| `references/pcb.md` | PCB 侧：自建封装通路、CAD→立创 映射表、走线/过孔/铺铜/板框、层号、导出可用性矩阵、DFM 与补线要点 |
| `references/verification.md` | 验收体系：自证循环盲区、两侧验收口径、图像对账五法、诊断五步法、交付前自检清单 |
| `references/bridge-and-env.md` | bridge/代理/重连/护栏、JS 调用三条契约、页签治理、分块 base64 取文件、台账同步 |
| `scripts/smoke.py` | ★ **环境自检 + 工程速览**（首次使用先跑它，任何一步不过都给可操作提示） |
| `scripts/ee_bridge.py` | bridge 客户端（换窗重试 + 工程护栏 + `batched` 分批 + `poll` / `stable_read`） |
| `scripts/img_overlay.py` | 图像对账：`calib` 定标 / `overlay` 叠色 / `gaps` 逐格缺口 / `side` 并排 |
| `examples/demo_roundtrip.py` | ★ **最小可跑示例**：备份 → 落图 → 关页签重开复核 → 数量对账 → 清场（把你指定的页临时改一下） |
| `examples/selftest.py` | 示例的**判据自检**（不依赖 EDA，验证"对账逻辑"本身没写错） |
| `README.md` | 面向社区：亮点 / 安装 / 快速开始 / **FAQ** / 适用边界 |

**落地建议（通用模块划分）**：把流程拆成 6 个可复跑的子命令 ——
`status`（盘点工程/页/板）、`build`（落图或建板）、`verify`（逐项对账）、`rollback`（回滚包还原）、
`report`（出报告）、`audit`（交付物自洽）。
**每个子命令都要幂等**（写前读回已有 key 做差集），这样中断后可续跑。
