/**
 * The pattern library on disk.
 *
 * Two scopes, and the split matters:
 *
 *   user     ~/.claude/plugins/data/som-som-marketplace/patterns.json
 *            What this operator has learned about working this way. Follows
 *            the person between projects, like the autonomy ledger.
 *
 *   project  <project>/.som/patterns.json
 *            What this repo taught. Travels with the repo, outranks user
 *            scope on a tie, and is the one worth committing.
 *
 * Evidence is verified against the filesystem here rather than in the domain
 * module, so the arithmetic stays testable without a disk.
 */
import {
  closeSync, existsSync, fsyncSync, mkdirSync, openSync, readFileSync,
  renameSync, statSync, unlinkSync, writeSync,
} from "node:fs";
import { randomBytes } from "node:crypto";
import { dirname, join, isAbsolute, resolve, sep } from "node:path";
import { homedir } from "node:os";

import { STATE_DIR } from "./store.mjs";
import {
  applyOutcome, matchPatterns, newPattern, patternHash, renderForBrief,
  validatePattern, PATTERN_SCHEMA, TRUSTED_AT,
} from "../domain/patterns.mjs";

/**
 * Where the operator-scope library lives.
 *
 * Resolved, not created: this runs in the `Conduct` constructor, i.e. on every
 * command including read-only ones, and an unguarded mkdir there turned a
 * misconfigured `CLAUDE_PLUGIN_DATA` into a throw that broke every command.
 * Creation happens in atomicWrite, where a failure has somewhere to go.
 */
function userDir() {
  return process.env.CLAUDE_PLUGIN_DATA ??
    join(homedir(), ".claude", "plugins", "data", "som-som-marketplace");
}

function readJson(p, fallback) {
  if (!existsSync(p)) return fallback;
  try { return JSON.parse(readFileSync(p, "utf8")); } catch { return fallback; }
}

/**
 * tmp -> fsync -> rename, with a per-writer tmp name.
 *
 * The tmp path used to be a fixed `<file>.tmp`, shared by every writer. Two
 * processes proposing at once crashed with ENOENT/EPERM out of `renameSync` and
 * lost up to 47 of 50 writes -- one had already renamed the file the other was
 * still holding. A unique name plus a short retry removes the collision and
 * survives the transient Windows lock that AV scanners cause.
 *
 * Lost updates are still possible: this is read-modify-write with no lock. The
 * library is advisory, so a dropped pattern is a nuisance rather than
 * corruption, and the file is never left half-written.
 */
function atomicWrite(path, text) {
  mkdirSync(dirname(path), { recursive: true });
  const tmp = `${path}.${process.pid}.${randomBytes(4).toString("hex")}.tmp`;
  const fd = openSync(tmp, "w");
  try { writeSync(fd, text); fsyncSync(fd); } finally { closeSync(fd); }
  let lastErr;
  for (let i = 0; i < 4; i += 1) {
    try { renameSync(tmp, path); return; } catch (e) { lastErr = e; }
  }
  try { unlinkSync(tmp); } catch { /* nothing else to try */ }
  throw lastErr;
}

const SCOPES = ["project", "user"];

export class PatternLibrary {
  constructor(project = process.cwd()) {
    this.project = project;
    // Paths only. `stateDir()` mkdirs, and this constructor runs on every
    // command including read-only ones -- so it created `.som/` just to list
    // patterns, and threw outright when the path was not writable, taking
    // every command with it. Directories are created in atomicWrite, where a
    // failure has somewhere to go.
    this.paths = {
      user: join(userDir(), "patterns.json"),
      project: join(project, STATE_DIR, "patterns.json"),
    };
  }

  /** Validate the scope name rather than indexing with it blindly. */
  pathFor(scope) {
    if (!SCOPES.includes(scope)) {
      throw new Error(`알 수 없는 scope ${JSON.stringify(scope)} — ` +
                      `${SCOPES.join(" 또는 ")} 중 하나여야 합니다.`);
    }
    return this.paths[scope];
  }

  load(scope) {
    const f = readJson(this.pathFor(scope), null);
    // A hand-edited file can put anything here. Everything downstream assumed
    // an array and threw a raw TypeError out of load/match/brief/stats.
    const list = Array.isArray(f?.patterns) ? f.patterns : [];
    return list
      .filter((p) => p && typeof p === "object")
      .map((p) => ({ ...p, scope }));
  }

  /** Everything, both scopes, project first. */
  all() {
    return [...this.load("project"), ...this.load("user")];
  }

  saveScope(scope, patterns) {
    atomicWrite(this.pathFor(scope), JSON.stringify({
      schema: PATTERN_SCHEMA,
      updatedAt: new Date().toISOString(),
      patterns: patterns.map(({ scope: _s, ...rest }) => rest),
    }, null, 2) + "\n");
  }

  /**
   * Does this evidence anchor point at something real?
   *
   * A run id has to be in this project's state, a file has to be on disk.
   * Measurements are taken on trust because they carry their own numbers, but
   * they still cannot be the *only* anchor -- that is enforced by requiring at
   * least one checkable kind below.
   */
  evidenceExists(e) {
    const kind = String(e?.kind ?? "");
    const ref = String(e?.ref ?? "").trim();
    if (!ref) return false;

    if (kind === "file" || kind === "test") {
      // Strip a trailing :line, then require a real file inside the project.
      // `.`, `..`, `lib`, `C:/Windows` and `../../etc/passwd` all passed the
      // earlier check -- "a path that exists" is not evidence of anything.
      const bare = ref.replace(/:\d+(:\d+)?$/, "");
      const abs = isAbsolute(bare) ? resolve(bare) : resolve(this.project, bare);
      const root = resolve(this.project);
      if (abs === root) return false;
      if (!abs.startsWith(root + sep)) return false;      // no escaping upward
      try { return statSync(abs).isFile(); } catch { return false; }
    }

    if (kind === "run") {
      // Must be a run this project actually recorded. The shape check that
      // used to back-stop this accepted `run_deadbeef` and anything else
      // matching the pattern, and the runs-index branch beside it read a
      // file nothing in the repo writes.
      const st = readJson(join(this.project, STATE_DIR, "state.json"), null);
      if (st?.runId === ref) return true;
      const seen = readJson(join(this.project, STATE_DIR, "runs.json"), null);
      return Array.isArray(seen?.runs) && seen.runs.includes(ref);
    }

    // A measurement carries its own numbers but cannot stand alone; propose()
    // requires at least one checkable anchor beside it.
    if (kind === "measurement") return true;
    return false;
  }

  /**
   * Add a pattern, or refuse with reasons.
   *
   * Refusing is the common case and it is not an error condition -- most
   * candidate lessons are proverbs. The reasons go back to the caller so the
   * skill can say what was missing instead of silently dropping it.
   */
  propose(fields, { scope = "project" } = {}) {
    const p = newPattern({ ...fields, scope });
    const problems = validatePattern(p, { evidenceExists: (e) => this.evidenceExists(e) });

    // At least one anchor that can actually be checked. A measurement alone is
    // a number with nothing behind it.
    const checkable = (p.evidence ?? []).some(
      (e) => ["run", "file", "test"].includes(String(e?.kind)));
    if (!checkable && !problems.some((x) => x.startsWith("evidence 가 없습니다"))) {
      problems.push("evidence 에 run · file · test 중 하나가 필요합니다 " +
                    "(measurement 만으로는 확인할 수 없습니다).");
    }
    if (problems.length) return { ok: false, problems, pattern: p };

    const sync = scope === "project" ? this.syncWarning() : null;

    const existing = this.load(scope);
    const dup = existing.find((x) => x.id === p.id || patternHash(x) === p.id);
    if (dup?.retired) {
      // Reporting a merge here was a lie: the pattern is not in the match set
      // and no amount of re-confirmation put it back, so the caller was told
      // "저장됨, 신뢰도 올림" about something that would never be injected.
      return {
        ok: false, retiredDuplicate: true, pattern: dup,
        problems: [
          `이 패턴은 내려가 있습니다 (${dup.retiredReason ?? "사유 미기록"}). ` +
          `같은 내용을 다시 저장해도 주입되지 않습니다. 정말 되살릴 값이 있으면 ` +
          `무엇이 달라졌는지 action 에 반영해 새 패턴으로 내세요.`,
        ],
      };
    }
    if (dup) {
      // Not a failure. The same lesson arriving twice is confirmation, so it
      // counts as a win on the pattern already there.
      const merged = applyOutcome(dup, "win");
      merged.evidence = dedupeEvidence([...(dup.evidence ?? []), ...(p.evidence ?? [])]);
      this.saveScope(scope, existing.map((x) => (x.id === dup.id ? merged : x)));
      return { ok: true, merged: true, pattern: merged, problems: [], warning: sync };
    }
    this.saveScope(scope, [...existing, p]);
    return { ok: true, merged: false, pattern: p, problems: [], warning: sync };
  }

  /**
   * Is the project-scope library sitting in a synced tree?
   *
   * `engine/somsql/conn.py` refuses a private key inside a OneDrive or
   * SharePoint path, and `doctor.py` warns when the project itself is in one.
   * The pattern library had no equivalent, and it is durable text that a
   * mistake can put a credential into -- in a tree that keeps previous
   * versions. Reported rather than refused: the operator's working directory
   * is genuinely there and blocking the feature would be worse.
   */
  syncWarning() {
    const p = String(this.paths.project);
    if (!/OneDrive|SharePoint|Dropbox|Google Drive/i.test(p)) return null;
    return [
      `패턴 라이브러리가 동기 폴더 안에 있습니다: ${p}`,
      "  두 머신에서 동시에 쓰면 갱신이 유실될 수 있고, 실수로 들어간 값은",
      "  이전 버전으로도 남습니다.",
      "  자격증명은 저장 시점에 거부하지만 정규식이 이기지 못하는 형태도",
      "  있습니다 (base64, 헤더 없는 키 본문).",
    ].join("\n");
  }

  /** Patterns worth putting in front of a worker for this piece of work. */
  match(context, opts = {}) {
    return matchPatterns(this.all(), context, opts);
  }

  brief(context, opts = {}) {
    return renderForBrief(this.match(context, opts));
  }

  /**
   * Record how a run that carried these patterns turned out.
   *
   * Called from finish(). The whole library is only self-pruning if this is
   * wired to real outcomes rather than to someone remembering to grade it.
   */
  recordOutcome(ids, outcome) {
    const touched = [];
    for (const scope of ["project", "user"]) {
      const list = this.load(scope);
      let changed = false;
      const next = list.map((p) => {
        // A retired pattern is never injected, so it is never graded again.
        // Grading it further would just drive a number nobody reads.
        if (!ids.includes(p.id) || p.retired) return p;
        changed = true;
        const u = applyOutcome(p, outcome);
        touched.push(u);
        return u;
      });
      if (changed) this.saveScope(scope, next);
    }
    return touched;
  }

  retire(id, reason) {
    for (const scope of ["project", "user"]) {
      const list = this.load(scope);
      const hit = list.find((p) => p.id === id);
      if (!hit) continue;
      this.saveScope(scope, list.map((p) => (p.id === id
        ? { ...p, retired: true, retiredReason: reason ?? "사람이 내렸습니다." }
        : p)));
      return true;
    }
    return false;
  }

  stats() {
    const all = this.all();
    const live = all.filter((p) => !p.retired);
    return {
      total: all.length,
      live: live.length,
      retired: all.length - live.length,
      trusted: live.filter((p) => p.confidence >= TRUSTED_AT).length,
      byScope: {
        project: all.filter((p) => p.scope === "project").length,
        user: all.filter((p) => p.scope === "user").length,
      },
      uses: all.reduce((a, p) => a + (p.uses ?? 0), 0),
      losses: all.reduce((a, p) => a + (p.losses ?? 0), 0),
    };
  }
}

function dedupeEvidence(list) {
  const seen = new Set();
  const out = [];
  for (const e of list) {
    const k = `${e?.kind}:${e?.ref}`;
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(e);
  }
  return out.slice(0, 8);
}
