#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""立创EDA bridge 客户端（通用版）。

要点（全部来自实盘踩坑，详见 ../references/bridge-and-env.md）：
- bridge 必须**受管后台任务**常驻；跑之前先 `export no_proxy="127.0.0.1,localhost"`。
- `js()` 发**裸代码**（不要自己套 IIFE）：扩展会把代码包成 async 函数，你 `return` 就能拿回结果。
- **单次调用 30s 上限** ⇒ 大批量用 `batched()` 自动分批。
- 扩展重连后 `windowId` 会变 ⇒ `execute()` 认 **两种**错误措辞并自动换窗重发。
- 写操作前先过 `guard_project()`。

用法：
    import ee_bridge as E
    wid = E.require_connected()
    E.guard_project('MyProject_Rebuild', wid)
    d = E.js("const n = (await eda.sch_PrimitiveWire.getAll()).length; return {n};", wid)
    E.batched(range(163), 15, lambda part: E.js("/* ... */", wid))
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

PORT = int(os.environ.get('EE_BRIDGE_PORT', '49620'))
BASE = 'http://127.0.0.1:%d' % PORT


# ───────────────────────── 基础 HTTP ─────────────────────────
def _post(path: str, payload: dict, timeout: float = 40.0):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def health(timeout: float = 10.0) -> dict:
    try:
        with urllib.request.urlopen(BASE + '/health', timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def active_window() -> str | None:
    return health().get('activeWindowId')


def execute(code: str, window_id: str | None = None):
    """返回 (ok, payload)。★ 扩展重连后自动换窗重发（认两种措辞）。"""
    resp = _post('/execute', {k: v for k, v in
                              (('code', code), ('windowId', window_id)) if v is not None})
    if resp.get('success'):
        return True, resp.get('result')
    err = resp.get('error') or json.dumps(resp, ensure_ascii=False)
    if ('no longer connected' in err) or ('disconnected' in err):
        w = active_window()
        if w and w != window_id:
            resp = _post('/execute', {'code': code, 'windowId': w})
            if resp.get('success'):
                print('  （已自动切到新窗口 %s 重发成功）' % w)
                return True, resp.get('result')
            err = resp.get('error') or err
    return False, err


def js(body: str, window_id: str | None = None):
    """执行一段 JS。**发裸代码**（不要 IIFE），末尾 `return xxx;`。"""
    ok, payload = execute(body, window_id)
    if not ok:
        raise RuntimeError('EDA 执行失败：%s' % payload)
    return payload


def jstr(s: str) -> str:
    """把 Python 字符串安全塞进 JS 字面量（写页源时用这个，别自己拼）。"""
    return json.dumps(s, ensure_ascii=False)


# ───────────────────────── 连接与护栏 ─────────────────────────
def require_connected(verbose: bool = True) -> str | None:
    h = health()
    if not h.get('edaConnected') or not h.get('edaWindowCount'):
        print('✗ bridge 在跑，但没有 EDA 窗口连接。health =', json.dumps(h, ensure_ascii=False))
        print('  → 在立创EDA里点 API Gateway → 重新连接；或先确认扩展已启用。')
        return None
    wid = h.get('activeWindowId')
    if verbose:
        print('✓ bridge %s connected，active window = %s' % (BASE, wid))
    return wid


def guard_project(expected_name: str, window_id: str | None = None):
    """★★ 防误写护栏：确认当前活动工程就是目标工程。写操作前必过。"""
    info = js('const p = await eda.dmt_Project.getCurrentProjectInfo();'
              'return p ? {friendlyName: p.friendlyName, uuid: p.uuid} : null;', window_id)
    if not info:
        raise RuntimeError('护栏失败：当前没有打开的工程')
    if info['friendlyName'] != expected_name:
        raise RuntimeError('护栏失败：当前工程 = %r，期望 %r' % (info['friendlyName'], expected_name))
    print('✓ 护栏通过：当前活动工程 = %r' % info['friendlyName'])
    return info


# ───────────────────────── 分批与轮询 ─────────────────────────
def batched(items, size: int, fn, pause: float = 0.4):
    """★ 单次 bridge 调用 30s 上限 ⇒ 大批量必须分批。返回每批结果列表。

    fn(part) 收到一批；若某批抛错，打印并继续（由调用方决定是否重试）。
    """
    out = []
    items = list(items)
    for i in range(0, len(items), size):
        part = items[i:i + size]
        try:
            out.append(fn(part))
        except Exception as e:                       # noqa: BLE001
            print('  第 %d 批失败：%s' % (i // size + 1, e))
            out.append(None)
        time.sleep(pause)
    return out


def poll(fn, until, timeout: float = 90.0, interval: float = 1.0, label: str = ''):
    """轮询直到 until(值) 为真。★ 单次读超时**不算结束**（异常只记一笔、继续）。

    返回最后一次的值；超时返回该值。
    """
    t0 = time.time()
    val = None
    while time.time() - t0 < timeout:
        try:
            val = fn()
            if until(val):
                return val
        except Exception as e:                       # noqa: BLE001
            print('  %s 轮询异常（忽略）：%s' % (label, e))
        time.sleep(interval)
    return val


def stable_read(fn, expect_same: int = 3, interval: float = 2.0, label: str = '', cap: float = 120.0):
    """★ 落图/写操作是**异步提交**的 ⇒ 等到"连续 N 次读数不变"才算稳定。"""
    prev, same, t0 = object(), 0, time.time()
    while time.time() - t0 < cap:
        cur = fn()
        if cur == prev:
            same += 1
            if same >= expect_same:
                return cur
        else:
            same = 0
            prev = cur
        time.sleep(interval)
    return prev


def fire(code: str, window_id: str | None = None) -> str | None:
    """★ 慢接口（openDocument / createSchematicPage…）用这个：**发射不等待**。

    之后一定要 `poll()` 到位再继续，否则会写到上一个文档。
    """
    return js("try{const pr=(function(){ %s })(); if(pr&&pr.catch)pr.catch(()=>{}); return 'fired';}"
              "catch(e){return 'E:'+e;}" % code, window_id)


def cur_doc(window_id: str | None = None) -> str | None:
    return js('const d = await eda.dmt_SelectControl.getCurrentDocumentInfo();'
              'return d ? d.uuid : null;', window_id)


def open_page(window_id: str, page_uuid: str, timeout: float = 120.0, settle: float = 2.5):
    """把某页开成页签并等它成为当前文档。★ 唯一能主动打开指定页的入口，**很慢**。"""
    if cur_doc(window_id) == page_uuid:
        return True
    fire('return eda.dmt_EditorControl.openDocument(%s);' % jstr(page_uuid), window_id)
    ok = poll(lambda: cur_doc(window_id), lambda v: v == page_uuid,
              timeout=timeout, interval=1.5, label='open_page')
    if ok == page_uuid:
        time.sleep(settle)
        return True
    return False


if __name__ == '__main__':
    print(json.dumps(health(), ensure_ascii=False, indent=1))
