#!/usr/bin/env node
/**
 * Publish a release from this private repository to the public one.
 *
 *   node tools/release.mjs 0.2.0 --public ../som-plugin [--push] [--dry-run]
 *
 * The two repositories are deliberately not related. This copies *files*, not
 * commits, so the public history is one commit per release and can never carry
 * a development mistake that was fixed later. That is the whole reason the
 * split exists: an account identifier lived in this repo's history for thirty
 * commits, and removing it from the working tree did not remove it from what
 * `git log -p` would have published.
 *
 * Direction is one-way and enforced by what this script does not do: it never
 * adds a remote, never fetches, never merges. If the public repo has to be
 * fixed, it gets fixed here and released again. A hotfix committed directly
 * over there would be silently reverted by the next release, which is the
 * failure mode that makes two-repo setups rot.
 *
 * Every gate runs before anything is copied, and any failure stops the whole
 * thing. There is no --force.
 */
import { execFileSync } from "node:child_process";
import {
  copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync,
  statSync, writeFileSync,
} from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { checkChangelog, checkRelease, checkVersions } from "./gate.mjs";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const SOM = join(ROOT, "som");

const argv = process.argv.slice(2);
const version = argv.find((a) => !a.startsWith("-"));
const has = (f) => argv.includes(`--${f}`);
const flag = (f, d) => {
  const i = argv.indexOf(`--${f}`);
  return i >= 0 && argv[i + 1] ? argv[i + 1] : d;
};

const say = (s = "") => process.stdout.write(`${s}\n`);
function stop(msg, lines = []) {
  say(`\n릴리스 중단: ${msg}`);
  for (const l of lines) say(`  · ${l}`);
  say("\n아무것도 복사하지 않았고 아무것도 푸시하지 않았습니다.");
  process.exit(1);
}

function run(cmd, args, opts = {}) {
  return execFileSync(cmd, args, {
    cwd: ROOT, encoding: "utf8", maxBuffer: 64 * 1024 * 1024, ...opts,
  });
}

function quiet(cmd, args, opts = {}) {
  try { return { ok: true, out: run(cmd, args, { stdio: "pipe", ...opts }) }; }
  catch (e) { return { ok: false, out: `${e.stdout ?? ""}${e.stderr ?? ""}` }; }
}

if (!version || !/^\d+\.\d+\.\d+$/.test(version)) {
  stop("버전이 필요합니다", ["예: node tools/release.mjs 0.2.0 --public ../som-plugin"]);
}
const publicDir = resolve(ROOT, flag("public", "../som-plugin"));
const dryRun = has("dry-run");

say(`som 릴리스 ${version}`);
say(`  출처 ${ROOT}`);
say(`  대상 ${publicDir}${dryRun ? "  (dry-run)" : ""}`);

// ---------------------------------------------------------------- 1. tree
say("\n[1/5] 작업 트리");
const dirty = run("git", ["status", "--porcelain"]).trim();
if (dirty) {
  stop("커밋되지 않은 변경이 있습니다",
       [...dirty.split("\n").slice(0, 10), "릴리스는 HEAD 기준입니다. 먼저 커밋하세요."]);
}
const head = run("git", ["rev-parse", "--short", "HEAD"]).trim();
say(`  HEAD ${head} · 깨끗함`);

// --------------------------------------------------------------- 2. suites
say("\n[2/5] 테스트");
const nodeTest = quiet("node", ["--test", ...readdirSync(join(SOM, "test"))
  .filter((f) => f.endsWith(".test.mjs")).map((f) => `test/${f}`)], { cwd: SOM });
const failLine = (nodeTest.out.match(/^# fail (\d+)$/m) ?? [])[1];
if (!nodeTest.ok || failLine !== "0") {
  stop("node 테스트가 실패합니다",
       nodeTest.out.split("\n").filter((l) => l.startsWith("not ok")).slice(0, 10));
}
say(`  node  ${(nodeTest.out.match(/^# pass (\d+)$/m) ?? [])[1]}건 통과`);

const env = { ...process.env, PYTHONPATH: join(SOM, "engine"), PYTHONUTF8: "1" };
for (const f of readdirSync(join(SOM, "engine", "tests")).filter((x) => /^test_.*\.py$/.test(x))) {
  const r = quiet(process.env.PYTHON ?? "python", [`engine/tests/${f}`], { cwd: SOM, env });
  const m = r.out.match(/(\d+)\/(\d+) passed/);
  if (!r.ok || !m || m[1] !== m[2]) {
    stop(`${f} 가 실패합니다`, r.out.split("\n").filter((l) => /FAIL|ERROR/.test(l)).slice(0, 8));
  }
  say(`  ${f.padEnd(28)} ${m[0]}`);
}

const counts = quiet(process.env.PYTHON ?? "python", ["docs/check_counts.py"], { cwd: SOM, env });
if (!counts.ok || !/일치합니다/.test(counts.out)) {
  stop("발행된 테스트 개수가 실측과 다릅니다",
       counts.out.split("\n").filter((l) => l.trim().startsWith("-")).slice(0, 8));
}
say("  발행된 숫자 = 실측");

const doctor = quiet(process.env.PYTHON ?? "python",
                     ["standard/scripts/doctor.py", "--project", "."], { cwd: SOM, env });
const dm = doctor.out.match(/(\d+) ok · (\d+) warn · (\d+) fail/);
if (dm && dm[3] !== "0") stop("doctor 가 실패 항목을 보고합니다", [dm[0]]);
say(`  doctor ${dm ? dm[0] : "(출력을 읽지 못함)"}`);

// ------------------------------------------------------- 3. the file set
say("\n[3/5] 공개 대상 파일");
const tracked = run("git", ["ls-files", "-z"]).split("\0").filter(Boolean);
const files = [];
for (const rel of tracked) {
  const abs = join(ROOT, rel);
  let st;
  try { st = statSync(abs); } catch { continue; }
  if (!st.isFile()) continue;
  let text = "";
  try { text = readFileSync(abs, "utf8"); } catch { text = ""; }   // binary
  files.push([rel, text]);
}
say(`  추적 파일 ${files.length}건 (미추적·무시된 파일은 애초에 포함되지 않습니다)`);

// ------------------------------------------------------------- 4. gates
say("\n[4/5] 공개 게이트");
const owner = (() => {
  try {
    return JSON.parse(readFileSync(join(ROOT, ".claude-plugin", "marketplace.json"), "utf8"))
      ?.owner?.email ?? "";
  } catch { return ""; }
})();

const { problems, notes } = checkRelease(files, { ownerEmail: owner });
problems.push(...checkVersions(files, version));
problems.push(...checkChangelog(files, version));
if (problems.length) stop(`공개 게이트 ${problems.length}건`, problems);
say("  실제 account identifier · 접속 endpoint · 키 본문 · 개발자 경로 — 없음");
say("  버전 3곳 일치 · CHANGELOG 항목 있음");
for (const n of notes) say(`  참고: ${n}`);

if (dryRun) {
  say("\ndry-run 이라 여기서 멈춥니다. 위 게이트를 전부 통과했습니다.");
  process.exit(0);
}

// ------------------------------------------------------------ 5. publish
say("\n[5/5] 공개 저장소에 반영");
if (!existsSync(join(publicDir, ".git"))) {
  stop(`${publicDir} 이 git 저장소가 아닙니다`, [
    "먼저 만들어 두세요:",
    `  mkdir -p ${publicDir} && cd ${publicDir} && git init && git remote add origin <URL>`,
    "이 저장소를 remote 로 추가하지 마세요 — 이력은 공유하지 않습니다.",
  ]);
}
const crossed = quiet("git", ["remote", "-v"], { cwd: publicDir });
if (crossed.ok && crossed.out.includes("som-claude-plugin")) {
  stop("공개 저장소가 비공개 저장소를 remote 로 갖고 있습니다", [
    "이력이 섞이면 이 구조의 의미가 사라집니다. 해당 remote 를 제거하세요.",
  ]);
}

// Wipe everything but .git, then lay down exactly what HEAD tracks. Copying
// over the top would leave a deleted file behind forever.
for (const name of readdirSync(publicDir)) {
  if (name === ".git") continue;
  rmSync(join(publicDir, name), { recursive: true, force: true });
}
for (const [rel] of files) {
  const dest = join(publicDir, rel);
  mkdirSync(dirname(dest), { recursive: true });
  copyFileSync(join(ROOT, rel), dest);
}
say(`  ${files.length}건 복사`);

const notes_md = (() => {
  const cl = files.find(([f]) => f.replace(/\\/g, "/") === "CHANGELOG.md")?.[1] ?? "";
  const m = cl.match(new RegExp(`^##\\s*\\[?v?${version.replace(/\./g, "\\.")}\\]?[^\\n]*\\n([\\s\\S]*?)(?=\\n##\\s|$)`, "m"));
  return (m?.[1] ?? "").trim();
})();

writeFileSync(join(publicDir, ".release"), `${version}\n`, "utf8");
run("git", ["add", "-A"], { cwd: publicDir });
const staged = quiet("git", ["diff", "--cached", "--quiet"], { cwd: publicDir });
if (staged.ok) {
  say("  바뀐 내용이 없습니다 — 커밋하지 않았습니다.");
  process.exit(0);
}
const message = `v${version}\n\n${notes_md}\n`;
run("git", ["commit", "-q", "-F", "-"], { cwd: publicDir, input: message });
run("git", ["tag", "-f", `v${version}`], { cwd: publicDir });
say(`  커밋 v${version}`);

if (!has("push")) {
  say("\n푸시는 하지 않았습니다. 확인 후:");
  say(`  cd ${publicDir} && git push origin main --tags`);
  process.exit(0);
}
run("git", ["push", "origin", "HEAD", "--tags"], { cwd: publicDir, stdio: "inherit" });
say(`\nv${version} 공개 완료.`);
