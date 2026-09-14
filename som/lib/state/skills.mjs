/**
 * Writing generated skills into the person's own skills directory.
 *
 * This is the one place in the plugin that writes outside its own data area,
 * and it is a deliberate exception rather than a loosening. `edit.user-config`
 * is a hard floor -- `~/.claude/settings.json`, `CLAUDE.md`, plugin settings --
 * and that floor does not move. A new skill directory is a different thing: it
 * is additive, it is namespaced, it is inert until Claude reads it, and
 * deleting the folder undoes it completely.
 *
 * Four rules make that true, and each is enforced here rather than promised:
 *
 *   1. **`som-` prefix, always.** A generated skill cannot be named anything
 *      else, so it can never shadow one the person wrote.
 *   2. **The marker is the licence.** Nothing is overwritten or removed unless
 *      its SKILL.md carries `som-generated: true`. A hand-written file at a
 *      colliding path is left exactly as it is and reported.
 *   3. **Nothing but SKILL.md.** One file per directory. No scripts, no
 *      references, nothing executable.
 *   4. **Never silently.** Every write is returned to the caller so the skill
 *      can show it before it happens; `--yes` is the caller's decision, not
 *      this module's default.
 */
import {
  closeSync, existsSync, fsyncSync, mkdirSync, openSync, readFileSync,
  readdirSync, renameSync, rmSync, statSync, unlinkSync, writeSync,
} from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { randomBytes } from "node:crypto";

import { PROMOTE, clusterPatterns, promotable, renderSkill } from "../domain/promote.mjs";

const MARKER = "som-generated: true";

/**
 * Where Claude Code loads a user's own skills from.
 *
 * `SOM_SKILLS_DIR` exists so the tests never touch a real home directory --
 * a test suite that writes into `~/.claude/skills` would be a worse bug than
 * anything it could catch.
 */
export function skillsDir() {
  return process.env.SOM_SKILLS_DIR ?? join(homedir(), ".claude", "skills");
}

function atomicWrite(path, text) {
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

/** Read one generated skill, or null when the path is not ours to touch. */
function readOne(dir, name) {
  const file = join(dir, name, "SKILL.md");
  if (!existsSync(file)) return null;
  let text;
  try { text = readFileSync(file, "utf8"); } catch { return null; }
  const ours = text.includes(MARKER);
  const sources = (text.match(/^som-sources:\s*\[(.*)\]\s*$/m)?.[1] ?? "")
    .split(",").map((s) => s.trim()).filter(Boolean);
  const confidence = Number(text.match(/^som-confidence:\s*(\d+)\s*$/m)?.[1] ?? 0);
  const at = text.match(/^som-generated-at:\s*(\S+)\s*$/m)?.[1] ?? null;
  const description = text.match(/^description:\s*(.*)$/m)?.[1] ?? "";
  return { name, file, ours, sources, confidence, generatedAt: at, description };
}

export class SkillWriter {
  constructor(dir = skillsDir()) {
    this.dir = dir;
  }

  /** Every `som-` directory, marked with whether this plugin may touch it. */
  list() {
    if (!existsSync(this.dir)) return [];
    let names;
    try { names = readdirSync(this.dir); } catch { return []; }
    const out = [];
    for (const name of names.sort()) {
      if (!name.startsWith("som-")) continue;
      try { if (!statSync(join(this.dir, name)).isDirectory()) continue; } catch { continue; }
      const one = readOne(this.dir, name);
      if (one) out.push(one);
    }
    return out;
  }

  /** Generated skills only -- the ones that count against the cap. */
  mine() {
    return this.list().filter((s) => s.ours);
  }

  /**
   * What `promote` would do, without doing any of it.
   *
   * The skill shows this to the person first. A feature that writes into
   * someone's home directory and then tells them is the wrong order.
   */
  plan(patterns, { cap = PROMOTE.maxSkills } = {}) {
    const existing = this.mine().map((s) => s.name);
    const clusters = clusterPatterns(patterns);
    const { ready, held } = promotable(clusters, { existing, cap });
    return {
      dir: this.dir,
      existing,
      ready: ready.map((c) => ({ cluster: c, ...renderSkill(c) })),
      held,
    };
  }

  /**
   * Write one rendered skill. Returns what happened; throws only on IO.
   *
   * A collision with a file this plugin did not generate is not an error and
   * not an overwrite -- it is reported and skipped. That is the marker rule,
   * and it is the reason this is safe to run unattended.
   */
  write({ name, text }, { update = false } = {}) {
    if (!name?.startsWith("som-")) {
      return { name, written: false, reason: "생성 스킬 이름은 som- 으로 시작해야 합니다" };
    }
    const target = join(this.dir, name);
    const file = join(target, "SKILL.md");
    if (existsSync(file)) {
      const cur = readOne(this.dir, name);
      if (!cur?.ours) {
        return { name, written: false, file,
                 reason: "직접 만드신 스킬이 같은 이름으로 있습니다 — 건드리지 않았습니다" };
      }
      if (!update) {
        return { name, written: false, file, reason: "이미 있습니다 (--update 로 갱신)" };
      }
    }
    mkdirSync(target, { recursive: true });
    atomicWrite(file, text);
    return { name, written: true, file };
  }

  /**
   * Remove a generated skill. Refuses anything without the marker.
   *
   * `rmSync` with recursive is pointed at a path this module constructed from
   * a validated `som-` name inside its own directory, and only after reading a
   * SKILL.md that says we wrote it.
   */
  retire(name) {
    if (!name?.startsWith("som-") || name.includes("..") ||
        name.includes("/") || name.includes("\\")) {
      return { name, removed: false, reason: "이름이 올바르지 않습니다" };
    }
    const cur = readOne(this.dir, name);
    if (!cur) return { name, removed: false, reason: "그런 스킬이 없습니다" };
    if (!cur.ours) {
      return { name, removed: false,
               reason: "som 이 만든 스킬이 아닙니다 — 직접 지우셔야 합니다" };
    }
    rmSync(join(this.dir, name), { recursive: true, force: true });
    return { name, removed: true, file: cur.file };
  }
}
