/**
 * Ambiguity scoring for the intake interview.
 *
 * Benchmarked against oh-my-claudecode's `deep-interview` skill (Socratic
 * questioning with weighted-dimension ambiguity gating). The dimension set,
 * the weights, the topology-before-depth gate and the per-round progress table
 * are taken from there because they are good and there is no reason to invent
 * a second vocabulary. Three things are deliberately different:
 *
 *   1. The threshold is 5% and settings may only make it stricter. There it is
 *      0.2 and freely configurable. Here it is a ceiling, in the same spirit as
 *      the hard floors in autonomy.mjs: a number that argues with you.
 *
 *   2. Hitting the round cap does NOT let the run start. There, round 20
 *      "proceeds with current clarity level". Here the verdict stays `refuse`,
 *      because a cap is evidence the request is not understood, not permission
 *      to guess. (Writing the unfinished spec is the interview skill's job, not
 *      this module's.)
 *
 *   3. The arithmetic lives in code, not in the scoring prompt. The model
 *      supplies per-dimension judgements as data; this module computes the
 *      score, catches self-contradiction, and returns the verdict. Same split
 *      as humanize_io: the model does the language, the code does the judging.
 */

/** Weights by project type. Sum to 1.0 in both cases. */
export const WEIGHTS = {
  greenfield: { goal: 0.40, constraints: 0.30, criteria: 0.30 },
  brownfield: { goal: 0.35, constraints: 0.25, criteria: 0.25, context: 0.15 },
};

export const LABEL = {
  goal: "목표 명확도",
  constraints: "제약 · 경계 명확도",
  criteria: "완료 기준 명확도",
  context: "기존 시스템 파악도",
};

/**
 * The ceiling, not a default.
 *
 * Work may start only below this. Settings can lower it; nothing raises it.
 */
export const THRESHOLD_CEILING = 0.05;

/** Rounds. Soft warning, then a hard stop that refuses rather than proceeds. */
export const ROUND_SOFT_WARN = 10;
export const ROUND_HARD_CAP = 20;

/** Below this many rounds, "그냥 시작해" does not open the gate either. */
export const MIN_ROUNDS = 1;

/**
 * Two components this close in ambiguity count as equally weak.
 *
 * The benchmark says "tied or similarly weak"; a strict tie almost never
 * happens with floats, so without a tolerance the rotation was dead code.
 */
export const ROTATE_TOLERANCE = 0.05;

/**
 * A score that is not a finite number in range is not a score.
 *
 * This used to be `Math.max(0, Math.min(1, Number(n)))`, and `Number()`
 * accepts more than it should: `"1"` became a full score, `Infinity` clamped
 * to one, and `NaN` propagated all the way into a stored ambiguity of NaN.
 * Two of those opened the 5% gate on a request nobody had actually scored.
 *
 * The rule three lines down -- "an ambiguity that is not a number is not
 * zero" -- was already right; it just was not applied here. A malformed score
 * is treated as unscored, which the weighting already counts as fully
 * ambiguous, so the gate closes rather than opens.
 */
const clamp01 = (n) =>
  typeof n === "number" && Number.isFinite(n) ? Math.max(0, Math.min(1, n)) : 0;

/**
 * An ambiguity that is not a number is not zero.
 *
 * `Number(null)`, `Number("")` and `Number(false)` are all 0, so a record with
 * `ambiguity: null` read as "perfectly clear" and opened the gate. The
 * dimension path already gets this right -- an unscored dimension counts as
 * unclear -- and the record path had the polarity inverted.
 */
export const asAmbiguity = (n) =>
  // Out of range is not clamped either: -5 is not "perfectly clear", it is a
  // value nobody computed, and the safe reading of an uncomputed number is 1.
  typeof n === "number" && Number.isFinite(n) && n >= 0 && n <= 1 ? n : 1;

/**
 * One decimal below 10%, whole numbers above.
 *
 * Near the threshold the rounding is the whole story: 5.4% and 4.6% land on
 * opposite sides of the gate and both print as "5%" at zero decimals, which
 * makes a refusal look arbitrary.
 */
export const pct = (n) => {
  if (typeof n !== "number" || !Number.isFinite(n)) return "알 수 없음";
  const v = clamp01(n) * 100;
  if (Math.abs(v - Math.round(v)) < 1e-9) return `${Math.round(v)}%`;
  if (v >= 10) return `${v.toFixed(0)}%`;
  // Two decimals below 10, then trimmed. One decimal printed 5.01% and 4.99%
  // identically as "5.0%" -- a refusal and a pass rendered the same.
  return `${Number(v.toFixed(2))}%`;
};

/**
 * Resolve the threshold, reporting where it came from.
 *
 * Precedence matches the benchmark (project overrides user overrides default),
 * with the ceiling applied last so no settings file can loosen the gate.
 */
export function resolveThreshold({ projectSettings, userSettings } = {}) {
  const read = (settings, where) => {
    const raw = settings?.som?.interview?.ambiguityThreshold;
    if (raw === undefined) return { value: undefined, dropped: null };
    const n = typeof raw === "number" ? raw
      : typeof raw === "string" && raw.trim() !== "" ? Number(raw)
      : NaN;
    if (Number.isFinite(n) && n > 0 && n <= 1) return { value: n, dropped: null };
    return { value: undefined, dropped: `${where} 의 ${JSON.stringify(raw)}` };
  };
  const p = read(projectSettings, "./.claude/settings.json");
  const u = read(userSettings, "~/.claude/settings.json");

  // The strictest of the three wins. Precedence used to be project ?? user,
  // which let a project file at 0.05 discard a user setting of 0.001 -- i.e.
  // raise the bar, which the framing explicitly promises cannot happen.
  const candidates = [
    { value: THRESHOLD_CEILING, source: "기본값" },
    ...(u.value !== undefined ? [{ value: u.value, source: "~/.claude/settings.json" }] : []),
    ...(p.value !== undefined ? [{ value: p.value, source: "./.claude/settings.json" }] : []),
  ];
  const chosen = candidates.reduce((a, b) => (b.value < a.value ? b : a));

  const loosened = [p, u]
    .map((x) => x.value)
    .filter((v) => v !== undefined && v > THRESHOLD_CEILING);
  const notes = [];
  if (loosened.length) {
    notes.push(`설정값 ${loosened.map((v) => pct(v)).join(" · ")} 는 상한 ` +
               `${pct(THRESHOLD_CEILING)} 보다 느슨해서 적용되지 않았습니다. ` +
               `이 문턱은 더 조일 수만 있습니다.`);
  }
  const dropped = [p.dropped, u.dropped].filter(Boolean);
  if (dropped.length) {
    // Silently ignoring a malformed value is how a user who wrote "0.01"
    // meaning stricter ends up on the looser default without being told.
    notes.push(`설정값을 읽을 수 없어 무시했습니다: ${dropped.join(" · ")}. ` +
               `0 초과 1 이하의 숫자여야 합니다.`);
  }
  return {
    value: chosen.value,
    source: chosen.source,
    requested: p.value ?? u.value ?? chosen.value,
    note: notes.length ? notes.join(" ") : null,
  };
}

/**
 * One round's scores → ambiguity.
 *
 * `scores` is { dimension: { score, justification, gap } }. A dimension the
 * project type does not use is ignored; a missing one counts as 0, because an
 * unscored dimension is not a clear one.
 */
export function ambiguityOf(scores, { brownfield = false } = {}) {
  const w = brownfield ? WEIGHTS.brownfield : WEIGHTS.greenfield;
  const parts = [];
  let clarity = 0;
  for (const [dim, weight] of Object.entries(w)) {
    const raw = scores?.[dim];
    const effective = effectiveScore(raw);
    clarity += effective.score * weight;
    parts.push({
      dim, label: LABEL[dim], weight,
      score: effective.score,
      reported: effective.reported,
      capped: effective.capped,
      decided: effective.decided,
      gap: effective.gap,
      justification: raw?.justification ?? "",
    });
  }
  return { ambiguity: clamp01(1 - clarity), clarity, parts };
}

/**
 * A dimension cannot be 0.95 clear and still have an open gap.
 *
 * This is the anti-inflation guard, and it is the whole reason the arithmetic
 * is not left in the prompt. Under pressure to reach a 5% threshold, a scorer
 * will drift upward while still writing down what it does not know. When both
 * appear, the written gap wins and the score is capped just under the "clear"
 * band -- which keeps the loop running instead of declaring victory.
 */
export const GAP_CAP = 0.89;

/**
 * A gap the human has decided to leave open.
 *
 * Without this the cap locks out the most honest user. The arithmetic: a capped
 * dimension leaves weight x 0.11 of ambiguity, so on greenfield two capped
 * dimensions floor at 0.60 x 0.11 = 6.6% and no number of further rounds can
 * reach 5%. Verified by running the loop to round 25. Worse, the docs told the
 * user to answer "미확정" -- and that string reads as an open gap, so the very
 * word the interview offers as an escape was the word that closed the gate.
 *
 * The fix is not to whitelist that word. "I have not worked it out" and "we
 * have decided to proceed without it" are different states and only the second
 * should release the gate. So the second is an explicit flag a human sets, not
 * a phrase a scorer can drift into: `decided: true`. The gap text stays on the
 * record and in the spec's 미확정 section -- it is still unknown, it is just no
 * longer undecided.
 */
function isDecided(raw) {
  return raw?.decided === true || raw?.acceptedUnknown === true;
}

function effectiveScore(raw) {
  const reported = clamp01(raw?.score ?? 0);
  const gap = typeof raw?.gap === "string" ? raw.gap.trim() : "";
  const openGap = gap !== "" && !/^(없음|clear|none|n\/a|-)$/i.test(gap);
  if (openGap && isDecided(raw)) {
    // Recorded, shown in the table, but not held against the gate.
    return { score: reported, reported, capped: false, gap, decided: true };
  }
  if (openGap && reported > GAP_CAP) {
    return { score: GAP_CAP, reported, capped: true, gap, decided: false };
  }
  return {
    score: reported, reported, capped: false,
    gap: openGap ? gap : "", decided: false,
  };
}

/**
 * Which component and dimension the next question should attack.
 *
 * Rotation matters: with several weak components, always asking about the last
 * one produces one beautifully specified component and three vague siblings.
 * That failure is why the topology gate exists at all, so the tiebreak here
 * prefers a component that was not the previous target.
 */
export function nextTarget(components, { brownfield = false, lastTargetId = null } = {}) {
  const active = (components ?? []).filter((c) => c.status !== "deferred");
  if (!active.length) return null;

  const w = brownfield ? WEIGHTS.brownfield : WEIGHTS.greenfield;
  const ranked = active.map((c) => {
    const { ambiguity } = ambiguityOf(c.scores ?? {}, { brownfield });
    let worst = null;
    for (const dim of Object.keys(w)) {
      const s = effectiveScore(c.scores?.[dim]).score;
      if (!worst || s < worst.score) worst = { dim, score: s };
    }
    return { component: c, ambiguity, dimension: worst.dim, dimScore: worst.score };
  });

  // Rotation has to trigger on "similarly weak", not on an exact float tie.
  // With A at 36.0% and B at 34.5% the strict comparison kept returning A
  // round after round -- which is precisely the depth-first overfitting the
  // topology gate exists to prevent. Verified before this tolerance existed.
  ranked.sort((a, b) => {
    const gap = b.ambiguity - a.ambiguity;
    if (Math.abs(gap) > ROTATE_TOLERANCE) return gap;
    const aJust = a.component.id === lastTargetId ? 1 : 0;
    const bJust = b.component.id === lastTargetId ? 1 : 0;
    return (aJust - bJust) ||
      (gap !== 0 ? gap : String(a.component.id).localeCompare(String(b.component.id)));
  });
  return ranked[0];
}

/** Overall ambiguity across active components: the weakest one, not the mean. */
export function overall(components, { brownfield = false } = {}) {
  const active = (components ?? []).filter((c) => c.status !== "deferred");
  if (!active.length) return { ambiguity: 1, per: [] };
  const per = active.map((c) => ({
    id: c.id, name: c.name,
    ...ambiguityOf(c.scores ?? {}, { brownfield }),
  }));
  // Coverage-weighted would let a clear component hide a vague sibling. The
  // run is only as understood as its least understood part.
  return { ambiguity: Math.max(...per.map((p) => p.ambiguity)), per };
}

/**
 * Ontology stability.
 *
 * Entities that keep being renamed mean the scope is still moving even when
 * dimension scores look fine, so this is reported next to them. A rename is
 * convergence, not churn -- same type plus over half the fields in common is
 * treated as the same concept under a better name.
 */
export function ontologyDelta(prev, curr) {
  const before = prev ?? [];
  const after = curr ?? [];
  if (!after.length) {
    return { ratio: null, stable: [], changed: [], added: [], removed: [],
             reasoning: "엔티티가 없어 안정성을 계산하지 않았습니다." };
  }
  if (!before.length) {
    return { ratio: null, stable: [], changed: [],
             added: after.map((e) => e.name), removed: [],
             reasoning: "첫 라운드라 비교 대상이 없습니다." };
  }

  const norm = (s) => String(s ?? "").trim().toLowerCase();
  const fields = (e) => new Set((e.fields ?? []).map(norm));
  const overlap = (a, b) => {
    const fa = fields(a), fb = fields(b);
    if (!fa.size && !fb.size) return 0;
    let hit = 0;
    for (const f of fa) if (fb.has(f)) hit += 1;
    return hit / Math.max(fa.size, fb.size);
  };

  const unmatchedPrev = [...before];
  const stable = [], changed = [], added = [];
  const why = [];

  for (const e of after) {
    const byName = unmatchedPrev.findIndex((p) => norm(p.name) === norm(e.name));
    if (byName >= 0) {
      stable.push(e.name);
      why.push(`${e.name}: 이름 일치`);
      unmatchedPrev.splice(byName, 1);
      continue;
    }
    let best = -1, bestScore = 0;
    unmatchedPrev.forEach((p, i) => {
      if (norm(p.type) !== norm(e.type)) return;
      const o = overlap(p, e);
      if (o > 0.5 && o > bestScore) { best = i; bestScore = o; }
    });
    if (best >= 0) {
      changed.push(`${unmatchedPrev[best].name} → ${e.name}`);
      why.push(`${e.name}: ${unmatchedPrev[best].name} 의 개칭 (필드 ${Math.round(bestScore * 100)}% 일치)`);
      unmatchedPrev.splice(best, 1);
    } else {
      added.push(e.name);
      why.push(`${e.name}: 신규`);
    }
  }
  const removed = unmatchedPrev.map((p) => p.name);
  for (const r of removed) why.push(`${r}: 사라짐`);

  return {
    ratio: (stable.length + changed.length) / after.length,
    stable, changed, added, removed,
    reasoning: why.join(" · "),
  };
}

/**
 * May the run start?
 *
 * Three outcomes and the last two are not the same thing. `ask` means keep
 * going. `refuse` means stop asking and hand the spec back unfinished -- there
 * is nothing left for another round to fix.
 */
export function gate({ ambiguity, threshold, round, userWantsToStop = false }) {
  const a = clamp01(ambiguity);
  if (round >= MIN_ROUNDS && a < threshold) {
    return {
      verdict: "start",
      line: `모호도 ${pct(a)} < 문턱 ${pct(threshold)} — 시작할 수 있습니다.`,
    };
  }
  if (round >= ROUND_HARD_CAP) {
    return {
      verdict: "refuse",
      line: `라운드 ${ROUND_HARD_CAP}회에 도달했는데 모호도가 ${pct(a)} 입니다 ` +
            `(문턱 ${pct(threshold)}). 시작하지 않습니다.`,
      why: "라운드 상한은 '이제 추측해도 된다'는 뜻이 아니라 요청이 아직 " +
           "이해되지 않았다는 증거입니다. 스펙을 미완성으로 남기고 넘깁니다.",
    };
  }
  if (userWantsToStop) {
    return {
      verdict: "refuse",
      line: `모호도가 ${pct(a)} 라서 아직 시작할 수 없습니다 (문턱 ${pct(threshold)}).`,
      why: "여기서 멈추면 인터뷰 기록과 미완성 스펙만 남깁니다. 파일은 만들지 " +
           "않습니다. 남은 질문에 답하시거나, 모르는 항목을 '미확정'으로 " +
           "확정해 주시면 그 상태로 진행할 수 있습니다.",
    };
  }
  const remaining = a - threshold;
  return {
    verdict: "ask",
    line: `모호도 ${pct(a)} · 문턱 ${pct(threshold)} — ` +
      (a < threshold
        // Below the threshold but still asking: the only reason is that no
        // round has happened yet. Saying "동률" here was simply wrong.
        ? `라운드가 아직 ${MIN_ROUNDS}회에 못 미칩니다. 최소 한 번은 물어봅니다.`
        : remaining * 100 < 0.005
          ? `문턱 **미만**이어야 합니다. 동률은 통과가 아닙니다.`
          : `${pct(remaining)} 더 줄여야 합니다.`),
    warn: round >= ROUND_SOFT_WARN
      ? `라운드 ${round}회입니다. 질문이 겉돌면 남은 항목을 '미확정'으로 확정하는 편이 낫습니다.`
      : null,
  };
}

/**
 * The per-round report, rendered here so every round looks the same.
 *
 * The benchmark shows this table after each answer and it is the feature's
 * best idea: the person answering can see which question actually moved the
 * number, which is what makes the next answer better.
 */
export function progressReport({
  round, target, parts, ambiguity, threshold, components = [],
  componentAmbiguity = null, perComponent = null, ontology = null,
  rationale = "", verdict = null,
}) {
  const L = [];
  L.push(`라운드 ${round} 완료 · **전체 모호도 ${pct(ambiguity)}** / 문턱 ${pct(threshold)}`);
  L.push("");
  L.push(target
    ? `이번 라운드 대상: **${target}**`
    : "이번 라운드 채점");
  L.push("");
  L.push("| 차원 | 점수 | 가중 | 기여 | 남은 것 |");
  L.push("|---|---|---|---|---|");
  for (const p of parts) {
    const score = p.capped
      ? `${p.score.toFixed(2)} (보고 ${p.reported.toFixed(2)})`
      : p.score.toFixed(2);
    L.push(`| ${p.label} | ${score} | ${p.weight.toFixed(2)} | ` +
           `${(p.score * p.weight).toFixed(3)} | ${p.gap || "없음"} |`);
  }
  // This row belongs to the component the table is about. Printing the overall
  // number here made the column read as though it summed to something else --
  // 0.380 + 0.267 + 0.135 under a row saying 100%.
  L.push(`| **이 구성의 모호도** | | | **${pct(componentAmbiguity ?? ambiguity)}** | |`);

  if (parts.some((p) => p.capped)) {
    L.push("");
    L.push(`> 남은 것이 적혀 있는 차원은 ${GAP_CAP} 로 제한했습니다. ` +
           "모른다고 쓰면서 명확하다고 점수를 줄 수는 없습니다.");
  }

  const active = components.filter((c) => c.status !== "deferred");
  const deferred = components.filter((c) => c.status === "deferred");
  if (components.length) {
    L.push("");
    L.push(`**구성** 활성 ${active.length}개 · 보류 ${deferred.length}개 — ` +
           "전체 모호도는 이 중 **가장 높은** 값입니다 (평균이 아닙니다)");
    for (const c of active) {
      const a = perComponent?.find((p) => p.id === c.id)?.ambiguity;
      const scored = Object.keys(c.scores ?? {}).length > 0;
      L.push(`  - ${c.name}: ` +
             (scored ? pct(a ?? 1) : "아직 채점 안 함 (100%)"));
    }
    for (const c of deferred) L.push(`  - ${c.name}: 보류`);
  }
  if (ontology && ontology.ratio !== null) {
    L.push(`**엔티티**: ${ontology.stable.length + ontology.changed.length + ontology.added.length}개 · ` +
           `안정 ${(ontology.ratio * 100).toFixed(0)}% · ` +
           `유지 ${ontology.stable.length} · 개칭 ${ontology.changed.length} · ` +
           `신규 ${ontology.added.length} · 사라짐 ${ontology.removed.length}`);
  }
  // No "next question" line once the gate is open -- it reads like the loop is
  // still running when the answer is that it is finished.
  if (rationale && verdict?.verdict !== "start") {
    L.push("");
    L.push(`**다음 질문**: ${rationale}`);
  }
  if (verdict) {
    L.push("");
    L.push(verdict.line);
    if (verdict.warn) L.push(verdict.warn);
    if (verdict.why) L.push(verdict.why);
  }
  return L.join("\n");
}
