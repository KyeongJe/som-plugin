/**
 * Typed wrappers for the Orca orchestration surface.
 *
 * Callers never build argv. Every invariant that is easy to get wrong is
 * enforced here so it becomes unreachable rather than documented:
 *
 *   - `ask --options` is CSV; `gate-create --options` is a JSON array. Callers
 *     pass arrays to both, and the asymmetry disappears.
 *   - `--effort` requires `--model`, and neither combines with `--terminal`.
 *   - creation flags (name/repo/baseBranch/setup) are invalid with an existing
 *     worktree, including `current`.
 *   - `worker_done` requires `--outcome`, and neither `worker_done` nor
 *     `heartbeat` may target a group address.
 *   - `--run` is injected only on the commands that accept it. Verified from
 *     `orca agent-context --json`, not assumed: gate-create, gate-resolve and
 *     the worker lifecycle commands (release/retain/stop/abandon/show/read)
 *     have NO --run flag, so a bound coordinator terminal is REQUIRED, not
 *     optional. `withRun` on an unsupporting command is an invalid_argument
 *     error, which is how this was found.
 *
 * One file rather than seven modules: the invariants above cross-reference each
 * other, and splitting them made the rules harder to see than the code.
 */
import { orcaJson, orcaJsonSync, orcaJsonWait } from "./exec.mjs";

const GROUP_ADDR = /^@/;

function flags(pairs) {
  const out = [];
  for (const [k, v] of pairs) {
    if (v === undefined || v === null || v === false) continue;
    if (v === true) {
      out.push(k);
      continue;
    }
    out.push(k, String(v));
  }
  return out;
}

/**
 * Which orchestration commands accept `--run`. Read out of
 * `orca agent-context --json` on 2026-09-09 and pinned here **by hand**.
 *
 * Nothing checks it. This comment used to claim "a test asserts this table
 * still matches the live CLI" -- there was no such test, and CI runs without
 * Orca installed, so there could not be. If the CLI changes which commands
 * take `--run`, the first sign is an `invalid_argument` at run time, not a red
 * build. Re-read it with `orca agent-context --json` when Orca is upgraded.
 *
 * The commands NOT in this set need a bound coordinator terminal. That is why
 * `run.use()` is mandatory before gates or worker lifecycle calls.
 */
export const RUN_FLAG_SUPPORTED = new Set([
  "ask", "check", "dispatch", "gate-list", "reply", "send",
  "task-create", "task-list", "task-update", "worker-list", "worker-start",
]);

let RUN_ID = null;
export function useRun(id) {
  RUN_ID = id || null;
  return RUN_ID;
}
/** Append --run only where the command supports it. */
function withRun(argv, run) {
  const sub = argv[0] === "orchestration" ? argv[1] : null;
  if (!sub || !RUN_FLAG_SUPPORTED.has(sub)) return argv;
  const id = run ?? RUN_ID;
  return id ? [...argv, "--run", id] : argv;
}

// ---------------------------------------------------------------------- run
export const run = {
  create({ objective, from, retryRequest } = {}) {
    if (!objective) throw new TypeError("run.create needs an objective");
    return orcaJsonSync([
      "orchestration", "run-create",
      ...flags([["--objective", objective], ["--from", from],
                ["--retry-request", retryRequest]]),
    ]);
  },
  use({ id, takeoverLegacy, from } = {}) {
    if (!id) throw new TypeError("run.use needs an id");
    const env = orcaJsonSync([
      "orchestration", "run-use",
      ...flags([["--id", id], ["--takeover-legacy", takeoverLegacy],
                ["--from", from]]),
    ]);
    useRun(id);
    return env;
  },
  current({ from } = {}) {
    return orcaJsonSync(["orchestration", "run-current", ...flags([["--from", from]])]);
  },
  list({ limit = 25 } = {}) {
    return orcaJsonSync(["orchestration", "run-list", "--limit", String(limit)]);
  },
  show({ id }) {
    return orcaJsonSync(["orchestration", "run-show", "--id", id]);
  },
};

// --------------------------------------------------------------------- task
export const task = {
  create({ spec, title, displayName, deps, parent, run: r, from, retryRequest } = {}) {
    if (!spec) throw new TypeError("task.create needs a spec");
    if (deps !== undefined && !Array.isArray(deps)) {
      throw new TypeError("task.create deps must be an array of task ids");
    }
    return orcaJsonSync(withRun([
      "orchestration", "task-create",
      ...flags([
        ["--spec", spec],
        ["--task-title", title],
        ["--display-name", displayName],
        // --deps takes a JSON array; the wrapper owns the encoding.
        ["--deps", deps && deps.length ? JSON.stringify(deps) : undefined],
        ["--parent", parent],
        ["--from", from],
        ["--retry-request", retryRequest],
      ]),
    ], r));
  },
  list({ status, ready, brief, run: r, from } = {}) {
    return orcaJsonSync(withRun([
      "orchestration", "task-list",
      ...flags([["--status", status], ["--ready", ready], ["--brief", brief],
                ["--from", from]]),
    ], r));
  },
  update({ id, status, result, run: r, from } = {}) {
    if (!id || !status) throw new TypeError("task.update needs id and status");
    return orcaJsonSync(withRun([
      "orchestration", "task-update",
      ...flags([["--id", id], ["--status", status],
                ["--result", result ? JSON.stringify(result) : undefined],
                ["--from", from]]),
    ], r));
  },
};

// ------------------------------------------------------------------- worker
export const worker = {
  /**
   * Preferred over `dispatch --inject`: only worker-start creates a
   * worker_dispatches row, and therefore real supervision, stop and release.
   */
  start({
    task: taskId, worktree = "current", agent, terminal, model, effort,
    name, repo, baseBranch, displayName, comment, setup, on, retryOf,
    timeoutMs, run: r, from, retryRequest,
  } = {}) {
    if (!taskId) throw new TypeError("worker.start needs a task id");
    if (!agent && !terminal) {
      throw new TypeError("worker.start needs either agent or terminal");
    }
    if (agent && terminal) {
      throw new TypeError("worker.start takes agent or terminal, not both");
    }
    if (effort && !model) {
      throw new TypeError("--effort requires --model");
    }
    if (terminal && (model || effort)) {
      throw new TypeError(
        "model/effort cannot combine with --terminal. Reuse a terminal only " +
        "for a same-role follow-up, otherwise the model choice is silently lost.",
      );
    }
    const creationFlags = { name, repo, baseBranch, displayName, comment, setup };
    const usedCreation = Object.entries(creationFlags)
      .filter(([, v]) => v !== undefined && v !== null)
      .map(([k]) => k);
    const isExisting = terminal || worktree === "current" ||
      /^(id|name|path|branch|issue|identity):/.test(String(worktree)) ||
      worktree === "active";
    if (isExisting && usedCreation.length) {
      throw new TypeError(
        `creation flags ${usedCreation.join(", ")} are invalid with an existing ` +
        `worktree (${terminal ? "--terminal" : worktree}). Orca rejects them; ` +
        `failing here gives a better message.`,
      );
    }
    return orcaJsonSync(withRun([
      "orchestration", "worker-start",
      ...flags([
        ["--task", taskId],
        ["--worktree", terminal ? undefined : worktree],
        ["--agent", agent],
        ["--terminal", terminal],
        ["--model", model],
        ["--effort", effort],
        ["--name", name],
        ["--repo", repo],
        ["--base-branch", baseBranch],
        ["--display-name", displayName],
        ["--comment", comment],
        ["--setup", setup],
        ["--on", on],
        ["--retry-of", retryOf],
        ["--timeout-ms", timeoutMs],
        ["--from", from],
        ["--retry-request", retryRequest],
      ]),
    ], r), { timeoutMs: 240_000 });
  },
  show({ dispatch, run: r } = {}) {
    return orcaJsonSync(withRun(["orchestration", "worker-show", "--dispatch", dispatch], r));
  },
  read({ dispatch, source = "auto", cursor, limit = 50, run: r } = {}) {
    return orcaJsonSync(withRun([
      "orchestration", "worker-read",
      ...flags([["--dispatch", dispatch], ["--source", source],
                ["--cursor", cursor], ["--limit", limit]]),
    ], r));
  },
  list({ terminalState, run: r } = {}) {
    return orcaJsonSync(withRun([
      "orchestration", "worker-list",
      ...flags([["--terminal-state", terminalState]]),
    ], r));
  },
  release({ dispatch, run: r, retryRequest } = {}) {
    return orcaJsonSync(withRun([
      "orchestration", "worker-release",
      ...flags([["--dispatch", dispatch], ["--retry-request", retryRequest]]),
    ], r));
  },
  retain({ dispatch, run: r, retryRequest } = {}) {
    return orcaJsonSync(withRun([
      "orchestration", "worker-retain",
      ...flags([["--dispatch", dispatch], ["--retry-request", retryRequest]]),
    ], r));
  },
  stop({ dispatch, run: r, retryRequest } = {}) {
    return orcaJsonSync(withRun([
      "orchestration", "worker-stop",
      ...flags([["--dispatch", dispatch], ["--retry-request", retryRequest]]),
    ], r));
  },
  abandon({ dispatch, run: r, retryRequest } = {}) {
    return orcaJsonSync(withRun([
      "orchestration", "worker-abandon",
      ...flags([["--dispatch", dispatch], ["--retry-request", retryRequest]]),
    ], r));
  },
  /** dispatch --dry-run --return-preamble: read Orca's injected brief, no effects. */
  preamble({ task: taskId, to, run: r }) {
    return orcaJsonSync(withRun([
      "orchestration", "dispatch",
      "--task", taskId, "--to", to, "--dry-run", "--return-preamble",
    ], r));
  },
};

// ---------------------------------------------------------------- messaging
const LIFECYCLE_TYPES = new Set(["worker_done", "heartbeat"]);

export const msg = {
  send({
    subject, to, body, type = "status", priority, taskId, dispatchId,
    outcome, filesModified, reportPath, phase, threadId, run: r, from,
    retryRequest,
  } = {}) {
    if (!subject) throw new TypeError("msg.send needs a subject");
    if (type === "worker_done" && !outcome) {
      throw new TypeError(
        "worker_done requires outcome: 'succeeded' | 'failed'. Never encode " +
        "failure only in prose.",
      );
    }
    if (LIFECYCLE_TYPES.has(type) && to && GROUP_ADDR.test(to)) {
      throw new TypeError(
        `${type} is an exact-dispatch signal and cannot target a group ` +
        `address (${to}). Omit the "to" field to use the run mailbox.`,
      );
    }
    if (Array.isArray(filesModified) === false && filesModified !== undefined) {
      throw new TypeError("filesModified must be an array of paths");
    }
    return orcaJsonSync(withRun([
      "orchestration", "send",
      ...flags([
        ["--subject", subject], ["--to", to], ["--body", body],
        ["--type", type], ["--priority", priority],
        ["--task-id", taskId], ["--dispatch-id", dispatchId],
        ["--outcome", outcome],
        ["--files-modified", filesModified?.length ? filesModified.join(",") : undefined],
        ["--report-path", reportPath], ["--phase", phase],
        ["--thread-id", threadId], ["--from", from],
        ["--retry-request", retryRequest],
      ]),
    ], r));
  },

  /**
   * The blocking consumer. `check` mutates FIFO delivery state, so exactly one
   * process in the system may call this. See monitors/inbox-watch.mjs.
   */
  check({
    ack, wait = false, types, timeoutMs = 900_000, peek, all, terminal,
    run: r, onKeepalive,
  } = {}) {
    const argv = withRun([
      "orchestration", "check",
      ...flags([
        ["--ack", ack], ["--wait", wait],
        ["--types", types?.length ? types.join(",") : undefined],
        ["--timeout-ms", wait ? timeoutMs : undefined],
        ["--peek", peek], ["--all", all], ["--terminal", terminal],
      ]),
    ], r);
    if (!wait) return orcaJsonSync(argv);
    return orcaJsonWait(argv, { timeoutMs, onKeepalive });
  },

  reply({ id, body, run: r, from } = {}) {
    if (!id || !body) throw new TypeError("msg.reply needs id and body");
    return orcaJsonSync(withRun([
      "orchestration", "reply",
      ...flags([["--id", id], ["--body", body], ["--from", from]]),
    ], r));
  },

  /** Worker -> coordinator question. `options` is CSV on the wire. */
  ask({ question, resume, options, timeoutMs, to, run: r, from, retryRequest } = {}) {
    if (!question && !resume) throw new TypeError("msg.ask needs question or resume");
    if (options !== undefined && !Array.isArray(options)) {
      throw new TypeError("msg.ask options must be an array; the wrapper joins it");
    }
    return orcaJsonSync(withRun([
      "orchestration", "ask",
      ...flags([
        ["--question", question], ["--resume", resume],
        ["--options", options?.length ? options.join(",") : undefined],
        ["--timeout-ms", timeoutMs], ["--to", to], ["--from", from],
        ["--retry-request", retryRequest],
      ]),
    ], r));
  },

  inbox({ limit = 25, full, terminal } = {}) {
    return orcaJsonSync([
      "orchestration", "inbox",
      ...flags([["--limit", limit], ["--full", full], ["--terminal", terminal]]),
    ]);
  },
};

// -------------------------------------------------------------------- gates
/**
 * NOT WIRED. Nothing in the engine calls these three.
 *
 * That is deliberate for now and load-bearing for one of the hard floors:
 * `gate.self-resolve` is declared in `ENFORCED_AT` as enforced by *absence*,
 * and `test/floors.test.mjs` fails if a call site appears. Wiring gates means
 * adding `decide("gate.self-resolve")` here plus a check that the resolver is
 * not the creator, and changing that declaration in the same commit.
 */
export const gate = {
  /** Coordinator-managed DAG decision. `options` is a JSON ARRAY on the wire. */
  create({ task: taskId, question, options, run: r, from } = {}) {
    if (!taskId || !question) throw new TypeError("gate.create needs task and question");
    if (options !== undefined && !Array.isArray(options)) {
      throw new TypeError("gate.create options must be an array");
    }
    return orcaJsonSync(withRun([
      "orchestration", "gate-create",
      ...flags([
        ["--task", taskId], ["--question", question],
        ["--options", options?.length ? JSON.stringify(options) : undefined],
        ["--from", from],
      ]),
    ], r));
  },
  resolve({ id, resolution, run: r, from } = {}) {
    if (!id || !resolution) throw new TypeError("gate.resolve needs id and resolution");
    return orcaJsonSync(withRun([
      "orchestration", "gate-resolve",
      ...flags([["--id", id], ["--resolution", resolution], ["--from", from]]),
    ], r));
  },
  list({ task: taskId, status, run: r, from } = {}) {
    return orcaJsonSync(withRun([
      "orchestration", "gate-list",
      ...flags([["--task", taskId], ["--status", status], ["--from", from]]),
    ], r));
  },
};

// -------------------------------------------------------- fleet / terminals
export const fleet = {
  /**
   * The whole observability layer. Returns per-worktree agents[] with state,
   * taskTitle, toolName and a preview, which is why this engine reads no
   * SQLite.
   */
  ps({ limit = 50 } = {}) {
    return orcaJsonSync(["worktree", "ps", "--limit", String(limit)]);
  },
  worktrees({ limit = 50 } = {}) {
    return orcaJsonSync(["worktree", "list", "--limit", String(limit)]);
  },
  currentWorktree() {
    return orcaJsonSync(["worktree", "current"]);
  },
  /** Progress on the sidebar card. Free, and the user is already looking. */
  setStatus({ worktree = "active", workspaceStatus, comment } = {}) {
    return orcaJsonSync([
      "worktree", "set",
      ...flags([["--worktree", worktree],
                ["--workspace-status", workspaceStatus],
                ["--comment", comment]]),
    ]);
  },
};

/**
 * NOT WIRED. Terminal accounting goes through `worker.release` / `worker.retain`
 * instead, because a settled dispatch must be accounted for by the worker API
 * that owns it -- `terminal close` is explicitly the wrong tool and the
 * accounting notes say never to substitute it. Kept because the Orca surface
 * exists and a future run-recover path may need to inspect terminals, but
 * nothing calls it today.
 */
export const term = {
  list({ worktree, limit = 50 } = {}) {
    return orcaJsonSync([
      "terminal", "list",
      ...flags([["--worktree", worktree], ["--limit", limit]]),
    ]);
  },
  create({ worktree = "active", title, command, focus } = {}) {
    return orcaJsonSync([
      "terminal", "create",
      ...flags([["--worktree", worktree], ["--title", title],
                ["--command", command], ["--focus", focus]]),
    ], { timeoutMs: 180_000 });
  },
  /** Default read is a STREAM, not the rendered screen. Use screen:true. */
  read({ terminal, cursor, limit = 200, screen } = {}) {
    if (screen && cursor !== undefined) {
      throw new TypeError("--screen and --cursor are mutually exclusive");
    }
    return orcaJsonSync([
      "terminal", "read",
      ...flags([["--terminal", terminal], ["--cursor", cursor],
                ["--limit", limit], ["--screen", screen]]),
    ]);
  },
  wait({ terminal, forWhat = "tui-idle", timeoutMs = 60_000 } = {}) {
    return orcaJsonSync([
      "terminal", "wait",
      ...flags([["--terminal", terminal], ["--for", forWhat],
                ["--timeout-ms", timeoutMs]]),
    ], { timeoutMs: timeoutMs + 30_000 });
  },
  send({ terminal, text, enter = true, interrupt } = {}) {
    return orcaJsonSync([
      "terminal", "send",
      ...flags([["--terminal", terminal], ["--text", text],
                ["--enter", enter], ["--interrupt", interrupt]]),
    ]);
  },
};

// ----------------------------------------------------------- idempotency IO
export const request = {
  /**
   * `absent` is NOT proof that nothing happened. Callers must inspect real
   * state before deciding.
   */
  show({ request: id }) {
    return orcaJsonSync(["orchestration", "request-show", "--request", id]);
  },
};

export { orcaJson, orcaJsonSync, orcaJsonWait };
