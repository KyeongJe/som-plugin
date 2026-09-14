"""Diagram text must not overflow, spill out of its box, or collide.

Three failures shipped in the README, and nothing could have caught them --
the diagrams are generated, byte-compared against themselves, and never
measured:

  1. `graph-tests` had its height hardcoded at 378 for ten rows. Adding two
     suites pushed the last two labels 37px off the bottom of the canvas.
  2. `coordinator-loop` put a 660px sentence in the 64px gap between two
     bands -- the same strip three arrow labels live in -- so it ran straight
     through all of them.
  3. `ambiguity-gate` put a 474px line inside a 440px box, on the same
     baseline as the label in the box beside it.
  4. `how_it_works` drew a six-item recipe list into a panel whose height was
     hardcoded for a shorter one, so the last row hung 21px below the border.
     Every label was inside the canvas and inside its own box -- only the box
     had escaped its container, which is why a text-only check missed it.

Two diagrams draw their panels as `<path>` and their badges as `<circle>`, so
counting only `<rect>` left every box check on `before-after` and
`guardrails` vacuous: one rectangle each, nothing to compare. Arrows are
`<path>` too, and crossing a box is what an arrow is for, so connectors take
part in the canvas-bounds check and nothing else.

Metrics are approximate -- Korean ~1.0em, Latin ~0.52em, ascent 0.85em,
descent 0.28em -- which is plenty for failures measured in tens of pixels.

    python engine/tests/test_diagrams.py
"""
import re
import sys
from pathlib import Path

SOM = Path(__file__).resolve().parents[2]
IMG = SOM / "docs" / "img"
sys.stdout.reconfigure(encoding="utf-8")

TEXT = re.compile(r"<text\b([^>]*)>(.*?)</text>", re.S)
RECT = re.compile(r"<rect\b([^>]*?)/?>")
ATTR = re.compile(r'(\w[\w-]*)\s*=\s*"([^"]*)"')
TAG = re.compile(r"<[^>]+>")


def width_of(s: str, size: float) -> float:
    w = 0.0
    for ch in s:
        if "\uac00" <= ch <= "\ud7a3" or "\u4e00" <= ch <= "\u9fff":
            w += size
        elif ch in " .,:·|'":
            w += size * 0.30
        else:
            w += size * 0.52
    return w


def num(a, k, d=0.0):
    try:
        return float(str(a.get(k, d)).replace("px", ""))
    except ValueError:
        return d


def parse(svg):
    """Text boxes, drawn panels, and connector endpoints.

    `<path>` is included as a bounding box because two diagrams draw their
    panels that way -- counting only `<rect>` made every box check on
    `before-after` and `guardrails` vacuous, one rectangle each with nothing to
    compare. It is tagged so the checks that must ignore arrows can.
    """
    texts, rects, pts = [], [], []
    for attrs, inner in TEXT.findall(svg):
        a = dict(ATTR.findall(attrs))
        label = TAG.sub("", inner).strip()
        if not label:
            continue
        size = num(a, "font-size", 12)
        w = width_of(label, size)
        x, y = num(a, "x"), num(a, "y")
        anchor = a.get("text-anchor", "start")
        x0 = x - w if anchor == "end" else (x - w / 2 if anchor == "middle" else x)
        texts.append({"x0": x0, "x1": x0 + w, "y": y, "size": size, "text": label})

    for attrs in RECT.findall(svg):
        a = dict(ATTR.findall(attrs))
        rects.append({"x": num(a, "x"), "y": num(a, "y"), "w": num(a, "width"),
                      "h": num(a, "height"), "kind": "rect"})
    for attrs in re.findall(r"<path([^>]*?)/?>", svg):
        a = dict(ATTR.findall(attrs))
        xs, ys = [], []
        for mx, my in re.findall(r"(-?[\d.]+)[ ,](-?[\d.]+)", a.get("d", "")):
            xs.append(float(mx))
            ys.append(float(my))
        if len(xs) >= 3:
            rects.append({"x": min(xs), "y": min(ys), "w": max(xs) - min(xs),
                          "h": max(ys) - min(ys), "kind": "path"})
    for attrs in re.findall(r"<circle([^>]*?)/?>", svg):
        a = dict(ATTR.findall(attrs))
        cx, cy, rad = num(a, "cx"), num(a, "cy"), num(a, "r")
        if rad > 0:
            rects.append({"x": cx - rad, "y": cy - rad, "w": 2 * rad,
                          "h": 2 * rad, "kind": "circle"})

    for attrs in re.findall(r"<(?:line|polyline)([^>]*?)/?>", svg):
        a = dict(ATTR.findall(attrs))
        if "x1" in a:
            pts.append((num(a, "x1"), num(a, "y1")))
            pts.append((num(a, "x2"), num(a, "y2")))
        for mx, my in re.findall(r"(-?[\d.]+)[ ,](-?[\d.]+)", a.get("points", "")):
            pts.append((float(mx), float(my)))
    return texts, rects, pts


def segments(svg):
    """Straight runs of every connector, as ((x1,y1),(x2,y2)) pairs.

    Arrows in these diagrams are polylines of horizontal and vertical runs, so
    the segments are what actually gets drawn across the canvas -- and what can
    be drawn across a label.
    """
    out = []
    for attrs in re.findall(r"<(?:polyline|path)([^>]*?)/?>", svg):
        a = dict(ATTR.findall(attrs))
        raw = a.get("points") or a.get("d") or ""
        pts = [(float(x), float(y))
               for x, y in re.findall(r"(-?[\d.]+)[ ,](-?[\d.]+)", raw)]
        # A marker definition is a tiny arrowhead in <defs>, not a connector.
        if len(pts) < 2 or max(x for x, _ in pts) - min(x for x, _ in pts) < 12 and \
           max(y for _, y in pts) - min(y for _, y in pts) < 12:
            continue
        out.extend(zip(pts, pts[1:]))
    for attrs in re.findall(r"<line([^>]*?)/?>", svg):
        a = dict(ATTR.findall(attrs))
        out.append(((num(a, "x1"), num(a, "y1")), (num(a, "x2"), num(a, "y2"))))
    return out


def crossings(texts, segs):
    """Labels a connector is drawn straight through.

    The arrow out of DAG descended at x=585 while the band caption occupied
    x 512-652 at the same height, so the line cut through "실행  ·  워커". Every
    other check passed: the text was inside the canvas, inside no box, and
    collided with no other text.
    """
    bad = []
    for t in texts:
        top, bot = t["y"] - t["size"] * 0.85, t["y"] + t["size"] * 0.28
        for (x1, y1), (x2, y2) in segs:
            # Bounding boxes are not enough: a diagonal leader line passes
            # under a label whose box it overlaps. Clip the segment to the
            # label's x-range and compare the real y there.
            lo_x = max(min(x1, x2), t["x0"] + 2)
            hi_x = min(max(x1, x2), t["x1"] - 2)
            if lo_x > hi_x:
                continue
            if abs(x2 - x1) < 1e-9:                      # vertical
                ys = (min(y1, y2), max(y1, y2))
            else:
                m = (y2 - y1) / (x2 - x1)
                ya = y1 + (lo_x - x1) * m
                yb = y1 + (hi_x - x1) * m
                ys = (min(ya, yb), max(ya, yb))
            if ys[1] < top + 1 or ys[0] > bot - 1:
                continue
            bad.append(f'선이 글자를 관통합니다 — "{t["text"][:30]}" '
                       f'(글자 x {t["x0"]:.0f}~{t["x1"]:.0f}, y {top:.0f}~{bot:.0f} · '
                       f'선 ({x1:.0f},{y1:.0f})→({x2:.0f},{y2:.0f}))')
            break
    return bad


def aiming(rects, segs):
    """Arrowheads that land on a box's edge instead of at a box.

    Both arrows out of DAG pointed into the same box: one at its centre and one
    at x=700, which is that box's right edge. The neighbour had nothing aimed
    at it, so the diagram said "this box gets the work twice" rather than "the
    work splits in two". Every other check passed -- the endpoint was inside
    the canvas, the line crossed no label, and the boxes did not overlap.

    A head that stops just short of a box is how an arrow is drawn, so only the
    horizontal position is judged: within a box's span but inside its 8px
    margin is aimed; landing in the gap between two boxes, or on the seam, is
    not.
    """
    panels = [r for r in rects
              if r.get("kind") != "path" and r["w"] >= 60 and 20 <= r["h"] <= 120]
    if len(panels) < 2:
        return []
    bad = []
    for (x1, y1), (x2, y2) in segs:
        # The head is the far end of the last run, and only vertical runs that
        # stop above a row of boxes are aiming at one.
        if abs(x2 - x1) > 1e-9 or y2 <= y1:
            continue
        row = [p for p in panels if y2 <= p["y"] <= y2 + 14]
        if len(row) < 2:
            continue                       # not a row of alternatives
        inside = [p for p in row if p["x"] + 8 <= x2 <= p["x"] + p["w"] - 8]
        if inside:
            continue
        near = min(row, key=lambda p: min(abs(x2 - p["x"]), abs(x2 - p["x"] - p["w"])))
        bad.append(f'화살표가 박스 가장자리를 가리킵니다 — 끝점 x={x2:.0f} · '
                   f'박스 {near["x"]:.0f}~{near["x"] + near["w"]:.0f} '
                   f'(어느 박스를 가리키는지 모호합니다)')
    return bad


def check(path: Path):
    svg = path.read_text(encoding="utf-8")
    m = re.search(r'viewBox="([-\d.]+) ([-\d.]+) ([\d.]+) ([\d.]+)"', svg)
    if not m:
        return ["viewBox 없음"]
    vx, vy, vw, vh = (float(g) for g in m.groups())
    texts, rects, pts = parse(svg)
    rects.sort(key=lambda r: r["w"] * r["h"])     # innermost container first
    solid = [r for r in rects if r["kind"] != "path"]
    out = []

    # --- text against the canvas -------------------------------------------
    for t in texts:
        if t["x1"] > vx + vw + 0.5:
            out.append(f'글자가 캔버스 오른쪽으로 {t["x1"] - vx - vw:.0f}px — "{t["text"][:36]}"')
        if t["x0"] < vx - 0.5:
            out.append(f'글자가 캔버스 왼쪽으로 {vx - t["x0"]:.0f}px — "{t["text"][:36]}"')
        if t["y"] > vy + vh + 0.5:
            out.append(f'글자가 캔버스 아래로 {t["y"] - vy - vh:.0f}px — "{t["text"][:36]}"')
        # Descenders inside the box are not enough: a diagram sits directly
        # above the next block on the page, so 4px reads as touching it.
        gap = (vy + vh) - (t["y"] + t["size"] * 0.28)
        if 0 <= gap < 8:
            out.append(f'아래 여백 {gap:.0f}px — 다음 블록과 붙어 보입니다 — "{t["text"][:32]}"')

    # --- panels against the canvas -----------------------------------------
    for r in solid:
        if r["w"] <= 0 or r["h"] <= 0:
            out.append(f'크기가 0 이하인 상자 @ ({r["x"]:.0f},{r["y"]:.0f})')
        if r["x"] + r["w"] > vx + vw + 0.5 or r["y"] + r["h"] > vy + vh + 0.5:
            out.append(f'상자가 캔버스를 벗어납니다 — {r["w"]:.0f}x{r["h"]:.0f} '
                       f'@ ({r["x"]:.0f},{r["y"]:.0f})')

    # --- text inside the panel it belongs to --------------------------------
    for t in texts:
        # An arrow caption sitting BESIDE a panel is not inside it. Requiring
        # the label's midpoint to fall within the box tells the two apart;
        # without it every caption within a box's width read as an overflow.
        mid = (t["x0"] + t["x1"]) / 2
        cands = [r for r in solid
                 if r["w"] > 12 and r["h"] > 12
                 and r["y"] <= t["y"] <= r["y"] + r["h"]
                 and r["x"] <= mid <= r["x"] + r["w"]]
        if not cands:
            continue
        b = cands[0]
        over = max(t["x1"] - (b["x"] + b["w"]), b["x"] - t["x0"])
        if over > 2:
            out.append(f'글자가 박스 밖으로 {over:.0f}px — "{t["text"][:36]}" '
                       f'(박스 폭 {b["w"]:.0f}, 글자 {t["x1"] - t["x0"]:.0f})')

    # --- panel inside the panel it sits in ----------------------------------
    for c in solid:
        if c["w"] < 8 or c["h"] < 8:
            continue
        for b in solid:
            if b is c or b["w"] <= c["w"] or b["h"] <= c["h"]:
                continue
            if not (c["x"] >= b["x"] - 1 and c["x"] + c["w"] <= b["x"] + b["w"] + 1):
                continue
            if not (b["y"] - 1 <= c["y"] < b["y"] + b["h"]):
                continue
            spill = (c["y"] + c["h"]) - (b["y"] + b["h"])
            if spill > 1:
                out.append(f'박스가 바깥 상자 아래로 {spill:.0f}px — '
                           f'{c["w"]:.0f}x{c["h"]:.0f} @ ({c["x"]:.0f},{c["y"]:.0f}) 가 '
                           f'{b["w"]:.0f}x{b["h"]:.0f} @ ({b["x"]:.0f},{b["y"]:.0f}) 밖으로')
            break

    # --- two panels sharing the same space ----------------------------------
    for i, a in enumerate(solid):
        if a["w"] < 30 or a["h"] < 16:
            continue
        for b in solid[i + 1:]:
            if b["w"] < 30 or b["h"] < 16:
                continue
            inside = ((a["x"] <= b["x"] and a["y"] <= b["y"]
                       and a["x"] + a["w"] >= b["x"] + b["w"]
                       and a["y"] + a["h"] >= b["y"] + b["h"])
                      or (b["x"] <= a["x"] and b["y"] <= a["y"]
                          and b["x"] + b["w"] >= a["x"] + a["w"]
                          and b["y"] + b["h"] >= a["y"] + a["h"]))
            if inside:
                continue
            ox = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
            oy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
            if ox > 2 and oy > 2:
                out.append(f'두 박스가 {ox:.0f}x{oy:.0f}px 겹칩니다 — '
                           f'({a["x"]:.0f},{a["y"]:.0f}) ↔ ({b["x"]:.0f},{b["y"]:.0f})')

    # --- connector endpoints ------------------------------------------------
    for x, y in pts:
        if x < vx - 1 or x > vx + vw + 1 or y < vy - 1 or y > vy + vh + 1:
            out.append(f'선 끝점이 캔버스 밖 ({x:.0f},{y:.0f})')

    # --- arrowheads that aim at nothing --------------------------------------
    out.extend(aiming(rects, segments(svg)))

    # --- connectors drawn across a label ------------------------------------
    out.extend(crossings(texts, segments(svg)))

    # --- glyph boxes that intersect -----------------------------------------
    def band(t):
        return (t["y"] - t["size"] * 0.85, t["y"] + t["size"] * 0.28)
    for i, a in enumerate(texts):
        at, ab = band(a)
        for b in texts[i + 1:]:
            bt, bb = band(b)
            if ab + 2 <= bt or bb + 2 <= at:
                continue
            if a["x0"] < b["x1"] - 1.5 and b["x0"] < a["x1"] - 1.5:
                out.append(f'글자 겹침 (y 차이 {abs(a["y"] - b["y"]):.0f}px) — '
                           f'"{a["text"][:24]}" ↔ "{b["text"][:24]}"')
    return out


def test_no_diagram_text_overflows_or_collides():
    problems = []
    for p in sorted(IMG.glob("*.svg")):
        for x in check(p):
            problems.append(f"{p.name}: {x}")
    joined = "\n  ".join(problems[:20])
    assert not problems, f"다이어그램 {len(problems)}건:\n  {joined}"


def test_every_diagram_declares_a_viewbox():
    missing = [p.name for p in sorted(IMG.glob("*.svg"))
               if not re.search(r'viewBox="', p.read_text(encoding="utf-8"))]
    assert not missing, "viewBox 없는 다이어그램: " + ", ".join(missing)


def test_light_and_dark_have_the_same_geometry():
    """Only colours may differ. A size that drifts between them is a bug in one."""
    bad = []
    for light in sorted(IMG.glob("*-light.svg")):
        dark = light.with_name(light.name.replace("-light.svg", "-dark.svg"))
        if not dark.exists():
            bad.append(f"{light.name}: 짝이 되는 dark 판이 없습니다")
            continue
        a = re.search(r'viewBox="([^"]+)"', light.read_text(encoding="utf-8"))
        b = re.search(r'viewBox="([^"]+)"', dark.read_text(encoding="utf-8"))
        if a and b and a.group(1) != b.group(1):
            bad.append(f"{light.name}: viewBox 가 다릅니다 ({a.group(1)} vs {b.group(1)})")
    assert not bad, "\n  ".join(bad)


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in fns:
        try:
            f()
            print(f"  PASS  {n}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {n}: {e}")
        except Exception as e:
            failed += 1
            print(f"  FAIL  {n}: {type(e).__name__}: {e}")
    print()
    print(f"{len(fns) - failed}/{len(fns)} passed")
    raise SystemExit(1 if failed else 0)
