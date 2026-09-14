/**
 * Regressions from the adversarial audit of the pattern library.
 *
 * Every test here corresponds to something that got junk past the first
 * version of the gate, proved by execution rather than argued. The comment on
 * each says what it did: a test named only after its fix is easy to delete by
 * accident and hard to re-derive.
 *
 *   node --test test/patterns-audit.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { scratch } from "./tmp.mjs";

import {
  LIMITS, MIN_TRIGGER, START_CONFIDENCE,
  applyOutcome, matchPatterns, newPattern, renderForBrief,
} from "../lib/domain/patterns.mjs";
import { PatternLibrary } from "../lib/state/patterns.mjs";

function project() {
  const d = scratch("som-audit-");
  mkdirSync(join(d, "lib"), { recursive: true });
  writeFileSync(join(d, "lib", "real.mjs"), "// exists\n", "utf8");
  process.env.CLAUDE_PLUGIN_DATA = scratch("som-adata-");
  return d;
}

const GOOD = {
  title: "워커에게는 절대경로를 준다",
  trigger: "워커 spec 에 파일 경로를 쓸 때",
  action: "상대경로를 쓰지 말고 절대경로로 적는다. 워커 cwd 는 Orca 워크트리이고 " +
          "오케스트레이터의 cwd 가 아니다.",
  triggers: ["절대경로", "worktree", "워커 spec"],
  evidence: [{ kind: "file", ref: "lib/real.mjs" }],
};

// ------------------------------------------------------------- triggers
test("a one-character trigger is refused: it matched every brief", () => {
  // triggers:["a"] scored 78 against completely unrelated work, and three such
  // patterns evicted every real one from the three-slot budget.
  const lib = new PatternLibrary(project());
  for (const t of [["a"], ["the "], ["파일"], [" "]]) {
    assert.equal(lib.propose({ ...GOOD, triggers: t }).ok, false,
                 `should refuse trigger ${JSON.stringify(t)}`);
  }
  assert.ok(MIN_TRIGGER >= 3);
});

test("a word common enough to be universal is refused as a trigger", () => {
  const lib = new PatternLibrary(project());
  for (const t of ["code", "test", "작업", "코드"]) {
    assert.equal(lib.propose({ ...GOOD, triggers: [t] }).ok, false, t);
  }
});

// ------------------------------------------------------------- size
test("an oversized action is refused: it was injected verbatim", () => {
  // A 2.5 MB action passed the gate and rendered into every matching brief.
  const lib = new PatternLibrary(project());
  const r = lib.propose({ ...GOOD, action: `다음을 따르라: ${"가".repeat(200_000)}` });
  assert.equal(r.ok, false);
  assert.ok(r.problems.some((p) => p.includes(String(LIMITS.action))),
            r.problems.join(" / "));
});

test("the rendered brief stays bounded even with three long patterns", () => {
  const long = Array.from({ length: 3 }, (_, i) => newPattern({
    title: `제목 ${i} 입니다`,
    trigger: `상황 ${i} 에서 적용된다 언제나`,
    action: `docs/${i} 를 렌더한다. ${"자세한 설명이 이어진다. ".repeat(40)}`,
    triggers: [`렌더${i}`], confidence: 50,
  }));
  const text = renderForBrief(matchPatterns(long, { spec: "렌더0 렌더1 렌더2" }));
  assert.ok(text.length <= LIMITS.brief_total + 600, `brief is ${text.length} chars`);
});

// ------------------------------------------------------------- secrets
test("credentials in triggers and tags are refused too", () => {
  // The scan covered only the prose fields, so a token in `triggers` -- stored
  // on disk and used as a match key -- walked straight through.
  const lib = new PatternLibrary(project());
  assert.equal(lib.propose({
    ...GOOD, triggers: ["ghp_16C7e42F292c6912E7710c838347Ae178B4a"],
  }).ok, false);
  assert.equal(lib.propose({
    ...GOOD, tags: ["password: sup3rSecret99"],
  }).ok, false);
});

test("a credential inside a URL is refused", () => {
  // The old pattern required user@domain.tld: and so missed every realistic
  // connection string.
  const lib = new PatternLibrary(project());
  for (const action of [
    "접속 문자열은 snowflake://svc_etl:Hunter2Hunter2@kissprod 이다. 그대로 쓰라.",
    "postgres://root:Hunter2Hunter2@db/analytics 로 붙는다. 이 값을 쓰라.",
  ]) {
    assert.equal(lib.propose({ ...GOOD, action }).ok, false, action.slice(0, 24));
  }
});

// ------------------------------------------------------------- evidence
test("a path that merely exists is not evidence", () => {
  // `.`, a directory, and `../../etc/passwd` all counted as verified.
  const lib = new PatternLibrary(project());
  for (const ref of [".", "..", "lib", "../../etc/passwd", "C:/Windows"]) {
    assert.equal(lib.propose({ ...GOOD, evidence: [{ kind: "file", ref }] }).ok,
                 false, `should refuse evidence ${ref}`);
  }
  // A real file inside the project still works, with or without a :line.
  assert.equal(lib.propose({
    ...GOOD, evidence: [{ kind: "file", ref: "lib/real.mjs:12" }],
  }).ok, true);
});

test("a run id that was never recorded is not evidence", () => {
  // Anything matching the run-id shape used to pass on shape alone.
  const lib = new PatternLibrary(project());
  assert.equal(lib.propose({
    ...GOOD, evidence: [{ kind: "run", ref: "run_deadbeef" }],
  }).ok, false);
});

// ------------------------------------------------------------- proverbs
test("a proverb is refused however it is padded", () => {
  // The generic check fired only between 20 and 79 characters, so the module's
  // own named example of a proverb sailed through at 32 characters and padding
  // past 80 defeated it entirely.
  const lib = new PatternLibrary(project());
  for (const action of [
    "쿼리는 작게 나누는 게 좋다. 가능하면 항상 그렇게 하라.",
    "성능을 개선하라 그리고 품질도 향상시켜라.",
    "리팩터링을 신중하게 진행하라 그리고 검토하라.",
    `best practice 를 따르고 코드를 깨끗하게 유지하라. ${"자세히 검토하라. ".repeat(6)}`,
  ]) {
    assert.equal(lib.propose({ ...GOOD, action }).ok, false, action.slice(0, 22));
  }
});

test("a specific action is still accepted", () => {
  // The counterweight to the test above: the gate has to reject proverbs
  // without rejecting the real thing.
  const lib = new PatternLibrary(project());
  for (const action of [
    "상대경로를 쓰지 말고 절대경로로 적는다. 워커 cwd 는 Orca 워크트리다.",
    "verifier 워커는 구현 코드를 읽지 말고 원자료에서 다시 세게 한다.",
    "metrics.json 의 값을 프로즈에 직접 타이핑하지 말고 참조로만 넣는다.",
  ]) {
    const r = lib.propose({ ...GOOD, action, triggers: [`키워드${action.length}`] });
    assert.equal(r.ok, true, `${action.slice(0, 24)} → ${r.problems.join(" / ")}`);
  }
});

test("a question is not an action", () => {
  const lib = new PatternLibrary(project());
  assert.equal(lib.propose({
    ...GOOD, action: "이 쿼리를 정말 lib/x.mjs 처럼 나누는 게 맞을까요?",
  }).ok, false);
});

test("invisible padding does not satisfy a length check", () => {
  const lib = new PatternLibrary(project());
  assert.equal(lib.propose({
    ...GOOD, title: `제목${"\u200b".repeat(20)}`,
  }).ok, false);
});

test("filler characters are not a trigger sentence", () => {
  const lib = new PatternLibrary(project());
  assert.equal(lib.propose({ ...GOOD, trigger: ".........." }).ok, false);
});

// ------------------------------------------------------------- dedup
test("a caller-supplied id cannot defeat content dedup", () => {
  // Three proposals of identical content with different ids became three rows
  // and consumed all three brief slots.
  const lib = new PatternLibrary(project());
  for (const id of ["aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb", "cccccccccccccccc"]) {
    lib.propose({ ...GOOD, id });
  }
  assert.equal(lib.load("project").length, 1);
  assert.equal(lib.match({ spec: "절대경로 worktree" }).length, 1);
});

test("the same pattern in both scopes occupies one slot, not two", () => {
  const lib = new PatternLibrary(project());
  lib.propose(GOOD, { scope: "project" });
  lib.propose(GOOD, { scope: "user" });
  assert.equal(lib.match({ spec: "절대경로 worktree" }).length, 1);
});

// ------------------------------------------------------------- robustness
test("an unknown scope is refused by name, not by a raw path error", () => {
  const lib = new PatternLibrary(project());
  for (const scope of ["proj", "Project", "", "__proto__", "toString"]) {
    assert.throws(() => lib.propose(GOOD, { scope }), /알 수 없는 scope/,
                  `scope ${JSON.stringify(scope)}`);
  }
});

test("a non-array patterns field degrades instead of throwing", () => {
  // `(f.patterns ?? []).map` threw a raw TypeError out of load, match, brief,
  // stats, propose, recordOutcome and retire alike.
  const d = project();
  mkdirSync(join(d, ".som"), { recursive: true });
  writeFileSync(join(d, ".som", "patterns.json"),
                JSON.stringify({ schema: 1, patterns: { a: 1 } }), "utf8");
  const lib = new PatternLibrary(d);
  assert.deepEqual(lib.load("project"), []);
  assert.deepEqual(lib.match({ spec: "무엇이든" }), []);
  assert.equal(lib.brief({ spec: "무엇이든" }), "");
  assert.equal(lib.stats().total, 0);
  assert.equal(lib.propose(GOOD).ok, true, "a bad file must not block writing");
});

test("a hand-edited confidence falls back rather than to zero", () => {
  // Number(null), Number([]) and Number(true) are all finite, so a permissive
  // coercion turned confidence:null into 0 -- one win from retirement -- while
  // confidence:"40" plus one win became 100.
  for (const bad of [null, [], true, {}, "high", NaN, undefined]) {
    const p = newPattern({ ...GOOD, confidence: bad });
    assert.equal(p.confidence, START_CONFIDENCE, `confidence ${JSON.stringify(bad)}`);
    assert.equal(applyOutcome(p, "win").retired, false);
  }
  assert.equal(newPattern({ ...GOOD, confidence: "40" }).confidence, 40);
});

test("a win never retires a pattern", () => {
  assert.equal(applyOutcome(newPattern({ ...GOOD, confidence: 1 }), "win").retired,
               false);
});

test("the retirement reason describes the state that caused it", () => {
  // It read "0회 빗나갔습니다" while retiring on a win.
  const p = applyOutcome(newPattern({ ...GOOD, confidence: 20 }), "loss");
  assert.equal(p.retired, true);
  assert.match(p.retiredReason, /빗나감 1회/);
});

test("re-proposing a retired pattern is refused, not reported as saved", () => {
  // It returned ok/merged and raised the confidence of something that would
  // never be injected again, so the caller was told "saved" about a pattern
  // permanently out of the match set.
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
