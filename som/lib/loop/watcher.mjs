/**
 * The coordinator loop. The ONLY process in this system that calls
 * `orchestration check`.
 *
 * `check` mutates FIFO delivery state: it returns the bound run's oldest
 * delivery (up to 50 messages) and replays that exact batch until it is
 * acknowledged. If two processes call it, deliveries get consumed out from
 * under each other and events are lost silently. So the split is:
 *
 *   watcher (here, a host-managed Monitor)  consumes, emits one line per
 *                                           actionable message, waits for ack
 *   router  (the Claude Code session)       decides, mutates Orca, writes ack
 *
 * What falls out for free: if the router dies mid-batch nothing was acked, so
 * the next session's watcher re-reads the identical batch and re-emits it.
 * Inbound durability needs no separate mechanism.
 *
 * Emission discipline is not cosmetic. Monitors that produce too many events
 * are stopped by the host, and a dead watcher looks exactly like a quiet run.
 * So: one line per actionable message, heartbeats and status never emitted,
 * long silences stay silent.
 */
import { msg, fleet, worker, useRun } from "../orca/commands.mjs";
import { Store } from "../state/store.mjs";

export const ACTIONABLE = ["worker_done", "escalation", "question", "merge_ready"];

/** Messages that prove liveness but are not progress and not completion. */
const NEVER_EMIT = new Set(["heartbeat", "status"]);

/** Dispatch/worker states that mean "no longer running". */
export const SETTLED_STATES = new Set([
  "succeeded", "failed", "stopped", "abandoned", "completed", "circuit_broken",
]);

const WINDOW_MS = 900_000;      // 15 min per check window
const NAG_AFTER_MS = 600_000;   // one nag if the router has not acked
const QUIET_PROBE_EVERY = 3;    // liveness probe every 3rd empty window
const BATCH_INLINE_MAX = 12;    // above this, emit one summary line

/**
 * `payload` arrives as a JSON STRING, not an object -- verified against a live
 * worker_done. Reading `m.payload.taskId` silently yields undefined, which is
 * how the router ends up unable to account for a dispatch it was just told
 * about.
 */
export function parsePayload(m) {
  const p = m?.payload;
  if (!p) return {};
  if (typeof p === "object") return p;
  try {
    return JSON.parse(p);
  } catch {
    return {};
  }
}

function compact(m) {
  const p = parsePayload(m);
  return {
    k: "msg",
    id: m.id,
    type: m.type,
    task: p.taskId ?? m.task_id ?? null,
    dispatch: p.dispatchId ?? m.dispatch_id ?? null,
    outcome: p.outcome ?? m.outcome ?? null,
    files: p.filesModified ?? null,
    report: p.reportPath ?? null,
    from: m.from_handle ?? null,
    subject: String(m.subject ?? "").slice(0, 120),
    body: String(m.body ?? "").slice(0, 300),
  };
}

function tally(messages) {
  const out = {};
  for (const m of messages) out[m.type] = (out[m.type] ?? 0) + 1;
  return out;
}

export class Watcher {
  constructor({ project, emit, log, windowMs = WINDOW_MS } = {}) {
    this.store = new Store(project);
    this.emit = emit ?? ((o) => process.stdout.write(JSON.stringify(o) + "\n"));
    this.log = log ?? (() => {});
    this.windowMs = windowMs;
    this.stopped = false;
    this.lastEmittedDelivery = null;
    this.emptyWindows = 0;
  }

  stop() {
    this.stopped = true;
  }

  /** Read-only liveness probe. Never mutates, never releases. */
  liveness() {
    try {
      const ps = fleet.ps({ limit: 30 });
      const agents = (ps.result?.worktrees ?? ps.result?.rows ?? [])
        .flatMap((w) => w.agents ?? []);
      const working = agents.filter((a) => a.state === "working").length;
      const idle = agents.filter((a) => a.state === "idle").length;
      let openDispatches = 0;
      try {
        // worker-list returns camelCase and has no `state` field: the fields
        // are workerState / dispatchStatus / terminalState.
        const wl = worker.list({});
        openDispatches = (wl.result?.workers ?? [])
          .filter((w) => !SETTLED_STATES.has(
            w.workerState ?? w.dispatchStatus ?? ""))
          .length;
      } catch { /* older host */ }
      return { working, idle, openDispatches };
    } catch (e) {
      return { error: e.code ?? e.constructor.name };
    }
  }

  async waitForAck(deliveryId) {
    const started = Date.now();
    let nagged = false;
    while (!this.stopped) {
      const { ackRequest } = this.store.readAck();
      if (ackRequest === deliveryId) return true;
      if (!nagged && Date.now() - started > NAG_AFTER_MS) {
        nagged = true;
        // One line, once. The router may legitimately be inside a long
        // decision; this is not an error.
        this.emit({
          k: "ack_pending", delivery: deliveryId,
          waited_ms: Date.now() - started,
          note: "delivery emitted but not acknowledged yet; still holding it",
        });
      }
      await new Promise((r) => setTimeout(r, 2_000));
    }
    return false;
  }

  /**
   * One check window, for the in-process orchestrator.
   *
   * `Conduct` drives waves itself and needs the delivery back rather than
   * emitted, so it calls this instead of `run()`. Still the only `check`
   * consumer in that process -- the two entry points are never used at once.
   *
   * Returns {deliveryId, messages} with heartbeats and status stripped, or
   * null on an empty window. An empty window is a checkpoint, not a failure.
   */
  async oneWindow(runId, { ack } = {}) {
    useRun(runId);
    const pending = ack ?? this.store.loop().awaitingAckPending ?? null;
    let env;
    try {
      env = await msg.check({
        ack: pending ?? undefined,
        wait: true,
        types: ACTIONABLE,
        timeoutMs: this.windowMs,
        run: runId,
        onKeepalive: () =>
          this.store.writeLoop({ lastKeepaliveAt: new Date().toISOString() }),
      });
    } catch (e) {
      const code = e.code ?? e.constructor.name;
      this.store.event({ kind: "check_error", code });
      if (code === "stale_delivery") {
        this.store.writeLoop({ awaitingAckPending: null });
        return null;
      }
      throw e;
    }

    const d = env.result ?? {};
    if (d.acknowledged) {
      this.store.writeLoop({ lastAckedDeliveryId: d.acknowledged, awaitingAckPending: null });
    }
    if (d.timedOut || (d.count ?? 0) === 0) {
      this.store.writeLoop({ lastKeepaliveAt: new Date().toISOString() });
      return null;
    }

    const messages = d.messages ?? [];
    this.store.writeLoop({
      awaitingAck: d.deliveryId,
      awaitingAckPending: d.deliveryId,
      lastKeepaliveAt: new Date().toISOString(),
    });
    this.store.event({ kind: "delivery", deliveryId: d.deliveryId, count: d.count });
    return { deliveryId: d.deliveryId, messages, replayed: Boolean(d.replayed) };
  }

  /**
   * @param {string} runId
   * @param {{maxWindows?:number}} opts  maxWindows bounds the loop for tests
   */
  async run(runId, { maxWindows = Infinity } = {}) {
    useRun(runId);
    let pendingAck = this.store.loop().lastAckedDeliveryId ?? null;
    let windows = 0;

    this.store.writeLoop({
      runId, startedAt: new Date().toISOString(),
      lastKeepaliveAt: new Date().toISOString(),
    });

    while (!this.stopped && windows < maxWindows) {
      windows++;
      let env;
      try {
        env = await msg.check({
          ack: pendingAck ?? undefined,
          wait: true,
          types: ACTIONABLE,
          timeoutMs: this.windowMs,
          run: runId,
          onKeepalive: () =>
            this.store.writeLoop({ lastKeepaliveAt: new Date().toISOString() }),
        });
      } catch (e) {
        const code = e.code ?? e.constructor.name;
        this.emit({ k: "check_error", code, message: String(e.message).slice(0, 200) });
        this.store.event({ kind: "check_error", code });
        // stale_delivery means we are acking a delivery from another run:
        // drop the ack and re-read rather than looping on the error.
        if (code === "stale_delivery") {
          pendingAck = null;
          continue;
        }
        if (code === "run_required" || code === "OrcaUnavailable") return { windows, fatal: code };
        await new Promise((r) => setTimeout(r, 5_000));
        continue;
      }

      const d = env.result ?? {};
      if (d.acknowledged) {
        pendingAck = null;
        this.store.writeLoop({ lastAckedDeliveryId: d.acknowledged });
      }

      // A timeout or an empty window is a CHECKPOINT, not a failure. Coding
      // tasks routinely run 15-60 minutes.
      if (d.timedOut || (d.count ?? 0) === 0) {
        this.emptyWindows++;
        this.store.writeLoop({
          lastKeepaliveAt: new Date().toISOString(),
          emptyWindows: this.emptyWindows,
        });
        if (this.emptyWindows % QUIET_PROBE_EVERY === 0) {
          const live = this.liveness();
          // Only speak up when nothing is working yet dispatches are open --
          // that is the shape of a genuinely stuck run.
          if (live.working === 0 && live.openDispatches > 0) {
            this.emit({ k: "liveness_alarm", ...live,
              note: "no worker is working but dispatches are open; inspect, " +
                    "do not kill -- a worker parked on a prompt is healthy" });
          }
        }
        continue;
      }
      this.emptyWindows = 0;

      const messages = d.messages ?? [];
      const deliveryId = d.deliveryId;

      // Replay: the same batch reappears because the router has not acked.
      if (deliveryId && deliveryId === this.lastEmittedDelivery) {
        this.store.writeLoop({ replayed: true, awaitingAck: deliveryId });
        await this.waitForAck(deliveryId);
        pendingAck = deliveryId;
        continue;
      }

      const emitted = messages.filter((m) => !NEVER_EMIT.has(m.type));
      this.store.writeLoop({
        awaitingAck: deliveryId,
        pendingDelivery: { deliveryId, count: d.count, byType: tally(messages) },
        lastKeepaliveAt: new Date().toISOString(),
        replayed: Boolean(d.replayed),
      });
      this.store.event({ kind: "delivery", deliveryId, count: d.count, byType: tally(messages) });

      if (emitted.length === 0) {
        // The whole batch was heartbeats/status. Ack it and stay silent.
        // Nothing for the router to decide: ack on the next window ourselves.
        pendingAck = deliveryId;
        this.lastEmittedDelivery = deliveryId;
        continue;
      }

      if (emitted.length <= BATCH_INLINE_MAX) {
        for (const m of emitted) this.emit(compact(m));
      } else {
        this.emit({
          k: "batch", delivery: deliveryId, count: emitted.length,
          byType: tally(emitted), detail: ".som/loop.json",
        });
      }

      this.lastEmittedDelivery = deliveryId;
      await this.waitForAck(deliveryId);
      pendingAck = deliveryId;
    }
    return { windows, fatal: null };
  }
}
