/**
 * The pattern library: what it refuses, what it injects, what it retires.
 *
 * A learning feature is only worth having if it says no most of the time, so
 * most of these tests are candidate lessons that must be refused: proverbs,
 * unverifiable claims, credentials, and success stories.
 *
 *   node --test test/patterns.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { writeFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { scratch } from "./tmp.mjs";

import {
  START_CONFIDENCE, RETIRE_BELOW, MAX_INJECTED, MATCH_THRESHOLD, MIN_TRIGGER,
  LIMITS, applyOutcome, matchPatterns, newPattern, patternHash, renderForBrief,
  scorePattern, validatePattern,
} from "../lib/domain/patterns.mjs";
import { PatternLibrary } from "../lib/state/patterns.mjs";

/** A project with one real file, so `file` evidence can resolve. */
function project() {
  const d = scratch("som-pat-");
  mkdirSync(join(d, "lib"), { recursive: true });
  writeFileSync(join(d, "lib", "real.mjs"), "// exists\n", "utf8");
  writeFileSync(join(d, "lib", "other.mjs"), "// exists\n", "utf8");
  process.env.CLAUDE_PLUGIN_DATA = scratch("som-pdata-");
  return d;
}

const GOOD = {
  title: "워커에게는 절대경로를 준다",
  trigger: "워커 spec 에 파일 경로를 쓸 때",
  action: "상대경로를 쓰지 말고 절대경로로 적는다. 워커 cwd 는 Orca 워크트리이고 " +
          "오케스트레이터의 cwd 가 아니다.",
  why: "첫 e2e 에서 워커가 플러그인 디렉토리에 파일을 썼다",
  triggers: ["절대경로", "worktree", "워커 spec"],
  evidence: [{ kind: "file", ref: "lib/real.mjs", note: "고친 자리" }],
};

// ------------------------------------------------------------- the gate
test("a proverb is refused, and the reasons say why", () => {
  const problems = validatePattern(newPattern({
    title: "테스트 잘하기", trigger: "항상", action: "테스트를 잘 작성하라",
    triggers: ["테스트작성"],
  }));
  assert.ok(problems.length >= 3, problems.join(" / "));
  assert.ok(problems.some((p) => p.includes("trigger")));
  assert.ok(problems.some((p) => p.includes("evidence")));
});

test("generic advice is caught even when it is long enough", () => {
  const problems = validatePattern(newPattern({
    title: "코드 품질 유지", trigger: "구현 태스크를 시작할 때는 언제나",
    action: "best practice 를 따르고 코드를 깨끗하게 유지하라",
    triggers: ["코드품질"], evidence: [{ kind: "file", ref: "x" }],
  }), { evidenceExists: () => true });
  assert.ok(problems.some((p) => p.includes("일반론")), problems.join(" / "));
});

test("evidence that does not exist is refused", () => {
  const lib = new PatternLibrary(project());
  const r = lib.propose({ ...GOOD, evidence: [{ kind: "file", ref: "lib/nope.mjs" }] });
  assert.equal(r.ok, false);
  assert.ok(r.problems.some((p) => p.includes("확인할 수 없습니다")), r.problems.join(" / "));
});

test("a measurement alone is not evidence", () => {
  const lib = new PatternLibrary(project());
  const r = lib.propose({
    ...GOOD, evidence: [{ kind: "measurement", ref: "73초 → 41초" }],
  });
  assert.equal(r.ok, false);
  assert.ok(r.problems.some((p) => p.includes("measurement")), r.problems.join(" / "));
});

test("credentials are refused at write time", () => {
  const lib = new PatternLibrary(project());
  for (const action of [
    "접속할 때 password: hunter2xyzzy 를 쓰면 붙는다. 그 값을 그대로 넣어라.",
    "키는 -----BEGIN RSA PRIVATE KEY----- 로 시작하는 파일을 쓴다. 그대로 붙여라.",
    "토큰 ghp_abcdefghijklmnopqrstuvwxyz01 을 헤더에 넣어야 통과한다. 이대로 쓰라.",
  ]) {
    const r = lib.propose({ ...GOOD, action });
    assert.equal(r.ok, false, `should refuse: ${action.slice(0, 30)}`);
    assert.ok(r.problems.some((p) => p.includes("자격증명")), r.problems.join(" / "));
  }
});

test("a grounded, actionable pattern is stored at the starting confidence", () => {
  const lib = new PatternLibrary(project());
  const r = lib.propose(GOOD);
  assert.equal(r.ok, true, r.problems.join(" / "));
  assert.equal(r.pattern.confidence, START_CONFIDENCE);
  assert.equal(r.pattern.retired, false);
  assert.equal(r.merged, false);
  assert.equal(lib.load("project").length, 1);
});

test("the same lesson twice is confirmation, not a duplicate row", () => {
  const lib = new PatternLibrary(project());
  lib.propose(GOOD);
  const again = lib.propose({
    // Both anchors must be real now -- a run id has to be one this project
    // actually recorded, so a made-up one is refused rather than merged.
    ...GOOD, evidence: [{ kind: "file", ref: "lib/real.mjs", note: "두 번째" },
                        { kind: "file", ref: "lib/other.mjs", note: "같은 교훈" }],
  });
  assert.equal(again.merged, true);
  assert.ok(again.pattern.confidence > START_CONFIDENCE);
  assert.equal(lib.load("project").length, 1, "must not create a second row");
  assert.ok(again.pattern.evidence.length >= 2, "evidence is merged, not replaced");
});

test("the hash keys on trigger set plus action, not on the prose around it", () => {
  const a = patternHash({ triggers: ["a", "b"], action: "X 를 하라" });
  const b = patternHash({ triggers: ["b", "a"], action: "x 를 하라" });
  assert.equal(a, b, "order and case must not create a new pattern");
  assert.notEqual(a, patternHash({ triggers: ["a", "b"], action: "Y 를 하라" }));
});

// ------------------------------------------------------------- matching
test("an unrelated task matches nothing", () => {
  const lib = new PatternLibrary(project());
  lib.propose(GOOD);
  assert.equal(lib.match({ objective: "메일 초안", spec: "Outlook 초안" }).length, 0);
});

test("a related task matches, and the brief names the evidence", () => {
  const lib = new PatternLibrary(project());
  lib.propose(GOOD);
  const m = lib.match({ objective: "리포트", spec: "worktree 에 절대경로를 쓴다" });
  assert.equal(m.length, 1);
  assert.ok(m[0].score >= MATCH_THRESHOLD);
  const brief = renderForBrief(m);
  assert.match(brief, /lib\/real\.mjs/);
  assert.match(brief, /신뢰도/);
  assert.match(brief, /지시가 아님/, "an injected pattern is advice, not an order");
});

test("a path trigger still fires on the basename alone", () => {
  const p = newPattern({ triggers: ["engine/somsql/guard.py"], confidence: 50 });
  const s = scorePattern(p, { spec: "guard.py 를 고친다" });
  assert.ok(s.score > 0);
  assert.equal(s.tier, "path");
});

test("never more than three patterns reach a brief", () => {
  const many = Array.from({ length: 9 }, (_, i) => newPattern({
    title: `제목 ${i} 입니다`, action: `docs/${i} 를 렌더한다`,
    triggers: [`렌더${i}`], confidence: 40 + i,
  }));
  const m = matchPatterns(many, {
    spec: "렌더0 렌더1 렌더2 렌더3 렌더4 렌더5 렌더6 렌더7 렌더8 을 렌더한다",
  });
  assert.equal(m.length, MAX_INJECTED);
  // Highest confidence first, so the cap keeps the best rather than the first.
  assert.equal(m[0].pattern.confidence, 48);
});

test("project scope outranks user scope at the same score", () => {
  // Different actions, so they are different patterns rather than one deduped.
  const u = newPattern({ scope: "user", action: "user 쪽 조치", triggers: ["렌더러"], confidence: 50 });
  const p = newPattern({ scope: "project", action: "project 쪽 조치", triggers: ["렌더러"], confidence: 50 });
  assert.equal(matchPatterns([u, p], { spec: "렌더러" })[0].pattern.scope, "project");
});

test("a retired pattern never matches", () => {
  const p = newPattern({ triggers: ["렌더러"], confidence: 90, retired: true });
  assert.equal(matchPatterns([p], { spec: "렌더러" }).length, 0);
});

// ------------------------------------------------------------- outcomes
test("losses cost more than wins pay", () => {
  const base = newPattern({ confidence: 50 });
  const up = applyOutcome(base, "win").confidence - 50;
  const down = 50 - applyOutcome(base, "loss").confidence;
  assert.ok(down > up * 2, `win +${up} vs loss -${down}`);
});

test("repeated losses retire the pattern and keep it on file", () => {
  const lib = new PatternLibrary(project());
  const id = lib.propose(GOOD).pattern.id;
  lib.recordOutcome([id], "loss");
  lib.recordOutcome([id], "loss");
  const p = lib.load("project").find((x) => x.id === id);
  assert.ok(p.confidence < RETIRE_BELOW);
  assert.equal(p.retired, true);
  assert.match(p.retiredReason, /빗나감 2회/);
  assert.equal(lib.load("project").length, 1, "retired means flagged, not deleted");
  assert.equal(lib.match({ spec: "절대경로 worktree" }).length, 0);
});

test("a retired pattern is not graded again", () => {
  const lib = new PatternLibrary(project());
  const id = lib.propose(GOOD).pattern.id;
  lib.recordOutcome([id], "loss");
  lib.recordOutcome([id], "loss");
  const before = lib.load("project").find((x) => x.id === id);
  lib.recordOutcome([id], "loss");
  const after = lib.load("project").find((x) => x.id === id);
  assert.equal(after.confidence, before.confidence);
  assert.equal(after.losses, before.losses);
});

test("re-proposing a retired pattern is refused, not reported as merged", () => {
  // It used to return ok/merged and raise the confidence of something that
  // would never be injected again -- the caller was told "saved" about a
  // pattern permanently out of the match set.
  const lib = new PatternLibrary(project());
  const id = lib.propose(GOOD).pattern.id;
  lib.recordOutcome([id], "loss");
  lib.recordOutcome([id], "loss");
  const again = lib.propose(GOOD);
  assert.equal(again.ok, false);
  assert.equal(again.retiredDuplicate, true);
  assert.match(again.problems[0], /내려가 있습니다/);
  assert.equal(lib.load("project").find((x) => x.id === id).confidence, 0,
               "a refusal must not raise confidence");
});

test("stats separate live from retired", () => {
  const lib = new PatternLibrary(project());
  const id = lib.propose(GOOD).pattern.id;
  assert.deepEqual(
    { live: lib.stats().live, retired: lib.stats().retired }, { live: 1, retired: 0 });
  lib.retire(id, "손으로 내림");
  assert.deepEqual(
    { live: lib.stats().live, retired: lib.stats().retired }, { live: 0, retired: 1 });
});

test("an empty library injects nothing rather than an empty header", () => {
  const lib = new PatternLibrary(project());
  assert.equal(lib.brief({ objective: "무엇이든" }), "");
  assert.equal(renderForBrief([]), "");
});
