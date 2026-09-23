/**
 * What to do with each message the watcher hands over.
 *
 * Two rules run through everything here:
 *
 *  1. Task status and terminal ownership are separate. A completed task can
 *     still own a live terminal, so every settled worker_done is followed by
 *     an explicit accounting decision: reuse, retain, or release.
 *  2. Slow is not failed. Timeouts, heartbeats, questions and escalations
 *     never release a terminal and never stop a worker.
 *
 * Idempotency is structural: every message is keyed by id, so a replayed
 * delivery is recognised and skipped for side effects while still counting
 * toward the ack.
 */
import { task, worker, msg } from "../orca/commands.mjs";
import { parsePayload } from "./watcher.mjs";
import { launchFor, globToRegExp } from "../domain/plan.mjs";

/** States that mean a task no longer needs a worker. */
const TASK_SETTLED = new Set(["completed", "failed"]);

export class Router {
  /**
   * @param {object} o
   * @param {import("../state/store.mjs").Store} o.store
   * @param {(q:{question:string,options?:string[],context?:object})=>Promise<string>} o.askHuman
   * @param {(line:string)=>void} [o.say]
   */
  constructor({ store, askHuman, say = () => {}, autonomy = 2 }) {
    this.store = store;
    this.askHuman = askHuman;
    this.say = say;
    this.autonomy = autonomy;
  }

  /** Handle one delivery, then ack it. Returns a summary for the caller. */
  async handleDelivery({ deliveryId, messages }) {
    const summary = { deliveryId, handled: 0, skipped: 0, actions: [] };
    for (const m of messages) {
      const seen = this.store.load()?.processedMessages?.[m.id];
      if (seen) { summary.skipped++; continue; }
      const action = await this.handle(m);
      summary.handled++;
      summary.actions.push(action);
      this.store.update((s) => {
        s.processedMessages[m.id] = { at: new Date().toISOString(), action: action.kind };
        return s;
      });
    }
    // Ack only after every message and every required accounting decision.
    this.store.requestAck(deliveryId);
    return summary;
  }

  async handle(m) {
    switch (m.type) {
      case "worker_done":  return this.onWorkerDone(m);
      case "escalation":   return this.onEscalation(m);
      case "question":     return this.onQuestion(m);
      case "merge_ready":  return this.onMergeReady(m);
      case "heartbeat":    return this.onHeartbeat(m);
      case "status":       return this.onStatus(m);
      default:
        this.store.event({ kind: "unknown_message_type", type: m.type, id: m.id });
        return { kind: "ignored", type: m.type };
    }
  }

  // ------------------------------------------------------------ worker_done
  async onWorkerDone(m) {
    const p = parsePayload(m);
    const state = this.store.load() ?? {};
    const known = state.dispatches?.[p.dispatchId];

    // A worker_done for a dispatch we did not start, or for one already
    // settled, is not accepted and does not release anything.
    if (!p.dispatchId || !known) {
      this.store.event({ kind: "stale_worker_done", id: m.id, dispatch: p.dispatchId });
      this.say(`무시: 알 수 없는 dispatch 의 worker_done (${p.dispatchId ?? "id 없음"})`);
      return { kind: "stale_worker_done", dispatch: p.dispatchId };
    }
    if (known.settled) {
      return { kind: "duplicate_worker_done", dispatch: p.dispatchId };
    }

    // Orca settles the task automatically from the dispatched pane. When pane
    // identity cannot be proven it does not, and the task hangs forever unless
    // we notice. So verify rather than assume.
    let settledByOrca = false;
    try {
      const t = task.list({}).result?.tasks ?? [];
      const row = t.find((x) => x.id === p.taskId);
      settledByOrca = row ? TASK_SETTLED.has(row.status) : false;
      if (!settledByOrca && p.taskId) {
        task.update({
          id: p.taskId,
          status: p.outcome === "failed" ? "failed" : "completed",
          result: { via: "router-fallback", messageId: m.id },
        });
        this.store.event({ kind: "settlement_fallback", task: p.taskId });
      }
    } catch (e) {
      this.store.event({ kind: "settlement_check_failed", code: e.code });
    }

    this.store.update((s) => {
      const d = s.dispatches[p.dispatchId];
      d.settled = true;
      d.outcome = p.outcome ?? null;
      d.filesModified = p.filesModified ?? [];
      d.reportPath = p.reportPath ?? null;
      d.summary = String(m.body ?? "").slice(0, 500);
      d.settledAt = new Date().toISOString();
      const t = s.tasks[p.taskId];
      if (t) t.status = p.outcome === "failed" ? "failed" : "completed";
      s.timeline.push({
        at: new Date().toISOString(), kind: "worker_done",
        task: p.taskId, outcome: p.outcome,
      });
      return s;
    });

    // Declared scope vs what was actually touched. A violation is a real
    // finding: the wave scheduler trusted that glob.
    const declared = state.tasks?.[p.taskId]?.writes ?? [];
    const violations = this.checkWrites(declared, p.filesModified ?? []);
    if (violations.length) {
      this.store.event({ kind: "writes_violation", task: p.taskId, violations });
      // Written onto the dispatch too, not only announced and returned. The
      // line above scrolled past in the terminal and the finding died with it:
      // the report read `d.violations`, `signalsFrom` read `d.violations`, and
      // nothing ever put it there. A run that actually broke its write scope
      // still finished with a clean record.
      this.store.update((s) => {
        const d = s.dispatches[p.dispatchId];
        if (d) d.violations = violations;
        return s;
      });
      this.say(`주의: ${state.tasks?.[p.taskId]?.key ?? p.taskId} 가 선언 범위 밖 파일을 ` +
               `건드렸습니다 — ${violations.slice(0, 3).join(", ")}`);
    }

    const accounting = await this.account(p.dispatchId);
    this.say(`완료: ${state.tasks?.[p.taskId]?.key ?? p.taskId} ` +
             `(${p.outcome ?? "outcome 없음"}) · 터미널 ${accounting}`);
    return {
      kind: "worker_done", task: p.taskId, dispatch: p.dispatchId,
      outcome: p.outcome, accounting, violations, settledByOrca,
    };
  }

  /**
   * Files touched that no declared glob covers.
   *
   * The wave scheduler trusted those globs to decide what could run beside
   * this task, so a violation is not a style note -- it means the isolation
   * argument for that wave was wrong.
   */
  checkWrites(declared, actual) {
    if (!declared.length || !actual.length) return [];
    const cwd = process.cwd().replace(/\\/g, "/");
    const rel = (p) => {
      const s = String(p).replace(/\\/g, "/");
      return s.toLowerCase().startsWith(cwd.toLowerCase())
        ? s.slice(cwd.length).replace(/^\//, "")
        : s;
    };
    const res = declared.map((g) =>
      globToRegExp(String(g).replace(/\\/g, "/").replace(/^\.\//, "")));
    return actual.map(rel).filter((f) => !res.some((re) => re.test(f)));
  }

  /**
   * Terminal accounting. Mandatory after every settled worker_done, before ack.
   * Not doing it leaks a live agent terminal per task.
   */
  async account(dispatchId) {
    const s = this.store.load() ?? {};
    const next = s.reuseQueue?.[dispatchId];
    try {
      if (next) {
        // Reuse transfers cleanup ownership to the new dispatch. Only valid
        // for a same-role follow-up: --terminal cannot carry model/effort.
        const handle = worker.show({ dispatch: dispatchId })
          .result?.worker?.agentTerminalHandle;
        if (handle) {
          worker.start({ task: next, terminal: handle });
          this.mark(dispatchId, "reused");
          return "reused";
        }
      }
      if (s.keepAlive?.[dispatchId]) {
        worker.retain({ dispatch: dispatchId });
        this.mark(dispatchId, "retained");
        return "retained";
      }
      const r = worker.release({ dispatch: dispatchId });
      const st = r.result?.releaseState ?? "released";
      this.mark(dispatchId, st);
      return st;
    } catch (e) {
      // release_unknown is the only exit-1 case. Never substitute terminal
      // close: follow the receipt's own recovery text.
      const code = e.code ?? "unknown";
      this.mark(dispatchId, code === "already_released" ? "already_released" : "leaked");
      if (code !== "already_released") {
        this.say(`터미널 정리 확인 필요 (${code}). Orca 안내: ` +
                 `${(e.nextSteps ?? []).join(" | ") || "receipt 참조"}`);
      }
      return code;
    }
  }

  mark(dispatchId, accounting) {
    this.store.update((s) => {
      if (s.dispatches[dispatchId]) s.dispatches[dispatchId].accounting = accounting;
      return s;
    });
  }

  // ------------------------------------------------------------- escalation
  async onEscalation(m) {
    const p = parsePayload(m);
    // Never release on an escalation: the worker is alive and blocked.
    let context = "";
    try {
      const r = worker.read({ dispatch: p.dispatchId, limit: 60 });
      context = String(r.result?.text ?? r.result?.output ?? "").slice(-2000);
    } catch { /* older host, or unprovable identity */ }

    this.store.event({ kind: "escalation", task: p.taskId, dispatch: p.dispatchId });
    this.say(`막힘: ${m.subject ?? ""}`);

    const answer = await this.askHuman({
      question: `워커가 막혔습니다: ${m.subject ?? ""}\n${String(m.body ?? "").slice(0, 400)}`,
      options: ["지침을 주고 계속", "이 태스크를 blocked 로 두고 진행", "런 중단"],
      context: { task: p.taskId, dispatch: p.dispatchId, tail: context },
    });

    if (answer?.startsWith("지침")) {
      msg.send({
        to: `dispatch:${p.dispatchId}`, type: "status",
        subject: "guidance", body: answer,
      });
      return { kind: "escalation_guided", dispatch: p.dispatchId };
    }
    if (answer?.startsWith("이 태스크")) {
      if (p.taskId) task.update({ id: p.taskId, status: "blocked" });
      return { kind: "escalation_blocked", task: p.taskId };
    }
    return { kind: "escalation_stop", dispatch: p.dispatchId };
  }

  // --------------------------------------------------------------- question
  async onQuestion(m) {
    // A worker is blocked on this. It must be answered before the ack.
    const p = parsePayload(m);

    // Whether this reaches a human is exactly what the level is for, and it
    // was never consulted -- every question went to the person regardless, so
    // the published row "워커 질문 답변 · 사람 / 사람 / 스펙 내 자동 ..." was
    // describing something with no code behind it.
    const scoped = p.scopeChange === true ? "answer.scope-change" : "answer.question";
    const may = this.autonomy?.decide?.(scoped) ?? { verdict: "gate" };
    if (may.verdict === "auto") {
      const body = this.answerFromSpec(p);
      if (body) {
        msg.reply({ id: m.id, body });
        this.store.event({ kind: "question_auto_answered", id: m.id, scoped });
        return { kind: "question_answered", id: m.id, auto: true };
      }
      // The spec does not obviously cover it, so it goes to a person after
      // all. A level is permission to answer from the spec, not permission to
      // make something up.
    }

    const answer = await this.askHuman({
      question: `워커 질문: ${m.subject ?? ""}\n${String(m.body ?? "").slice(0, 400)}`,
      options: p.options ?? undefined,
      context: { task: p.taskId, dispatch: p.dispatchId },
    });
    msg.reply({ id: m.id, body: answer ?? "판단은 워커에게 맡깁니다. 보수적으로 진행하세요." });
    this.store.event({ kind: "question_answered", id: m.id });
    return { kind: "question_answered", id: m.id };
  }

  /**
   * What the task spec already answers, for the levels allowed to reply
   * without a human.
   *
   * Deliberately thin. It restates the acceptance condition and the declared
   * write scope and stops there; if there is no acceptance recorded it returns
   * nothing and the question goes to a person. An autonomy level is permission
   * to answer from the spec, never permission to invent an answer.
   */
  answerFromSpec(p) {
    const t = this.store.load()?.tasks?.[p.taskId];
    if (!t?.acceptance) return null;
    return `이 태스크의 완료 조건: ${t.acceptance}\n` +
           `선언된 쓰기 범위: ${(t.writes ?? []).join(", ") || "(없음)"}\n` +
           "이 안에서 판단해 진행하고, 범위를 벗어나야 하면 다시 물어보세요.";
  }

  // ------------------------------------------------------------ merge_ready
  onMergeReady(m) {
    const p = parsePayload(m);
    this.store.update((s) => {
      s.mergeQueue = s.mergeQueue ?? [];
      s.mergeQueue.push({ task: p.taskId, dispatch: p.dispatchId, at: new Date().toISOString() });
      return s;
    });
    // Do not release: the work is ready to integrate, not finished with.
    return { kind: "merge_queued", task: p.taskId };
  }

  // --------------------------------------------------- liveness only, silent
  onHeartbeat(m) {
    const p = parsePayload(m);
    this.store.update((s) => {
      const d = s.dispatches[p.dispatchId];
      if (d) { d.lastHeartbeatAt = new Date().toISOString(); d.phase = p.phase ?? null; }
      return s;
    });
    return { kind: "heartbeat", dispatch: p.dispatchId };
  }

  onStatus(m) {
    this.store.update((s) => {
      s.timeline.push({
        at: new Date().toISOString(), kind: "status",
        subject: String(m.subject ?? "").slice(0, 160),
      });
      return s;
    });
    return { kind: "status" };
  }

  // ------------------------------------------------------- circuit breaker
  /**
   * Orca circuit-breaks a task after three consecutive failures. Attempt three
   * must therefore change something material -- a smaller spec, or one step
   * more model -- and once Orca marks it failed we create a NEW task rather
   * than forcing the old one back to ready.
   */
  repairPlan(node, attempts) {
    if (attempts >= 3) {
      return {
        action: "redecompose",
        why: "Orca circuit-breaks at three consecutive failures. The next " +
             "attempt needs a smaller task, not the same one again.",
      };
    }
    return { action: "retry", launch: launchFor(node.role, { attempt: attempts + 1 }) };
  }
}
