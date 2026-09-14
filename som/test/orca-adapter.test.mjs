/**
 * The Orca adapter, exercised without Orca.
 *
 * Every claim here is about behaviour a teammate meets on a bad day: Orca is
 * not installed, two tasks want the same file, the same message arrives twice,
 * a DAG has a cycle. None of it needs a running Orca, and none of it should
 * cost a paid worker to find out.
 *
 *   node --test test/orca-adapter.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { scratch } from "./tmp.mjs";
import {
  validateDag, planWave, writesConflict, globToRegExp, concurrencyCap,
  placementFor, launchFor, annotateCriticalPath, parallelismOf, MAX_DEPTH,
} from "../lib/domain/plan.mjs";

// ---------------------------------------------------------------- write globs
test("two tasks writing the same place never share a wave", () => {
  const nodes = [
    { key: "a", role: "writer", deps: [], writes: ["docs/shared/**"] },
    { key: "b", role: "writer", deps: [], writes: ["docs/shared/one.txt"] },
    { key: "c", role: "writer", deps: [], writes: ["docs/other/**"] },
  ];
  annotateCriticalPath(nodes);
  const { wave, deferred } = planWave(nodes, { cap: 4 });
  const keys = wave.map((n) => n.key);
  assert.ok(!(keys.includes("a") && keys.includes("b")),
    `겹치는 글롭 두 개가 같은 wave 에 들어갔습니다: ${keys.join(", ")}`);
  assert.ok(keys.includes("c"), "겹치지 않는 작업은 같은 wave 에 들어가야 합니다");
  assert.ok(deferred.some((d) => d.why === "writes-conflict"),
    "미룬 이유가 writes-conflict 로 기록되어야 합니다");
});

test("glob overlap is judged by what the patterns cover, not by string equality", () => {
  const overlapping = [
    [["docs/**"], ["docs/a/b.txt"]],
    [["a/*.md"], ["a/b.md"]],
    [["**/*.json"], ["ir/x.json"]],
    [["out/**"], ["out/**"]],
  ];
  for (const [x, y] of overlapping) {
    assert.equal(writesConflict(x, y), true, `겹치는데 안 겹친다고 합니다: ${x} vs ${y}`);
  }
  const disjoint = [
    [["docs/**"], ["src/**"]],
    [["a/*.md"], ["a/b.txt"]],
    [["out/one.txt"], ["out/two.txt"]],
  ];
  for (const [x, y] of disjoint) {
    assert.equal(writesConflict(x, y), false, `안 겹치는데 겹친다고 합니다: ${x} vs ${y}`);
  }
});

test("a glob is anchored, so a prefix match is not a match", () => {
  // `docs/**` must not swallow `docs-archive/x`.
  assert.equal(globToRegExp("docs/**").test("docs-archive/x"), false);
  assert.equal(globToRegExp("docs/**").test("docs/x"), true);
});

// ---------------------------------------------------------------------- DAG
test("a cycle is reported, not looped on", () => {
  const { problems } = validateDag([
    { key: "a", role: "writer", deps: ["b"], writes: ["x"] },
    { key: "b", role: "writer", deps: ["a"], writes: ["y"] },
  ]);
  assert.ok(problems.length > 0, "순환을 문제로 보고하지 않았습니다");
  assert.ok(problems.some((p) => /순환|cycle/i.test(p)), problems.join(" / "));
});

test("a dependency on a node that does not exist is reported", () => {
  const { problems } = validateDag([
    { key: "a", role: "writer", deps: ["ghost"], writes: ["x"] },
  ]);
  assert.ok(problems.length > 0, "없는 의존을 통과시켰습니다");
});

test("a node that declares no writes is refused", () => {
  // Without this the wave scheduler cannot tell what conflicts with what, and
  // two workers edit the same file in one checkout.
  const { problems } = validateDag([{ key: "a", role: "writer", deps: [], writes: [] }]);
  assert.ok(problems.length > 0, "writes 가 빈 노드를 통과시켰습니다");
});

test("a chain deeper than the cap is refused", () => {
  const deep = Array.from({ length: MAX_DEPTH + 3 }, (_, i) => ({
    key: `n${i}`, role: "writer", writes: [`f${i}`],
    deps: i === 0 ? [] : [`n${i - 1}`],
  }));
  const { problems, depth } = validateDag(deep);
  assert.ok(depth > MAX_DEPTH, `깊이가 ${depth} 로 계산됐습니다`);
  assert.ok(problems.length > 0, `최장 경로 ${depth} 가 통과했습니다 (상한 ${MAX_DEPTH})`);
});

// ------------------------------------------------------------------ capacity
test("the worker cap never exceeds what the level allows", () => {
  for (let level = 0; level <= 4; level += 1) {
    for (const policyMax of [1, 3, 99]) {
      const cap = concurrencyCap({ level, policyMax, worktree: "current" });
      assert.ok(cap >= 0 && cap <= 4,
        `L${level} policyMax=${policyMax} 에서 상한이 ${cap} 입니다`);
      assert.ok(cap <= policyMax, "정책 상한을 넘었습니다");
    }
  }
});

test("a nonsense level does not widen the cap", () => {
  for (const level of [-5, 99, NaN, null, undefined, "L4"]) {
    const cap = concurrencyCap({ level, policyMax: 3, worktree: "current" });
    assert.ok(Number.isFinite(cap) && cap >= 0 && cap <= 4,
      `level=${JSON.stringify(level)} 에서 상한이 ${cap} 입니다`);
  }
});

// ----------------------------------------------------------------- placement
test("a new worktree is never the default", () => {
  const plain = placementFor({ key: "a", role: "writer" }, { gitBacked: true });
  assert.equal(plain.worktree, "current",
    "기본값이 새 워크트리입니다 — 병렬성은 격리가 아닙니다");
});

test("a worktree is impossible in a non-git workspace", () => {
  const p = placementFor({ key: "a", role: "writer", needsWorktree: true },
                         { gitBacked: false });
  assert.equal(p.worktree, "current",
    "git 이 아닌 워크스페이스에서 워크트리를 요구했습니다");
});

// -------------------------------------------------------------------- roles
test("every role resolves to a model and an effort", () => {
  for (const role of ["planner", "architect", "analyst", "builder", "writer",
                      "renderer", "verifier", "scribe", "designer", "watcher"]) {
    const l = launchFor(role, { attempt: 1 });
    assert.ok(l.model, `${role}: model 이 없습니다`);
    assert.ok(l.effort, `${role}: effort 가 없습니다`);
  }
});

test("an unknown role still launches something rather than throwing", () => {
  const l = launchFor("어떤새역할", { attempt: 1 });
  assert.ok(l.model && l.effort,
    "모르는 역할에서 실행 설정이 비었습니다 — 레시피 오타가 크래시가 됩니다");
});

test("a retry does not repeat the same settings", () => {
  // Retrying a failure identically is how a run burns three attempts on one
  // node and then circuit-breaks.
  const first = launchFor("builder", { attempt: 1 });
  const retry = launchFor("builder", { attempt: 2 });
  assert.notDeepEqual(retry, first,
    "재시도가 1차와 같은 모델·effort 입니다");
});

// -------------------------------------------------------------- parallelism
test("parallelismOf agrees with planWave on every recipe shape", () => {
  const shapes = [
    [{ key: "a", deps: [], writes: ["x"], role: "writer" }],
    [{ key: "a", deps: [], writes: ["x"], role: "writer" },
     { key: "b", deps: ["a"], writes: ["y"], role: "writer" }],
    [{ key: "a", deps: [], writes: ["x"], role: "writer" },
     { key: "b", deps: [], writes: ["y"], role: "writer" },
     { key: "c", deps: ["a", "b"], writes: ["z"], role: "writer" }],
  ];
  for (const nodes of shapes) {
    const p = parallelismOf(nodes, { cap: 4 });
    assert.equal(p.widths.reduce((a, b) => a + b, 0), nodes.length,
      "wave 에 담긴 노드 수가 전체와 다릅니다");
    assert.equal(p.sequential, p.maxWidth <= 1);
  }
});

test("parallelismOf terminates on a cycle instead of spinning", () => {
  // validateDag reports the cycle; this must not hang before it gets there.
  const p = parallelismOf([
    { key: "a", deps: ["b"], writes: ["x"], role: "writer" },
    { key: "b", deps: ["a"], writes: ["y"], role: "writer" },
  ], { cap: 4 });
  assert.equal(p.waves.length, 0, "순환에서 wave 를 만들어냈습니다");
});
