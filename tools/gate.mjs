/**
 * What must be true before anything leaves the private repository.
 *
 * This exists because "check before publishing" was a thing a person was
 * supposed to remember, and a real Snowflake account identifier went public
 * inside a `.sample` file that nobody re-read. A procedure that depends on
 * remembering is not a procedure.
 *
 * Everything here is a pure function over `[path, text]` pairs, so the release
 * driver can run it against the exact file set it is about to copy -- not
 * against the working tree, which contains things the release will not carry.
 *
 * These duplicate some of `engine/tests/test_manifest.py` on purpose. Those run
 * on every commit and keep the private repo clean; these run on the archive
 * that is actually leaving, and a release gate that trusts an upstream check
 * is one refactor away from checking nothing.
 */

/** Files that exist to run the project, never to be published. */
const NEVER_PUBLISH = [
  /(^|[\\/])\.som[\\/]/,          // run state
  /(^|[\\/])_cache[\\/]/,
  /(^|[\\/])node_modules[\\/]/,
  /(^|[\\/])__pycache__[\\/]/,
  /\.bundle$/,
  /\.jsonl$/,                     // transcripts and journals
  /\.parquet$/,
  /(^|[\\/])scratch/,
  /\.env(\.|$)/,
  /\.(p8|pem|pfx|key)$/,
  /(^|[\\/])id_rsa/,
  /(^|[\\/])sf_login_info\.json$/,
  /(^|[\\/])connections\.toml$/,
];

const TEXT_EXT = /\.(md|mjs|js|json|py|txt|toml|ya?ml|sql|ps1|css|html|sample)$/i;

/**
 * A developer's home directory names the person and their folder layout.
 *
 * Matched on the segment that follows, so the bare `Users` folder (as in a doc
 * telling someone where their own files live) is fine and a named home
 * directory is not.
 *
 * A segment that is plainly a stand-in is excluded. Documentation elides the
 * middle of a path as `...` and writes fake homes as `example` or `you`;
 * flagging those taught nobody anything -- between them they were the first
 * three things this gate reported, on its own repository. Anything that is not
 * recognisably a stand-in is still a finding, so the check did not get weaker:
 * the fixture that read like a real username was renamed rather than excused.
 */
const ELIDED = /^(\.+|example|examples|you|your|username|user|me|home|<[^>]*>)$/i;
const DEV_PATH = [
  /[A-Za-z]:[\\/]+Users[\\/]+([A-Za-z0-9._-]+)[\\/]/,
  /\/home\/([a-z0-9._-]+)\//i,
  /\/Users\/([A-Za-z0-9._-]+)\//,
];

const ACCOUNT_FIELD = /"account"\s*:\s*"([^"]*)"/g;
const ENDPOINT = /([A-Za-z0-9_<>-]+)\.snowflakecomputing\.com/g;
const KEY_BODY = /-----BEGIN[A-Z ]*PRIVATE KEY-----\s*[\r\n]+\s*[A-Za-z0-9+/=]{40}/;

const isPlaceholder = (v) => {
  const t = String(v ?? "").trim();
  if (!t || t.includes("<") || t.includes(">")) return true;
  // An interpolation is code, not an identifier.
  if (t.includes("${") || t.startsWith("$")) return true;
  return /example|your|changeme|placeholder|xxxx|sample/i.test(t);
};

/** Paths that legitimately carry the owner's contact details. */
const MANIFESTS = [
  ".claude-plugin/marketplace.json",
  "som/.claude-plugin/plugin.json",
];

const norm = (p) => String(p).replace(/\\/g, "/");

/**
 * Run every gate. Returns `{ problems, notes }`.
 *
 * `problems` stop the release. `notes` are things a person might want to
 * change but which are not unsafe -- kept separate so the stop condition
 * stays unambiguous. A gate that mixes "you must" with "you might" gets
 * overridden as a habit.
 */
export function checkRelease(files, { ownerEmail = "" } = {}) {
  const problems = [];
  const notes = [];

  // -- files that should not be in the archive at all ----------------------
  for (const [p] of files) {
    const hit = NEVER_PUBLISH.find((re) => re.test(norm(p)));
    if (hit) problems.push(`공개 대상이 아닌 파일이 포함됐습니다: ${norm(p)}  (${hit})`);
  }

  for (const [p, text] of files) {
    const path = norm(p);
    if (!TEXT_EXT.test(path)) continue;

    // -- a live Snowflake account identifier -------------------------------
    for (const m of text.matchAll(ACCOUNT_FIELD)) {
      if (!isPlaceholder(m[1])) {
        problems.push(`${path}: 실제 account identifier "${m[1]}" — placeholder 로 바꾸세요`);
      }
    }
    for (const m of text.matchAll(ENDPOINT)) {
      if (!isPlaceholder(m[1])) problems.push(`${path}: 실제 접속 endpoint ${m[0]}`);
    }

    // -- key material ------------------------------------------------------
    if (KEY_BODY.test(text)) problems.push(`${path}: private key 본문이 들어 있습니다`);

    // -- the developer's own machine ---------------------------------------
    for (const re of DEV_PATH) {
      const m = text.match(re);
      if (m && !ELIDED.test(m[1])) {
        problems.push(`${path}: 개발자 홈 경로가 그대로 있습니다 — ${m[0]}`);
        break;
      }
    }

    // -- contact details, confined to the manifests ------------------------
    if (ownerEmail && text.includes(ownerEmail) && !MANIFESTS.includes(path)) {
      problems.push(`${path}: 소유자 이메일이 매니페스트 밖으로 퍼졌습니다`);
    }
  }

  if (ownerEmail) {
    notes.push(
      `매니페스트의 소유자 이메일 ${ownerEmail} 은 그대로 공개됩니다. ` +
      "공용 별칭이나 GitHub 핸들로 바꾸실 수 있습니다 (기능에는 영향 없음).");
  }
  return { problems, notes };
}

/**
 * Every published version string has to be the same one.
 *
 * Three files carry it and they drifted before. A marketplace that advertises
 * 0.1.0 while the plugin reports 0.2.0 is the kind of thing nobody notices
 * until someone is debugging the wrong version.
 */
export function checkVersions(files, version) {
  const problems = [];
  const read = (p) => {
    const hit = files.find(([f]) => norm(f) === p);
    if (!hit) { problems.push(`${p} 이 아카이브에 없습니다`); return null; }
    try { return JSON.parse(hit[1]); } catch (e) { problems.push(`${p}: ${e.message}`); return null; }
  };

  const plugin = read("som/.claude-plugin/plugin.json");
  const market = read(".claude-plugin/marketplace.json");
  const found = [];
  if (plugin) found.push(["plugin.json", plugin.version]);
  if (market) {
    found.push(["marketplace.json metadata", market?.metadata?.version]);
    const entry = (market?.plugins ?? []).find((x) => x?.name === "som");
    if (!entry) problems.push("marketplace.json 에 som 플러그인 항목이 없습니다");
    else found.push(["marketplace.json som 항목", entry.version]);
  }
  for (const [where, v] of found) {
    if (v !== version) problems.push(`${where} 이 ${v} 입니다 — 릴리스는 ${version}`);
  }
  return problems;
}

/** The release notes have to actually mention what is being released. */
export function checkChangelog(files, version) {
  const hit = files.find(([f]) => norm(f) === "CHANGELOG.md");
  if (!hit) return ["CHANGELOG.md 가 없습니다"];
  const re = new RegExp(`^##\\s*\\[?v?${version.replace(/\./g, "\\.")}\\]?`, "m");
  return re.test(hit[1])
    ? []
    : [`CHANGELOG.md 에 ${version} 항목이 없습니다 — 무엇이 바뀌었는지 적으세요`];
}

export const _internal = { NEVER_PUBLISH, DEV_PATH, isPlaceholder, MANIFESTS };
