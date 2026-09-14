/**
 * The CLI's behaviour on ordinary mistakes.
 *
 * `bin/som.mjs` used to tell a refusal from a crash by regex-matching Korean
 * words in the error message. So `--recipe nope` -- a typo, the most ordinary
 * mistake there is -- fell through to the crash branch and printed a stack
 * trace at the person who made it, and any refusal phrased in English would
 * have done the same. Refusals now carry a type.
 *
 * Nothing here needs Orca, a network, or credentials.
 *
 *   node --test test/cli.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { mkdirSync, writeFileSync } from "node:fs";

import { scratch } from "./tmp.mjs";

const SOM = join(dirname(fileURLToPath(import.meta.url)), "..");
const CLI = join(SOM, "bin", "som.mjs");

function run(args, cwd = scratch("som-cli-")) {
  const r = spawnSync(process.execPath, [CLI, ...args], {
    cwd, encoding: "utf8", timeout: 60_000,
    env: { ...process.env, SOM_PROJECT: cwd },
  });
  return { code: r.status, out: (r.stdout ?? "") + (r.stderr ?? "") };
}

/** Ordinary mistakes: a person typed something wrong. */
const MISTAKES = [
  ["conduct", "asks", "--recipe", "nope"],
  ["conduct", "plan", "무언가", "--recipe", "zzz"],
  ["conduct", "run", "무언가", "--recipe", "없는레시피"],
  ["conduct", "나쁜서브커맨드"],
  ["interview", "show", "존재하지않는슬러그"],
  ["interview", "new"],
  ["interview", "new", "목표만", "--recipe", "없음"],
  ["interview", "gate"],
  ["learn", "retire", "zzz"],
  ["learn", "propose", "--evidence", "[깨진JSON"],
  ["learn", "나쁜서브커맨드"],
  ["없는명령"],
  ["conduct", "run", "x", "--recipe", "doc", "--answers", "없는파일.json"],
];

test("no ordinary mistake ever prints a stack trace", () => {
  const leaked = [];
  for (const args of MISTAKES) {
    const { out } = run(args);
    if (/\n\s+at /.test(out) || out.includes("node:internal")) {
      leaked.push(`${args.join(" ")}\n    ${out.split("\n").slice(0, 3).join("\n    ")}`);
    }
  }
  assert.deepEqual(leaked, [], "스택을 노출한 명령:\n" + leaked.join("\n"));
});

test("every mistake exits non-zero and says something in Korean", () => {
  for (const args of MISTAKES) {
    const { code, out } = run(args);
    assert.notEqual(code, 0, `${args.join(" ")} 가 0 으로 끝났습니다`);
    assert.ok(/[가-힣]/.test(out),
      `${args.join(" ")} 의 메시지에 한국어가 없습니다: ${out.slice(0, 120)}`);
  }
});

test("a refusal names what was valid instead", () => {
  // The useful half of a refusal is the list of things that would have worked.
  const { out } = run(["conduct", "asks", "--recipe", "nope"]);
  for (const id of ["doc", "prd", "watch", "analyze", "build", "data"]) {
    assert.ok(out.includes(id), `레시피 목록에 ${id} 가 없습니다: ${out}`);
  }
});

test("--help and the bare invocation both explain, and do not fail", () => {
  for (const args of [[], ["--help"], ["help"]]) {
    const { code, out } = run(args);
    assert.equal(code, 0, `${args.join(" ") || "(인자 없음)"} 가 ${code} 로 끝났습니다`);
    for (const cmd of ["interview", "learn", "conduct", "paths", "threshold"]) {
      assert.ok(out.includes(cmd), `사용법에 ${cmd} 가 없습니다`);
    }
  }
});

test("paths and threshold work in an empty directory", () => {
  // The first two things a new teammate runs, in a folder with nothing in it.
  for (const args of [["paths"], ["threshold"]]) {
    const { code, out } = run(args);
    assert.equal(code, 0, `${args[0]} 가 ${code} 로 끝났습니다: ${out}`);
    assert.ok(out.trim().length > 0, `${args[0]} 가 아무것도 출력하지 않았습니다`);
  }
});

test("a corrupt state file is refused, not crashed on", () => {
  for (const junk of ["", "{", "[]", "null", '{"rounds":"not an array"}']) {
    const cwd = scratch("som-corrupt-");
    mkdirSync(join(cwd, ".som", "interview"), { recursive: true });
    writeFileSync(join(cwd, ".som", "interview", "x.json"), junk, "utf8");
    const { out } = run(["interview", "show", "x"], cwd);
    assert.ok(!/\n\s+at /.test(out),
      `손상된 상태 파일 ${JSON.stringify(junk)} 이 스택을 냈습니다:\n${out.slice(0, 300)}`);
  }
});
