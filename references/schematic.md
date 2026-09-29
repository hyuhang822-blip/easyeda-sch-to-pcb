# 原理图侧：落图与改图

> 前提：已有一份**结构化的页面图元清单**（器件 / 导线（含网名）/ 电源符号 / 端口 / 文字 / 折线 / NC），
> 来源可以是 CAD 导出、PDF 解析或人工整理。
> 目标：把它落到立创EDA专业版的指定页并**程序化验收**（ERC 在离线模式下不可用，见 `verification.md`）。

---

## 1. 图元映射表（实测，别凭文档猜）

| 来源图元 | 立创对应物 | 调用 |
|---|---|---|
| 电气导线 | `WIRE` + 子 `ATTR key:"NET"` | `sch_PrimitiveWire.create(line, net)` **+ `await o.done()`** |
| **local 网络标号** | **就是所属导线的 NET 属性**（`valueVisible:true` 自动显示）→ **不建图元** | — |
| global 网络标号（跨页） | 网络端口 | `sch_PrimitiveComponent.createNetPort('BI'\|'IN'\|'OUT', net, x, y)` |
| 电源/地符号 | NetFlag | `…createNetFlag('Power'\|'Ground'\|'AnalogGround'\|'ProtectGround', net, x, y)` |
| CAD 的 `PWR_FLAG` 类 | **无对应物 → 跳过**（那是 ERC 脚手架） | — |
| 结点 junction | **无 API** → 渲染期自动画 | — |
| 图形折线 / 框线 | `sch_PrimitivePolygon.create(line, color, fillColor, lineWidth, lineType)` | ★ **样式参数必须显式给全**；第 3 参是**填充色不是 net** |
| 说明文字 | `sch_PrimitiveText.create(x, y, content, rot, color, font, fontSize, bold, italic, underline, alignMode)` | **坐标在前** |
| NC 旗 | 引脚 `p.setState_NoConnected(true)` → **`await p.done()`** | setState 只改内存 |
| 器件 | 外部扩展库 DEVICE 落件 | `{libraryType:'3', libraryUuid, uuid}` |

**关键认知**：立创的 `NetFlag / NetPort` 是**组件实例**，语义与 CAD 的 label 不同；
**local label 根本不用建图元** —— 把网名写进**导线的 net 参数**即可，平台会自动挂属性并显示。

---

## 2. ★★★ 客户端 8 个隐藏机制（每条都是踩出来的）

1. **图元 API 没有"指定页"参数** —— 永远落在**当前打开的页**。
   ⇒ **把目标页钉进运行器**：每步前 `getCurrentDocumentInfo()` 自检，漂移就
   `activateDocument('<页uuid>@<工程uuid>')` 拉回（拉不回就**中止**，宁可不写也不写错页）。
   另留一个全局开关（如 `window.__STOP = true`）让外部随时掐死。
2. **页签额度上限**（量级：Start Page + 8 页）：满后新建页**拿不到页签** → 不成当前页 → `activateDocument` 恒 false。
   ⇒ 每页验收存盘后 **`closeDocument(tabId)`** 腾槽。
3. **绝不能把（原理图）页签全关**：一个都不剩时 `getCurrentSchematicInfo()` 返 `{}`，建页直接抛错 ⇒ 保底 ≥2。
4. **建页前必须先有"已打开的原理图上下文"**：先 `openDocument(任一已存在页)` 锚住，再建页（新页通常 3s 内拿签）。
   `openDocument(页uuid)` 是**唯一**能主动打开指定页的入口，但**很慢**（几秒~90s，promise 不及时 resolve）
   ⇒ **发射不等待 + 长轮询 ≤90~120s**。
5. **`createSchematicPage / deleteSchematicPage` 挂 30s 但操作生效** ⇒ 发射不等待 + 轮询页表确认。
   新建页会有默认名（重名自动递增）。⚠ 用**多余参数**建的页会**半残**（激活不了、删不掉）。
6. **一页刚落图结束时，下一页第一次开页/激活常失败** ⇒ 必须有重试（activate ×1 + openDocument ×3）。
7. **轮询单次读超时 ≠ 运行器结束** ⇒ 异常只记一笔、继续轮询，等稳定后补读终态。
8. **落图是异步提交的**：运行器跑完那刻 `getAll()` 可能还是 0，几十秒后才陆续出现
   ⇒ 对账前等稳定（**连续 3 次读数不变**）。

---

## 3. 每页标准流程

```
锚上下文（openDocument 一个已存在页，等当前页）
  → 建页（发射不等待 + 轮询页表拿 uuid + 改名 + 复核）或复用同名页（clean 后重落，比重建稳）
  → 钉页确认（activate，失败则 openDocument 兜底 ×3）
  → clean（只保留页框，按页框符号 uuid 判）
  → 注册本页外部库（每页一个 title；每实例唯一 symbol/device uuid；不带 footprint）
  → 落图：器件 → 电源符号 → 端口 → 文字 → 折线 → 导线 → NC →（注记/框线）
  → 等提交稳定 → 逐段对账 → 通过才 save → 关页签腾槽
```

⚠ `(外部库, 符号 uuid)` **只能落件一次**（第二次挂 30s）⇒ 每实例必须唯一 uuid。
⚠ 器件项里**带 `footprint` 关联 ⇒ 落件挂 30s** ⇒ 原理图阶段不带封装。

---

## 4. ★★★ 改图首选：页源文本手术

```
sys_FileManager.getDocumentSource()          → 该页 V3 明文源（单页可达数百 KB，内嵌符号定义）
  按行删/替换（★ 在宿主侧拼好整份字符串）
sys_FileManager.setDocumentSource(src)       → sch_Document.save() → 关页签重开复核
```

**为什么是首选**：① 外科手术，只动那几行 ⇒ **零格式漂移**；② 绕开"页名可能改不动、页序会变"两道坎；
③ **备份就是源文本本身**（最可靠的撤回物）。

### 记录格式（实测样式）

```
WIRE  {"type":"WIRE","ticket":N,"id":"<wid>"}||{"zIndex":1800}|
LINE  {"type":"LINE","ticket":N,"id":".."}||{"startX":1044.9979,"startY":135,"endX":1044.9979,
        "endY":165,"lineGroup":"<wid>","strokeWidth":null,…}|            ← ★ 几何在这里；源 y = 正值(向下)
ATTR  {"type":"ATTR",…,"x":1044.9979,"y":150,"rotation":90,"value":"VUSB",
        "valueVisible":true,"key":"NET","parentId":"<wid>",…}|            ← ★ 网名标签（位置 + 可见性）
TEXT  {"type":"TEXT","ticket":77,"id":"…"}||{"x":370,"y":345,"rotation":0,"color":"#0000FF",
        "fontSize":20,"fontWeight":true,"align":"CENTER_MIDDLE","value":"PCB",…}|   ← align 是明文字符串
POLY  {"type":"POLY",…}||{"points":[…],"strokeColor":…,"strokeWidth":…,"fillColor":"none",…}|
```

**要点**
- ⚠ **源里的 `y` 与 API 的 `y` 反号**（API 返 −599，源里是 `598.9988`）；`x` 同号。
- ⚠ 源很大 ⇒ 统计"页面级 TEXT 条数"要按 `"type":"TEXT"` 计数，**别用形如 `grep '"align"'`**（内嵌符号里也有）。
- ⚠ 同一图元**常有重复记录**（多内容流）⇒ 去重按 `(内容,x,y)` / 包围盒。
- ✅ 流程固定：`plan`（只读，看要删/改哪几行，**未知组必须为 0**）→ `apply` → **`verify`（关页签重开读回）**。

### 常见手术

| 目标 | 做法 |
|---|---|
| 删文字 | 按内容删 `"type":"TEXT"` 行（★ **要按内容删所有副本**，早期 y 符号写错会留下镜像副本） |
| 隐藏某条走线的网名标签 | 把该 `ATTR key:"NET"` 的 `"valueVisible":true` 改成 `false`（**网名仍在走线上，电气不变**） |
| 改走线几何 | 改 `LINE` 记录的 `startX/startY/endX/endY` |
| 改文字位置/字号/内容 | 改 `TEXT` 记录（**这是唯一可靠通路**） |

---

## 5. 文字坐标的两个换算约定（实测标定）

- **`x ≈ 渲染墨迹左端 − 约 2.5 单位`**；
- **`y = 渲染墨迹的垂直中心`**（左对齐 + 垂直居中模式，alignMode 写值 **2**）；
- ⚠ 从 PDF 抄来的 bbox 是**行盒**（含升/降部）⇒ 对齐要按**基准图上的墨迹**量；
- **字号换算**：`fontSize(立创) = PDF 字号(pt) / 0.72`；
- **校准技巧**：用户截图里**我们自己画的图元**就是标尺 —— 两三条已知坐标的线即可解出 `px = s·u + b`。

---

## 6. 从**原始 PDF 矢量**补齐"非电气但可见"的内容（通用套路）

很多"我们页面缺一块"的问题，根源是**更早的转换阶段就丢了**。原始 PDF 最权威：

```python
import pymupdf; p = pymupdf.open(pdf)[页序]; U = 1/0.72       # 1 单位 = 0.254mm = 0.72pt
p.get_text('dict')      # 文字：bbox / size / color；fontSize(立创) = size_pt / 0.72
p.get_drawings()        # 线/框：color / fill / width / items
```

- **判据要"窄"**：例如"分区框 = 纯蓝 `(0,0,1)` 且 `width=2.16pt`"、"小标题 = 某字号纯蓝"、
  "网名 = 某字号纯红" —— 窄判据**零误检**；靠"位置 + 长度"猜会混进文字/器件。
- ⚠ 同一图元/文字常**重复 2~3 次**（多内容流）⇒ 按 `(内容,x,y)` 或包围盒**去重**；
  折线还会成对出现**退化零长段** ⇒ 先滤掉。
- ⚠ **位图**（logo 等）在 PDF 里是 image，平台**无插图 API** ⇒ 只能用文字近似替代，并在报告里注明。
- 落完之后：把新图元**同步进台账**，再跑对账，"位置"才会被校验到；
  肉眼验收用**合成渲染**（把新落图元用绘图库画出来与基准同区域并排）。

---

## 7. ★★ 修正"器件块"位置的标准做法

判断"某个器件整体摆错"时：

1. 用符号定义（CAD mm × **3.937007874** = 立创单位）算**引脚外端** = 引脚定义点（**不是** 定义点+长度）；
2. **实测基准图里该器件的本体中心**（PDF 矢量 / 基准图），两者之差就是 Δ；
3. 找**挂在它引脚上的导线**（端点落在引脚外端 ≤1.5 单位），确认它们**远端什么都没挂**
   ⇒ 该器件 + 这些短线是一个**自包含块**，可安全整体平移；
4. `Component.modify(id, {x,y})` 移元件 + 导线 **delete + create**（分批）；
5. **独立验证**：把新本体矩形 + 全部引脚外端投到基准图上，看是否重合 —— **别只信 API 读回**。

★ 判"是摆放错还是符号画错"：看**本体尺寸**是否与基准一致 ——
尺寸吻合（差 <1%）就是**摆放错**（改锚点）；尺寸差很多就是**符号几何错**（要改符号定义）。
