/**
 * How much the orchestrator may do without asking.
 *
 * The level widens as a record accumulates. The floors do not: they are frozen,
 * and no level, score, or human "yes" moves them. That asymmetry is the whole
 * design -- a gate is a question the engine is permitted to ask, a floor is
 * something the human must go and do themselves, elsewhere.
 *
 * Stored per operator under the plugin's data directory rather than per
 * project: what has been earned is a property of the person and this engine,
 * not of a folder.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";

export const LEVELS = ["L0", "L1", "L2", "L3", "L4"];
const THRESHOLD = { L0: 0, L1: 20, L2: 40, L3: 65, L4: 85 };
const COOLDOWN_MS = 30 * 60_000;

/**
 * `never` means no level is ever enough. `gate` means every level asks.
 */
export const ACTIONS = {
  "run.create":            "L1",
  "task.create":           "L1",
  "worker.start":          "L2",
  "worker.reuse":          "L2",
  "worker.release":        "L1",
  "worker.stop":           "L3",
  "worker.abandon":        "L3",
  "answer.question":       "L2",   // when the answer is in the spec
  "answer.scope-change":   "L3",
  "write.in-scope":        "L1",
  "write.out-of-scope":    "gate", // always asks; L0 denies outright
  "inject.task":           "L2",
  "worktree.create":       "never",
  "gate.self-resolve":     "never",
  "orchestration.reset":   "never",
  "snowflake.write":       "gate",   // 모든 레벨에서 사람 확인
  "publish":               "never",
  "edit.user-config":      "never",
  "git.force-push":        "never",
};
// A comment saying "frozen" is not a freeze. Without this, any module that
// imports ACTIONS -- a recipe loader, a hook, an .mjs a worker wrote -- flips
// every floor to `auto` with seven assignments, and `summary()` then prints an
// empty floor list so the person reading it concludes there never were any.
Object.freeze(ACTIONS);

/** Frozen. Not a policy default -- a property of the system. */
export const HARD_FLOORS = [
  ["orchestration.reset", "orchestration reset. 진행 중인 조율 상태를 파괴합니다."],
  ["git.force-push", "force push · 브랜치 삭제 · dirty 트리 reset --hard."],
  ["publish", "artifact 발행 · 공유 폴더 자동 복사. 산출물은 로컬 파일이고 배포는 사람이 합니다."],
  ["edit.user-config", "~/.claude/settings.json · CLAUDE.md · 플러그인 설정 편집."],
  ["gate.self-resolve", "자기가 만든 게이트를 자기가 해제하는 것."],
  ["worktree.create", "새 워크트리 생성. 사람 확인이 항상 필요합니다."],
];
HARD_FLOORS.forEach(Object.freeze);
Object.freeze(HARD_FLOORS);

/**
 * Where each hard floor is actually enforced.
 *
 * The floors were a list of strings with nothing behind them: of seven, only
 * `snowflake.write` had a real guard, and `decide()` was never asked about six
 * of them. The published table said 거부 at every level for rows the engine
 * never consulted, so a person reading it believed the engine would stop
 * something it had no opinion about.
 *
 * A floor is honest in exactly one of three ways, and each is checkable:
 *
 *   "decide"  -- a boundary calls `decide(action)` and refuses on non-auto.
 *                The test greps lib/ for that call.
 *   "guard"   -- a separate layer refuses it before it reaches the engine.
 *                The test asserts the named file exists and mentions it.
 *   "absent"  -- the engine contains no code that performs the action at all.
 *                The test greps for the operation and asserts zero hits. This
 *                is the strongest of the three: there is nothing to bypass.
 *
 * `absent` is not a promise to stay absent -- it is a tripwire. Adding the
 * capability makes the test fail, which is the moment to decide whether it
 * needs a gate.
 */
export const ENFORCED_AT = Object.freeze({
  // snowflake.write is no longer a floor. It was one on the grounds that "no
  // code path turns a human's yes into a write", and that stopped being true
  // the day this team needed to create a table. It is a gate at every level
  // instead: `somsql write` says in plain words what the statement does, to
  // which objects, and whether it can be undone, and runs only against an
  // approval bound to that statement's own hash.
  "worktree.create": Object.freeze({
    how: "decide",
    where: ["lib/conduct.mjs"],
    note: "startWave 가 new- 로 시작하는 배치를 띄우기 전에 묻는다.",
  }),
  "gate.self-resolve": Object.freeze({
    how: "absent",
    where: [],
    forbid: ["gate.resolve("],
    note: "gate.resolve 를 부르는 코드가 없다. 배선하는 커밋이 이 항목을 decide 로 바꿔야 한다.",
  }),
  "orchestration.reset": Object.freeze({
    how: "absent",
    where: [],
    forbid: ['"reset"', "orchestration reset"],
    note: "orca orchestration reset 을 호출하는 래퍼가 없다.",
  }),
  "git.force-push": Object.freeze({
    how: "absent",
    where: [],
    forbid: ["--force", "push --", "git push"],
    note: "엔진은 git 을 실행하지 않는다.",
  }),
  publish: Object.freeze({
    how: "absent",
    where: [],
    forbid: ["Artifact(", "artifact-create", "upload("],
    note: "발행 경로가 없다. 산출물은 로컬 파일이고 복사는 사람이 한다.",
  }),
  "edit.user-config": Object.freeze({
    how: "absent",
    where: [],
    forbid: ["writeFileSync(userSettings", "writeFileSync(join(home"],
    note: "settings.json 은 모호도 문턱을 읽기 위해 읽기만 한다. 쓰는 곳이 없다.",
  }),
});


/** Events that end a clean streak. Anything else is merely bookkeeping. */
const BAD_NEWS = new Set(["worker_failed", "gate_overridden", "floor_trip",
                          "human_interrupted", "rework"]);

const DELTA = {
  wave_clean: +4,
  five_clean_waves: +6,
  gate_agreed: +3,
  run_accepted: +8,
  verifier_clean: +2,
  worker_failed: -4,
  circuit_broken: -10,
  gate_overridden: -6,
  human_interrupted: -8,
  rework: -10,
  floor_trip: -25,
};

function dataDir() {
  const base = process.env.CLAUDE_PLUGIN_DATA ??
    join(homedir(), ".claude", "plugins", "data", "som-som-marketplace");
  mkdirSync(base, { recursive: true });
  return base;
}

function fresh() {
  return {
    version: 1, score: 0, level: "L0",
    levelSince: new Date().toISOString(), cooldownUntil: null,
    runsSinceUpgrade: 0,
    stats: { runs: 0, waves: 0, dispatches: 0, accepted: 0, failed: 0,
             gatesRaised: 0, gatesOverridden: 0, floorTrips: 0, cleanWaves: 0 },
    history: [],
  };
}

export class Autonomy {
  constructor(dir = dataDir()) {
    // A caller-supplied directory may not exist yet; save() must not be the
    // place that discovers it.
    mkdirSync(dir, { recursive: true });
    this.path = join(dir, "autonomy.json");
    this.state = existsSync(this.path)
      ? { ...fresh(), ...JSON.parse(readFileSync(this.path, "utf8")) }
      : fresh();
  }

  save() {
    writeFileSync(this.path, JSON.stringify(this.state, null, 2) + "\n", "utf8");
    return this.state;
  }

  get level() { return this.state.level; }
  get score() { return this.state.score; }

  levelIndex(l = this.state.level) { return LEVELS.indexOf(l); }

  /**
   * May the engine do this without asking?
   *
   * Three outcomes, and the difference between the last two matters:
   *   auto  — go ahead
   *   gate  — ask the human; a yes proceeds
   *   deny  — a floor. There is nothing to ask. Say so and stop.
   */
  decide(action) {
    // Own property only. `ACTIONS["constructor"]` walked the prototype chain
    // and returned a function -- truthy, so the fail-closed branch below was
    // skipped, and LEVELS.indexOf(function) is -1, which every level clears.
    // decide("__proto__") returned `auto`.
    const need = Object.prototype.hasOwnProperty.call(ACTIONS, action)
      ? ACTIONS[action] : undefined;
    if (need === "never") {
      const floor = HARD_FLOORS.find(([a]) => a === action);
      return {
        verdict: "deny",
        reason: floor?.[1] ?? "이 동작은 어떤 자율 레벨로도 허용되지 않습니다.",
        remediation: "사람이 직접, 이 엔진 밖에서 수행해야 합니다.",
      };
    }
    if (!need) return { verdict: "gate", reason: `알 수 없는 동작 ${action}` };
    if (action === "write.out-of-scope" && this.state.level === "L0") {
      return { verdict: "deny", reason: "L0 에서는 선언 범위 밖 쓰기를 거부합니다." };
    }
    if (need === "gate") {
      // Always asks, at every level. It used to be listed as "L1" -- the same
      // requirement as an in-scope write -- so it was auto from L1 upward and
      // the distinction the whole row is about existed only at L0. The
      // published table said 확인 at L1-L4; the code said auto.
      return {
        verdict: "gate",
        reason: `${action} 는 어떤 레벨에서도 사람 확인이 필요합니다.`,
      };
    }
    return this.levelIndex() >= LEVELS.indexOf(need)
      ? { verdict: "auto" }
      : { verdict: "gate", reason: `${action} 는 ${need} 이상이 필요합니다 (현재 ${this.state.level}).` };
  }

  /** Parallel workers allowed at this level. */
  maxWorkers() {
    // An unknown level made decide() close (gate) while this opened (2, above
    // L0's 1). One object cannot be fail-closed on permission and fail-open on
    // capacity: the number a person reads would disagree with the engine
    // exactly when the ledger is corrupt and they most need it to be right.
    return [1, 2, 3, 4, 4][this.levelIndex()] ?? 1;
  }

  record(event, detail = {}) {
    // decide() was hardened against inherited property names twelve lines up;
    // this bare lookup was not. `record("valueOf")` returned a function, passed
    // `?? 0`, and made score NaN -- which JSON.stringify writes as null, so the
    // ledger silently loses the history a person earned.
    const raw = Object.prototype.hasOwnProperty.call(DELTA, event) ? DELTA[event] : 0;
    const d = Number.isFinite(raw) ? raw : 0;
    const s = this.state;
    s.score = Math.max(0, Math.min(100, s.score + d));
    s.stats.runs += event === "run_accepted" ? 1 : 0;
    s.stats.waves += event === "wave_clean" ? 1 : 0;
    // Counting *consecutive* wave_clean events made this counter unreachable:
    // conduct records run_accepted immediately before wave_clean on every
    // successful run, so the counter reset to 0 every time and the +6 bonus at
    // five never fired. It now counts clean waves since the last bad news.
    if (event === "wave_clean") s.stats.cleanWaves += 1;
    else if (BAD_NEWS.has(event)) s.stats.cleanWaves = 0;
    s.stats.failed += event === "worker_failed" ? 1 : 0;
    s.stats.gatesOverridden += event === "gate_overridden" ? 1 : 0;

    if (event === "floor_trip") {
      // Not a demotion by score. An immediate reset, because whatever produced
      // it was not a near miss.
      s.stats.floorTrips += 1;
      s.level = "L0";
      s.levelSince = new Date().toISOString();
      s.runsSinceUpgrade = 0;
      s.history.push({ at: s.levelSince, to: "L0", trigger: "floor_trip",
                       score: s.score, detail });
      return this.save();
    }
    if (s.stats.cleanWaves >= 5) {
      s.score = Math.min(100, s.score + DELTA.five_clean_waves);
      s.stats.cleanWaves = 0;
    }
    if (event === "run_accepted") s.runsSinceUpgrade += 1;

    this.reconsider(detail);
    return this.save();
  }

  /**
   * Upgrades need a cooldown AND a completed run since the last one, so a
   * single lucky run cannot ratchet the level up inside itself. Downgrades
   * apply immediately: the reason to be cautious arrived already.
   */
  reconsider(detail = {}) {
    const s = this.state;
    const now = Date.now();
    const idx = this.levelIndex();
    const earned = LEVELS.filter((l) => s.score >= THRESHOLD[l]).pop() ?? "L0";
    const earnedIdx = LEVELS.indexOf(earned);

    if (earnedIdx > idx) {
      const cooled = !s.cooldownUntil || now >= Date.parse(s.cooldownUntil);
      if (!cooled || s.runsSinceUpgrade < 1) return;
      s.level = LEVELS[idx + 1];
      s.levelSince = new Date().toISOString();
      s.cooldownUntil = new Date(now + COOLDOWN_MS).toISOString();
      s.runsSinceUpgrade = 0;
      s.history.push({ at: s.levelSince, to: s.level, trigger: "score", score: s.score, detail });
    } else if (earnedIdx < idx) {
      s.level = LEVELS[idx - 1];
      s.levelSince = new Date().toISOString();
      s.cooldownUntil = new Date(now + COOLDOWN_MS).toISOString();
      s.history.push({ at: s.levelSince, to: s.level, trigger: "score-drop", score: s.score, detail });
    }
  }

  /** What a human sees when they ask where things stand. */
  summary() {
    const s = this.state;
    return [
      `자율 레벨 ${s.level} · 점수 ${s.score}/100 · 동시 워커 최대 ${this.maxWorkers()}`,
      `실행 ${s.stats.runs}회 · 실패 ${s.stats.failed} · 게이트 뒤집힘 ${s.stats.gatesOverridden}` +
        (s.stats.floorTrips ? ` · 하드플로어 위반 ${s.stats.floorTrips}` : ""),
      "",
      "어떤 레벨로도 허용되지 않는 것:",
      ...HARD_FLOORS.map(([, why]) => `  - ${why}`),
    ].join("\n");
  }
}
