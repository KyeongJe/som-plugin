/**
 * Promoting learned patterns into a real, personal skill.
 *
 * The feature exists because a pattern only reaches a worker when `conduct`
 * builds a brief -- so everything the plugin learned was invisible the moment
 * someone opened Claude to do something else. A skill in the person's own
 * skills directory is loaded without being asked, which is the only way
 * "personalised" means anything.
 *
 * Writing into someone's home directory is the part that has to be right, so
 * most of this file is about refusing rather than writing: the marker rule,
 * the name prefix, the cap, and the gate that keeps a single lucky lesson from
 * becoming a permanent instruction.
 *
 *   node --test test/promote.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { scratch } from "./tmp.mjs";
import {
  PROMOTE, clusterPatterns, promotable, renderSkill, slugFor,
} from "../lib/domain/promote.mjs";
import { SkillWriter } from "../lib/state/skills.mjs";
import { newPattern } from "../lib/domain/patterns.mjs";

/** A pattern that has earned its place, unless told otherwise. */
function proven(fields = {}) {
  return newPattern({
    title: "표를 먼저 낸다",
    trigger: "리포트를 만들 때",
    action: "요약 문단보다 표를 먼저 배치하고 `row_grain` 을 표 위에 쓴다",
    why: "경영진이 산문을 건너뛰고 표부터 본다",
    triggers: ["리포트", "report"],
    tags: ["report-style"],
    evidence: [{ kind: "run", ref: "run_1", note: "" }],
    confidence: 80, uses: 4, wins: 4, losses: 0,
    ...fields,
  });
}

function cluster3(tag = "report-style") {
  return [
    proven({ title: "표를 먼저 낸다", tags: [tag], triggers: ["리포트"] }),
    proven({ title: "숫자에 출처를 붙인다", tags: [tag], triggers: ["리포트", "지표"],
             action: "인쇄되는 모든 수치에 `metrics.json` 키 참조를 단다" }),
    proven({ title: "결론을 차트 위에 굵게 쓴다", tags: [tag], triggers: ["차트"],
             action: "차트마다 takeaway 한 줄을 차트 **위에** bold 로 렌더한다" }),
  ];
}

// ------------------------------------------------------------- clustering
test("related patterns become one cluster, not three skills", () => {
  const cs = clusterPatterns(cluster3());
  assert.equal(cs.length, 1, "같은 태그인데 묶이지 않았습니다");
  assert.equal(cs[0].members.length, 3);
  assert.equal(cs[0].avgConfidence, 80);
  assert.equal(cs[0].uses, 12);
});

test("a chain of overlaps lands in one cluster, not two overlapping ones", () => {
  // A-B share a trigger, B-C share a different one. Without union-find these
  // become two clusters that both contain B, so B would be promoted twice and
  // pay its always-on cost twice.
  const a = proven({ title: "A", tags: [], triggers: ["alpha"] });
  const b = proven({ title: "B", tags: [], triggers: ["alpha", "beta"] });
  const c = proven({ title: "C", tags: [], triggers: ["beta"] });
  const cs = clusterPatterns([a, b, c]);
  assert.equal(cs.length, 1, cs.map((x) => x.members.map((m) => m.title)));
  assert.equal(cs[0].members.length, 3);
});

test("a retired pattern is not clustered", () => {
  const ps = cluster3();
  ps[0].retired = true;
  const cs = clusterPatterns(ps);
  assert.equal(cs[0].members.length, 2);
});

// ------------------------------------------------------------------- gate
test("three proven patterns clear the gate", () => {
  const { ready, held } = promotable(clusterPatterns(cluster3()));
  assert.equal(ready.length, 1, JSON.stringify(held, null, 2));
  assert.equal(held.length, 0);
});

test("one lucky lesson is not a habit", () => {
  const { ready, held } = promotable(clusterPatterns([proven()]));
  assert.equal(ready.length, 0);
  assert.match(held[0].why.join(" "), /관련 패턴이 1건/);
});

test("confidence has to be earned, not asserted", () => {
  const weak = cluster3().map((p) => ({ ...p, confidence: 55 }));
  const { ready, held } = promotable(clusterPatterns(weak));
  assert.equal(ready.length, 0);
  assert.match(held[0].why.join(" "), /평균 신뢰도 55/);
});

test("a cluster nobody has actually applied is held", () => {
  const unused = cluster3().map((p) => ({ ...p, uses: 1, wins: 1 }));
  const { ready, held } = promotable(clusterPatterns(unused));
  assert.equal(ready.length, 0);
  assert.match(held[0].why.join(" "), /적용 3회/);
});

test("a member that has never been right holds the whole cluster", () => {
  const ps = cluster3();
  ps[2].wins = 0;
  const { ready, held } = promotable(clusterPatterns(ps));
  assert.equal(ready.length, 0);
  assert.match(held[0].why.join(" "), /한 번도 맞은 적 없는/);
});

test("more misses than hits is not a habit worth keeping", () => {
  const ps = cluster3().map((p) => ({ ...p, wins: 1, losses: 3, uses: 4 }));
  const { ready, held } = promotable(clusterPatterns(ps));
  assert.equal(ready.length, 0);
  assert.match(held[0].why.join(" "), /빗나감/);
});

test("the cap is a context budget, and it says so", () => {
  const existing = Array.from({ length: PROMOTE.maxSkills }, (_, i) => `som-x${i}`);
  const { ready, held } = promotable(clusterPatterns(cluster3()), { existing });
  assert.equal(ready.length, 0);
  assert.match(held[0].why.join(" "), /항상 읽히므로/);
});

// ---------------------------------------------------------------- naming
test("a generated name is always som- prefixed and ASCII", () => {
  for (const label of ["report-style", "리포트 양식", "  ", "a/../../etc"]) {
    const s = slugFor(label);
    assert.match(s, /^som-[a-z0-9-]+$/, `${label} -> ${s}`);
    assert.ok(!s.includes(".."), s);
  }
});

test("two different Korean labels do not collide on one name", () => {
  // Neither transliterates, so both fall back. Falling back to the same string
  // would silently overwrite one skill with the other.
  const a = slugFor("리포트 양식");
  const b = slugFor("쿼리 습관");
  assert.match(a, /^som-/);
  assert.match(b, /^som-/);
});

// --------------------------------------------------------------- rendering
test("the rendered skill carries the marker, the sources and the evidence", () => {
  const c = clusterPatterns(cluster3())[0];
  const { name, text, description } = renderSkill(c);
  assert.match(name, /^som-/);
  assert.match(text, /^---\n/);
  assert.match(text, /som-generated: true/);
  assert.match(text, /som-confidence: 80/);
  for (const p of c.members) assert.ok(text.includes(p.id), `출처 id ${p.id} 없음`);
  assert.ok(text.includes("metrics.json"), "패턴의 실제 행동이 빠졌습니다");
  assert.ok(description.length <= PROMOTE.descriptionMax, description.length);
  // It must say it can be wrong. A generated instruction presented as certain
  // is the failure mode of the whole idea.
  assert.match(text, /안 맞으면/);
  assert.match(text, /som learn skills retire/);
});

test("the description does not run past its budget", () => {
  const long = cluster3().map((p) => ({ ...p, title: "가".repeat(400) }));
  const { description } = renderSkill(clusterPatterns(long)[0]);
  assert.ok(description.length <= PROMOTE.descriptionMax, description.length);
});

// ------------------------------------------------------------ writing safely
test("promotion writes one SKILL.md and nothing else", () => {
  const dir = scratch("som-skills-");
  const w = new SkillWriter(dir);
  const plan = w.plan(cluster3());
  assert.equal(plan.ready.length, 1);
  const res = w.write(plan.ready[0]);
  assert.ok(res.written, JSON.stringify(res));
  assert.ok(existsSync(res.file));
  assert.match(readFileSync(res.file, "utf8"), /som-generated: true/);
  assert.equal(w.mine().length, 1);
});

test("a skill the person wrote themselves is never overwritten", () => {
  // The marker is the licence. Without it this module does not touch the file,
  // even when the names match exactly.
  const dir = scratch("som-skills-");
  const w = new SkillWriter(dir);
  const plan = w.plan(cluster3());
  const name = plan.ready[0].name;
  const mine = join(dir, name);
  mkdirSync(mine, { recursive: true });
  writeFileSync(join(mine, "SKILL.md"), "---\nname: mine\n---\n손으로 쓴 것", "utf8");

  const res = w.write(plan.ready[0], { update: true });
  assert.equal(res.written, false);
  assert.match(res.reason, /직접 만드신/);
  assert.match(readFileSync(join(mine, "SKILL.md"), "utf8"), /손으로 쓴 것/);
});

test("an existing generated skill is left alone unless update is asked for", () => {
  const dir = scratch("som-skills-");
  const w = new SkillWriter(dir);
  const r = w.plan(cluster3()).ready[0];
  assert.ok(w.write(r).written);
  assert.equal(w.write(r).written, false, "두 번째 쓰기가 덮어썼습니다");
  assert.ok(w.write(r, { update: true }).written);
});

test("retire removes only what this plugin generated", () => {
  const dir = scratch("som-skills-");
  const w = new SkillWriter(dir);
  const r = w.plan(cluster3()).ready[0];
  w.write(r);

  mkdirSync(join(dir, "som-handmade"), { recursive: true });
  writeFileSync(join(dir, "som-handmade", "SKILL.md"), "---\nname: x\n---\n", "utf8");

  assert.equal(w.retire("som-handmade").removed, false);
  assert.ok(existsSync(join(dir, "som-handmade", "SKILL.md")));

  assert.ok(w.retire(r.name).removed);
  assert.ok(!existsSync(join(dir, r.name)));
});

test("retire refuses a name that could escape the directory", () => {
  const w = new SkillWriter(scratch("som-skills-"));
  for (const bad of ["../../etc", "som-../x", "som-a/b", "other", ""]) {
    assert.equal(w.retire(bad).removed, false, bad);
  }
});

test("a directory that is not ours is listed but marked untouchable", () => {
  const dir = scratch("som-skills-");
  mkdirSync(join(dir, "som-theirs"), { recursive: true });
  writeFileSync(join(dir, "som-theirs", "SKILL.md"), "---\nname: theirs\n---\n", "utf8");
  const w = new SkillWriter(dir);
  const [one] = w.list();
  assert.equal(one.name, "som-theirs");
  assert.equal(one.ours, false);
  assert.equal(w.mine().length, 0, "남의 스킬이 상한에 포함됐습니다");
});

test("a missing skills directory is empty, not an error", () => {
  const w = new SkillWriter(join(scratch("som-skills-"), "does", "not", "exist"));
  assert.deepEqual(w.list(), []);
  assert.deepEqual(w.mine(), []);
});
