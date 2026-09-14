#!/usr/bin/env node
/**
 * One entry point, so no document has to hand-write a `node -e` blob.
 *
 * A correction is recorded here, because getting it wrong twice cost more than
 * the original problem. The skills used to drive the engine with eleven `node
 * -e` snippets interpolating `${CLAUDE_PLUGIN_ROOT}`. Those were reported as
 * broken -- "bash does not expand inside single quotes" -- and replaced. The
 * report was wrong. Claude Code substitutes `${CLAUDE_PLUGIN_ROOT}` into the
 * skill text *before the model sees it*, so the shell never meets the variable
 * and the quoting is irrelevant. Measured: loading `som:snowflake-safe` yields
 * `export PYTHONPATH="C:/Users/.../som/0.1.0/engine"`, and the original
 * conduct snippet, run with the path the host supplies, prints `ready`.
 *
 * The mistake both times was the same one: checking the raw repository file
 * instead of the path a teammate actually travels.
 *
 * So this file does not exist to fix a break. It exists because:
 *
 *   - a hand-written import hardcodes the module layout, and nothing notices
 *     when the layout moves;
 *   - `test_every_cli_subcommand_the_docs_name_actually_exists` can execute a
 *     subcommand and fail on "unknown command", which is not possible for a
 *     prose snippet;
 *   - substitution happens only in SKILL.md and commands/*.md. A reference
 *     file opened with Read, or the file read straight off GitHub, still shows
 *     the literal -- and `bin/som.mjs` needs no substitution at all.
 *
 * It resolves its own location, so a caller needs this script's path and
 * nothing else:
 *
 *     node <plugin>/bin/som.mjs interview new "<요청>" --recipe doc
 *
 * Every subcommand prints something a human can read and exits non-zero on
 * refusal, so a skill can check the exit code instead of parsing prose.
 */
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const SOM = resolve(HERE, "..");
const load = (rel) => import(new URL(`../${rel}`, import.meta.url).href);

const argv = process.argv.slice(2);
const project = process.env.SOM_PROJECT || process.cwd();

function flag(name, fallback = undefined) {
  const i = argv.indexOf(`--${name}`);
  return i >= 0 && argv[i + 1] && !argv[i + 1].startsWith("--")
    ? argv[i + 1] : fallback;
}
const has = (name) => argv.includes(`--${name}`);
const positional = argv.filter((a, i) =>
  !a.startsWith("--") && !(i > 0 && argv[i - 1].startsWith("--") &&
                           argv[i - 1] !== "--json"));

function out(text) { process.stdout.write(`${text}\n`); }
function die(text, code = 1) { process.stderr.write(`${text}\n`); process.exit(code); }

const USAGE = `som — 플러그인 엔진 진입점

  node <플러그인>/bin/som.mjs <명령> [...]

인터뷰
  interview new "<요청>" --recipe <id> [--brownfield]   기록을 만들고 slug 를 출력
  interview topology <slug> --components '<JSON 배열>'  Round 0 확정
  interview round <slug> --component <id> --dimension <차원>
                         --question "..." --answer "..." --scores '<JSON>'
                         [--entities '<JSON>']          한 라운드 채점 + 진행 표
  interview accept <slug> --component <id> --dimension <차원> --note "..."
                                                        모르는 채로 가기로 확정
  interview defer <slug> --component <id> --reason "..." 구성 보류
  interview show <slug>                                 현재 상태
  interview gate "<요청>" [--recipe <id>]               지금 시작할 수 있는지

패턴
  learn propose --title ... --trigger ... --action ... --triggers a,b
                --evidence '<JSON 배열>' [--scope project|user]
  learn list [--all]                                    살아있는 패턴 (--all 이면 내린 것도)
  learn brief "<다음에 할 일>"                            다음 런에 뭐가 주입되는지
  learn retire <id> [--reason "..."]                    손으로 내리기
  learn skills list                                     생성된 개인 스킬 목록
  learn skills plan                                     승격 후보와 보류 사유 (아무것도 안 씀)
  learn skills promote --yes [--update]                 ~/.claude/skills/ 에 실제로 생성
  learn skills retire <이름>                             생성된 스킬 지우기

오케스트레이션
  conduct preflight                                     Orca 가 준비됐는지 (문제만 출력)
  conduct recipes                                       레시피 목록
  conduct choose "<사용자 원문>"                          어느 레시피로 갈지
  conduct asks --recipe <id> [--slots '<JSON>']         아직 안 채워진 슬롯과 그 이유
  conduct plan "<요청>" --recipe <id> [--slots '<JSON>'] 계획만 세우고 멈춘다 (워커 0)
  conduct run  "<요청>" --recipe <id> --answers <파일.json>
               [--approve-waves] [--dry-run]              실제 실행 (워커 기동)

기타
  paths                                                 이 플러그인의 경로
  threshold                                             모호도 문턱과 출처

작업 디렉토리는 현재 폴더입니다. SOM_PROJECT 로 바꿀 수 있습니다.`;

const json = (s, what) => {
  try { return JSON.parse(s); } catch (e) { die(`${what} 가 올바른 JSON 이 아닙니다: ${e.message}`); }
};

async function interviewCmd(sub) {
  const M = await load("lib/state/interview.mjs");
  const store = new M.Interviews(project);
  const need = (slug) => {
    if (!slug) die("slug 가 필요합니다. `interview new` 가 출력한 값을 쓰세요.");
    const rec = store.load(slug);
    if (!rec) die(`기록을 찾을 수 없습니다: ${slug}\n  있는 것: ` +
                  (store.list().map((x) => x.slug).join(", ") || "(없음)"));
    return rec;
  };

  if (sub === "new") {
    const objective = positional[2];
    if (!objective) die('요청 원문이 필요합니다: interview new "R&R 문서 만들어줘" --recipe doc');
    const recipeId = flag("recipe");
    if (!recipeId) {
      die("--recipe 가 필요합니다 (doc | prd | watch | analyze | build | data).\n" +
          "  레시피를 모르는 기록은 어떤 레시피도 승인하지 못합니다.");
    }
    // Validate here rather than at plan() time. An interview used to accept a
    // recipe id that did not exist and exit 0, so the whole interview -- rounds,
    // scoring, the gate opening -- could be completed before anything mentioned
    // that the recipe was a typo. Refuse the moment it is nameable.
    try {
      (await load("lib/conduct.mjs")).loadRecipe(recipeId);
    } catch (e) {
      die(e.message, 2);
    }
    const rec = store.save(M.emptyInterview({
      objective, recipeId, project, brownfield: has("brownfield"),
    }));
    out(`모호도 문턱: ${(rec.threshold * 100).toFixed(0)}% (출처: ${rec.thresholdSource})`);
    if (rec.thresholdNote) out(rec.thresholdNote);
    out(`slug: ${rec.slug}`);
    out(`유형: ${rec.type} · 레시피: ${rec.recipe}`);
    return;
  }

  if (sub === "topology") {
    const rec = need(positional[2]);
    const comps = json(flag("components") ?? "[]", "--components");
    const next = store.save(store.confirmTopology(rec, comps));
    const active = next.topology.components.filter((c) => c.status !== "deferred");
    out(`구성 확정 · 활성 ${active.length}개 · 보류 ${next.topology.deferrals.length}개`);
    for (const c of next.topology.components) {
      out(`  ${c.id.padEnd(14)}${c.name}${c.status === "deferred" ? "  (보류)" : ""}`);
    }
    return;
  }

  if (sub === "round") {
    const rec = need(positional[2]);
    const r = store.addRound(rec, {
      componentId: flag("component"),
      dimension: flag("dimension"),
      question: flag("question") ?? "",
      answer: flag("answer") ?? "",
      scores: json(flag("scores") ?? "{}", "--scores"),
      entities: flag("entities") ? json(flag("entities"), "--entities") : null,
      challengeMode: flag("challenge") ?? null,
    });
    store.save(r.iv);
    out(M.report(r));
    process.exitCode = r.iv.verdict === "start" ? 0 : 2;
    return;
  }

  if (sub === "accept") {
    const rec = need(positional[2]);
    const next = store.save(M.Interviews.prototype.acceptUnknown.call(
      store, rec, flag("component"), flag("dimension"), flag("note")));
    out(`미확정으로 확정: ${flag("component")} / ${flag("dimension")}`);
    out(`모호도 ${(next.ambiguity * 100).toFixed(1)}% · 판정 ${next.verdict}`);
    return;
  }

  if (sub === "defer") {
    const rec = need(positional[2]);
    const next = store.save(store.deferComponent(
      rec, flag("component"), flag("reason")));
    out(`보류: ${flag("component")} — ${flag("reason") ?? "사용자 확인"}`);
    out(`모호도 ${(next.ambiguity * 100).toFixed(1)}%`);
    return;
  }

  if (sub === "show") {
    const rec = need(positional[2]);
    out(`${rec.slug}\n  요청: ${rec.objective}\n  레시피: ${rec.recipe}` +
        `\n  라운드: ${(rec.rounds ?? []).length}회` +
        `\n  모호도: ${(rec.ambiguity * 100).toFixed(1)}% / 문턱 ${(rec.threshold * 100).toFixed(0)}%` +
        `\n  판정: ${rec.verdict}`);
    for (const c of rec.topology?.components ?? []) {
      const scored = Object.keys(c.scores ?? {}).length;
      out(`    ${c.id.padEnd(14)}${c.name}  ${c.status}  채점 ${scored}차원`);
    }
    return;
  }

  if (sub === "gate") {
    const objective = positional[2];
    if (!objective) die('요청 원문이 필요합니다: interview gate "R&R 문서 만들어줘"');
    const g = M.clarityGate({ project, objective, recipeId: flag("recipe") });
    out(g.ok ? g.gate.line : g.problem);
    process.exitCode = g.ok ? 0 : 2;
    return;
  }

  die(`알 수 없는 interview 명령: ${sub}\n\n${USAGE}`);
}

async function learnCmd(sub) {
  const M = await load("lib/state/patterns.mjs");
  const lib = new M.PatternLibrary(project);

  if (sub === "propose") {
    const r = lib.propose({
      title: flag("title") ?? "",
      trigger: flag("trigger") ?? "",
      action: flag("action") ?? "",
      why: flag("why") ?? "",
      triggers: (flag("triggers") ?? "").split(",").map((x) => x.trim()).filter(Boolean),
      tags: (flag("tags") ?? "").split(",").map((x) => x.trim()).filter(Boolean),
      evidence: json(flag("evidence") ?? "[]", "--evidence"),
    }, { scope: flag("scope", "project") });
    if (!r.ok) {
      die(`거부:\n  ${r.problems.join("\n  ")}`, 2);
    }
    out(`${r.merged ? "병합" : "저장"}: ${r.pattern.id} (신뢰도 ${r.pattern.confidence})`);
    if (r.warning) out(r.warning);
    return;
  }

  if (sub === "list") {
    const all = lib.all();
    const show = has("all") ? all : all.filter((p) => !p.retired);
    if (!show.length) { out("(패턴 없음)"); return; }
    for (const p of show) {
      out(`${p.id}  ${p.retired ? "[내림] " : ""}${p.title}`);
      out(`    ${p.scope} · 신뢰도 ${p.confidence} · 적용 ${p.uses} · 빗나감 ${p.losses}`);
      out(`    언제: ${p.trigger}`);
      if (p.retired && p.retiredReason) out(`    사유: ${p.retiredReason}`);
    }
    const s = lib.stats();
    out(`\n총 ${s.total} · 활성 ${s.live} · 내림 ${s.retired} · 신뢰 ${s.trusted}`);
    return;
  }

  if (sub === "brief") {
    const objective = positional[2] ?? "";
    const text = lib.brief({ objective, spec: objective });
    out(text || "(매칭 없음 — triggers 가 실제 쓰는 말과 안 맞습니다)");
    return;
  }

  if (sub === "retire") {
    const id = positional[2];
    if (!id) die("패턴 id 가 필요합니다. `learn list` 로 확인하세요.");
    if (lib.retire(id, flag("reason"))) { out(`내렸습니다: ${id}`); return; }
    // Printing "not found" and exiting 0 let a caller checking the exit code
    // believe it had retired something. The pattern is still live.
    die(`찾을 수 없습니다: ${id}\n  있는 것: ` +
        (lib.all().map((p) => p.id).join(", ") || "(패턴 없음)"), 2);
  }

  if (sub === "skills") {
    const S = await load("lib/state/skills.mjs");
    const w = new S.SkillWriter();
    const act = positional[2] ?? "list";

    if (act === "list") {
      const all = w.list();
      if (!all.length) { out(`(생성된 스킬 없음) ${w.dir}`); return; }
      for (const s of all) {
        out(`${s.name}${s.ours ? "" : "   [som 이 만든 것 아님 — 건드리지 않습니다]"}`);
        if (s.ours) {
          out(`    신뢰도 ${s.confidence} · 출처 패턴 ${s.sources.length}건 · ${s.generatedAt ?? ""}`);
        }
        out(`    ${s.file}`);
      }
      return;
    }

    if (act === "plan" || act === "promote") {
      const plan = w.plan(lib.all());
      if (!plan.ready.length && !plan.held.length) {
        out("승격할 만한 패턴 묶음이 아직 없습니다. 패턴이 쌓이면 다시 보세요.");
        return;
      }
      for (const r of plan.ready) {
        out(`승격 가능  ${r.name}   (평균 신뢰도 ${r.cluster.avgConfidence}, ` +
            `패턴 ${r.cluster.members.length}건, 적용 ${r.cluster.uses}회)`);
        out(`    ${r.description}`);
        for (const m of r.cluster.members) out(`    - ${m.title}`);
      }
      for (const h of plan.held) {
        out(`보류      ${h.cluster.slug}`);
        for (const x of h.why) out(`    · ${x}`);
      }
      if (act === "plan") {
        out(`\n쓰기 위치: ${plan.dir}`);
        out("실제로 만들려면: som learn skills promote --yes");
        return;
      }
      // A file in someone's home directory is not something to create because
      // a command was typed near it. The plan above is printed either way.
      if (!has("yes")) {
        die("promote 는 --yes 가 필요합니다. 위 내용을 먼저 확인하세요.", 2);
      }
      let made = 0;
      for (const r of plan.ready) {
        const res = w.write(r, { update: has("update") });
        if (res.written) { made += 1; out(`만들었습니다: ${res.file}`); }
        else out(`건너뜀 ${res.name}: ${res.reason}`);
      }
      out(`\n${made}개 생성. 새 대화부터 적용됩니다.`);
      return;
    }

    if (act === "retire") {
      const name = positional[3];
      if (!name) die("스킬 이름이 필요합니다. `learn skills list` 로 확인하세요.");
      const r = w.retire(name);
      if (r.removed) { out(`지웠습니다: ${r.file}`); return; }
      die(`${name}: ${r.reason}`, 2);
    }

    die(`알 수 없는 skills 명령: ${act}  (list | plan | promote | retire)`, 2);
  }

  die(`알 수 없는 learn 명령: ${sub}\n\n${USAGE}`);
}


/**
 * The orchestration engine had no CLI at all. `skills/conduct/SKILL.md` drove
 * it with four `node -e` blobs that interpolated ${CLAUDE_PLUGIN_ROOT} inside
 * single quotes -- the exact bug this file was created to remove, left behind
 * in the one skill that needed it most. Every conduct command a teammate ran
 * died with ERR_MODULE_NOT_FOUND before doing anything.
 *
 * `run` is deliberately absent: it launches paid workers and needs a channel
 * to ask a human through, which a one-shot process does not have. The skill
 * drives that step. Everything up to it -- preflight, recipe choice, asks,
 * plan -- is decidable here and is what the snippets were doing.
 */
async function conductCmd(sub) {
  const C = await load("lib/conduct.mjs");

  if (sub === "preflight") {
    // A person is reading this terminal, so the human channel exists -- the
    // skill supplies the real one. Without this the CLI reported "no way to
    // ask a human" as an environment problem, which is about wiring, not
    // about whether Orca is ready.
    const pf = new C.Conduct({ project, askHuman: async () => null }).preflight();
    if (!pf.problems.length) { out("ready"); return; }
    for (const p of pf.problems) out(p);
    process.exitCode = 2;
    return;
  }

  if (sub === "recipes") {
    for (const r of C.listRecipes()) {
      out(`${String(r.id).padEnd(10)}${r.title ?? ""}`);
      if (r.when) out(`    언제: ${r.when}`);
    }
    return;
  }

  if (sub === "choose") {
    const text = positional[2];
    if (!text) die('사용자 원문이 필요합니다: conduct choose "R&R 문서 만들어줘"');
    const r = C.chooseRecipe(text);
    if (!r) {
      die("맞는 레시피가 없습니다. 지어내지 말고 아래에서 고르게 하세요:\n  " +
          C.listRecipes().map((x) => x.id).join(", "), 2);
    }
    out(`레시피: ${r.id}`);
    if (r.title) out(`제목: ${r.title}`);
    return;
  }

  if (sub === "asks") {
    const id = flag("recipe");
    if (!id) die("--recipe 가 필요합니다. `conduct recipes` 로 확인하세요.");
    const recipe = C.loadRecipe(id);
    const slots = json(flag("slots") ?? "{}", "--slots");
    const missing = C.missingAsks(recipe, slots);
    if (!missing.length) { out("빠진 슬롯 없음"); return; }
    for (const a of missing) {
      out(`${a.slot.padEnd(16)}${a.q}`);
      if (a.why) out(`${" ".repeat(16)}왜: ${a.why}`);
    }
    process.exitCode = 2;
    return;
  }

  if (sub === "plan") {
    const objective = positional[2];
    if (!objective) die('요청 원문이 필요합니다: conduct plan "<요청>" --recipe doc');
    const id = flag("recipe");
    if (!id) die("--recipe 가 필요합니다. `conduct recipes` 로 확인하세요.");
    const c = new C.Conduct({ project });
    const p = c.plan({ objective, recipeId: id, slots: json(flag("slots") ?? "{}", "--slots") });
    if (p.problems?.length) {
      for (const x of p.problems) out(`막힘: ${x}`);
      process.exitCode = 2;
      return;
    }
    out(`노드 ${p.nodes?.length ?? 0}개 · 최장 경로 ${p.depth ?? "?"}` +
        ` · wave ${p.parallelism?.widths.join("-") ?? "?"}`);
    if (p.singleAgent) {
      out("");
      out("전부 순차입니다 — Orca 도 워커도 필요 없습니다.");
      out("이 세션에서 아래 순서대로 직접 수행하세요:");
      for (const [i, st] of (p.steps ?? []).entries()) {
        out(`  ${i + 1}. ${st.key.padEnd(14)}${st.role}`);
      }
    } else {
      out("");
      out(`동시에 도는 구간이 있습니다 (최대 ${p.parallelism.maxWidth}개) — ` +
          `Orca 로 ${p.parallelism.wavesSaved}단계를 줄입니다.`);
      for (const [i, w] of (p.parallelism?.waves ?? []).entries()) {
        out(`  wave ${i + 1}: ${w.join(", ")}`);
      }
    }
    return;
  }

  if (sub === "run") {
    const objective = positional[2];
    if (!objective) die('요청 원문이 필요합니다: conduct run "<요청>" --recipe doc --answers a.json');
    const id = flag("recipe");
    if (!id) die("--recipe 가 필요합니다. `conduct recipes` 로 확인하세요.");

    // Everything the person already decided, supplied up front. A one-shot
    // process cannot open a dialogue, so the contract is: answer in advance,
    // or the run stops and tells you exactly what it needed. It never guesses
    // -- a made-up slot value is how invented names end up in an R&R document.
    let answers = {};
    if (flag("answers")) {
      try {
        answers = JSON.parse(readFileSync(flag("answers"), "utf8"));
      } catch (e) {
        die(`--answers 파일을 읽지 못했습니다: ${e.message}`);
      }
    }
    const approve = has("approve-waves");

    // The one thing a headless run must not do is silently continue past a
    // question. Anything unanswered prints as SOM-ASK and exits 3, so the
    // caller can collect the answer and re-invoke with it in --answers.
    const pending = [];
    const askHuman = async (q) => {
      if (q?.kind === "intake") {
        const got = {};
        const missing = [];
        for (const item of q.questions ?? []) {
          const v = answers[item.slot];
          if (v !== undefined && v !== null && String(v).trim() !== "") got[item.slot] = v;
          else missing.push(item);
        }
        if (missing.length) pending.push({ kind: "intake", questions: missing });
        return got;
      }
      if (q?.kind === "gate") {
        if (approve) return "계속";
        pending.push({ kind: "gate", question: q.question, options: q.options });
        return "중단";
      }
      pending.push({ kind: "question", question: q?.question ?? "", options: q?.options });
      return null;
    };

    const c = new C.Conduct({
      project, askHuman,
      say: (line) => out(line),
      dryRun: has("dry-run"),
    });
    const result = await c.run({ objective, recipeId: id, slots: answers });

    if (pending.length) {
      process.stderr.write("SOM-ASK " + JSON.stringify(pending, null, 2) + "\n");
      process.stderr.write(
        "\n답을 --answers JSON 에 채우고 다시 부르세요. " +
        "wave 승인은 --approve-waves 입니다.\n" +
        "지어내지 마세요 — 모르면 사용자에게 물으세요.\n");
      process.exitCode = 3;
      return;
    }
    if (!result?.ok) {
      for (const p of result?.problems ?? []) out(`막힘: ${p}`);
      process.exitCode = 2;
      return;
    }
    if (result.singleAgent) {
      out("");
      out("단계 (이 세션에서 순서대로):");
      for (const [i, st] of (result.steps ?? []).entries()) {
        out(`  ${i + 1}. ${st.key}  [${st.role}]`);
        if (st.spec) out(`     ${String(st.spec).replace(/\s+/g, " ").slice(0, 150)}`);
        if (st.acceptance) out(`     통과 조건: ${String(st.acceptance).slice(0, 120)}`);
      }
      return;
    }
    if (result.dryRun) {
      out(`계획만 확인했습니다 (워커 0개). 노드 ${result.nodes?.length ?? 0}개.`);
      for (const n of result.nodes ?? []) out(`  ${String(n.key).padEnd(18)}${n.role ?? ""}`);
      return;
    }
    out(`완료: ${result.done ?? "?"}/${result.total ?? "?"} · 실패 ${result.failed ?? 0}`);
    return;
  }

  die(`알 수 없는 conduct 명령: ${sub}\n\n${USAGE}`);
}

async function main() {
  const cmd = positional[0];
  if (!cmd || has("help") || cmd === "help") { out(USAGE); return; }

  if (cmd === "paths") {
    out(`플러그인: ${SOM}`);
    out(`작업 폴더: ${project}`);
    out(`인터뷰 기록: ${join(project, ".som", "interview")}`);
    out(`패턴 (프로젝트): ${join(project, ".som", "patterns.json")}`);
    return;
  }
  if (cmd === "threshold") {
    const M = await load("lib/state/interview.mjs");
    const t = M.threshold(project);
    out(`모호도 문턱: ${(t.value * 100).toFixed(0)}% (출처: ${t.source})`);
    if (t.note) out(t.note);
    return;
  }
  if (cmd === "interview") return interviewCmd(positional[1]);
  if (cmd === "learn") return learnCmd(positional[1]);
  if (cmd === "conduct") return conductCmd(positional[1]);
  die(`알 수 없는 명령: ${cmd}\n\n${USAGE}`);
}

main().catch((e) => {
  // A refusal is a message; anything else still shows its stack, because that
  // one is a bug worth seeing. This used to decide which was which by looking
  // for Korean words in the message, so `--recipe nope` -- an ordinary typo --
  // printed a stack trace, and any refusal phrased in English would have too.
  // A type says what a language guess cannot.
  if (e?.refusal === true || e?.name === 'Refusal' || e instanceof TypeError) {
    die(e.message, 2);
  }
  process.stderr.write(`${e?.stack ?? e?.message ?? String(e)}\n`);
  process.exit(1);
});
