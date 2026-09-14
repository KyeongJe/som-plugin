/**
 * The release gate, attacked with the things it exists to stop.
 *
 * This gate is the reason there are two repositories. If it silently stops
 * catching something, the split provides nothing and costs a sync step
 * forever, so every rule below is tested with a case that must fail and a
 * neighbouring case that must not.
 *
 * The false-positive half is load-bearing, exactly as in the Snowflake guard.
 * A release gate that cries wolf gets `--force`d, and this one deliberately
 * has no --force -- which only works if it is right.
 *
 *   node --test test/release.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { checkChangelog, checkRelease, checkVersions } from "../../tools/gate.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

/** Built by concatenation so this file does not trip the gate it tests. */
const REAL_ACCOUNT = "UKDV" + "SEA-NPB" + "82638";
const HOME = "C:" + "\\Users\\" + "jdoe" + "\\Desktop\\x.txt";

const problems = (files, opts) => checkRelease(files, opts).problems;

// ------------------------------------------------------- files that must not ship
test("run state and caches never reach the archive", () => {
  for (const p of [
    ".som/ledger.ndjson", "som/.som/patterns.json", "_cache/01.parquet",
    "node_modules/x/index.js", "som/engine/__pycache__/x.pyc",
    "history.bundle", "transcript.jsonl", "data.parquet",
    "keys/som.p8", "certs/a.pem", ".env.local", "sf_login_info.json",
    "connections.toml", "scratchpad/notes.md",
  ]) {
    assert.ok(problems([[p, ""]]).length > 0, `통과하면 안 됩니다: ${p}`);
  }
});

test("ordinary project files are not mistaken for run state", () => {
  for (const p of [
    "README.md", "som/lib/conduct.mjs", "som/engine/somsql/conn.py",
    "som/standard/themes/tokens.css", "som/sql/01_visit_gap.sql",
    "som/standard/secrets/sf_login_info.json.sample",   // the .sample IS shipped
    "som/docs/img/guardrails-light.svg",
  ]) {
    assert.deepEqual(problems([[p, "ordinary"]]), [], p);
  }
});

// ------------------------------------------------------------- the account id
test("a live account identifier stops the release", () => {
  const found = problems([["a/config.json", `{"account": "${REAL_ACCOUNT}"}`]]);
  assert.equal(found.length, 1, found);
  assert.match(found[0], /account identifier/);
});

test("a placeholder account does not", () => {
  for (const v of ["<ORG>-<ACCOUNT>", "your-account", "EXAMPLE-ACCT", ""]) {
    assert.deepEqual(problems([["a/config.json", `{"account": "${v}"}`]]), [], v);
  }
});

test("the same value in URL form is caught too", () => {
  const host = `${REAL_ACCOUNT}.snowflake` + "computing.com";
  assert.ok(problems([["doc.md", `https://${host}`]]).length > 0);
  assert.deepEqual(problems([["doc.md", "https://<account>.snowflake" + "computing.com"]]), []);
});

// --------------------------------------------------------------- key material
test("a key body stops the release; the bare header does not", () => {
  const header = "-----BEGIN" + " RSA " + "PRIVATE KEY-----";
  const body = `${header}\nMIIEowIBAAKCAQEAxYZ0pQr7mN3vKtLbW9sFh2gJdC1nRyUoPiEaQwXsZmTvBkHgLd\n`;
  assert.ok(problems([["k.pem.txt", body]]).length > 0);
  // Prose naming the header is how the guard is documented and tested.
  assert.deepEqual(problems([["doc.md", `키는 ${header} 로 시작합니다.`]]), []);
});

// ------------------------------------------------------------ developer paths
test("a named home directory stops the release", () => {
  const found = problems([["a.md", `see ${HOME}`]]);
  assert.equal(found.length, 1, found);
  assert.match(found[0], /개발자 홈 경로/);
  // Concatenated like HOME above: a literal here trips the gate on this very
  // file. It did -- twice, because the comment written to explain the first
  // fix spelled the path out again.
  assert.ok(problems([["a.md", "/home/" + "jdoe" + "/src/x"]]).length > 0);
  assert.ok(problems([["a.md", "/Users/" + "jdoe" + "/src/x"]]).length > 0);
});

test("an elided or obviously fake home does not", () => {
  // Docs elide the middle of a path, and fixtures need a fake one. Both were
  // reported by the first run of this gate on its own repository.
  for (const t of [
    'PYTHONPATH="C:/Users/.../som/0.2.0/engine"',
    "C:" + "\\Users\\example\\Temp\\x",
    "C:" + "\\Users\\<you>\\.snowflake\\keys",
    "/home/user/project",
    "C:" + "\\Users\\  (본인 계정 폴더)",
  ]) {
    assert.deepEqual(problems([["doc.md", t]]), [], t);
  }
});

// ---------------------------------------------------------------- owner email
test("the owner email may live in the manifests and nowhere else", () => {
  const mail = "someone@example.com";
  assert.deepEqual(
    problems([[".claude-plugin/marketplace.json", mail]], { ownerEmail: mail }), []);
  assert.deepEqual(
    problems([["som/.claude-plugin/plugin.json", mail]], { ownerEmail: mail }), []);
  const spread = problems([["som/docs/notes.md", mail]], { ownerEmail: mail });
  assert.equal(spread.length, 1, spread);
  assert.match(spread[0], /매니페스트 밖/);
});

test("publishing the owner email is a note, not a refusal", () => {
  // It is a real choice, not an unsafe one. Mixing "you must" with "you might"
  // is how a gate gets overridden by habit.
  const { problems: p, notes } = checkRelease([], { ownerEmail: "a@b.com" });
  assert.deepEqual(p, []);
  assert.equal(notes.length, 1);
});

// -------------------------------------------------------------------- versions
const manifests = (v) => [
  ["som/.claude-plugin/plugin.json", JSON.stringify({ version: v })],
  [".claude-plugin/marketplace.json",
   JSON.stringify({ metadata: { version: v }, plugins: [{ name: "som", version: v }] })],
];

test("three version strings have to be the one being released", () => {
  assert.deepEqual(checkVersions(manifests("0.2.0"), "0.2.0"), []);
  assert.equal(checkVersions(manifests("0.1.0"), "0.2.0").length, 3);
});

test("a drifted marketplace entry is named specifically", () => {
  const files = manifests("0.2.0");
  files[1][1] = JSON.stringify({
    metadata: { version: "0.2.0" }, plugins: [{ name: "som", version: "0.1.9" }] });
  const found = checkVersions(files, "0.2.0");
  assert.equal(found.length, 1, found);
  assert.match(found[0], /som 항목/);
});

test("a missing manifest is a problem, not a crash", () => {
  assert.ok(checkVersions([], "0.2.0").length >= 2);
  assert.ok(checkVersions([["som/.claude-plugin/plugin.json", "{ bad"]], "0.2.0").length > 0);
});

// ------------------------------------------------------------------- changelog
test("the release notes have to mention the release", () => {
  assert.deepEqual(checkChangelog([["CHANGELOG.md", "## 0.2.0 — 2026-09-14\n바뀐 것"]], "0.2.0"), []);
  assert.deepEqual(checkChangelog([["CHANGELOG.md", "## [v0.2.0]\n바뀐 것"]], "0.2.0"), []);
  assert.equal(checkChangelog([["CHANGELOG.md", "## 0.1.0\n"]], "0.2.0").length, 1);
  assert.equal(checkChangelog([], "0.2.0").length, 1);
});

// ------------------------------------------------- the gate runs on this repo
test("this repository currently passes its own gate", () => {
  // The point of the split is that what leaves is clean. If this fails, the
  // next release is already blocked -- better to learn it here than at
  // release time.
  const owner = JSON.parse(
    readFileSync(join(ROOT, ".claude-plugin", "marketplace.json"), "utf8"))?.owner?.email ?? "";
  const files = [];
  for (const rel of ["README.md", "CHANGELOG.md",
                     "som/.claude-plugin/plugin.json",
                     ".claude-plugin/marketplace.json",
                     "som/standard/secrets/README.md",
                     "som/standard/examples/rnr.example.somdoc.json"]) {
    files.push([rel, readFileSync(join(ROOT, rel), "utf8")]);
  }
  assert.deepEqual(problems(files, { ownerEmail: owner }), []);
});

test("the gate is actually looking at something", () => {
  // "0 problems" and "the check never ran" print identically. A deliberate
  // failure proves the difference.
  assert.ok(problems([["x.md", `${HOME} and {"account": "${REAL_ACCOUNT}"}`]]).length >= 2);
});
