#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逻辑自检：不碰 EDA，只测示例里"对账判据"这类纯逻辑。

发布前跑一遍，确保**判据本身**没问题（判据错了，示例会给出误导性的结论）：
    python selftest.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import demo_roundtrip as D          # noqa: E402


def fake_read(wires=(), polys=(), texts=()):
    return {'ok': True,
            'W': [{'l': list(w), 'n': 'DEMO'} for w in wires],
            'P': [list(p) for p in polys],
            'T': [{'c': c, 'x': 0, 'y': 0} for c in texts]}


def main():
    bad = 0

    # ① key() 必须与点的顺序无关（否则同一段线会被判成"缺失"）
    a = D.key([200, -200, 300, -200])
    b = D.key([300, -200, 200, -200])
    print('① key() 顺序无关：%s' % ('✓' if a == b else '✗'))
    bad += 0 if a == b else 1

    # ② 全部落齐 → 应通过
    full = fake_read(wires=[w for (w, _) in D.WIRES], polys=D.POLYS,
                     texts=[c for (_, _, c) in D.TEXTS])
    ok = D.audit(full, '自检-全齐')
    print('② 全齐应通过：%s' % ('✓' if ok else '✗'))
    bad += 0 if ok else 1

    # ③ 少一条导线 → 必须**不通过**（这是"只查能否找到"会漏掉的那类）
    miss1 = dict(full)
    miss1['W'] = full['W'][:-1]
    ok = D.audit(miss1, '自检-缺1导线')
    print('③ 缺一条导线应不通过：%s' % ('✓' if not ok else '✗'))
    bad += 0 if not ok else 1

    # ④ 少一条文字 → 必须不通过
    miss2 = dict(full)
    miss2['T'] = full['T'][:-1]
    ok = D.audit(miss2, '自检-缺1文字')
    print('④ 缺一条文字应不通过：%s' % ('✓' if not ok else '✗'))
    bad += 0 if not ok else 1

    # ⑤ 清场后（全空）→ 应不通过（说明"清场"这一步真的被验证到了）
    ok = D.audit(fake_read(), '自检-已清空')
    print('⑤ 清空后应不通过：%s' % ('✓' if not ok else '✗'))
    bad += 0 if not ok else 1

    print('\n%s' % ('全部通过 ✓' if not bad else '有 %d 项不通过 ✗' % bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
