#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""图像对账工具（截图 ↔ 基准图）：定标 / 叠色 / 逐格缺口 / 并排对照。

**为什么需要**：立创专业版没有导出图片的 API，屏幕渲染 API 也读不回"画成了什么样"
⇒ "看着不对"类问题只能靠**用户截图 + 外部基准**做量化对账（见 ../references/verification.md）。

## 定标（关键一步）
把截图与我们**自己画的、绝对坐标已知的图元**配对，解出 `px = s·u + b`（u 为页坐标）：
    python img_overlay.py calib --shot S.png --pairs "476.5:400.0,827.5:850.0" --axis x
    # --pairs 格式 = "截图px:页坐标"，给两个以上已知对应点即可（示例值为示意）
横竖的 s 应当相同（等比例缩放）；**先把画布灰色网格从墨迹里排除**，否则定标会被网格带走。

## 三种对账图
    python img_overlay.py overlay  --shot S.png --base B.png --out o.png --calib 0.7803,83.8,0.7804,47.0
    python img_overlay.py gaps     --shot S.png --base B.png --out g.png --calib ... --cell 40
    python img_overlay.py side     --shot S.png --base B.png --out s.png --calib ... --zone 995,1169,228,700

叠色约定：**红 = 基准有我们没有（缺口）/ 青 = 我们有基准没有（多余）/ 黑 = 对齐**。

⚠ `--size` 是"页坐标"的宽高（单位数），按你的图幅给：A4 横向常为 `1169,827`（1 单位 = 0.01in）。
"""
from __future__ import annotations

import argparse
import os
import sys

UW, UH = 1169, 827          # 默认 A4 横向的页单位尺寸；用 --size 覆盖

_FONT_CANDIDATES = [
    'C:/Windows/Fonts/msyh.ttc',                 # Windows
    '/System/Library/Fonts/PingFang.ttc',        # macOS
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',   # Linux
    '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
]


def _font(size: int):
    """跨平台取一个能显示中文的字体；**取不到就返回 None**（调用方必须容错）。

    发布到社区时这一点很重要：字体路径是机器相关的，绝不能硬依赖某一个。
    """
    from PIL import ImageFont
    for p in _FONT_CANDIDATES:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return None


def _np():
    import numpy as np
    return np


def inkmask(R):
    """彩色或深色油墨；★ 排除中性灰网格（否则定标/对账会被立创的画布网格污染）。"""
    mx = R.max(axis=2)
    mn = R.min(axis=2)
    return ((mx - mn) > 45) | (R.sum(axis=2) < 420)


def load_base(path, size=(UW, UH)):
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    return Image.open(path).convert('RGB').resize(size, Image.LANCZOS)


def warp_shot(shot, calib, size=(UW, UH)):
    """把截图按定标反投影回页坐标。calib = (sx, ox, sy, oy)"""
    from PIL import Image
    np = _np()
    Image.MAX_IMAGE_PIXELS = None
    sx, ox, sy, oy = calib
    S = np.asarray(Image.open(shot).convert('RGB')).astype(np.int16)
    uu, vv = np.meshgrid(np.arange(size[0]), np.arange(size[1]))
    px = np.clip((sx * uu + ox).astype(int), 0, S.shape[1] - 1)
    py = np.clip((sy * vv + oy).astype(int), 0, S.shape[0] - 1)
    return S[py, px]


def cmd_calib(a):
    """从"截图 px : 页坐标 u"的对应点解 s 与 b（最少两点）。"""
    pts = []
    for chunk in a.pairs.split(','):
        p, u = chunk.split(':')
        pts.append((float(p), float(u)))
    if len(pts) < 2:
        print('✗ 至少给两个对应点，例如 --pairs "100.0:50.0,600.0:700.0"（截图px:页坐标）')
        return 1
    (p1, u1), (p2, u2) = pts[0], pts[-1]
    s = (p2 - p1) / (u2 - u1)
    b = p1 - s * u1
    print('%s 轴：s = %.4f , b = %.2f    （校验：u=%.1f → px=%.1f，实测 %.1f）'
          % (a.axis, s, b, u1, s * u1 + b, p1))
    print('⇒ calib 的 %s 分量传 (%.4f, %.2f)' % (a.axis, s, b))
    return 0


def cmd_overlay(a):
    from PIL import Image
    np = _np()
    O = inkmask(np.asarray(load_base(a.base)).astype(np.int16))
    W = inkmask(warp_shot(a.shot, a.calib))
    if a.shift:
        dy, dx = a.shift
        W = np.roll(np.roll(W, int(dy), 0), int(dx), 1)
    ov = np.full((O.shape[0], O.shape[1], 3), 255, np.uint8)
    ov[O & ~W] = (230, 30, 30)
    ov[W & ~O] = (0, 170, 190)
    ov[O & W] = (60, 60, 60)
    Image.fromarray(ov).save(a.out)
    print('→ %s   红(缺口) %d ；青(多余) %d ；黑(对齐) %d'
          % (a.out, int((O & ~W).sum()), int((W & ~O).sum()), int((O & W).sum())))


def cmd_gaps(a):
    np = _np()
    O = inkmask(np.asarray(load_base(a.base)).astype(np.int16))
    W = inkmask(warp_shot(a.shot, a.calib))
    cell = a.cell
    nu, mu = O.shape[0] // cell, O.shape[1] // cell
    rows = []
    for i in range(nu):
        for j in range(mu):
            o = O[i * cell:(i + 1) * cell, j * cell:(j + 1) * cell].mean()
            w = W[i * cell:(i + 1) * cell, j * cell:(j + 1) * cell].mean()
            if o > a.thr and w < a.thr * 0.25:
                rows.append((o - w, j, i, o, w))
    rows.sort(reverse=True)
    print('=== 缺口（基准有墨迹、我们空白）格宽 %d 单位 ===' % cell)
    for (_, j, i, o, w) in rows[:a.top]:
        print('   行%-3d 列%-3d  x %4d→%-4d y %4d→%-4d   基准 %.3f  我们 %.4f'
              % (i, j, j * cell, (j + 1) * cell, i * cell, (i + 1) * cell, o, w))
    print('缺口格数 %d / 总格 %d' % (len(rows), nu * mu))


def cmd_side(a):
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    x0, x1, y0, y1 = a.zone
    s = a.scale
    w, h = int((x1 - x0) * s), int((y1 - y0) * s)
    base = load_base(a.base)
    sx, ox, sy, oy = a.calib
    b = Image.open(a.shot).convert('RGB').crop(
        (int(sx * x0 + ox), int(sy * y0 + oy), int(sx * x1 + ox), int(sy * y1 + oy)))
    from PIL import ImageDraw, ImageFont
    canv = Image.new('RGB', (w * 2 + 16, h + 30), 'white')
    d = ImageDraw.Draw(canv)
    F = _font(18)
    d.text((4, 4), '基准图（同区域）', fill=(0, 0, 0), font=F)
    d.text((w + 20, 4), '我们的截图', fill=(0, 0, 0), font=F)
    canv.paste(base.crop((x0, y0, x1, y1)).resize((w, h), Image.LANCZOS), (2, 26))
    canv.paste(b.resize((w, h), Image.LANCZOS), (w + 14, 26))
    canv.save(a.out)
    print('→ %s' % a.out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['calib', 'overlay', 'gaps', 'side'])
    ap.add_argument('--shot'); ap.add_argument('--base'); ap.add_argument('--out')
    ap.add_argument('--pairs', help='截图px:页坐标 的对应点，逗号分隔')
    ap.add_argument('--axis', default='x', choices=['x', 'y'])
    ap.add_argument('--calib', help='sx,ox,sy,oy')
    ap.add_argument('--shift', help='额外平移 dy,dx（整数页单位）')
    ap.add_argument('--cell', type=int, default=40)
    ap.add_argument('--thr', type=float, default=0.15)
    ap.add_argument('--top', type=int, default=30)
    ap.add_argument('--zone', help='x0,x1,y0,y1')
    ap.add_argument('--scale', type=float, default=2.2)
    a = ap.parse_args()
    if a.calib:
        a.calib = tuple(float(v) for v in a.calib.split(','))
    if a.shift:
        a.shift = tuple(int(v) for v in a.shift.split(','))
    if a.zone:
        a.zone = tuple(int(v) for v in a.zone.split(','))
    return {'calib': cmd_calib, 'overlay': cmd_overlay,
            'gaps': cmd_gaps, 'side': cmd_side}[a.cmd](a)


if __name__ == '__main__':
    sys.exit(main())
