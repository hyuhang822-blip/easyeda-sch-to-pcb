#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""最小可跑示例：把「备份 → 落图 → 关页签重开复核 → 数量对账 → 清场」整条链走通。

## 为什么这个示例值得跑一遍
它刻意只做 6 个图元，但把平台**最容易翻车的四步**全踩到了：
  ① 图元 API **只作用于当前文档** ⇒ 必须先钉页；
  ② 文字/折线类图元在会话内"改/删"会**假成功** ⇒ 必须 **save → 关页签 → 重开 → 再读回**；
  ③ 验收必须做**数量对账**（只查"能否找到"的话，"删失败"也会显示 ✓）；
  ④ 改动前必须留**回滚包** —— 这里用的是"整份页源"（最可靠的撤回物）。

## 安全性
- **不新建页**（保存过的页很难删）。请自己指定一个**空白页或废弃页**；
- 开始前会把该页**整份源**备份到 `demo_backup/`，跑完**自动清场**并把页恢复原状；
- 只动**内容全以 `DEMO_` 开头的图元**。

用法：
    export no_proxy="127.0.0.1,localhost" NO_PROXY="127.0.0.1,localhost"
    python demo_roundtrip.py --page "P1" [--project "我的工程"] [--keep]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'scripts'))   # 技能自己的 scripts/
import ee_bridge as E          # noqa: E402
BAKDIR = os.path.join(HERE, 'demo_backup')

# 要落的图元（页单位；★ y 传负值 —— `create` 会把它取反写进页源）
# ⚠ 三条导线**两两不接触**：实测"同一网名且端点相接"的导线会被平台**自动合并成一条**，
#   而读回只给出其中一段 ⇒ 会误报"缺失"。真实工程里对账要改用"覆盖/连通"口径（见 references/pitfalls.md J6）。
WIRES = [
    ([200, -200, 300, -200], 'DEMO_A'),
    ([200, -260, 300, -260], 'DEMO_A'),
    ([400, -200, 500, -200], 'DEMO_B'),
]
TEXTS = [
    (200, -180, 'DEMO_文本一'),
    (200, -300, 'DEMO_文本二'),
]
POLYS = [
    [200, -320, 500, -320],
]


# ───────────────────────── 小工具 ─────────────────────────
def find_page(wid, name):
    pages = E.js('return (await eda.dmt_Schematic.getAllSchematicPagesInfo()||[])'
                 '.map(p=>({u:p.uuid,n:p.name}));', wid)
    hit = [p for p in pages if p['n'] == name]
    return hit[0] if hit else None


def pin(wid, uuid, timeout=120):
    """★ 把目标页钉成当前文档：**轮询到达才算数**（openDocument 很慢，sleep(3) 会写错文档）。"""
    return E.open_page(wid, uuid, timeout=timeout)


def close_tab(wid, uuid):
    """关页签（只关签不删页）。tabId = `<docUuid>@<工程uuid>`。"""
    pid = E.js('const p = await eda.dmt_Project.getCurrentProjectInfo();'
               'return p ? p.uuid : null;', wid)
    E.fire('return eda.dmt_EditorControl.closeDocument(%s);' % E.jstr('%s@%s' % (uuid, pid)), wid)
    E.poll(lambda: E.cur_doc(wid), lambda v: v != uuid, timeout=20, label='close_tab')
    time.sleep(1.0)


def read_back(wid):
    """读回当前页的三类图元（文字内容 / 折线几何 / 导线几何+网名）。"""
    return E.js("""
try {
  const T = (await eda.sch_PrimitiveText.getAll() || [])
      .map(t => ({c: String(t.getState_Content()||''), x: t.getState_X(), y: t.getState_Y()}));
  const P = (await eda.sch_PrimitivePolygon.getAll() || [])
      .map(g => g.getState_Line());
  const W = (await eda.sch_PrimitiveWire.getAll() || [])
      .map(w => ({l: w.getState_Line(), n: w.getState_Net()}));
  return {ok: true, T: T, P: P, W: W};
} catch (e) { return {ok: false, err: String(e.message || e)}; }
""", wid)


def key(line):
    return tuple(sorted([(round(line[0], 1), round(line[1], 1)),
                         (round(line[2], 1), round(line[3], 1))]))


def audit(page_read, tag):
    """★ 数量对账 + 坐标对账（两条都要）。"""
    got_t = {t['c'] for t in page_read['T']}
    got_p = {key(l) for l in page_read['P']}
    got_w = {key(w['l']) for w in page_read['W']}
    m_t = [c for (_, _, c) in TEXTS if c not in got_t]
    m_p = [p for p in POLYS if key(p) not in got_p]
    m_w = [w for (w, _) in WIRES if key(w) not in got_w]
    print('   [%s] 期望 文字%d/折线%d/导线%d  →  缺失 文字%d/折线%d/导线%d'
          % (tag, len(TEXTS), len(POLYS), len(WIRES), len(m_t), len(m_p), len(m_w)))
    return not (m_t or m_p or m_w)


# ───────────────────────── 主流程 ─────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--page', required=True, help='目标页名（请用空白页/废弃页）')
    ap.add_argument('--project', help='期望的当前工程名（做护栏）')
    ap.add_argument('--keep', action='store_true', help='保留落的图元（跳过清场，便于观察）')
    a = ap.parse_args()

    print('① 连接')
    wid = E.require_connected()
    if not wid:
        return 1
    guard = E.js('const p = await eda.dmt_Project.getCurrentProjectInfo();'
                 'return p ? p.friendlyName : null;', wid)
    if a.project and guard != a.project:
        print('✗ 护栏不通过：当前工程 %r，期望 %r' % (guard, a.project))
        return 2
    print('   当前工程 = %r' % guard)

    pg = find_page(wid, a.page)
    if not pg:
        print('✗ 找不到页 %r（先用 smoke.py 看页名列表）' % a.page)
        return 1
    print('   目标页 = %r  uuid=%s' % (pg['n'], pg['u']))

    print('② 钉页（★ 轮询到"当前文档 == 目标页"才继续）')
    if not pin(wid, pg['u']):
        print('✗ 打不开该页（可能页签额度满了；先空出一个页签再试）')
        return 1
    print('   ✓ 已钉住')

    print('③ 备份整份页源（★ 这就是回滚包）')
    src = E.js('const s = await eda.sys_FileManager.getDocumentSource();'
               'return s || "";', wid)
    os.makedirs(BAKDIR, exist_ok=True)
    bf = os.path.join(BAKDIR, '%s_before.txt' % a.page)
    open(bf, 'w', encoding='utf-8').write(src)
    print('   → %s（%d 字符）' % (bf, len(src)))

    print('④ 落图（★ 分批：单次调用 30s 上限）')
    r1 = E.batched(WIRES, 8, lambda part: E.js(
        "const L = %s; let n=0; for (const it of L) { const o = await eda.sch_PrimitiveWire.create(it[0], it[1]);"
        " if (o) { try { await o.done(); } catch (e) {} n++; } } return {ok:true, made:n};"
        % json.dumps(part), wid))
    r2 = E.batched([TEXTS], 8, lambda part: E.js(
        "const L = %s; let n=0; for (const it of L) { const o = await eda.sch_PrimitiveText.create("
        "it[0], it[1], it[2], 0, '#0000FF', null, 12, false, false, false, 2);"
        " if (o) { try { await o.done(); } catch (e) {} n++; } } return {ok:true, made:n};"
        % json.dumps(part[0]), wid))
    r3 = E.batched([POLYS], 8, lambda part: E.js(
        "const L = %s; let n=0; for (const it of L) { const o = await eda.sch_PrimitivePolygon.create("
        "it, '#0000FF', 'none', 3, 1); if (o) { try { await o.done(); } catch (e) {} n++; } }"
        " return {ok:true, made:n};" % json.dumps(part[0]), wid))
    print('   返回：导线 %s / 文字 %s / 折线 %s' % (r1, r2, r3))

    print('⑤ 等提交稳定（★ 落图是异步提交的；等到"期望条数都到齐"或超时）')
    E.poll(lambda: read_back(wid) or {},
           lambda d: (len([t for t in d.get('T', []) if t['c'].startswith('DEMO_')]) >= len(TEXTS)
                      and len(d.get('W', [])) >= len(WIRES)
                      and len(d.get('P', [])) >= len(POLYS)),
           timeout=60, interval=2.0, label='settle')
    print('   会话内先看一眼（这一眼**不算数**，因为还没过"关页签重开"）')
    ok1 = audit(read_back(wid), '会话内')

    print('⑥ ★ 存盘 → 关页签 → 重开 → 再读回（**这一眼才算数**）')
    print('   save →', E.js('return await eda.sch_Document.save();', wid))
    time.sleep(5.0)                      # ★ save() 是异步的：等几秒再关页签
    close_tab(wid, pg['u'])
    time.sleep(2.0)
    if not pin(wid, pg['u']):
        print('✗ 重开失败')
        return 1
    ok2 = audit(read_back(wid), '重开后')

    if not a.keep:
        print('⑦ 清场：**用步骤③的备份整份写回**（最可靠的撤回方式，连"删不掉的文字"一起还原）')
        print('   setDocumentSource →', E.js(
            'const r = await eda.sys_FileManager.setDocumentSource(%s); return {ok: !!r};'
            % E.jstr(src), wid))
        print('   save →', E.js('return await eda.sch_Document.save();', wid))
        time.sleep(5.0)
        close_tab(wid, pg['u'])
        time.sleep(2.0)
        if pin(wid, pg['u']):
            after = read_back(wid)
            left_t = [t['c'] for t in after['T'] if t['c'].startswith('DEMO_')]
            left_p = [l for l in after['P'] if key(l) in {key(p) for p in POLYS}]
            left_w = [w for w in after['W'] if key(w['l']) in {key(x) for (x, _) in WIRES}]
            print('   清场复核（关页签重开后）：残留 文字%d / 折线%d / 导线%d'
                  % (len(left_t), len(left_p), len(left_w)))
            if not (left_t or left_p or left_w):
                print('   ✓ 页面已还原（与改动前逐项一致）')
        close_tab(wid, pg['u'])

    print('─' * 60)
    print('结果：会话内对账 %s ；**关页签重开后对账 %s**' % ('通过' if ok1 else '不通过',
                                                          '通过' if ok2 else '不通过'))
    if not ok2:
        print('重开后不通过 ⇒ 说明有一类图元没落盘。回滚包在 %s，' % bf)
        print('可用「页源文本手术」把它整份写回：setDocumentSource(<备份内容>) → save。')
        return 2
    print('✓ 整条链通了。下一步看 SKILL.md §3（原理图）或 §4（PCB）。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
