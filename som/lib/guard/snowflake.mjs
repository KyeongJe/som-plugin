/**
 * Shared Snowflake blast-radius detection for the PreToolUse guards.
 *
 * The classifier in engine/somsql only helps if it is the only way in. A worker
 * that writes a six-line connector script and runs it bypasses everything. So
 * these guards do not inspect SQL -- they refuse to let anything but the somsql
 * entrypoint open a Snowflake connection at all.
 *
 * Fail policy, and the reason for the asymmetry:
 *
 *   outside a Snowflake context   ALLOW, always, including on internal error.
 *                                 A guard bug must never block unrelated work.
 *   inside a proven context       DENY unless it is the somsql entrypoint.
 *
 * "Proven" means the command names a Snowflake surface explicitly. We never
 * guess from a bare `.sql` path or a table name, because a false deny here is
 * indistinguishable to the user from the tool being broken.
 */

/** Things that only appear when something is talking to Snowflake. */
const SURFACE = [
  /\bsnowsql\b/i,
  /\bsnow\s+sql\b/i,
  /\bimport\s+snowflake\b/i,
  /\bfrom\s+snowflake\b/i,
  /\bsnowflake\.connector\b/i,
  /\bsnowflake-connector-python\b/i,
  /\bsf_utils\b/i,
  /\bSF_LOGIN_INFO\b/,
  /\bsf_login_info[\w.]*\.json\b/i,
  /~?\/?\.snowflake\/connections\.toml/i,
  /\bSOMSQL_PROFILE\b/,
];

/** Execution verbs on a DB cursor. Only meaningful alongside a surface hit. */
const EXECUTION = [
  /\.execute\s*\(/,
  /\.execute_string\s*\(/,
  /\.executemany\s*\(/,
  /\.cursor\s*\(/,
  /\bread_sql\w*\s*\(/,
  /\bfetch_pandas\w*\s*\(/,
];

/** The one sanctioned way in. */
const ENTRYPOINT = [
  /\bpython[0-9.]*\s+-m\s+somsql\b/,
  /\bpython[0-9.]*\s+-m\s+somdoc\b/,        // somdoc never connects; harmless
  /\bsomsql\b\s+(run|classify|explain|plan|ledger|demand)\b/,
];

/**
 * Installing the connector is not using it.
 *
 * `snowflake-connector-python` is a SURFACE token, so `pip install
 * snowflake-connector-python` was denied -- the package this plugin's own
 * `requirements.txt` pins, and the one `/som:doctor` tells you to install when
 * it is missing. A guard that blocks its own setup instructions is a guard
 * someone turns off, and then nothing is guarded.
 *
 * A package manager resolving a name cannot execute a query, so these are
 * judged by what the command *is*, not by what string it contains.
 */
const PACKAGE_MANAGER = [
  /^\s*(?:sudo\s+)?(?:python[0-9.]*\s+-m\s+)?pip[0-9.]*\s+(install|download|show|uninstall|index|list|freeze)\b/i,
  /^\s*(?:sudo\s+)?(?:uv|poetry|pipenv|conda|mamba|pdm|rye)\s+(add|install|remove|sync|lock|search|show|pip)\b/i,
  /^\s*(?:sudo\s+)?pip[0-9.]*\s+install\s+-r\b/i,
];

export function isPackageManager(text) {
  return PACKAGE_MANAGER.some((re) => re.test(text));
}

/**
 * Writing *about* the surface is not touching it.
 *
 * A commit message explaining what the guard refuses necessarily names
 * `snowsql` and `sf_utils`, and `git commit -m "..."` put that text on the
 * command line -- so the guard refused the commit that documented it. Same for
 * `git log --grep`, `gh pr create --body`, and an `echo` into a note.
 *
 * None of these can execute a query: the text is an argument to a tool that
 * stores or searches prose. The surface check still applies to every other
 * segment, so `git commit -m "..." && snowsql -q ...` is refused on the
 * second segment.
 */
const PROSE_TOOL = [
  // Authoring prose: the argument is text the author wrote.
  /^\s*git\s+(commit|tag|notes)\b/i,
  /^\s*gh\s+(pr|issue|release)\s+\w+/i,
  /^\s*echo\b/i,
  // Searching prose: these read commit messages, not files on disk.
  /^\s*git\s+(log|grep)\b/i,
];
// Deliberately NOT here: `cat`, `head`, `tail`, `git show`, `git config`.
// They print file contents, and `cat ~/.snowflake/connections.toml` is exactly
// the thing this guard exists to refuse. Adding them to the exemption broke
// that, which is how they came back out. A plain `cat requirements.txt` needs
// no exemption -- it names no Snowflake surface, so it was always allowed.

/**
 * Command substitution is execution wearing prose clothing.
 *
 * `echo $(snowsql -q "SELECT 1")` and its backtick spelling run the query and
 * print the result. Adding the prose exemption opened exactly that hole, which
 * is why it is closed here rather than trusted to the tool name.
 */
const SUBSTITUTION = /\$\(|`/;

export function isProseTool(text) {
  if (SUBSTITUTION.test(text)) return false;
  return PROSE_TOOL.some((re) => re.test(text));
}

export function surfaceHits(text) {
  return SURFACE.filter((re) => re.test(text)).map((re) => re.source);
}

export function executionHits(text) {
  return EXECUTION.filter((re) => re.test(text)).map((re) => re.source);
}

export function isEntrypoint(text) {
  return ENTRYPOINT.some((re) => re.test(text));
}

const NL = String.fromCharCode(10);
const QUOTES = new Set(["'", '"', "`"]);

/**
 * Split a shell command into the pieces that run separately.
 *
 * The entrypoint check used to be applied to the whole string, so the token
 * `python -m somsql` appearing ANYWHERE switched the guard off for everything
 * beside it. All of these were allowed:
 *
 *   python -m somsql run --file x.sql; snowsql -q "INSERT INTO t VALUES (1)"
 *   snowsql -q "INSERT INTO t VALUES (1)"   # python -m somsql run
 *   echo "python -m somsql run"; python -c "import snowflake.connector ..."
 *
 * A trailing comment defeated the only pre-execution enforcement in the
 * project. So each segment is judged alone, and a comment is stripped first:
 * text after `#` never runs, so it can never sanction what does.
 */
export function commandSegments(command) {
  const text = String(command || "");

  let stripped = "";
  let quote = null;
  for (let i = 0; i < text.length; i += 1) {
    const c = text[i];
    if (quote) {
      if (c === quote && text[i - 1] !== "\\") quote = null;
      stripped += c;
    } else if (QUOTES.has(c)) {
      quote = c;
      stripped += c;
    } else if (c === "#") {
      const nl = text.indexOf(NL, i);
      if (nl === -1) break;
      i = nl;
      stripped += NL;
    } else {
      stripped += c;
    }
  }

  const segs = [];
  let cur = "";
  quote = null;
  for (let i = 0; i < stripped.length; i += 1) {
    const c = stripped[i];
    if (quote) {
      if (c === quote && stripped[i - 1] !== "\\") quote = null;
      cur += c;
      continue;
    }
    if (QUOTES.has(c)) { quote = c; cur += c; continue; }
    if (stripped.slice(i, i + 2) === "&&" || stripped.slice(i, i + 2) === "||") {
      segs.push(cur); cur = ""; i += 1; continue;
    }
    if (c === ";" || c === NL || c === "|" || c === "&") {
      segs.push(cur); cur = ""; continue;
    }
    cur += c;
  }
  segs.push(cur);
  return segs.map((x) => x.trim()).filter(Boolean);
}

/**
 * @returns {{decision:'allow'|'deny', reason?:string, matched?:string[]}}
 */
export function judgeCommand(command) {
  const text = String(command || "");
  if (!text.trim()) return { decision: "allow" };

  // One sanctioned segment does not sanction its neighbours.
  let surface = [];
  let exec = [];
  let segment = "";
  for (const seg of commandSegments(text)) {
    const hits = surfaceHits(seg);
    if (hits.length === 0 || isEntrypoint(seg) ||
        isPackageManager(seg) || isProseTool(seg)) continue;
    surface = hits;
    exec = executionHits(seg);
    segment = seg;
    break;
  }
  if (surface.length === 0) return { decision: "allow" };

  const matched = surface.concat(exec);
  const shown = segment.length > 160 ? `${segment.slice(0, 160)}…` : segment;
  // Warn, do not block.
  //
  // This used to deny. That was over-reach: the guard fires on any command
  // naming a Snowflake surface, which includes the scripts this team had
  // before the plugin existed -- so installing som broke work that had
  // nothing to do with som. A plugin governs what it produces; it does not
  // take away tools that were already there.
  //
  // What protects the data is not this hook. It is that `somsql` is worth
  // coming through: the classifier, the cost guard, the ledger, and an
  // approval step that says in plain words what a write will do. Those are
  // reasons to use the front door, and they survive someone walking past it.
  return {
    decision: "warn",
    matched,
    segment,
    reason:
      "SOMSQL-OUTSIDE  이 명령은 somsql 을 거치지 않고 Snowflake 에 닿습니다.\n" +
      `구간     : ${shown}\n` +
      `매치     : ${matched.slice(0, 4).join("  ")}\n` +
      "빠지는 것 : 읽기/쓰기 분류 · 비용 가드 · 원장 기록 · 쓰기 승인 절차.\n" +
      "           실행은 됩니다. 다만 .som/ledger.ndjson 에 남지 않아서,\n" +
      "           나중에 '언제 무엇을 했나' 를 되짚을 때 이 줄만 비어 있습니다.\n" +
      "대신 쓰려면:\n" +
      "             PYTHONPATH=\"$SOM/engine\" python -m somsql run   --file <path.sql>\n" +
      "             PYTHONPATH=\"$SOM/engine\" python -m somsql write --file <path.sql>\n" +
      "           읽기는 그대로 돌고, 쓰기는 무엇을 바꾸는지 보여준 뒤 승인받고 돕니다.",
  };
}

/**
 * File writes that would create a Snowflake client. Same asymmetry: only fires
 * when the content itself names a Snowflake surface.
 */
export function judgeFileWrite(path, content) {
  const text = String(content || "");
  const p = String(path || "");
  if (!text.trim()) return { decision: "allow" };

  // Prose describes these surfaces on purpose, and prose does not execute.
  if (/\.(md|txt|rst|adoc)$/i.test(p)) return { decision: "allow" };

  // The guard's own source, and the sanctioned client.
  if (/(?:^|[\\/])(lib[\\/]guard|engine[\\/]somsql)[\\/]/i.test(p)) {
    return { decision: "allow" };
  }

  // A test file has to be able to describe the surface -- these guards have
  // their own regression tests. But the exemption is the FILE NAME, not the
  // directory: `docs/loader.py` and `src/tests/loader.py` are not tests, and
  // the old path-substring rule waved both through however they were written.
  if (/(?:^|[\\/])(test_[\w-]+|[\w-]+_test|[\w-]+\.(test|spec))\.[a-z]+$/i.test(p)) {
    return { decision: "allow" };
  }

  // Everything else under docs/ or references/ is exempt only when it is not
  // an executable file.
  const inProse = /(?:^|[\\/])(docs?|references)[\\/]/i.test(p);
  const executable = /\.(py|mjs|cjs|js|ts|ipynb|sh|ps1|bat|cmd)$/i.test(p);
  if (inProse && !executable) return { decision: "allow" };

  // A write statement in a `.sql` file used to be refused here, because a
  // write could never run at all. It can now -- described, approved, recorded
  // -- so refusing the *file* would only push people to write it somewhere
  // this guard cannot see. `somsql write` is where the question gets asked.

  const surface = surfaceHits(text);
  if (surface.length === 0) return { decision: "allow" };

  // A surface hit alone is enough now. Requiring an execution verb in the SAME
  // file meant splitting a connector across two files cleared the guard: one
  // file imports snowflake.connector, its sibling calls .execute(). Neither
  // half tripped it and together they were a working bypass.
  const exec = executionHits(text);

  const matched = surface.concat(exec);
  // Warn, like the command guard. Refusing to let someone write a connector
  // script meant the plugin decided what files may exist in a repository it
  // does not own -- and the team had those files before it arrived.
  return {
    decision: "warn",
    matched,
    reason:
      "SOMSQL-OUTSIDE  이 파일은 자체적으로 Snowflake 에 접속합니다.\n" +
      `파일     : ${p}\n` +
      `매치     : ${matched.slice(0, 4).join("  ")}\n` +
      "빠지는 것 : 읽기/쓰기 분류 · 비용 가드 · 원장 기록 · 쓰기 승인 절차.\n" +
      "대신 쓰려면:\n" +
      "             from somsql.conn import read_sql, read_sql_files, write_sql\n" +
      "           read_sql 은 분류와 비용 가드를, write_sql 은 무엇을 바꾸는지\n" +
      "           보여주고 승인받는 절차를 그대로 가져갑니다.",
  };
}
