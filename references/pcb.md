# PCB 侧：自建封装 → 落板 → 布线/铺铜 → 交付

> 单位一律 **mil**（`mm × 39.3701`）；**+y 朝上**（KiCad/CAD 往往朝下 ⇒ 落板要翻转）。
> 层号：`TOP=1 / BOTTOM=2（恒为 2） / SILK=3,4 / MASK=5,6 / PASTE=7,8 / OUTLINE=11 / MULTI=12`；
> **内层从 15 起**（`In1=15 / In2=16`…）。⚠ `getAllLayers()` 会列出全部 Inner，**别按列表顺序推层号**。

---

## 1. ★★★ 自建封装几何（核心通路）

平台**没有**库域图元 API，但**把封装打开成文档**后，PCB 图元 API 就能写进那个封装：

```
① lib_Footprint.create('<工程库名>', 名, ['<分类>'])              → 空封装 uuid
② lib_Footprint.openInEditor(fpUuid, '<工程库名>')                → 变当前文档（docType=4，3~6s；大封装给 120s）
③ ★硬护栏：确认当前文档的 uuid == fpUuid 再动图元（否则会画到 PCB 板上！）
④ 画几何（单位 mil）：
     焊盘  pcb_PrimitivePad.create(layer, num, x, y, rot, padShape, net, hole)
     丝印  pcb_PrimitivePolyline.create('', 3, eda.pcb_MathPolygon.createPolygon([...]), width)
     位号  pcb_PrimitiveString.create(...)     ← ⚠ 这条在封装域是坏的，位号改在落件后 modify
⑤ pcb_Document.save()
⑥ ★复读验证：getFootprintFileByFootprintUuid(fp, lib, 'elibz2') → ZIP → *.elibu 数 PAD/POLY 记录
⑦ lib_Symbol.create + lib_Device.create({symbolType:2, symbolUuid, footprintUuid})
⑧ 回 PCB 文档 → pcb_PrimitiveComponent.create({libraryType:'3', libraryUuid, uuid:devUuid},
                                               layer, x, y, rot)     → 落件（带焊盘）
```

**焊盘形状**：`['RECT', w, h, r]` / `['ELLIPSE', w, h]`（**只正圆**）/ `['OVAL', w, h]`；
`hole = ['ROUND', d]`（**只 2 个元素**）或 `['SLOT', d, len]`；SMD 传 `null`。
**NPTH**：`metallization = false`（前面补 `undefined`），层 `MULTI(12)`，编号可给空串。
❌ `['POLYGON', …]` / `['NGON', …]` **建不出来** ⇒ 退化为外接矩形 `['RECT', w, h, 0]`。

**多边形 path 语法**：`[x1, y1, 'L', x2, y2, …]`（**裸数字开头**），支持
`'L' / 'ARC' / 'CARC' / 'C' / 'R' / 'CIRCLE'` ⇒ 丝印的折线/矩形/圆/弧/多边形全部可表达。
⚠ 坐标是**相对图元原点**：局部几何只乘 `39.3701`，**不要再叠加器件 `at` 偏移**。

---

## 2. CAD → 立创 映射表（通用）

| CAD 封装元素 | 立创 | 备注 |
|---|---|---|
| `pad rect` | `['RECT', w, h, 0]` | |
| `pad roundrect` | `['RECT', w, h, r]`，`r = 圆角比例 × min(w,h)` | 主力形状 |
| `pad circle` | `['ELLIPSE', d, d]` | 只正圆 |
| `pad custom / polygon` | ⚠ **退化为 `['RECT', w, h, 0]`** | 多边形 API 建不出 |
| `pad thru_hole` | `hole=['ROUND', d]`，层 `12` | |
| `pad np_thru_hole` | `hole` + `metallization:false`，层 `12`，编号 `''` | 非金属化孔 |
| **纯钢网开口（paste-only）** | ⚠ **会丢** | 焊盘模型 = **单铜层 + 派生掩膜/钢网**，表达不了；只影响钢网层 |
| 丝印/装配的线/矩形/圆/弧/多边形 | `POLY`（`pcb_PrimitivePolyline` + path） | |
| 走线 | `pcb_PrimitivePolyline.create(net, layer, poly, w)` | ❌ 别用坏掉的 `PrimitiveLine` |
| 过孔 | `pcb_PrimitiveVia.create(net, x, y, holeDia, dia, 0)` | 读回 `{x,y,holeDiameter,diameter,net,viaType}` |
| **板框** | **层 11 上一整条闭合 Polyline**（width 常用 6） | `create('', 11, poly, 6)` |
| 网络 | **靠焊盘自己的 `net` 参数** | 建两个带某网名的焊盘后，网络表里就出现该网名 |

---

## 3. ★★★★ 走线 / 过孔 / 铺铜

### ① 走线
```js
const poly = eda.pcb_MathPolygon.createPolygon([x1, y1, 'L', x2, y2]);   // ★ 必须带 'L'
const g = await eda.pcb_PrimitivePolyline.create(net, layerId, poly, widthMil);
```
- **绝对坐标**，不需要额外偏移；多段写成 `[…,'L',…,'L',…]`。
- 读回属性：**`net / layer / polygon({polygon:[…]}) / lineWidth`**（不是 `layerId/width/path`）。
- ★★ 读回的 `polygon` 是**裸数字与 `'L'` 混排**（长度可能是奇数）⇒ **先滤 number 再两两配对**。

### ② ★★★★ 实心铜只有 `Fill`

| 形态 | 生成方式 | 进 Gerber？ |
|---|---|---|
| `Pour`（覆铜框） | 原设计自带 / `convertToPour()` | ❌ 只是**定义域** |
| `Poured` | **无 API**（`create()` 是空壳） | — |
| `Region` | `convertToRegion()` | ❌ |
| `Fill.create` | 直建 | — 参数怎么给都抛 = 坏 API |
| **`Fill`（Polyline 转换）** | `Polyline.create(...)` → 取回对象 → `st.convertToFill()` | ✅ **立刻出现 `G36/G37`** |

```js
const poly = eda.pcb_MathPolygon.createPolygon(ring);        // ring = 扁平 [x,y,x,y,…]
const g = await eda.pcb_PrimitivePolyline.create('GND', 1, poly, 6);
const st = await eda.pcb_PrimitivePolyline.get(g.primitiveId);
const f = await st.convertToFill();                          // net 被继承
```
★ **`Fill` 只能表达"无孔单环"** ⇒ 平面上所有孔必须用 **细缝（slit）** 连到外边界，化成无孔多边形。
（典型实现：本地用几何库算铺铜 → 去孤岛 → **链式细缝开孔** → 再落成 `Fill`。）

### ③ 过孔
`pcb_PrimitiveVia.create(net, x, y, holeDia, dia, viaType)`，`viaType 0` = 通孔。

### ④ 批量写的幂等 + 去重（必做）
- 幂等键建议：走线 `net|layer|四坐标`；过孔 `net|x|y|drill|pad`；覆铜 `net|layer|点数`。
  写前把板上已有 key 全量读回做差集 ⇒ **天然可续跑**。
- ★ 超时会**整块重复**（返回里看着全绿，实际多出一整块）⇒ **写完必须去重兜底**。
- ★ **`clean` 别无脑 `getAll()` + `delete()`**：**板框也是层 11 上的一条 Polyline**，会被一起删掉！
  清"走线"要**按铜层过滤**。

---

## 4. 验收与交付

**四道独立验收**
1. **连通性**（自己写；**只有走线/焊盘/过孔/`Fill` 是导体**；必须做真正的"点在多边形内"判定；
   ★ 同层同网过孔「盘与平面环相交」也算连通，否则**真铜已连会报断开**）；
2. **铺铜间隙**（最小间距 vs 要求，违规 0）；
3. **交付物自洽**（**每层 `G36` 计数 == 该层 `Fill` 环数**；过孔数 == 钻孔文件行数）；
4. **与基线/第三方统计对账**（坐标图按**位号**配对拟合线性变换，看残差）。

⚠ **`pcb_Drc.check()` 不能当判据**：离线恒 `false`，**对故意违规也不敏感**
（判定实验：故意造一个异网过孔压在走线上 → 仍 `false`）⇒ `false` 的含义是"检查没跑起来"，不是"无违规"。

**制造导出可用性（离线/本地模式实测）**

| 方法 | 结果 |
|---|---|
| `getGerberFile()` | ✅ 数秒返回 zip（多层 Gerber + 丝印/阻焊/钢网 + 外形 + 钻孔 + 飞针 + 下单说明） |
| `getPickAndPlaceFile()` / `getTestPointFile()` | ✅ ★**实为 xlsx**（zip 容器），别按名字当 zip |
| `getBomFile()` / `get3DFile()`（真 STEP）/ `getNetlistFile()` / `getInteractiveBomFile()` | ✅ |
| **`getPdfFile()` / `getDxfFile()` / `getIpc2581CFile()`** | ❌ **Promise 永不落地** ⇒ 用渲染图合成 PDF，文件名带 `preview` |

★ 取回姿势：返回的是**内存 `File`（无路径）** ⇒ 客户端 `arrayBuffer` → **8KB 分块 base64** → 宿主分块拉回。
"发射不等待 + 轮询"是唯一稳妥写法（单次调用 30s 上限是硬墙）。

---

## 5. 布局布线的工程方法（要点）

- **判"能不能放"必须先量本板真实 DRC 间距**（从现有版图反推）。
  实测样例：全板异网铜最小 **4.0 mil**，而**铺铜净空** 6.3（外层）/9.8（内层）—— 两者差 1.6~2.5 倍，
  **混用会把自己判进死胡同**（"盘径 23.6→6 mil 全灭"的假象，换成真规则后立刻"有解"）。
- **常见板厂工艺下界**（例：4/6 层）最小过孔：内径 0.2mm / 外径 **0.45mm 标准、0.40mm 极限**
  （`0.45mm = 17.717 mil`、`0.40mm = 15.748 mil`）。
- **单网补线用 A\* 栅格布线器**（状态 `(层,x,y)`，换层 = 落一个过孔、代价给大）：
  - ★ **障碍掩膜与板边禁区必须分开画再合成**（合并画会把障碍整片擦掉 → 会"找到"一条**直线**路径，
    **长度 == 直线距离就是警报**）；
  - ★ **过孔掩膜要对全部铜层建**（通孔贯穿所有层）；
  - ★ **终点判定不限层**（终点焊盘常被异网铜围成小口袋，但它自带的贯通过孔能从别的层扎进来）；
    **起点必须限在起点焊盘所在层**（SMD 焊盘没有过孔可用）；
  - 净空要留**离散化余量**：`实际净空 ≥ 规则 + 半格对角线`。
- **改一处最小化**：优先"走线**局部折返**（两端端点不动 ⇒ 电气连通性不变）+ 过孔位移"；
  落地顺序 `改线 → 挪孔 → 重铺受影响层 → 落新孔`，**先存回滚包**，再走四道复验。
