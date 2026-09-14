/**
 * The interview record, and the gate that reads it.
 *
 * Kept next to run state under `.som/interview/` rather than inside
 * state.json, because an interview outlives the run it authorises: the same
 * answers should still open the gate after a crash, a compaction, or a day's
 * gap, and re-asking a person four questions they already answered is the
 * fastest way to make them stop using this.
 *
 * The record is also the audit trail for "why did it start?". Every round's
 * question, answer and score is in here, so the decision to begin is
 * reconstructible rather than remembered.
 */
import {
  closeSync, existsSync, fsyncSync, mkdirSync, openSync, readdirSync,
  readFileSync, renameSync, writeSync,
} from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";
import { createHash } from "node:crypto";

import { stateDir } from "./store.mjs";
import {
  asAmbiguity, ambiguityOf, gate, LABEL, nextTarget, ontologyDelta, overall,
  progressReport, resolveThreshold, ROUND_HARD_CAP, pct,
} from "../domain/clarity.mjs";

export const INTERVIEW_SCHEMA = 1;

function dir(project) {
  const d = join(stateDir(project), "interview");
  mkdirSync(d, { recursive: true });
  return d;
}

/**
 * The record's filename, and therefore what the gate is keyed on.
 *
 * The readable part is truncated, so it alone cannot identify a request: two
 * objectives differing only past the cut -- "...plan A which we will ship
 * first" versus "...plan B which is the opposite decision" -- collided onto one
 * slug, and B's run started on A's interview. Verified. Punctuation is
 * stripped too, which widens the class further.
 *
 * So the readable prefix is for humans reading the directory, and the digest of
 * the FULL objective is what actually distinguishes one record from another.
 */
const slugify = (s) => {
  // Normalised and trimmed: a trailing newline off $ARGUMENTS produced a
  // different digest, so plan() reported "인터뷰 기록이 없습니다" for an
  // interview that had just finished. NFC so 한국어 typed two ways is one key.
  const raw = String(s ?? "").normalize("NFC").trim();
  const readable = raw
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48)
    .replace(/-+$/g, "") || "interview";
  const digest = createHash("sha256").update(raw, "utf8").digest("hex").slice(0, 10);
  return `${readable}-${digest}`;
};

/** Acceptance is a human act recorded through acceptUnknown(), never input. */
function stripAcceptance(scores) {
  const out = {};
  for (const [dim, raw] of Object.entries(scores ?? {})) {
    if (!raw || typeof raw !== "object") continue;
    const { decided, acceptedUnknown, decidedAt, decidedNote, ...rest } = raw;
    void decided; void acceptedUnknown; void decidedAt; void decidedNote;
    out[dim] = rest;
  }
  return out;
}

function readJson(p, fallback = null) {
  if (!existsSync(p)) return fallback;
  try { return JSON.parse(readFileSync(p, "utf8")); } catch { return fallback; }
}

/** Settings, for the threshold. Absent files are normal, not an error. */
export function loadSettings(project = process.cwd()) {
  const home = process.env.CLAUDE_CONFIG_DIR || join(homedir(), ".claude");
  return {
    projectSettings: readJson(join(project, ".claude", "settings.json"), {}),
    userSettings: readJson(join(home, "settings.json"), {}),
  };
}

export function threshold(project = process.cwd()) {
  return resolveThreshold(loadSettings(project));
}

export function emptyInterview({ objective, recipeId, project, brownfield = false }) {
  const t = threshold(project);
  return {
    schema: INTERVIEW_SCHEMA,
    id: `iv-${Date.now().toString(36)}`,
    slug: slugify(objective),
    objective,
    recipe: recipeId ?? null,
    type: brownfield ? "brownfield" : "greenfield",
    threshold: t.value,
    thresholdSource: t.source,
    thresholdNote: t.note,
    startedAt: new Date().toISOString(),
    settledAt: null,
    topology: {
      status: "pending",          // pending | confirmed
      confirmedAt: null,
      components: [],             // { id, name, description, status, scores, evidence }
      deferrals: [],
      lastTargetId: null,
    },
    rounds: [],                   // { n, componentId, dimension, question, answer, scores, ambiguity }
    ontologySnapshots: [],
    challengeModesUsed: [],
    ambiguity: 1,
    verdict: "ask",
    specPath: null,
  };
}

export class Interviews {
  constructor(project = process.cwd()) {
    this.project = project;
    this.dir = dir(project);
  }

  path(slug) { return join(this.dir, `${slug}.json`); }

  /** tmp -> fsync -> rename, the same discipline as store.mjs. */
  save(iv) {
    // Clamp on the way out as well as on the way in, so a loose threshold is
    // never persisted at all. Reads are already guarded; this keeps the file
    // itself honest for anyone reading it without this module.
    const safe = this.normalise(iv);
    const p = this.path(safe.slug);
    const tmp = `${p}.tmp`;
    const fd = openSync(tmp, "w");
    try {
      writeSync(fd, JSON.stringify(safe, null, 2) + "\n");
      fsyncSync(fd);
    } finally {
      closeSync(fd);
    }
    renameSync(tmp, p);
    return safe;
  }

  /**
   * Re-clamp the threshold every time a record comes off disk.
   *
   * The ceiling used to be applied only where a record was created, which left
   * the stored number authoritative afterwards -- so a hand-edited file, or a
   * record written before the ceiling existed, could carry `threshold: 0.9`
   * and `clarityGate` would honour it and start work at 50% ambiguity.
   * Verified by writing exactly that file: the gate opened.
   *
   * A ceiling that only holds at creation time is not a ceiling. Every read
   * goes through here, so the clamp cannot be dodged by reaching for `load()`
   * instead of `findFor()`.
   */
  normalise(iv) {
    if (!iv || typeof iv !== "object") return iv;
    const ceiling = threshold(this.project).value;
    const stored = typeof iv.threshold === "number" && iv.threshold > 0
      ? iv.threshold : ceiling;
    if (stored <= ceiling) return { ...iv, threshold: stored };
    return {
      ...iv,
      threshold: ceiling,
      thresholdStored: stored,
      thresholdClamped: true,
      thresholdNote:
        `기록에 적힌 문턱 ${pct(stored)} 는 상한 ${pct(ceiling)} 보다 느슨해서 ` +
        `적용되지 않았습니다. 이 문턱은 더 조일 수만 있습니다.`,
    };
  }

  load(slug) { return this.normalise(readJson(this.path(slug))); }

  list() {
    if (!existsSync(this.dir)) return [];
    return readdirSync(this.dir)
      .filter((f) => f.endsWith(".json"))
      .map((f) => readJson(join(this.dir, f)))
      .filter(Boolean)
      .map((iv) => this.normalise(iv))
      .sort((a, b) => String(b.startedAt).localeCompare(String(a.startedAt)));
  }

  /** The most recent settled interview whose objective matches, if any. */
  findFor(objective) {
    const want = slugify(objective);
    return this.list().find((iv) => iv.slug === want) ?? null;
  }

  /**
   * Lock the topology after Round 0.
   *
   * There was no API for this -- `topology.status` had to be set by editing the
   * JSON, which meant the gate's own precondition was unreachable through the
   * module that owns it.
   */
  confirmTopology(iv, components) {
    const next = this.normalise(iv);
    const list = Array.isArray(components) ? components : [];
    if (!list.length) {
      // A confirmed topology with no components skipped the whole component
      // path: addRound scored the raw payload, overall() was never consulted,
      // and one perfect self-score opened the gate in a single round -- which
      // is exactly what Round 0 exists to prevent.
      throw new Error("구성이 비어 있습니다. Round 0 은 최소 1개를 확정해야 합니다.");
    }
    const ids = new Set();
    const built = list.map((c, i) => {
      const id = String(c.id ?? `c${i + 1}`);
      if (ids.has(id)) {
        // Duplicates were accepted, and then nextTarget returned the second
        // while addRound wrote to the first: the user answered perfectly
        // forever and the number never moved.
        throw new Error(`구성 id 가 중복입니다: ${id}`);
      }
      ids.add(id);
      return {
        id,
        name: String(c.name ?? c.id ?? `구성 ${i + 1}`),
        description: c.description ?? "",
        status: c.status === "deferred" ? "deferred" : "active",
        evidence: Array.isArray(c.evidence) ? c.evidence : [],
        // Strip the acceptance flags here too. Blocking them in addRound alone
        // left this door open one call earlier, and the strip there preserves
        // whatever it finds already on the record.
        scores: stripAcceptance(c.scores),
      };
    });
    if (!built.some((c) => c.status !== "deferred")) {
      throw new Error("모든 구성을 보류하면 확정할 것이 없습니다. 최소 1개는 활성이어야 합니다.");
    }
    next.topology = {
      ...next.topology,
      status: "confirmed",
      confirmedAt: new Date().toISOString(),
      components: built,
      deferrals: list
        .filter((c) => c.status === "deferred")
        .map((c) => ({ componentId: String(c.id), reason: c.reason ?? "사용자 확인",
                       confirmedAt: new Date().toISOString() })),
      lastTargetId: null,
    };
    return next;
  }

  /**
   * Put a component aside, with the reason on the record.
   *
   * Deferring is one of the two honest ways the number comes down, and it was
   * only reachable by hand-editing the file. `clarity.mjs` has always read the
   * flag; nothing could set it.
   */
  deferComponent(iv, componentId, reason) {
    const next = this.normalise(iv);
    const comp = next.topology.components.find((c) => c.id === componentId);
    if (!comp) throw new Error(`구성 ${componentId} 이 없습니다.`);
    comp.status = "deferred";
    next.topology.deferrals = [
      ...(next.topology.deferrals ?? []).filter((d) => d.componentId !== componentId),
      { componentId, reason: reason ?? "사용자 확인", confirmedAt: new Date().toISOString() },
    ];
    return next;
  }

  /**
   * Accept that something stays unknown, and stop holding the gate on it.
   *
   * The other honest way down. The docs told the user to answer 미확정 -- and
   * that string read as an open gap, so the word offered as the escape was the
   * word that closed the gate. Two capped dimensions floor the score at 6.6%
   * on greenfield, which no number of further rounds can bring under 5%: the
   * most honest user was permanently locked out.
   *
   * This is a human decision recorded as data, not a phrase a scorer can drift
   * into. The gap text stays on the record and in the spec's 미확정 section.
   */
  acceptUnknown(iv, componentId, dimension, note) {
    const next = this.normalise(iv);
    const comp = next.topology.components.find((c) => c.id === componentId);
    if (!comp) throw new Error(`구성 ${componentId} 이 없습니다.`);
    const cur = comp.scores?.[dimension];
    if (!cur) throw new Error(`${componentId} 의 ${dimension} 은 아직 채점되지 않았습니다.`);
    comp.scores = {
      ...comp.scores,
      [dimension]: {
        ...cur,
        decided: true,
        decidedAt: new Date().toISOString(),
        decidedNote: note ?? "사용자가 미확정으로 확정했습니다.",
      },
    };
    const o = overall(next.topology.components, { brownfield: next.type === "brownfield" });
    next.ambiguity = o.ambiguity;
    next.verdict = gate({
      ambiguity: next.ambiguity, threshold: next.threshold,
      round: (next.rounds ?? []).length,
    }).verdict;
    if (next.verdict === "start" && !next.settledAt) {
      next.settledAt = new Date().toISOString();
    }
    return next;
  }

  /**
   * Record one round and recompute everything derived from it.
   *
   * The caller supplies the question, the answer and the per-dimension scores.
   * Nothing else: the ambiguity, the verdict and the next target are computed
   * here so a round cannot be logged with a score that disagrees with the
   * number it produced.
   */
  addRound(iv, { componentId, dimension, question, answer, scores, entities = null,
                 challengeMode = null }) {
    // A caller may hand in a record it built itself rather than one from
    // load(). Clamp here too, or the verdict written into the file could say
    // "start" on the strength of a threshold that was never allowed.
    iv = this.normalise(iv);
    const brownfield = iv.type === "brownfield";
    const comps = Array.isArray(iv.topology?.components) ? iv.topology.components : [];
    const comp = comps.find((c) => c && c.id === componentId);
    if (!comp) {
      // Scoring without a component made the component path optional, and the
      // whole topology gate optional with it.
      throw new Error(
        `구성 ${JSON.stringify(componentId)} 이 없습니다. ` +
        `Round 0 (confirmTopology) 을 먼저 통과해야 합니다.`);
    }
    // Strip the acceptance flags out of anything a scorer hands in. They are
    // set only by acceptUnknown(), i.e. by a human deciding to proceed without
    // something. Left reachable from here, a scorer could mark its own gaps as
    // accepted and the cap would become voluntary -- exactly the drift the cap
    // exists to stop. Verified: decided:true on all three dimensions took
    // ambiguity to 0% and opened the gate in one round.
    const applied = stripAcceptance(scores);
    for (const [dim, val] of Object.entries(applied)) {
      // An acceptance already on the record survives a re-score of the same
      // dimension: the human decided, and a later round does not undecide it.
      const prior = comp.scores?.[dim];
      if (prior?.decided === true) {
        applied[dim] = { ...val, decided: true, decidedAt: prior.decidedAt,
                         decidedNote: prior.decidedNote };
      }
    }
    comp.scores = { ...(comp.scores ?? {}), ...applied };
    iv.topology.lastTargetId = componentId;

    const per = ambiguityOf(comp.scores ?? {}, { brownfield });

    const n = iv.rounds.length + 1;
    iv.rounds.push({
      n, componentId, dimension: dimension ?? null,
      question, answer,
      // The scores as applied, not as submitted: logging the raw payload left
      // the audit trail showing an acceptance that was stripped.
      scores: applied,
      ambiguity: per.ambiguity,
      at: new Date().toISOString(),
      challengeMode,
    });
    if (challengeMode && !iv.challengeModesUsed.includes(challengeMode)) {
      iv.challengeModesUsed.push(challengeMode);
    }
    if (entities) {
      iv.ontologySnapshots.push({ round: n, entities });
    }

    const o = overall(comps, { brownfield });
    iv.ambiguity = o.ambiguity;
    iv.verdict = gate({
      ambiguity: iv.ambiguity, threshold: iv.threshold, round: n,
    }).verdict;
    if (iv.verdict === "start" && !iv.settledAt) {
      iv.settledAt = new Date().toISOString();
    }
    return {
      iv, round: n, parts: per.parts, per: o.per,
      componentAmbiguity: per.ambiguity,
    };
  }
}

/**
 * Render what the user sees after a round.
 *
 * Takes the return value of addRound() so the skill cannot render a table
 * from numbers other than the ones that were just recorded.
 */
export function report(result) {
  const { iv, round, parts, per } = result;
  const brownfield = iv.type === "brownfield";
  const target = nextTarget(iv.topology.components, {
    brownfield, lastTargetId: iv.topology.lastTargetId,
  });
  const snaps = iv.ontologySnapshots;
  const ontology = snaps.length
    ? ontologyDelta(snaps.length > 1 ? snaps[snaps.length - 2].entities : [],
                    snaps[snaps.length - 1].entities)
    : null;
  // The stored number, not a recomputed one -- this renders what the record
  // says. `asAmbiguity` is why that is safe: a null or a string from a crash,
  // a partial write or a hand edit would otherwise print as 0% beside a
  // "시작할 수 있습니다" the real gate would never give. Unknown reads as 1.
  const shown = asAmbiguity(iv.ambiguity);
  const g = gate({ ambiguity: shown, threshold: iv.threshold, round });

  return progressReport({
    round,
    target: iv.topology.components.find(
      (c) => c.id === iv.topology.lastTargetId)?.name ?? null,
    parts,
    ambiguity: shown,
    componentAmbiguity: result.componentAmbiguity,
    threshold: iv.threshold,
    components: iv.topology.components,
    perComponent: per,
    ontology,
    rationale: target
      ? `${target.component.name} / ${LABEL[target.dimension]} — 여기가 가장 낮습니다 ` +
        `(${target.dimScore.toFixed(2)})`
      : "",
    verdict: g,
  });
}

/**
 * May a run start for this objective?
 *
 * Called by Conduct.plan(). Returns a problem string when it may not, so the
 * clarity gate reads the same as every other gate: a line in `problems`, not a
 * thrown exception or a warning someone can scroll past.
 */
export function clarityGate({ project, objective, recipeId }) {
  const t = threshold(project);
  const iv = new Interviews(project).findFor(objective);
  const refuse = (problem, extra = {}) =>
    ({ ok: false, interview: iv, threshold: t, problem, ...extra });

  if (!iv) {
    return {
      ok: false,
      threshold: t,
      problem:
        `INTERVIEW: 이 요청에 대한 인터뷰 기록이 없습니다. ` +
        `모호도가 ${pct(t.value)} 미만이어야 시작할 수 있습니다 ` +
        `(현재 100% — 아직 아무것도 묻지 않았습니다). ` +
        `interview 스킬로 인터뷰를 먼저 진행하세요.`,
    };
  }
  if (iv.schema !== INTERVIEW_SCHEMA) {
    // Declared and never checked, so a record in a future or older format
    // would have been misread rather than refused.
    return refuse(`INTERVIEW: 기록 형식이 다릅니다 (schema ${JSON.stringify(iv.schema)}, ` +
                  `이 버전은 ${INTERVIEW_SCHEMA}). 인터뷰를 다시 진행하세요.`);
  }
  if (iv.topology?.status !== "confirmed") {
    return refuse("INTERVIEW: 구성(토폴로지) 확인이 끝나지 않았습니다. " +
                  "Round 0 을 먼저 통과해야 합니다.");
  }

  const comps = Array.isArray(iv.topology.components)
    ? iv.topology.components.filter((c) => c && typeof c === "object")
    : [];
  if (!comps.length) {
    return refuse("INTERVIEW: 확정된 구성이 없습니다. 무엇에 대한 명확도인지 " +
                  "알 수 없으므로 시작할 수 없습니다.");
  }

  const rounds = Array.isArray(iv.rounds) ? iv.rounds : [];
  if (!rounds.length) {
    return refuse("INTERVIEW: 채점된 라운드가 없습니다. 최소 한 번은 물어봅니다.");
  }

  // An interview that does not know its recipe cannot authorise one. The check
  // used to require both sides truthy, so every record the documented flow
  // produced -- all of them with recipe: null -- authorised any recipe at all.
  if (!iv.recipe) {
    return refuse("INTERVIEW: 이 기록에 레시피가 적혀 있지 않습니다. " +
                  "어떤 종류의 작업을 위한 인터뷰였는지 알 수 없으므로 쓸 수 없습니다.");
  }
  if (recipeId && iv.recipe !== recipeId) {
    return refuse(`INTERVIEW: 이 인터뷰는 ${iv.recipe} 레시피로 확정된 것입니다 ` +
                  `(지금 실행하려는 것은 ${recipeId}). 레시피마다 물어보는 것이 ` +
                  `달라서 그대로 쓸 수 없습니다. ${recipeId} 로 인터뷰를 다시 하세요.`);
  }

  // Recomputed here, never read from the file. Trusting the cached field meant
  // `ambiguity: null` -- from a crash, a partial write, or a hand edit -- read
  // as 0% and opened the gate on a 99-byte record with no scores at all.
  const { ambiguity, per } = overall(comps, { brownfield: iv.type === "brownfield" });
  const cached = iv.ambiguity;
  const drifted = typeof cached !== "number" ||
    !Number.isFinite(cached) || Math.abs(cached - ambiguity) > 1e-9;

  const g = gate({ ambiguity, threshold: iv.threshold, round: rounds.length });
  if (g.verdict !== "start") {
    return refuse(
      `INTERVIEW: 모호도 ${pct(ambiguity)} — 문턱 ${pct(iv.threshold)} 미만이 ` +
      `아니라 시작할 수 없습니다. ` +
      (rounds.length >= ROUND_HARD_CAP
        ? `라운드 상한(${ROUND_HARD_CAP})에 도달했습니다. 스펙은 미완성으로 남았습니다.`
        : `남은 라운드를 계속하세요 (현재 ${rounds.length}회).`),
      { gate: g, ambiguity, perComponent: per, drifted });
  }
  return { ok: true, interview: iv, threshold: t, gate: g,
           ambiguity, perComponent: per, drifted };
}
