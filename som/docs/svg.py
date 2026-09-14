"""Hand-authored SVG for the som documentation.

Two output targets, one drawing code path:

  Palette.css()      var(--sds-*) -- for diagrams embedded in a somdoc
                     document, so they inherit the document theme and print
                     correctly.
  Palette.literal()  hex baked in -- for standalone .svg files committed to
                     the repo. GitHub renders committed SVGs but resolves no
                     CSS variables, so a var() diagram comes out invisible
                     there. A dark variant exists only for GitHub's dark mode
                     via <picture>; it is not a supported document theme.

Why not archify: archify SVGs carry no styles of their own -- they depend on
classes in archify's own page stylesheet (`c-mask`, `t-backend`, `a-default`).
Lifting a bare <svg> out of one loses every colour. Archify's standalone HTML
is the right artifact for an explorable diagram and is kept separately under
docs/diagrams/.
"""
from __future__ import annotations

FONT = 'font-family="IBM Plex Sans KR, Malgun Gothic, system-ui, sans-serif"'
MONO = 'font-family="IBM Plex Mono, Consolas, monospace"'


def esc(t) -> str:
    """SVG is XML. An unescaped & in text like "R&R" makes the file unparseable,
    and GitHub then renders nothing at all."""
    return (str("" if t is None else t)
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


class Palette:
    KEYS = ("ink", "ink2", "ink3", "rule", "firm", "surf", "sunk",
            "acc", "good", "warn", "crit", "info", "page", "s1", "s2", "s3")

    def __init__(self, **kw):
        for k in self.KEYS:
            setattr(self, k, kw[k])

    @classmethod
    def css(cls) -> "Palette":
        return cls(
            ink="var(--sds-ink)", ink2="var(--sds-ink-2)", ink3="var(--sds-ink-3)",
            rule="var(--sds-rule)", firm="var(--sds-rule-firm)",
            surf="var(--sds-surface)", sunk="var(--sds-sunk)",
            acc="var(--sds-accent)", good="var(--sds-good)", warn="var(--sds-warn)",
            crit="var(--sds-crit)", info="var(--sds-info)", page="none",
            s1="var(--sds-series-1)", s2="var(--sds-series-2)", s3="var(--sds-series-3)",
        )

    @classmethod
    def literal(cls, mode: str = "light") -> "Palette":
        if mode == "light":
            return cls(
                ink="#0f1b22", ink2="#465a62", ink3="#6b7f88",
                rule="#d7dfe1", firm="#b6c4c9", surf="#ffffff", sunk="#e8edef",
                acc="#1c5cab", good="#0e7a55", warn="#a86f00", crit="#c23a39",
                info="#1c5cab", page="#f1f4f6",
                s1="#2a78d6", s2="#eb6834", s3="#1baf7a",
            )
        return cls(
            ink="#e6edf3", ink2="#9fb0b8", ink3="#7d8f98",
            rule="#30393f", firm="#4a565d", surf="#171d21", sunk="#21292e",
            acc="#6ea8fe", good="#3fb98a", warn="#d9a03a", crit="#e26d6c",
            info="#6ea8fe", page="#0d1117",
            s1="#6ea8fe", s2="#f0855a", s3="#3fb98a",
        )


# ---------------------------------------------------------------- primitives
def _open(w: int, h: int, label: str, P: Palette) -> str:
    bg = "" if P.page == "none" else f'<rect width="{w}" height="{h}" fill="{P.page}"/>'
    return (f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="{esc(label)}">'
            f'{bg}'
            f'<defs>'
            f'<marker id="ar" markerWidth="9" markerHeight="7" refX="8.5" refY="3.5" orient="auto">'
            f'<path d="M0,0 L9,3.5 L0,7 Z" fill="{P.firm}"/></marker>'
            f'<marker id="ar-acc" markerWidth="9" markerHeight="7" refX="8.5" refY="3.5" orient="auto">'
            f'<path d="M0,0 L9,3.5 L0,7 Z" fill="{P.acc}"/></marker>'
            f'<marker id="ar-good" markerWidth="9" markerHeight="7" refX="8.5" refY="3.5" orient="auto">'
            f'<path d="M0,0 L9,3.5 L0,7 Z" fill="{P.good}"/></marker>'
            f'<marker id="ar-crit" markerWidth="9" markerHeight="7" refX="8.5" refY="3.5" orient="auto">'
            f'<path d="M0,0 L9,3.5 L0,7 Z" fill="{P.crit}"/></marker>'
            f'</defs>')


def _box(P, x, y, w, h, title, sub=None, *, stroke=None, fill=None, dash=None,
         title_fill=None, r=8, tw=650) -> str:
    stroke = stroke or P.firm
    fill = fill or P.surf
    title_fill = title_fill or P.ink
    d = f' stroke-dasharray="{dash}"' if dash else ""
    ty = y + (h / 2 + 4) if not sub else y + h / 2 - 4
    out = (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" '
           f'fill="{fill}" stroke="{stroke}" stroke-width="1.4"{d}/>'
           f'<text x="{x + w / 2}" y="{ty}" text-anchor="middle" {FONT} '
           f'font-size="12.5" font-weight="{tw}" fill="{title_fill}">{esc(title)}</text>')
    if sub:
        out += (f'<text x="{x + w / 2}" y="{y + h / 2 + 12}" text-anchor="middle" {FONT} '
                f'font-size="10.5" fill="{P.ink3}">{esc(sub)}</text>')
    return out


def _band(P, x, y, w, h, label, *, fill=None) -> str:
    fill = fill or P.sunk
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{fill}" '
            f'fill-opacity="0.55" stroke="{P.rule}" stroke-dasharray="5 4"/>'
            f'<text x="{x + 12}" y="{y + 17}" {FONT} font-size="10.5" '
            f'font-weight="650" fill="{P.ink3}" letter-spacing="0.04em">{esc(label)}</text>')


def _arrow(P, pts, label=None, *, color=None, marker="ar", dash=None,
           lx=None, ly=None, anchor="middle") -> str:
    color = color or P.firm
    d = "M" + " L".join(f"{x},{y}" for x, y in pts)
    da = f' stroke-dasharray="{dash}"' if dash else ""
    out = (f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.6" '
           f'marker-end="url(#{marker})"{da}/>')
    if label:
        mx = lx if lx is not None else (pts[0][0] + pts[-1][0]) / 2
        my = ly if ly is not None else (pts[0][1] + pts[-1][1]) / 2 - 6
        out += (f'<text x="{mx}" y="{my}" text-anchor="{anchor}" {FONT} '
                f'font-size="10.5" fill="{P.ink2}">{label}</text>')
    return out


def _tag(P, x, y, text) -> str:
    return f'<text x="{x}" y="{y}" {MONO} font-size="10" fill="{P.ink3}">{esc(text)}</text>'


def _note(P, x, y, text, *, size=11, weight=650, fill=None, anchor="start") -> str:
    return (f'<text x="{x}" y="{y}" text-anchor="{anchor}" {FONT} font-size="{size}" '
            f'font-weight="{weight}" fill="{fill or P.ink2}">{esc(text)}</text>')


# ===========================================================================
# 1. Five-stage gate pipeline
# ===========================================================================
def pipeline(P: Palette | None = None) -> str:
    P = P or Palette.css()
    W, H = 940, 300
    s = [_open(W, H, "som 5-stage gate pipeline", P)]

    stages = [
        ("INTAKE", "요청을 명세로", "사람"),
        ("BLUEPRINT", "DAG · writes 글롭", "코디네이터"),
        ("EXECUTE", "워커 wave 병렬", "워커 N"),
        ("ATTEST", "독립 검증", "다른 dispatch"),
        ("DELIVER", "bundle 발행", "사람"),
    ]
    gates = [
        ("G0", "grain · acceptance", P.acc),
        ("G1", "깊이 ≤4 · 글롭 필수", P.acc),
        ("G2", "숫자 역추적 · % 합 100", P.good),
        ("G3", "항상 사람", P.crit),
    ]

    bw, gap, x0, y = 150, 40, 24, 92
    for i, (name, sub, who) in enumerate(stages):
        x = x0 + i * (bw + gap)
        accent = P.good if i == 4 else P.acc
        s.append(f'<rect x="{x}" y="{y - 26}" width="{bw}" height="18" rx="9" '
                 f'fill="{accent}" fill-opacity="0.12"/>')
        s.append(f'<text x="{x + bw / 2}" y="{y - 13}" text-anchor="middle" {FONT} '
                 f'font-size="10" font-weight="650" fill="{accent}">{esc(who)}</text>')
        s.append(_box(P, x, y, bw, 74, name, sub, stroke=accent,
                      title_fill=accent if i == 4 else P.ink))
        s.append(f'<text x="{x + 10}" y="{y + 66}" {MONO} font-size="9.5" '
                 f'fill="{P.ink3}">0{i + 1}</text>')

    for i, (gid, cond, col) in enumerate(gates):
        gx = x0 + bw + i * (bw + gap)
        cx = gx + gap / 2
        s.append(_arrow(P, [(gx + 4, y + 37), (gx + gap - 6, y + 37)],
                        color=col, marker="ar-acc" if col == P.acc else
                        ("ar-good" if col == P.good else "ar-crit")))
        s.append(f'<circle cx="{cx}" cy="{y - 4}" r="13" fill="{col}" fill-opacity="0.14" '
                 f'stroke="{col}" stroke-width="1.2"/>')
        s.append(f'<text x="{cx}" y="{y}" text-anchor="middle" {FONT} font-size="10.5" '
                 f'font-weight="700" fill="{col}">{esc(gid)}</text>')
        s.append(f'<text x="{cx}" y="{y + 100}" text-anchor="middle" {FONT} '
                 f'font-size="9.5" fill="{P.ink3}">{esc(cond)}</text>')

    # rework loop: ATTEST -> EXECUTE
    ax = x0 + 3 * (bw + gap) + bw / 2
    ex = x0 + 2 * (bw + gap) + bw / 2
    s.append(_arrow(P, [(ax, y + 74), (ax, y + 128), (ex, y + 128), (ex, y + 74)],
                    "불일치 → 재개 최대 2회, 초과 시 사람", color=P.warn,
                    lx=(ax + ex) / 2, ly=y + 144))

    s.append(_note(P, 24, 44, "게이트는 산문이 아니라 코드가 판정한다. 통과 조건을 "
                   "만족하지 못하면 다음 스테이지가 열리지 않는다.", size=12))
    s.append(_note(P, 24, 272, "동시성은 이 플러그인이 정한다 — Orca 는 스케줄링·충돌 판단을 하지 않는다. "
                   "writes 글롭이 교차하면 다음 wave 로 미룬다.",
                   size=10.5, weight=400, fill=P.ink3))
    s.append("</svg>")
    return "".join(s)


# ===========================================================================
# 2. Two processes, one consumer
# ===========================================================================
def coordinator_loop(P: Palette | None = None) -> str:
    P = P or Palette.css()
    # 440, not 400: the explanatory line used to sit at y=200, in the 64px gap
    # between the two bands -- the same strip the three arrow labels live in.
    # At 660px wide it ran straight through all of them. It goes under the
    # diagram now, where nothing else is.
    W, H = 900, 440
    s = [_open(W, H, "som coordinator loop", P)]

    s.append(_band(P, 20, 18, 860, 150, "CLAUDE CODE 세션  ·  컨텍스트 압축 대상"))
    s.append(_box(P, 60, 52, 200, 88, "ROUTER", "som-supervisor + router.mjs",
                  stroke=P.acc, title_fill=P.acc))
    s.append(_tag(P, 70, 128, "state.json · ack.json"))
    s.append(_box(P, 330, 52, 190, 88, "판단 · 변경", "gate / reply / replan"))
    s.append(_box(P, 590, 52, 250, 88, "check 를 절대 호출하지 않는다", None,
                  stroke=P.crit, dash="4 3", title_fill=P.crit, tw=600))

    s.append(_band(P, 20, 232, 860, 150,
                   # Was "호스트 관리 MONITOR 프로세스  ·  압축 면역", 203px wide
                   # from x=32 -- and both arrows into this band come down at
                   # x=160 and x=238, straight through it.
                   "MONITOR  ·  압축 면역", fill=P.surf))
    s.append(_box(P, 60, 266, 200, 88, "WATCHER", "monitors/inbox-watch.mjs",
                  stroke=P.good, title_fill=P.good))
    s.append(_tag(P, 70, 342, "loop.json"))
    s.append(_box(P, 330, 266, 190, 88, "유일한 소비자",
                  "orchestration check --wait", stroke=P.good))
    s.append(_box(P, 590, 266, 250, 88, "Orca 런타임", "Run · Task · Dispatch"))

    # lx=168 put this caption at x 168-260, and the neighbouring arrow comes
    # down at x=238 -- through it. Left of its own arrow instead.
    s.append(_arrow(P, [(160, 266), (160, 140)], "한 줄 = 이벤트 하나",
                    color=P.good, marker="ar-good", lx=64, ly=210, anchor="start"))
    s.append(_arrow(P, [(238, 140), (238, 266)], "ack 요청 (파일)",
                    color=P.acc, marker="ar-acc", lx=246, ly=186, anchor="start"))
    s.append(_arrow(P, [(520, 310), (590, 310)], color=P.good, marker="ar-good"))
    # ly=352 put this baseline inside both boxes it sits between (they span
    # y 266-354), so it overprinted their borders. 370 is below both and still
    # inside the band.
    s.append(_arrow(P, [(590, 330), (520, 330)], "delivery (≤50, FIFO)",
                    lx=555, ly=370))
    s.append(_arrow(P, [(715, 266), (715, 140)], "mutate", color=P.acc,
                    marker="ar-acc", lx=723, ly=205, anchor="start"))

    s.append(_note(P, 20, 406, "router 가 배치 처리 중 죽으면 ack 가 안 되므로, "
                   "다음 세션의 watcher 가 같은 배치를 그대로 재발행한다.",
                   size=11, fill=P.ink2))
    s.append(_note(P, 20, 424, "인바운드 이벤트용 별도 durability 가 필요 없는 이유가 "
                   "이것입니다.", size=10.5, weight=400, fill=P.ink3))
    s.append("</svg>")
    return "".join(s)


# ===========================================================================
# 3. somdoc: one IR, four emitters, and where humanize sits
# ===========================================================================
def somdoc_pipeline(P: Palette | None = None) -> str:
    P = P or Palette.css()
    W, H = 900, 430
    s = [_open(W, H, "somdoc pipeline", P)]

    s.append(_box(P, 24, 40, 150, 62, "skeleton", "rnr · kpi · charter"))
    s.append(_box(P, 24, 124, 150, 62, "원천 데이터", "xlsx · Snowflake"))
    s.append(_box(P, 240, 82, 170, 62, "doc-drafter", "한국어 프로즈 작성", stroke=P.acc))

    s.append(_band(P, 240, 178, 170, 106, "HUMANIZE"))
    s.append(_box(P, 252, 204, 146, 30, "extract", None, r=6, tw=600))
    s.append(_box(P, 252, 242, 146, 30, "apply + 불변식", None, r=6, tw=600, stroke=P.warn))

    s.append(_box(P, 470, 82, 170, 62, "IR (커밋 대상)", "*.somdoc.json",
                  stroke=P.good, title_fill=P.good))
    s.append(_tag(P, 480, 162, "sha256 · sorted keys"))

    s.append(f'<line x1="672" y1="24" x2="672" y2="406" stroke="{P.acc}" '
             f'stroke-width="1.4" stroke-dasharray="6 5"/>')
    s.append(_note(P, 678, 38, "결정론 경계", size=10.5, fill=P.acc))
    s.append(_note(P, 678, 52, "오른쪽은 LLM 없음", size=10, weight=400, fill=P.ink3))

    for i, (label, sub, y, color) in enumerate([
        ("HTML", "열람 · 단일 파일", 78, P.good),
        ("XLSX", "데이터 진실", 148, P.info),
        ("DOCX", "회람 (Phase 2)", 218, P.ink3),
        ("PPTX", "회의 12장 (Phase 4)", 288, P.ink3),
    ]):
        s.append(_box(P, 760, y, 120, 54, label, sub, stroke=color, r=6))
        s.append(_arrow(P, [(700, 113), (740, 113), (740, y + 27), (756, y + 27)]
                        if i else [(700, 113), (756, 105)]))

    s.append(_arrow(P, [(174, 71), (232, 100)]))
    s.append(_arrow(P, [(174, 155), (232, 124)]))
    s.append(_arrow(P, [(325, 144), (325, 198)], color=P.warn))
    s.append(_arrow(P, [(398, 249), (440, 249), (440, 120), (466, 113)],
                    "윤문 적용", color=P.warn, lx=452, ly=190, anchor="start"))
    s.append(_arrow(P, [(410, 113), (466, 113)], color=P.acc, marker="ar-acc"))

    s.append(_note(P, 24, 330, "에이전트가 저작하는 것은 IR JSON 하나뿐이다.", fill=P.ink))
    s.append(_note(P, 24, 350, "skeleton · theme · emitter 는 전부 플러그인 파일이라, "
                   "팀원이 커밋된 IR 로 재렌더하면 LLM 패스 없이 같은 바이트가 나온다.",
                   size=10.5, weight=400))
    s.append(_note(P, 24, 372, "humanize 는 LLM 패스라 비결정적이므로 경계 왼쪽, "
                   "IR 저작 단계에 있다.", size=10.5, weight=400))
    s.append("</svg>")
    return "".join(s)


# ===========================================================================
# 4. Snowflake guard
# ===========================================================================
def snowflake_guard(P: Palette | None = None) -> str:
    """Reads run; a write is described, approved, then recorded.

    The write branch used to be two boxes ending in "exit 3 DENY", which was
    the truth until this team needed to create a table. Redrawing it as its own
    band is the point of the change: the write does not stop, it goes through a
    person, and the diagram has to show the person.
    """
    P = P or Palette.css()
    W, H = 900, 436
    s = [_open(W, H, "somsql guard", P)]

    s.append(_box(P, 24, 128, 130, 62, "에이전트 SQL", "1 파일 = 1 쿼리"))

    s.append(_band(P, 190, 32, 300, 250, "SOMSQL CLASSIFIER"))
    s.append(_box(P, 206, 68, 268, 76, "1차  sqlglot AST",
                  "화이트리스트 · 파싱 실패는 deny", stroke=P.info))
    s.append(_box(P, 206, 168, 268, 76, "2차  토큰 regex",
                  "불일치도 deny 로 기록", stroke=P.info))
    s.append(_note(P, 340, 264, "둘이 엇갈리면 그 자체를 기록",
                   size=10.5, fill=P.ink3, anchor="middle"))

    # --- read path ---------------------------------------------------------
    s.append(_box(P, 540, 60, 150, 62, "cost guard", "EXPLAIN bytes · LIMIT", stroke=P.warn))
    s.append(_box(P, 540, 150, 150, 62, "SELECT 실행", "단일 커넥션 · SSO 1회",
                  stroke=P.good, title_fill=P.good))
    s.append(_box(P, 740, 150, 140, 62, "parquet 캐시", "+ .meta.json", stroke=P.good))

    s.append(_arrow(P, [(154, 159), (200, 130)]))
    s.append(_arrow(P, [(474, 106), (536, 91)], "읽기", color=P.good, marker="ar-good"))
    s.append(_arrow(P, [(615, 122), (615, 146)], color=P.warn))
    s.append(_arrow(P, [(690, 181), (736, 181)], color=P.good, marker="ar-good"))

    # --- write path --------------------------------------------------------
    s.append(_band(P, 190, 316, 690, 104, "쓰기 — 거부가 아니라 확인"))
    s.append(_box(P, 206, 344, 196, 62, "exit 4  CONFIRM",
                  "무엇을 · 어디에 · 되돌릴 수 있나", stroke=P.warn, title_fill=P.warn))
    s.append(_box(P, 442, 344, 196, 62, "사람이 승인",
                  "문 해시 12자 + 사유", stroke=P.acc, title_fill=P.acc))
    s.append(_box(P, 678, 344, 186, 62, "실행 · 원장 기록",
                  "WRITE_LOG.md", stroke=P.good, title_fill=P.good))

    # Vertical, and to the right of the band label: a diagonal into the band
    # ran straight through "쓰기 — 거부가 아니라 확인".
    s.append(_arrow(P, [(340, 288), (340, 340)], "읽기가 아니면",
                    color=P.warn, lx=226, ly=308, anchor="start"))
    s.append(_arrow(P, [(402, 375), (438, 375)], color=P.acc, marker="ar-acc"))
    s.append(_arrow(P, [(638, 375), (674, 375)], color=P.good, marker="ar-good"))

    s.append(_note(P, 24, 238, "쓰기는 막지 않는다.", fill=P.ink))
    s.append(_note(P, 24, 258, "아무도 안 본 쓰기를", size=10.5, weight=400))
    s.append(_note(P, 24, 274, "막는다. 승인은 그", size=10.5, weight=400))
    s.append(_note(P, 24, 290, "문장에만 붙는다 —", size=10.5, weight=400))
    s.append(_note(P, 24, 306, "한 글자만 고쳐도", size=10.5, weight=400))
    s.append(_note(P, 24, 322, "무효가 된다.", size=10.5, weight=400))
    s.append("</svg>")
    return "".join(s)


# ===========================================================================
# 5. Graphs — real measured numbers
# ===========================================================================
def _hbar(P, x0, y0, width, rows, *, unit="", label_w=180, row_h=30, maxv=None):
    maxv = maxv or max(v for _, v, _ in rows) or 1
    plot = width - label_w - 70
    out = []
    for i, (name, val, color) in enumerate(rows):
        y = y0 + i * row_h
        w = (val / maxv) * plot
        out.append(f'<text x="{x0 + label_w - 10}" y="{y + row_h / 2 + 4}" '
                   f'text-anchor="end" {FONT} font-size="11" fill="{P.ink2}">{esc(name)}</text>')
        out.append(f'<rect x="{x0 + label_w}" y="{y + 6}" width="{plot}" '
                   f'height="{row_h - 12}" rx="3" fill="{P.sunk}"/>')
        out.append(f'<rect x="{x0 + label_w}" y="{y + 6}" width="{max(w, 1):.1f}" '
                   f'height="{row_h - 12}" rx="3" fill="{color}"/>')
        txt = f"{val:,.1f}".rstrip("0").rstrip(".") if isinstance(val, float) else f"{val:,}"
        out.append(f'<text x="{x0 + label_w + w + 8:.1f}" y="{y + row_h / 2 + 4}" '
                   f'{FONT} font-size="11" font-weight="650" fill="{P.ink}">{esc(txt)}{esc(unit)}</text>')
    return "".join(out)


def graph_tests(P: Palette | None = None, *, manifest=10, ir=25, humanize=11,
                somsql=19, guards=15, clarity=35, intake=7, learn=41,
                autonomy=10, bare=6, cli=6, skeletons=6) -> str:
    P = P or Palette.css()
    rows = [
        ("IR 규칙 · golden", ir, P.s1),
        ("SQL 분류 · cost guard", somsql, P.s2),
        ("우회 가드 (Node)", guards, P.crit),
        ("모호도 게이트 (Node)", clarity, P.acc),
        ("패턴 학습 (Node)", learn, P.good),
        ("자율 레벨·플로어 (Node)", autonomy, P.s2),
        ("인터뷰 · 라우팅 (Node)", intake, P.warn),
        ("humanize 불변식", humanize, P.s3),
        ("매니페스트 규칙", manifest, P.ink3),
        ("CLI · 효율 (Node)", cli, P.ink3),
        ("스켈레톤·다이어그램 렌더", skeletons, P.s1),
        ("맨몸 설치 (Claude 만)", bare, P.warn),
    ]
    total = sum(v for _, v, _ in rows)

    # Height follows the row count. It was hardcoded at 378 for ten rows, so
    # adding two suites clipped the last two labels off the bottom of the
    # canvas -- visible in the README, invisible to every test.
    TOP, ROW_H, PAD = 66, 30, 16
    W = 640
    H = TOP + len(rows) * ROW_H + PAD

    s = [_open(W, H, "som test suites", P)]
    s.append(_note(P, 20, 30, f"테스트 {total}건 전부 통과", size=13, fill=P.ink))
    s.append(_note(P, 20, 48, "안전에 직결된 항목 — 숫자 유실·발명, metric ref 변경, "
                   "쓰기 우회, 매니페스트 로드 실패", size=10, weight=400, fill=P.ink3))
    s.append(_hbar(P, 20, TOP, W - 40, rows, unit="건", label_w=170, row_h=ROW_H))
    s.append("</svg>")
    return "".join(s)


def graph_size(P: Palette | None = None, *, som_html=68.3, som_xlsx=26.9,
               archify=605.5) -> str:
    P = P or Palette.css()
    W, H = 640, 190
    s = [_open(W, H, "artifact size", P)]
    s.append(_note(P, 20, 30, "단일 파일 HTML 은 외부 요청이 0이다", size=13, fill=P.ink))
    s.append(_note(P, 20, 48, "archify 독립 아티팩트의 약 1/9 크기이고, 망분리 랩탑에서도 열린다",
                   size=10, weight=400, fill=P.ink3))
    s.append(_hbar(P, 20, 66, W - 40, [
        ("som HTML (자기완결)", som_html, P.s1),
        ("som xlsx", som_xlsx, P.s3),
        ("archify 독립 HTML", archify, P.ink3),
    ], unit=" KB", label_w=150))
    s.append("</svg>")
    return "".join(s)


def graph_tokens(P: Palette | None = None, *, som=530, humanize=3059) -> str:
    P = P or Palette.css()
    W, H = 170, 170
    s = [_open(640, H, "always-on token cost", P)]
    s.append(_note(P, 20, 30, "세션당 always-on 토큰 비용", size=13, fill=P.ink))
    s.append(_note(P, 20, 48, "som 은 스킬 5개 · 훅 5개를 싣고도 가볍다. 훅은 harness 전용이라 "
                   "모델 컨텍스트를 쓰지 않는다", size=10, weight=400, fill=P.ink3))
    rows = [("som", som, P.s1), ("humanize-korean", humanize, P.s2)]
    s.append(_hbar(P, 20, 66, 600, rows, unit=" tok", label_w=150))
    s.append("</svg>")
    return "".join(s)


# ===========================================================================
# 6. What a teammate actually does
# ===========================================================================
def how_it_works(P: Palette | None = None) -> str:
    P = P or Palette.css()
    W, H = 940, 400
    s = [_open(W, H, "som: one sentence to finished work", P)]

    s.append(_box(P, 24, 44, 200, 62, "한 문장", "\"R&R 문서 만들어줘\"",
                  stroke=P.acc, title_fill=P.acc))

    # All six, and `data` labelled by its source rather than as "분석": an
    # unsourced "분석해줘" routes to `analyze`, so calling `data` the analysis
    # recipe said the opposite of the routing rule.
    RECIPES = [("doc", "문서"), ("prd", "PRD"), ("watch", "밤샘 확인"),
               ("analyze", "엑셀 분석"), ("build", "개발"), ("data", "SF 분석")]
    # The band height was hardcoded at 176 for a list that grew to six, so the
    # last row hung 21px below the panel it was supposed to sit inside. Same
    # defect as graph-tests: a container measured once and never again.
    R_TOP, R_STEP, R_H, R_PAD = 52, 29, 24, 12
    band_h = (R_TOP + (len(RECIPES) - 1) * R_STEP + R_H + R_PAD) - 24
    s.append(_band(P, 262, 24, 200, band_h, "레시피 선택"))
    for i, (rid, label) in enumerate(RECIPES):
        y = R_TOP + i * R_STEP
        hit = rid == "doc"
        s.append(_box(P, 276, y, 172, R_H, f"{rid}  ·  {label}", None, r=5,
                      stroke=P.acc if hit else P.rule,
                      title_fill=P.acc if hit else P.ink3, tw=650 if hit else 400))

    s.append(_box(P, 500, 44, 170, 62, "DAG", "작업 쪼개기 + 의존성"))

    # "WAVE 실행  ·  워커 병렬" spanned x 512-652, and the arrow from DAG comes
    # down at x=585 -- straight through the glyphs. The two boxes below say
    # "병렬" better than the caption did, so the caption gets out of the way.
    s.append(_band(P, 500, 128, 416, 172, "WAVE 실행"))
    for i, (label, sub, y) in enumerate([
        ("워커 1", "opus / high", 156),
        ("워커 2", "sonnet / med", 156),
    ]):
        s.append(_box(P, 516 + i * 200, y, 184, 50, label, sub, r=6, stroke=P.good))
    s.append(_box(P, 516, 224, 384, 50, "다음 wave", "앞 작업이 끝나야 시작하는 것 + 파일이 겹치는 것",
                  r=6, dash="4 3"))

    s.append(_arrow(P, [(224, 75), (258, 90)], color=P.acc, marker="ar-acc"))
    s.append(_arrow(P, [(462, 75), (496, 75)], color=P.acc, marker="ar-acc"))
    # Both arrows used to land on 워커 1: one at x=585 and one at x=700, which
    # is that box's right edge (it spans 516-700). 워커 2 (716-900) had nothing
    # pointing at it at all, so the picture said "one box gets the work twice"
    # instead of "the work splits". Centred on each box: 608 and 808.
    s.append(_arrow(P, [(608, 106), (608, 152)], color=P.good, marker="ar-good"))
    s.append(_arrow(P, [(640, 106), (640, 118), (808, 118), (808, 152)],
                    color=P.good, marker="ar-good"))
    s.append(_arrow(P, [(708, 206), (708, 220)], color=P.firm))

    s.append(_box(P, 24, 216, 200, 62, "결과", "파일 + 워커별 요약",
                  stroke=P.good, title_fill=P.good))
    s.append(_arrow(P, [(500, 300), (124, 300), (124, 282)], "정산 · 보고",
                    color=P.good, marker="ar-good", lx=300, ly=294))

    s.append(_note(P, 24, 330, "레시피는 JSON 파일 하나다. 새로운 종류의 일이 생기면 "
                   "레시피만 추가하고 엔진은 건드리지 않는다.", size=11))
    s.append(_note(P, 24, 352, "파일이 겹치는 두 작업은 같은 wave 에 넣지 않는다 — "
                   "워크트리로 격리하는 대신 순서로 푼다.", size=10.5, weight=400, fill=P.ink3))
    s.append(_note(P, 24, 372, "느린 것과 실패한 것은 다르다. 15~60분 조용해도 죽이지 않는다.",
                   size=10.5, weight=400, fill=P.ink3))
    s.append("</svg>")
    return "".join(s)


# ===========================================================================
# 7. For a teammate: what you get, and what you need
# ===========================================================================
def what_you_get(P: Palette | None = None) -> str:
    """Six things, what each one asks you for, and what comes back.

    Written for someone deciding whether to install, not for someone
    maintaining it. Two things have to be obvious at a glance: most of these
    need nothing extra installed, and none of them start by guessing -- the
    middle column is what it asks *you*, because that is the part a teammate
    is actually agreeing to spend time on.
    """
    P = P or Palette.css()
    rows = [
        ("\"R&R 문서 만들어줘\"",   "어떤 문서 · 명단 어디 · 뭘 승인받나",
         "HTML + Excel, 늘 같은 양식", False),
        ("\"PRD 필요해\"",          "뭘 만드나 · 성공 기준 · 참고할 것",
         "화면 목록부터 레이아웃까지", False),
        ("\"이 엑셀 분석해줘\"",     "뭘 알고 싶나 · 파일 어디 · 행 1개 단위",
         "리포트 + 숫자마다 출처", False),
        ("\"밤새 확인해줘\"",        "뭘 볼까 · 뭐가 정상인가 · 몇 번",
         "이상만 골라서 재현 절차까지", False),
        ("\"스크립트 만들어줘\"",     "뭘 만드나 · 뭘로 다 된 걸 아나",
         "코드 + 검증 · 세팅 매뉴얼", False),
        ("\"Snowflake 에서 뽑아줘\"", "질문 · 행 1개 단위 · 기간과 범위",
         "리포트 + 역추적 가능한 숫자", True),
    ]
    # +12 over the old 150: the closing line sits at H-8, which left its
    # descenders 5px from the edge. A diagram sits directly above the next
    # paragraph on the page, so that reads as the two touching.
    W, H = 980, 162 + len(rows) * 58
    s = [_open(W, H, "som: what a teammate gets", P)]

    s.append(_note(P, 24, 40, "한 문장 말하면, 필요한 것만 묻고, 나머지는 알아서 합니다",
                   size=17, fill=P.ink))
    s.append(_note(P, 24, 62, "작업을 쪼개서 여러 AI 가 동시에 일하고, 끝나면 정리해서 줍니다.",
                   size=11.5, weight=400, fill=P.ink2))

    hy = 92
    for x, t in ((24, "이렇게 말하면"), (296, "이런 걸 물어봅니다"), (626, "이런 게 나옵니다")):
        s.append(_note(P, x, hy, t, size=10.5, fill=P.ink3))
    s.append(f'<line x1="24" y1="{hy + 8}" x2="{W - 24}" y2="{hy + 8}" stroke="{P.rule}"/>')

    y0 = hy + 22
    for i, (say, ask, out, needs_sf) in enumerate(rows):
        y = y0 + i * 58
        col = P.warn if needs_sf else P.good
        s.append(_box(P, 24, y, 254, 44, say, None, r=8,
                      stroke=col if needs_sf else P.acc, title_fill=P.ink, tw=600))
        s.append(_arrow(P, [(282, y + 22), (306, y + 22)], color=P.firm, marker="ar"))
        s.append(f'<rect x="310" y="{y}" width="290" height="44" rx="8" '
                 f'fill="{P.sunk}" fill-opacity="0.5" stroke="{P.rule}" '
                 f'stroke-dasharray="4 3"/>')
        s.append(f'<text x="455" y="{y + 27}" text-anchor="middle" {FONT} '
                 f'font-size="11" fill="{P.ink2}">{esc(ask)}</text>')
        s.append(_arrow(P, [(604, y + 22), (628, y + 22)], color=P.firm, marker="ar"))
        s.append(_box(P, 632, y, 244, 44, out, None, r=8, stroke=P.rule,
                      title_fill=P.ink2, tw=400))
        s.append(f'<text x="890" y="{y + 27}" {FONT} font-size="10.5" '
                 f'font-weight="{650 if needs_sf else 400}" fill="{col}">'
                 f'{esc("Snowflake 필요" if needs_sf else "바로 됨")}</text>')

    by = y0 + len(rows) * 58 + 4
    s.append(f'<rect x="24" y="{by}" width="{W - 48}" height="38" rx="10" '
             f'fill="{P.good}" fill-opacity="0.10" stroke="{P.good}"/>')
    s.append(_note(P, 40, by + 24, "여섯 가지 중 다섯은 추가 설치 없이 바로 됩니다. "
                   "Snowflake 를 안 쓰셔도 아무 문제 없습니다.",
                   size=12, fill=P.good))
    s.append("</svg>")
    return "".join(s)


# ===========================================================================
# 7b. The interview, and what happens when an answer is missing
# ===========================================================================
def interview(P: Palette | None = None) -> str:
    """Why it asks, and what it does instead of guessing.

    The failure this prevents is specific and worth naming on the picture: a
    worker handed an empty slot does not stop, it invents a plausible filler.
    Invented names in an R&R document are worse than no document.
    """
    P = P or Palette.css()
    W, H = 940, 330
    s = [_open(W, H, "som: it asks before it starts", P)]

    s.append(_note(P, 24, 40, "모르는 건 지어내지 않고 물어봅니다", size=17, fill=P.ink))
    s.append(_note(P, 24, 62, "질문은 한 번에 모아서 옵니다. 하나씩 되묻지 않습니다.",
                   size=11.5, weight=400, fill=P.ink2))

    s.append(_box(P, 24, 92, 176, 58, "\"R&R 문서 만들어줘\"", "한 문장",
                  stroke=P.acc, title_fill=P.ink))
    s.append(_arrow(P, [(204, 121), (232, 121)], color=P.firm, marker="ar"))

    s.append(f'<rect x="236" y="86" width="330" height="152" rx="10" fill="{P.sunk}" '
             f'fill-opacity="0.5" stroke="{P.acc}" stroke-dasharray="5 4"/>')
    s.append(_note(P, 252, 106, "한 화면에 모아서 질문", size=11, fill=P.acc))
    qs = [
        ("어떤 문서인가요?", "R&R · KPI · 차터"),
        ("명단은 어디 있나요?", "없으면 이름을 지어냅니다"),
        ("무엇을 승인받나요?", "문서 맨 앞에 들어갑니다"),
        ("누가 읽나요?", "요약 깊이가 달라집니다"),
    ]
    for i, (q, why) in enumerate(qs):
        y = 130 + i * 27
        s.append(f'<text x="252" y="{y}" {FONT} font-size="11.5" font-weight="600" '
                 f'fill="{P.ink}">{esc(q)}</text>')
        s.append(f'<text x="392" y="{y}" {FONT} font-size="10.5" '
                 f'fill="{P.ink3}">{esc(why)}</text>')

    s.append(_arrow(P, [(570, 140), (606, 140)], color=P.good, marker="ar-good"))
    s.append(_box(P, 610, 116, 306, 48, "답을 받으면 → 바로 시작", None, r=8,
                  stroke=P.good, title_fill=P.good))
    s.append(_arrow(P, [(570, 196), (606, 196)], color=P.crit, marker="ar-crit"))
    s.append(_box(P, 610, 172, 306, 48, "답이 없으면 → 시작하지 않습니다", None, r=8,
                  stroke=P.crit, title_fill=P.crit))

    s.append(f'<rect x="24" y="258" width="892" height="48" rx="10" '
             f'fill="{P.crit}" fill-opacity="0.08" stroke="{P.crit}"/>')
    s.append(_note(P, 40, 278, "빈칸을 만난 AI 는 멈추지 않습니다 — 그럴듯한 것으로 채웁니다.",
                   size=12, fill=P.crit))
    s.append(_note(P, 40, 296, "R&R 문서에 없는 사람 이름이 들어가는 건 문서가 없는 것보다 나쁩니다. "
                   "그래서 코드가 막습니다.", size=11, weight=400, fill=P.ink2))
    s.append("</svg>")
    return "".join(s)


# ===========================================================================
# 7c. The ambiguity gate
# ===========================================================================
def ambiguity_gate(P: Palette | None = None) -> str:
    """The number, the threshold, and what happens on each side of it.

    Two things have to read at a glance: the gate is a measured number rather
    than someone's judgement, and the way past it when an answer genuinely does
    not exist is to mark the unknown as undecided -- not to loosen the number.
    """
    P = P or Palette.css()
    W, H = 940, 400
    s = [_open(W, H, "som: the 5% ambiguity gate", P)]

    s.append(_note(P, 24, 40, "모호도 5% 미만일 때만 시작합니다", size=17, fill=P.ink))
    s.append(_note(P, 24, 62, "라운드마다 숫자를 보여드립니다. 언제 시작되는지 짐작하지 않으셔도 됩니다.",
                   size=11.5, weight=400, fill=P.ink2))

    # The bar: 100% down to the threshold.
    bx, by, bw, bh = 24, 96, 892, 30
    s.append(f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="6" '
             f'fill="{P.sunk}" fill-opacity="0.6" stroke="{P.rule}"/>')
    gate_x = bx + bw * 0.86
    s.append(f'<rect x="{bx}" y="{by}" width="{gate_x - bx}" height="{bh}" rx="6" '
             f'fill="{P.warn}" fill-opacity="0.16"/>')
    s.append(f'<rect x="{gate_x}" y="{by}" width="{bx + bw - gate_x}" height="{bh}" '
             f'rx="6" fill="{P.good}" fill-opacity="0.18"/>')
    s.append(f'<line x1="{gate_x}" y1="{by - 10}" x2="{gate_x}" y2="{by + bh + 10}" '
             f'stroke="{P.crit}" stroke-width="2"/>')
    s.append(f'<text x="{gate_x - 6}" y="{by - 16}" text-anchor="end" {FONT} '
             f'font-size="11" font-weight="650" fill="{P.crit}">문턱 5%</text>')
    s.append(f'<text x="{bx + 12}" y="{by + 20}" {FONT} font-size="11.5" '
             f'font-weight="650" fill="{P.warn}">100% ← 질문을 계속합니다</text>')
    s.append(f'<text x="{bx + bw - 12}" y="{by + 20}" text-anchor="end" {FONT} '
             f'font-size="11.5" font-weight="650" fill="{P.good}">시작</text>')

    # Rounds walking down the bar.
    marks = [("R0", 1.00, "구성 확인"), ("R1", 0.63, ""), ("R2", 0.37, ""),
             ("R3", 0.12, ""), ("R4", 0.02, "통과")]
    for tag, val, note in marks:
        # 100% sits at the left edge and the threshold at the gate line, so the
        # walk down the bar is drawn on the same scale the number is read on.
        x = (gate_x + (bx + bw - gate_x) * 0.45 if val <= 0.05
             else bx + (gate_x - bx) * (1 - val) / (1 - 0.05))
        col = P.good if val <= 0.05 else P.ink2
        s.append(f'<line x1="{x:.0f}" y1="{by + bh}" x2="{x:.0f}" y2="{by + bh + 16}" '
                 f'stroke="{col}"/>')
        s.append(f'<text x="{x:.0f}" y="{by + bh + 30}" text-anchor="middle" {FONT} '
                 f'font-size="10.5" font-weight="650" fill="{col}">{esc(tag)}</text>')
        s.append(f'<text x="{x:.0f}" y="{by + bh + 44}" text-anchor="middle" {FONT} '
                 f'font-size="10" fill="{P.ink3}">'
                 f'{esc(f"{val * 100:.0f}%" if val > 0.05 else "2%")}</text>')
        if note:
            s.append(f'<text x="{x:.0f}" y="{by + bh + 58}" text-anchor="middle" {FONT} '
                     f'font-size="10" fill="{col}">{esc(note)}</text>')

    # Four dimensions feeding the number.
    dy = 216
    s.append(_note(P, 24, dy, "숫자는 세 가지를 가중 평균한 값입니다 "
                   "(기존 코드를 고치는 작업이면 네 가지)", size=12, fill=P.ink))
    dims = [("목표", "0.40 / 0.35"), ("제약 · 경계", "0.30 / 0.25"),
            ("완료 기준", "0.30 / 0.25"), ("기존 시스템", "— / 0.15")]
    for i, (name, w) in enumerate(dims):
        x = 24 + i * 228
        last = i == 3
        s.append(_box(P, x, dy + 12, 214, 44, name,
                      f"가중 {w}" + (" · 수정 작업만" if last else ""),
                      stroke=P.rule if last else P.acc,
                      title_fill=P.ink3 if last else P.ink, r=8))
    s.append(_note(P, 24, dy + 70, "신규 작업 / 기존 수정. 각각 합이 1.00 입니다.",
                   size=10, weight=400, fill=P.ink3))

    # The two ways the number goes down, and the one that is not available.
    s.append(f'<rect x="24" y="{dy + 76}" width="440" height="102" rx="10" '
             f'fill="{P.good}" fill-opacity="0.10" stroke="{P.good}"/>')
    s.append(_note(P, 40, dy + 98, "숫자를 내리는 방법 두 가지", size=12, fill=P.good))
    s.append(_note(P, 40, dy + 118, "1. 질문에 답한다", size=11, weight=400, fill=P.ink2))
    s.append(_note(P, 40, dy + 136, "2. 정말 모르는 항목을 '미확정' 으로 확정한다",
                   size=11, weight=400, fill=P.ink2))
    # One line at 474px inside a 440px box, on the same baseline as the label
    # in the box beside it -- so it ran over its own border and collided with
    # its neighbour. Two shorter lines fit.
    s.append(_note(P, 40, dy + 152, "둘 다 정직한 답입니다. 2번은 사람이 확정해야",
                   size=10.5, weight=400, fill=P.ink3))
    s.append(_note(P, 40, dy + 166, "적용됩니다 — 글로 \"미확정\" 이라고만 쓰면 안 됩니다.",
                   size=10.5, weight=400, fill=P.ink3))

    s.append(f'<rect x="476" y="{dy + 76}" width="440" height="102" rx="10" '
             f'fill="{P.crit}" fill-opacity="0.08" stroke="{P.crit}"/>')
    s.append(_note(P, 492, dy + 98, "안 되는 방법", size=12, fill=P.crit))
    s.append(_note(P, 492, dy + 118, "문턱을 올린다 — 설정으로도 안 됩니다",
                   size=11, weight=400, fill=P.ink2))
    s.append(_note(P, 492, dy + 136, "모른다고 적으면서 점수를 올린다 — 코드가 자릅니다",
                   size=11, weight=400, fill=P.ink2))
    s.append(_note(P, 492, dy + 154, "\"그냥 시작해\" — 거부하고 2번을 권합니다",
                   size=10.5, weight=400, fill=P.ink3))

    s.append("</svg>")
    return "".join(s)


# ===========================================================================
# 7d. Learning loop
# ===========================================================================
def learning_loop(P: Palette | None = None) -> str:
    """What a finished run leaves behind, and what the next one reads.

    The gate in the middle is the point of the picture. A library that stores
    every remembered lesson costs tokens on every brief and tells you nothing,
    so most candidates have to be refused -- and the diagram should make
    refusal look like the normal path, not like an error.
    """
    P = P or Palette.css()
    W, H = 940, 430
    s = [_open(W, H, "som: learning from finished runs", P)]

    s.append(_note(P, 24, 40, "한 번 배운 것을 다음 런이 읽습니다", size=17, fill=P.ink))
    s.append(_note(P, 24, 62, "기억이 아니라 기록에서 뽑고, 근거를 확인하고, 결과로 채점합니다.",
                   size=11.5, weight=400, fill=P.ink2))

    s.append(_box(P, 24, 90, 190, 56, "끝난 런", "재시도 · 개입 · 측정값 변화",
                  stroke=P.acc, title_fill=P.ink))
    s.append(_arrow(P, [(218, 118), (250, 118)], color=P.firm, marker="ar"))
    s.append(_box(P, 254, 90, 190, 56, "후보 뽑기", "trigger · action · 근거",
                  stroke=P.rule, title_fill=P.ink2))
    s.append(_arrow(P, [(448, 118), (480, 118)], color=P.firm, marker="ar"))

    # The gate, with both exits.
    s.append(f'<rect x="484" y="82" width="200" height="72" rx="10" '
             f'fill="{P.crit}" fill-opacity="0.08" stroke="{P.crit}" stroke-width="1.6"/>')
    s.append(f'<text x="584" y="108" text-anchor="middle" {FONT} font-size="12.5" '
             f'font-weight="650" fill="{P.crit}">품질 게이트</text>')
    s.append(f'<text x="584" y="126" text-anchor="middle" {FONT} font-size="10.5" '
             f'fill="{P.ink2}">근거 확인 · 일반론 거부</text>')
    s.append(f'<text x="584" y="142" text-anchor="middle" {FONT} font-size="10.5" '
             f'fill="{P.ink2}">자격증명 거부 · 중복 병합</text>')

    s.append(_arrow(P, [(688, 118), (720, 118)], color=P.good, marker="ar-good"))
    s.append(_box(P, 724, 90, 192, 56, "패턴 라이브러리", "신뢰도 40 에서 시작",
                  stroke=P.good, title_fill=P.good))

    s.append(_arrow(P, [(584, 158), (584, 196)], color=P.crit, marker="ar-crit"))
    s.append(f'<rect x="410" y="200" width="348" height="34" rx="8" '
             f'fill="{P.sunk}" fill-opacity="0.5" stroke="{P.rule}" stroke-dasharray="4 3"/>')
    s.append(f'<text x="584" y="222" text-anchor="middle" {FONT} font-size="11" '
             f'fill="{P.ink3}">거부 — 후보 대부분은 여기로 옵니다</text>')

    # Reuse and grading.
    s.append(_arrow(P, [(820, 150), (820, 264)], color=P.good, marker="ar-good"))
    s.append(_box(P, 724, 268, 192, 56, "다음 런의 브리핑", "최대 3개만 주입",
                  stroke=P.acc, title_fill=P.ink))
    s.append(_arrow(P, [(724, 296), (470, 296)], color=P.firm, marker="ar"))
    s.append(_box(P, 254, 268, 212, 56, "태스크별 결과로 채점", "성공 +8 · 실패 −20",
                  stroke=P.rule, title_fill=P.ink2))
    s.append(_arrow(P, [(254, 296), (222, 296)], color=P.crit, marker="ar-crit"))
    s.append(_box(P, 24, 268, 194, 56, "신뢰도 15 미만", "내림 — 기록은 남김",
                  stroke=P.crit, title_fill=P.crit))

    s.append(f'<rect x="24" y="344" width="892" height="62" rx="10" '
             f'fill="{P.good}" fill-opacity="0.10" stroke="{P.good}"/>')
    s.append(_note(P, 40, 366, "\"쿼리는 작게 나누는 게 좋다\" 는 저장하지 않습니다.",
                   size=12, fill=P.good))
    s.append(_note(P, 40, 388, "언제 그런지 · 무엇을 하라는 것인지 · 어디서 알게 됐는지가 "
                   "없으면 격언이고, 격언 도서관은 빈 도서관보다 나쁩니다.",
                   size=11, weight=400, fill=P.ink2))
    s.append("</svg>")
    return "".join(s)


# ===========================================================================
# 8. Before and after, for the same piece of work
# ===========================================================================
def before_after(P: Palette | None = None) -> str:
    P = P or Palette.css()
    W, H = 940, 360
    s = [_open(W, H, "som: before and after", P)]

    s.append(f'<line x1="470" y1="30" x2="470" y2="330" stroke="{P.rule}" '
             f'stroke-dasharray="5 4"/>')
    s.append(_note(P, 24, 44, "지금", size=15, fill=P.ink3))
    s.append(_note(P, 494, 44, "som 을 쓰면", size=15, fill=P.acc))

    before = [
        "하나씩 순서대로 물어본다",
        "만드는 사람마다 양식이 다르다",
        "숫자가 맞는지는 눈으로 본다",
        "중간에 끊기면 처음부터",
        "쓰던 사람만 안다",
    ]
    after = [
        "상관없는 일은 동시에 돈다",
        "누가 만들어도 같은 양식",
        "합계·명단·출처를 자동 대조",
        "세션이 죽어도 이어받는다",
        "같은 명령이면 같은 결과",
    ]
    for i in range(5):
        y = 84 + i * 48
        s.append(f'<text x="40" y="{y}" {FONT} font-size="13" fill="{P.ink3}">·</text>')
        s.append(f'<text x="58" y="{y}" {FONT} font-size="13" fill="{P.ink2}">{esc(before[i])}</text>')
        s.append(_arrow(P, [(430, y - 5), (462, y - 5)], color=P.acc, marker="ar-acc"))
        s.append(f'<text x="510" y="{y}" {FONT} font-size="13" font-weight="600" '
                 f'fill="{P.ink}">{esc(after[i])}</text>')

    s.append(_note(P, 24, 332, "실측: 작업 3개 · 2번에 나눠 실행 · 73초 · 남은 찌꺼기 없음",
                   size=11, weight=400, fill=P.ink3))
    s.append("</svg>")
    return "".join(s)


# ===========================================================================
# 9. What it will not do
# ===========================================================================
def guardrails(P: Palette | None = None) -> str:
    P = P or Palette.css()
    W, H = 940, 342
    s = [_open(W, H, "som: what it will not do", P)]

    s.append(_note(P, 24, 42, "믿고 맡겨도 되는 이유", size=16, fill=P.ink))
    s.append(_note(P, 24, 64, "익숙해지면 물어보는 횟수는 줄어듭니다. "
                   "아래 다섯 가지는 그래도 안 합니다.",
                   size=11.5, weight=400, fill=P.ink2))

    # These are the actual hard floors, in plain words.
    #
    # Two rows here used to be neither. "데이터베이스에 쓰기 — 승인해도 안 됩니다"
    # stopped being true the day the team needed to create a table, and
    # "느리다고 중간에 죽이기" was never a floor at all: `worker.stop` is
    # automatic at L3. A picture that promises more than the engine does is
    # worse than no picture.
    items = [
        ("강제 푸시 · 브랜치 삭제", "되돌릴 수 없는 git 조작은 사람이"),
        ("어디에 올리거나 공유", "결과는 내 PC 파일로만. 공유는 사람이"),
        ("자기가 자기를 승인", "확인이 필요한 건 사람에게 옵니다"),
        ("내 설정 파일 건드리기", "Claude 설정은 손대지 않습니다"),
        ("말없이 새 작업공간 만들기", "워크트리 생성은 언제나 확인"),
    ]
    for i, (what, why) in enumerate(items):
        y = 96 + i * 38
        s.append(f'<circle cx="40" cy="{y - 4}" r="9" fill="{P.crit}" fill-opacity="0.14" '
                 f'stroke="{P.crit}" stroke-width="1.2"/>')
        s.append(f'<path d="M36,{y - 8} L44,{y} M44,{y - 8} L36,{y}" stroke="{P.crit}" '
                 f'stroke-width="1.6" stroke-linecap="round"/>')
        s.append(f'<text x="60" y="{y}" {FONT} font-size="13" font-weight="650" '
                 f'fill="{P.ink}">{esc(what)}</text>')
        s.append(f'<text x="300" y="{y}" {FONT} font-size="12" fill="{P.ink2}">{esc(why)}</text>')

    s.append(_note(P, 24, 292, "이건 설정이 아니라 구조입니다 — 켜고 끌 수 있는 옵션이 아닙니다.",
                   size=11, weight=400, fill=P.ink3))
    s.append(_note(P, 24, 314, "Snowflake 쓰기는 이 목록에 없습니다. 대신 무엇을 바꾸는지 "
                   "보여주고, 그 문장에만 붙는 승인을 사람이 넣어야 돕니다.",
                   size=11, weight=400, fill=P.ink3))
    s.append("</svg>")
    return "".join(s)


DIAGRAMS = {
    "what-you-get": what_you_get,
    "interview": interview,
    "ambiguity-gate": ambiguity_gate,
    "learning-loop": learning_loop,
    "before-after": before_after,
    "guardrails": guardrails,
    "how-it-works": how_it_works,
    "coordinator-loop": coordinator_loop,
    "somdoc-pipeline": somdoc_pipeline,
    "snowflake-guard": snowflake_guard,
    "graph-tests": graph_tests,
}
