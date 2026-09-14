/**
 * The single place this plugin talks to Orca.
 *
 * Facts this is built against, each verified on the machine rather than assumed:
 *
 *  - `orca.cmd` REFUSES `orchestration send` and `orchestration reply` (exit 2,
 *    "cannot safely forward orchestration message bodies"). Both `orca.exe` and
 *    `orca.cmd` sit on PATH, and `.EXE` precedes `.CMD` in PATHEXT today, so a
 *    naive spawn works by luck. We resolve the `.exe` ourselves.
 *  - Spawning with `shell: false` and an argv array removes the quoting layer
 *    entirely, which is why the Windows/PowerShell quoting notes in Orca's guide
 *    do not apply to this code path.
 *  - `check --wait` prints exactly one JSON document on stdout and keepalive
 *    lines on STDERR every 15s. Merging the streams breaks the parser. They are
 *    separated at the spawn boundary here, so it cannot happen.
 *  - The envelope is `{id, ok, result|error, _meta:{runtimeId}}`. `ok` is
 *    authoritative; the exit code is advisory (worker-release exits 0 for
 *    `retained`, `release_pending` and `already_released`).
 *  - Every orchestration command accepts `--run` and `--from`, so this engine
 *    never depends on ambient coordinator binding.
 */
import { spawn, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { delimiter, join, extname } from "node:path";

export class OrcaUnavailable extends Error {}
export class OrcaProtocolError extends Error {
  constructor(msg, raw, stderrTail) {
    super(msg);
    this.raw = raw;
    this.stderrTail = stderrTail;
  }
}
export class OrcaError extends Error {
  constructor(env, exitCode, argv) {
    const e = env?.error ?? {};
    super(e.message || "orca returned ok:false");
    this.code = e.code ?? "unknown";
    this.data = e.data ?? null;
    this.exitCode = exitCode;
    this.argv = argv;
    this.runtimeId = env?._meta?.runtimeId ?? null;
    // Orca's own recovery text. Echoed verbatim; never paraphrased.
    this.nextSteps = e.data?.nextSteps ?? [];
    this.stage = e.data?.stage ?? e.data?.failedStage ?? null;
    this.effects = e.data?.effects ?? null;
    this.residualResources = e.data?.residualResources ?? null;
  }
}

/** Commands whose bodies `orca.cmd` will not forward. */
const BODY_UNSAFE = new Set(["send", "reply"]);

let RESOLVED = null;

function pathCandidates(name) {
  if (extname(name)) return [name];
  const exts = (process.env.PATHEXT || ".COM;.EXE;.BAT;.CMD")
    .split(";")
    .filter(Boolean);
  const dirs = (process.env.PATH || "").split(delimiter).filter(Boolean);
  const out = [];
  for (const d of dirs) {
    for (const ext of exts) out.push(join(d, name + ext));
    out.push(join(d, name));
  }
  return out;
}

/**
 * Resolution order, and never a fall-through on failure: a different binary
 * could target a different Orca runtime and split state silently.
 */
export function resolveOrcaBin({ force = false } = {}) {
  if (RESOLVED && !force) return RESOLVED;

  let name, source;
  if (process.env.ORCA_CLI_COMMAND) {
    name = process.env.ORCA_CLI_COMMAND;
    source = "env";
  } else if (process.env.ORCA_DEV_REPO_ROOT) {
    name = "orca-dev";
    source = "dev";
  } else {
    name = "orca";
    source = "default";
  }

  let bin = name;
  let kind = "unresolved";
  for (const c of pathCandidates(name)) {
    if (existsSync(c)) {
      bin = c;
      kind = extname(c).toLowerCase();
      break;
    }
  }

  // Prefer a sibling .exe over a .cmd/.bat shim.
  if (kind === ".cmd" || kind === ".bat") {
    const sibling = bin.slice(0, -kind.length) + ".exe";
    if (existsSync(sibling)) {
      bin = sibling;
      kind = ".exe";
    }
  }

  RESOLVED = {
    bin,
    source,
    kind,
    // A shim cannot carry a message body safely.
    bodySafe: kind !== ".cmd" && kind !== ".bat",
  };
  return RESOLVED;
}

function assertBodySafe(argv) {
  const r = resolveOrcaBin();
  if (r.bodySafe) return;
  if (argv[0] === "orchestration" && BODY_UNSAFE.has(argv[1])) {
    throw new OrcaUnavailable(
      `orca resolved to ${r.bin}, a shim that refuses to forward ` +
        `orchestration ${argv[1]} bodies. Put the native orca.exe on PATH, or ` +
        `set ORCA_CLI_COMMAND to it. Not falling through to another binary: a ` +
        `different one could target a different runtime.`,
    );
  }
}

export function parseEnvelope(stdout, { argv, exitCode, stderrTail } = {}) {
  const text = String(stdout || "").trim();
  if (!text) {
    throw new OrcaProtocolError(
      `orca printed nothing on stdout (exit ${exitCode})`,
      "",
      stderrTail,
    );
  }
  let env;
  try {
    env = JSON.parse(text);
  } catch (e) {
    throw new OrcaProtocolError(
      `orca stdout was not one JSON document: ${e.message}`,
      text.slice(0, 2048),
      stderrTail,
    );
  }
  // `ok` decides, not the exit code.
  if (env.ok === false) throw new OrcaError(env, exitCode, argv);
  return env;
}

/** Fire-and-parse. Never used for `check --wait`; see orcaJsonWait. */
export function orcaJsonSync(argv, { timeoutMs = 120_000 } = {}) {
  const r = resolveOrcaBin();
  assertBodySafe(argv);
  const full = [...argv, "--json"];
  const res = spawnSync(r.bin, full, {
    shell: false,
    windowsHide: true,
    encoding: "utf8",
    timeout: timeoutMs,
    maxBuffer: 32 * 1024 * 1024,
  });
  if (res.error) {
    if (res.error.code === "ENOENT") {
      throw new OrcaUnavailable(
        `orca not found at ${r.bin}. Install Orca or set ORCA_CLI_COMMAND.`,
      );
    }
    throw res.error;
  }
  return parseEnvelope(res.stdout, {
    argv: full,
    exitCode: res.status,
    stderrTail: String(res.stderr || "").split("\n").slice(-32),
  });
}

export async function orcaJson(argv, { timeoutMs = 120_000 } = {}) {
  return orcaJsonSync(argv, { timeoutMs });
}

/**
 * `orchestration check --wait` and anything else that blocks.
 *
 * stdout accumulates and is parsed once at close. stderr is read line by line:
 * `{"_keepalive":true}` (and the deprecated `_heartbeat` alias) only prove the
 * process is alive. They are NOT progress and NOT completion.
 */
export function orcaJsonWait(
  argv,
  { timeoutMs = 900_000, hardGraceMs = 30_000, onKeepalive, onStderr } = {},
) {
  const r = resolveOrcaBin();
  assertBodySafe(argv);
  const full = [...argv, "--json"];

  return new Promise((resolve, reject) => {
    const child = spawn(r.bin, full, {
      shell: false,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });

    let out = "";
    let errBuf = "";
    let keepalives = 0;
    const diagnostics = [];
    let killedByUs = false;

    // Our own ceiling, above the one we asked Orca for. A fire here is
    // reported as a timeout, never as worker failure.
    const timer = setTimeout(() => {
      killedByUs = true;
      child.kill();
    }, timeoutMs + hardGraceMs);

    child.stdout.on("data", (c) => {
      out += c;
    });

    child.stderr.on("data", (c) => {
      errBuf += c;
      let nl;
      while ((nl = errBuf.indexOf("\n")) >= 0) {
        const line = errBuf.slice(0, nl).trim();
        errBuf = errBuf.slice(nl + 1);
        if (!line) continue;
        let obj = null;
        try {
          obj = JSON.parse(line);
        } catch {
          /* not JSON */
        }
        if (obj && (obj._keepalive || obj._heartbeat)) {
          keepalives++;
          onKeepalive?.(obj);
          continue;
        }
        if (diagnostics.length < 32) diagnostics.push(line);
        onStderr?.(line);
      }
    });

    child.on("error", (e) => {
      clearTimeout(timer);
      reject(
        e.code === "ENOENT"
          ? new OrcaUnavailable(`orca not found at ${r.bin}`)
          : e,
      );
    });

    child.on("close", (code) => {
      clearTimeout(timer);
      if (killedByUs && !out.trim()) {
        // No document arrived. Treat as a timeout, not a failure.
        resolve({
          ok: true,
          result: { timedOut: true, count: 0, messages: [], killedByHarness: true },
          keepalives,
          diagnostics,
        });
        return;
      }
      try {
        const env = parseEnvelope(out, {
          argv: full,
          exitCode: code,
          stderrTail: diagnostics,
        });
        resolve({ ...env, keepalives, diagnostics });
      } catch (e) {
        reject(e);
      }
    });
  });
}

/**
 * Runtime readiness and capability set. Gate features; do not assume them.
 *
 * Orca being absent is a normal state, not an exception. This used to throw
 * `OrcaUnavailable` straight out of `resolveOrcaBin`/`orcaJsonSync`, so a
 * teammate without Orca who typed /som got a stack trace instead of the
 * sentence this whole design is built around: stop and say what is missing.
 * Preflight exists to report what is missing, so it has to survive the most
 * ordinary thing that can be missing.
 */
export function preflight() {
  const absent = (bin, kind, bodySafe, why) => ({
    bin, binKind: kind, bodySafe,
    runtimeReady: false, runtimeId: null, appVersion: null,
    capabilities: new Set(), orchestrationEnabled: false,
    terminalHandle: process.env.ORCA_TERMINAL_HANDLE ?? null,
    worktreeId: process.env.ORCA_WORKTREE_ID ?? null,
    insideOrca: process.env.TERM_PROGRAM === "Orca",
    supportsLaunchPreferences: false,
    orcaInstalled: false,
    unavailable: why,
  });

  let r;
  try {
    r = resolveOrcaBin();
  } catch (e) {
    return absent(null, null, false, e?.message ?? String(e));
  }

  let status;
  try {
    status = orcaJsonSync(["status"]);
  } catch (e) {
    return absent(r.bin, r.kind, r.bodySafe, e?.message ?? String(e));
  }

  const runtime = status.result?.runtime ?? {};
  const caps = new Set(runtime.capabilities ?? []);

  let orchestrationEnabled = true;
  try {
    orcaJsonSync(["orchestration", "run-list", "--limit", "1"]);
  } catch {
    orchestrationEnabled = false;
  }

  return {
    bin: r.bin,
    binKind: r.kind,
    bodySafe: r.bodySafe,
    orcaInstalled: true,
    unavailable: null,
    runtimeReady: runtime.state === "ready",
    runtimeId: status._meta?.runtimeId ?? null,
    appVersion: status.result?.app?.version ?? process.env.ORCA_APP_VERSION ?? null,
    capabilities: caps,
    orchestrationEnabled,
    // Ambient identity Orca exports into every one of its terminals.
    terminalHandle: process.env.ORCA_TERMINAL_HANDLE ?? null,
    worktreeId: process.env.ORCA_WORKTREE_ID ?? null,
    insideOrca: process.env.TERM_PROGRAM === "Orca",
    supportsLaunchPreferences: caps.has(
      "orchestration.worker-launch-preferences.v1",
    ),
  };
}
