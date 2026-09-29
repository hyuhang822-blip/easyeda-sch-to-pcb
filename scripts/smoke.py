#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""环境自检 + 工程速览（**先用它确认链路通了，再干正事**）。

依次检查并给出可操作的提示：
  ① bridge 是否在跑   ② EDA 是否连上   ③ 当前工程/文档   ④ 原理图页 / PCB 板 / 库项速览

用法：
    export no_proxy="127.0.0.1,localhost"       # ★ 必做，否则本地端口会被代理拦成 502
    python smoke.py                             # 只读自检
    python smoke.py --expect-project "我的工程"   # 顺带做工程护栏检查
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ee_bridge as E          # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--expect-project', help='期望的当前工程名（做护栏检查）')
    a = ap.parse_args()

    print('─' * 56)
    print('① bridge 健康检查')
    h = E.health()
    if not h.get('ok', True):
        print('  ✗ 连不上 bridge：%s' % h.get('error'))
        print('  → 用**受管后台任务**启动 bridge 服务（不要用 nohup &），端口默认 49620。')
        return 1
    print('  service=%s  edaConnected=%s  edaWindowCount=%s'
          % (h.get('service'), h.get('edaConnected'), h.get('edaWindowCount')))
    if h.get('service') != 'easyeda-bridge':
        print('  ✗ /health 返回的不是 easyeda-bridge —— 这个端口被别的程序占用了。')
        return 1

    print('② EDA 扩展连接')
    wid = E.require_connected()
    if not wid:
        print('  → 在立创EDA专业版里：API Gateway → 重新连接（扩展重试约 5 次就停，不会自动重连）。')
        return 1

    print('③ 当前工程 / 文档')
    info = E.js('const p = await eda.dmt_Project.getCurrentProjectInfo();'
                'const d = await eda.dmt_SelectControl.getCurrentDocumentInfo();'
                'return {p: p ? {name: p.friendlyName, uuid: p.uuid} : null,'
                '        d: d ? {uuid: d.uuid} : null};', wid)
    proj = (info or {}).get('p')
    if not proj:
        print('  ✗ 当前没有打开的工程')
        return 1
    print('  工程 = %r' % proj['name'])
    print('  当前文档 uuid = %s' % ((info or {}).get('d') or {}).get('uuid'))
    if a.expect_project:
        if proj['name'] != a.expect_project:
            print('  ✗ 护栏不通过：期望 %r' % a.expect_project)
            return 2
        print('  ✓ 护栏通过')

    print('④ 内容速览')
    s = E.js("""
try {
  const pages = await eda.dmt_Schematic.getAllSchematicPagesInfo() || [];
  let pcbs = [];
  try { pcbs = (await eda.dmt_Pcb.getAllPcbsInfo() || []).map(p => p.name); } catch (e) {}
  let lib = {};
  try {
    const fps = await eda.lib_Footprint.search('', null, null, 500, 1) || [];
    lib.footprints = fps.length;
  } catch (e) {}
  let cnt = {};
  try { cnt.components = (await eda.sch_PrimitiveComponent.getAll() || []).length; } catch (e) {}
  try { cnt.wires      = (await eda.sch_PrimitiveWire.getAll() || []).length; } catch (e) {}
  try { cnt.texts      = (await eda.sch_PrimitiveText.getAll() || []).length; } catch (e) {}
  try { cnt.polys      = (await eda.sch_PrimitivePolygon.getAll() || []).length; } catch (e) {}
  return {pages: pages.map(p => p.name), pcbs: pcbs, lib: lib, cnt: cnt};
} catch (e) { return {err: String(e.message || e)}; }
""", wid)
    if s.get('err'):
        print('  ⚠ 速览失败：%s' % s['err'])
    else:
        print('  原理图页 %d：%s' % (len(s.get('pages') or []), ', '.join(s.get('pages') or [])[:120]))
        print('  PCB 板 %s' % (s.get('pcbs') or []))
        print('  工程库封装数 ≈ %s' % (s.get('lib') or {}).get('footprints'))
        print('  **当前页**图元：器件 %s / 导线 %s / 文字 %s / 折线 %s'
              % (s.get('cnt', {}).get('components'), s.get('cnt', {}).get('wires'),
                 s.get('cnt', {}).get('texts'), s.get('cnt', {}).get('polys')))
        print('  ⚠ 图元 API 只作用于**当前打开的文档**，上面四项是"当前文档"的，不是整个工程的。')

    print('─' * 56)
    print('✓ 链路已通。接下来按 SKILL.md 的 §3（原理图）或 §4（PCB）走。')
    print('★ 记住两条最容易翻车的：改完必须 **关页签重开再读回**；验收口径必须含**数量对账**。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
