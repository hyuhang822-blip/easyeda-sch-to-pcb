# bridge · 环境 · 文件取回 · 页签治理（通用）

---

## 1. 架构与起法

```
宿主脚本  --HTTP-->  bridge-server(本地端口，默认 49620)  --WebSocket-->  EDA 扩展 run-api-gateway.eext  --JS-->  立创客户端
```

- **起 bridge**：用**受管后台任务**（不要 `nohup … &`）；起好后 `GET /health`：
  期望 `{"service":"easyeda-bridge","status":"ok","edaConnected":true,"edaWindowCount":1}`。
- **扩展**：在立创里安装并启用 `run-api-gateway.eext`，加载时会自动扫端口段连接；
  **自动重试约 5 次即停** ⇒ bridge 重启过就必须在 EDA 里点 **API Gateway → 重新连接**。
- ★ **代理陷阱**：沙箱/CI 常注入 `http_proxy` ⇒ 本地端口会被拦成 **502**。
  跑任何脚本前：`export no_proxy="127.0.0.1,localhost" NO_PROXY=…`。
- ★ **重连后 `windowId` 会变** ⇒ 直连代码要认 `disconnected` 与 `no longer connected` **两种**措辞，
  自动取 `activeWindowId` 换窗重发（见 `scripts/ee_bridge.py`）。

---

## 2. JS 调用的三条契约

1. **发裸代码，不要自己套 IIFE** —— 扩展会把收到的代码包成 async 函数；
   再套一层 `(async()=>{...})()` 会让外层拿不到返回值（`success:true` 但 `result:null`）。
   代码里直接用 `eda`，末尾 `return xxx;`，支持 `await`。
2. **单次调用 30s 上限** ⇒ 批量操作**分批**（经验值：wire/poly/text 每批 **15 条** ≈3s）；
   慢接口一律"**发射不等待 + 轮询**"：
   ```js
   try{const pr=eda.dmt_EditorControl.openDocument(uuid); if(pr&&pr.catch)pr.catch(()=>{}); return 'fired';}catch(e){return 'E:'+e}
   ```
3. **全局 `window` 跨调用是持久的** —— `window.__X` 写一次后续调用读得到（含发出后异步写值）。
   所以"轮询不到值"不是上下文被清，而是那一步真抛了错或在等外部条件；
   ⇒ 先在客户端内把**异常原文**落到某个 `window.__ERR` 再读回。

---

## 3. ★ 工程护栏（写操作必过）

```python
info = js('const p = await eda.dmt_Project.getCurrentProjectInfo();'
          'return p ? {friendlyName: p.friendlyName, uuid: p.uuid} : null;', wid)
assert info['friendlyName'] == EXPECTED        # 目标工程名
```
**为什么必须有**：用户很可能**同时开着别的工程**（例如云端原稿与本地副本），
任何"以为在 A 里"的写都可能打到 B 上。

**常用查询**

| 目的 | 调用 |
|---|---|
| 当前文档 | `dmt_SelectControl.getCurrentDocumentInfo()` → `{uuid, …}` |
| 原理图页表 | `dmt_Schematic.getAllSchematicPagesInfo()` → `[{uuid, name}]` |
| 工程信息 | `dmt_Project.getCurrentProjectInfo()` |
| 页签树 | `dmt_EditorControl.getSplitScreenTree()`（**返回结构随版本变，先打出来看**） |
| 关页签 | `dmt_EditorControl.closeDocument(tabId)` |
| 切页（仅对已有页签有效） | `dmt_EditorControl.activateDocument('<docUuid>@<容器uuid>')` |
| 开页成页签（唯一入口，**慢**） | `dmt_EditorControl.openDocument(docUuid)` ⇒ 发射 + 长轮询 |

---

## 4. 页签治理（额度是硬约束）

- **库文档也攒页签**，攒满后 `openInEditor` 会**静默失败**（不是报错！症状是"当前文档还停在上一个"）。
  ⇒ **建完一个就关**（`closeDocument(fpUuid)`）；每 10 个 + 开跑前清一遍；
  **只关库文档那一类，绝不碰原理图页/PCB**。
- **绝不能把原理图页签全关**：一个都不剩时会话上下文丢失、建页直接抛错 ⇒ 保底留 ≥2。
- tabId 形态（实测）：工程内文档 `<docUuid>@<工程uuid>`；库文档 `<docUuid>@<库名>`。

---

## 5. 取出库文件 / 交付文件（必须分块 base64）

接口返回的是**内存 `File`（没有本地路径）**，只能走"客户端读 → 分块 base64 → 宿主拉回"：

```js
const f = await eda.sys_FileManager.getFootprintFileByFootprintUuid(fpUuid, lib, 'elibz2'); // 或各类 getXxxFile()
const ab = await f.arrayBuffer(); const u8 = new Uint8Array(ab);
let out = ''; const CH = 8192;
for (let i = 0; i < u8.length; i += CH) {
  out += btoa(String.fromCharCode.apply(null, u8.subarray(i, i + CH)));
  window.__CH = out;                       // 供宿主分块拉回
}
window.__FILE = {name: f.name, size: f.size, b64: out};
```
- ★ **必须 8KB 分块**：逐字符 `s += String.fromCharCode(u8[i])` 在大文件上会占死主线程。
- ★ 用"发射不等待" + 轮询 `window.__FILE`（单次 30s 上限是硬墙）。
- 库文件是 **ZIP**：内含 V3 源文本（`ProLib_*.elibu`）+ 库索引 JSON。
  **想学某种源格式，先建一个空的/取一个现成的把文件取出来看** —— 比读文档可靠。
- ⚠ 批量导出时**键名错配会被 `except` 静默吞掉、伪装成"超时"** ⇒
  每项即时 flush 打印、`except` 带 `str(e)`、写盘前 `assert key in EXT`。

---

## 6. 台账同步（程序化改动必做）

程序化改动**必须同步订正自己的台账**，否则下一轮验收立刻报 missing。建议目录约定：

| 台账 | 内容 |
|---|---|
| `<页>_ops.json` | 该页期望图元的**权威清单**（器件/导线/端口/电源/文字/折线/NC）—— **验收就靠它** |
| `<页>.json` | 页面图元的结构化描述（mm、y 正向），供生成器使用 |
| `*_bak/` | 各类**回滚包**（改动前全量快照） |

★ 每次改动固定四步：**先备份 → 改 EDA → 同步台账 → 复跑验收 + 写报告**。
