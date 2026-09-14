/**
 * The intake gate, and recipe routing when the source is not named.
 *
 * Two regressions this locks down, both found by a reader rather than a test:
 *
 *   1. `asks` was referenced in SKILL.md but was a list of bare strings on the
 *      recipes -- prose, not data. A run could start with every question
 *      unanswered, and a worker handed {{source}} does not fail, it invents a
 *      roster. The gate has to be code.
 *
 *   2. Analysis was wired to Snowflake alone, so a teammate without an account
 *      had no path at all. "매출 분석해줘" with no source named must land on
 *      the file recipe, and naming Snowflake must still win.
 *
 *   node --test test/intake.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  listRecipes, loadRecipe, chooseRecipe, asksFor, missingAsks,
  unfilledSlots, buildDag,
} from "../lib/conduct.mjs";

const REAL = listRecipes().filter((r) => !r.id.startsWith("_"));

test("every recipe declares its questions as objects, not prose", () => {
  for (const r of REAL) {
    assert.ok(Array.isArray(r.asks), `${r.id}: asks missing`);
    assert.ok(r.asks.length > 0, `${r.id}: no asks`);
    for (const a of r.asks) {
      assert.equal(typeof a, "object", `${r.id}: ask is a bare string`);
      assert.ok(a.slot, `${r.id}: ask has no slot`);
      assert.ok(a.q && a.q.length > 4, `${r.id}: ${a.slot} has no question`);
      if (a.required !== false) {
        assert.ok(a.why, `${r.id}: required ask ${a.slot} has no why`);
      }
    }
  }
});

test("asked-for slots and used slots are the same set", () => {
  for (const r of REAL) {
    // Answer everything, then no placeholder may survive. Catches a spec that
    // uses {{roster}} with no matching question -- the worker would otherwise
    // be briefed with the literal text "원천 {{roster}} 을 읽어라".
    const slots = { ...r.defaults };
    for (const a of asksFor(r)) slots[a.slot] = "ANSWER";
    assert.deepEqual(unfilledSlots(buildDag(r, slots)), [],
                     `${r.id}: placeholder with no ask`);

    // And every question must actually reach a worker, or it is a question
    // asked for nothing.
    const bare = buildDag(r, {});
    const open = new Set(unfilledSlots(bare));
    for (const a of asksFor(r)) {
      if (r.defaults?.[a.slot] !== undefined) continue;
      assert.ok(open.has(a.slot),
                `${r.id}: asks for ${a.slot} but no node uses it`);
    }
  }
});

test("a required question left blank is missing; a default is not", () => {
  const doc = loadRecipe("doc");
  const bare = missingAsks(doc, { ...doc.defaults }).map((a) => a.slot);
  assert.ok(bare.includes("source"), "roster source must be asked for");
  assert.ok(bare.includes("decision"), "what is being approved must be asked for");
  assert.ok(!bare.includes("slug"), "a defaulted slot is not missing");

  // Whitespace is not an answer.
  assert.equal(missingAsks(doc, { ...doc.defaults, source: "   " }).some(
    (a) => a.slot === "source"), true);

  const watch = loadRecipe("watch");
  const w = missingAsks(watch, { ...watch.defaults }).map((a) => a.slot);
  assert.ok(!w.includes("rounds"), "optional asks do not block");
});

test("routing: no source named goes to the recipe that needs no account", () => {
  assert.equal(chooseRecipe("매출 분석해줘").id, "analyze");
  assert.equal(chooseRecipe("지난달 데이터 리포트 만들어줘").id, "analyze");
});

test("routing: naming the source settles it either way", () => {
  assert.equal(chooseRecipe("snowflake 에서 매출 분석해줘").id, "data");
  assert.equal(chooseRecipe("쿼리 짜줘").id, "data");
  assert.equal(chooseRecipe("엑셀로 매출 분석해줘").id, "analyze");
  assert.equal(chooseRecipe("이 csv 집계해줘").id, "analyze");
});

test("routing: the other work products still land where they should", () => {
  assert.equal(chooseRecipe("R&R 문서 만들어줘").id, "doc");
  assert.equal(chooseRecipe("PRD 필요해").id, "prd");
  assert.equal(chooseRecipe("챗봇 밤새 확인해줘").id, "watch");
  assert.equal(chooseRecipe("자동화 스크립트 만들어줘").id, "build");
});

test("only the Snowflake recipe declares the Snowflake dependency", () => {
  const need = REAL.filter((r) => (r.requires ?? []).includes("snowflake"));
  assert.deepEqual(need.map((r) => r.id), ["data"]);
  assert.ok(REAL.length - need.length >= 5,
            "most of the catalogue must work with nothing installed");

  // `analyze` mentions Snowflake in a question on purpose -- it offers to hand
  // off -- so the dependency must be a declared field, never a text scan.
  const analyze = loadRecipe("analyze");
  assert.ok(JSON.stringify(analyze).toLowerCase().includes("snowflake"));
  assert.deepEqual(analyze.requires, []);
});
