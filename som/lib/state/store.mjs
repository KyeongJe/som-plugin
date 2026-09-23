/**
 * Run state on disk.
 *
 * Single writer per file, no locks:
 *   state.json  router-owned   the wave plan, role map, intents, decisions
 *   loop.json   watcher-owned  awaitingAck, lastKeepaliveAt, pending delivery
 *   ack.json    router-owned   the ack handshake back to the watcher
 *
 * Every write is tmp -> fsync -> rename, so a crash mid-write leaves the
 * previous version rather than a truncated file.
 *
 * The division of ownership matters more than the format: local state holds
 * ONLY what Orca cannot know. Anything Orca owns (task status, dispatch state,
 * gates, the fleet) is re-derived on every reconcile and never trusted from
 * disk. That invariant is what makes a restart safe.
 */
import {
  closeSync, existsSync, fsyncSync, mkdirSync, openSync, readFileSync,
  renameSync, writeSync, appendFileSync,
} from "node:fs";
import { dirname, join } from "node:path";

export const STATE_DIR = ".som";

export function stateDir(project = process.env.CLAUDE_PROJECT_DIR || process.cwd()) {
  const d = join(project, STATE_DIR);
  mkdirSync(d, { recursive: true });
  return d;
}

function atomicWrite(path, text) {
  mkdirSync(dirname(path), { recursive: true });
  const tmp = `${path}.tmp`;
  const fd = openSync(tmp, "w");
  try {
    writeSync(fd, text);
    fsyncSync(fd);
  } finally {
    closeSync(fd);
  }
  renameSync(tmp, path);
}

function readJson(path, fallback) {
  if (!existsSync(path)) return fallback;
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return fallback;
  }
}

const stable = (o) => JSON.stringify(o, Object.keys(o).sort ? undefined : undefined, 2);

// ------------------------------------------------------------------- state
export const SCHEMA = 1;

export function emptyState(overrides = {}) {
  return {
    schema: SCHEMA,
    runId: null,
    objective: null,
    recipe: null,
    stage: "INTAKE",
    wave: 0,
    startedAt: new Date().toISOString(),
    cwd: process.cwd(),
    orca: {},
    // dagKey -> taskId, so a re-created task can be re-linked
    dagKeys: {},
    tasks: {},
    dispatches: {},
    processedMessages: {},
    intents: {},
    gates: {},
    timeline: [],
    decisions: [],
    ...overrides,
  };
}

export class Store {
  constructor(project) {
    this.dir = stateDir(project);
    this.statePath = join(this.dir, "state.json");
    this.loopPath = join(this.dir, "loop.json");
    this.ackPath = join(this.dir, "ack.json");
    this.eventsPath = join(this.dir, "events.ndjson");
  }

  // --- router-owned -----------------------------------------------------
  load() {
    return readJson(this.statePath, null);
  }
  save(state) {
    atomicWrite(this.statePath, JSON.stringify(state, null, 2) + "\n");
    return state;
  }
  /** Read, mutate, write. Router only. */
  update(fn) {
    const s = this.load() ?? emptyState();
    const out = fn(s) ?? s;
    return this.save(out);
  }

  requestAck(deliveryId) {
    atomicWrite(this.ackPath, JSON.stringify({
      ackRequest: deliveryId, at: new Date().toISOString(),
    }, null, 2) + "\n");
  }
  readAck() {
    return readJson(this.ackPath, {});
  }

  // --- watcher-owned ----------------------------------------------------
  loop() {
    return readJson(this.loopPath, {});
  }
  writeLoop(patch) {
    const cur = this.loop();
    atomicWrite(this.loopPath, JSON.stringify({ ...cur, ...patch }, null, 2) + "\n");
  }

  // --- append-only ------------------------------------------------------
  event(obj) {
    try {
      appendFileSync(this.eventsPath, JSON.stringify({
        ts: new Date().toISOString(), ...obj,
      }) + "\n", "utf8");
    } catch {
      /* losing an audit line must not fail a run */
    }
  }

  /**
   * Read the events back.
   *
   * There was no reader, and that mattered the moment something wanted to
   * learn from a run: `signalsFrom` looked for `state.events`, which has never
   * existed -- events live only in this file. Two of its three signals could
   * therefore never fire, and the unit tests missed it because they built the
   * state object by hand with an `events` array in it.
   *
   * A torn line is skipped rather than thrown. This is an audit trail being
   * read for advice, not a transaction log.
   */
  events() {
    if (!existsSync(this.eventsPath)) return [];
    let raw;
    try { raw = readFileSync(this.eventsPath, "utf8"); } catch { return []; }
    const out = [];
    for (const line of raw.split("\n")) {
      const t = line.trim();
      if (!t) continue;
      try { out.push(JSON.parse(t)); } catch { /* skip a torn line */ }
    }
    return out;
  }

  /** Has the watcher been alive recently? The Stop hook asks this. */
  watcherFresh(maxAgeMs = 90_000) {
    const t = this.loop().lastKeepaliveAt;
    if (!t) return false;
    return Date.now() - Date.parse(t) < maxAgeMs;
  }
}

/**
 * The resume card injected by SessionStart and PostCompact.
 *
 * Deliberately small. Its job is to tell the next session that a run exists
 * and how to pick it up, not to replay it -- Orca is the authority for
 * everything it owns, so the session should reconcile rather than read state.
 */
export function resumeCard(state, loop) {
  if (!state?.runId) return null;
  const dispatches = Object.entries(state.dispatches ?? {});
  const open = dispatches.filter(([, d]) => !d.settled);
  const gates = Object.entries(state.gates ?? {})
    .filter(([, g]) => g.status === "pending");
  const lines = [
    `som run in progress: ${state.runId}`,
    `objective : ${state.objective ?? "(none recorded)"}`,
    `recipe    : ${state.recipe ?? "(none)"}`,
    `stage     : ${state.stage}  wave ${state.wave}`,
    `tasks     : ${Object.keys(state.tasks ?? {}).length} known, ` +
      `${open.length} dispatch(es) still open`,
  ];
  if (gates.length) {
    lines.push(`gates     : ${gates.length} pending -> ` +
      gates.map(([id, g]) => `${id} ${g.question ?? ""}`).join(" | "));
  }
  const fresh = loop?.lastKeepaliveAt
    ? Date.now() - Date.parse(loop.lastKeepaliveAt) < 90_000
    : false;
  lines.push(`watcher   : ${fresh ? "alive" : "NOT alive -- re-arm it"}`);
  lines.push("");
  lines.push("Do not trust the numbers above for anything but orientation. " +
    "Reconcile first: Orca owns task status, dispatch state, gates and the " +
    "fleet, and local state holds only the plan and the decisions.");
  return lines.join("\n");
}
