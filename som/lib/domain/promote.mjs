/**
 * Turning what was learned into a skill the person's Claude actually loads.
 *
 * A pattern is a single lesson. It reaches a worker only when `conduct` builds
 * a brief, which means it helps inside a som run and nowhere else. A habit --
 * "this person always wants the table before the prose, and every number
 * carries its source" -- is not one lesson, and it should apply when they open
 * Claude on a Tuesday to do something unrelated.
 *
 * So a *cluster* of patterns that has repeatedly proven itself is promoted
 * into a real `SKILL.md` under the person's own skills directory. That is the
 * only location Claude Code loads without being asked, which is the whole
 * point: personalisation you have to invoke is not personalisation.
 *
 * Three things keep this from becoming clutter, and all three are arithmetic
 * rather than judgement:
 *
 *   1. **A cluster, not a lesson.** One pattern is an observation. Three
 *      related patterns that all held up is a way of working.
 *   2. **Earned, not asserted.** The cluster's average confidence has to clear
 *      TRUSTED_AT, which only happens through repeated wins, and the patterns
 *      have to have actually been applied.
 *   3. **A hard cap.** Every skill's description is always-on context in every
 *      conversation the person has. Eight is already generous; without a cap
 *      this feature would slowly tax every prompt they ever write.
 *
 * Nothing here touches a disk. `lib/state/skills.mjs` does that.
 */
import { TRUSTED_AT } from "./patterns.mjs";

/** The promotion gate. Strict on purpose -- see the header. */
export const PROMOTE = Object.freeze({
  minCluster: 3,          // related patterns before it is a habit
  minAvgConfidence: TRUSTED_AT,
  minUses: 6,             // summed across the cluster: it has been applied
  minWinsEach: 1,         // every member has been right at least once
  maxSkills: 8,           // always-on context budget
  descriptionMax: 240,
  bodyMax: 6000,
});

const clean = (s) => String(s ?? "").replace(/[​-‍﻿]/g, "").trim();
const num = (v, d) => (typeof v === "number" && Number.isFinite(v) ? v : d);

/**
 * Slug for a skill directory: ASCII, lowercase, always `som-` prefixed.
 *
 * Korean tags are common here and transliterating them badly would produce
 * names nobody recognises, so a tag with no usable ASCII falls back to a hash
 * of itself rather than to an empty string -- `som-` alone would collide
 * across every such cluster and silently overwrite.
 */
export function slugFor(label, fallback = "habit") {
  const ascii = clean(label)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
  const base = ascii || clean(fallback).toLowerCase().replace(/[^a-z0-9]+/g, "-") || "habit";
  return `som-${base}`.replace(/-+/g, "-").replace(/-$/, "");
}

/**
 * Group live patterns into clusters that describe the same way of working.
 *
 * Tags first, because a tag is the author saying "these belong together".
 * Patterns with no tag fall back to sharing a trigger, which is weaker but is
 * how untagged lessons about the same subject actually relate. Union-find so
 * that A-B and B-C end up in one cluster rather than two overlapping ones --
 * overlapping clusters would promote the same pattern into two skills and
 * double its always-on cost.
 */
export function clusterPatterns(patterns = []) {
  const live = patterns.filter((p) => p && !p.retired);
  const parent = live.map((_, i) => i);
  const find = (i) => (parent[i] === i ? i : (parent[i] = find(parent[i])));
  const union = (a, b) => { const ra = find(a), rb = find(b); if (ra !== rb) parent[rb] = ra; };

  const keyed = new Map();                       // tag|trigger -> first index
  live.forEach((p, i) => {
    const keys = [
      ...(p.tags ?? []).map((t) => `t:${clean(t).toLowerCase()}`),
      ...(p.triggers ?? []).map((t) => `g:${clean(t).toLowerCase()}`),
    ].filter((k) => k.length > 2);
    for (const k of keys) {
      if (keyed.has(k)) union(keyed.get(k), i);
      else keyed.set(k, i);
    }
  });

  const groups = new Map();
  live.forEach((p, i) => {
    const r = find(i);
    if (!groups.has(r)) groups.set(r, []);
    groups.get(r).push(p);
  });

  return [...groups.values()].map(summarise)
    .sort((a, b) => b.avgConfidence - a.avgConfidence || b.uses - a.uses);
}

function summarise(members) {
  const conf = members.map((p) => num(p.confidence, 0));
  const tally = new Map();
  for (const p of members) {
    for (const t of p.tags ?? []) {
      const k = clean(t);
      if (k) tally.set(k, (tally.get(k) ?? 0) + 2);   // a tag outvotes a trigger
    }
    for (const t of p.triggers ?? []) {
      const k = clean(t);
      if (k) tally.set(k, (tally.get(k) ?? 0) + 1);
    }
  }
  const ranked = [...tally.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const label = ranked[0]?.[0] ?? members[0]?.title ?? "habit";
  return {
    label,
    slug: slugFor(label, members[0]?.title),
    members,
    triggers: ranked.slice(0, 8).map(([k]) => k),
    avgConfidence: conf.length ? Math.round(conf.reduce((a, b) => a + b, 0) / conf.length) : 0,
    uses: members.reduce((a, p) => a + num(p.uses, 0), 0),
    wins: members.reduce((a, p) => a + num(p.wins, 0), 0),
    losses: members.reduce((a, p) => a + num(p.losses, 0), 0),
  };
}

/**
 * Which clusters may become skills, and why the others may not.
 *
 * Returns both halves. A gate that only reports its passes teaches nobody what
 * it wants, and "nothing was promoted" with no reason reads as a broken
 * feature rather than a working standard.
 */
export function promotable(clusters = [], { existing = [], cap = PROMOTE.maxSkills } = {}) {
  const ready = [];
  const held = [];
  const taken = new Set(existing.map((s) => String(s?.name ?? s)));
  let room = Math.max(0, cap - taken.size);

  for (const c of clusters) {
    const why = [];
    if (c.members.length < PROMOTE.minCluster) {
      why.push(`관련 패턴이 ${c.members.length}건 — ${PROMOTE.minCluster}건 이상이어야 습관으로 봅니다`);
    }
    if (c.avgConfidence < PROMOTE.minAvgConfidence) {
      why.push(`평균 신뢰도 ${c.avgConfidence} — ${PROMOTE.minAvgConfidence} 이상이어야 합니다`);
    }
    if (c.uses < PROMOTE.minUses) {
      why.push(`적용 ${c.uses}회 — ${PROMOTE.minUses}회 이상 실제로 쓰여야 합니다`);
    }
    if (c.members.some((p) => num(p.wins, 0) < PROMOTE.minWinsEach)) {
      why.push("한 번도 맞은 적 없는 패턴이 섞여 있습니다");
    }
    if (c.losses > c.wins) {
      why.push(`빗나감 ${c.losses}회 > 맞음 ${c.wins}회`);
    }
    if (taken.has(c.slug)) {
      why.push(`이미 ${c.slug} 스킬이 있습니다 — 갱신하려면 promote --update`);
    }

    if (why.length) { held.push({ cluster: c, why }); continue; }
    if (room <= 0) {
      held.push({ cluster: c, why: [
        `생성된 스킬이 이미 ${taken.size}개입니다 (상한 ${cap}). 스킬 설명문은 ` +
        "모든 대화에서 항상 읽히므로, 늘리는 대신 안 쓰는 것을 retire 하세요."] });
      continue;
    }
    room -= 1;
    ready.push(c);
  }
  return { ready, held };
}

/**
 * The SKILL.md text.
 *
 * The frontmatter carries `som-generated: true` and the source pattern ids.
 * That marker is what makes this safe: nothing else in the plugin will ever
 * overwrite or delete a skill directory that does not have it, so a file the
 * person wrote themselves is untouchable even if the names collide.
 */
export function renderSkill(cluster, { now = new Date() } = {}) {
  const triggers = cluster.triggers.filter(Boolean).slice(0, 6);
  const summary = cluster.members.map((p) => clean(p.title)).filter(Boolean);

  let description =
    `${summary[0] || cluster.label}. 이전 작업에서 반복 확인된 진행 방식입니다` +
    (triggers.length ? ` — ${triggers.slice(0, 4).join(", ")} 관련 작업에서 적용하세요.` : ".");
  if (description.length > PROMOTE.descriptionMax) {
    description = `${description.slice(0, PROMOTE.descriptionMax - 1)}…`;
  }

  const lines = [
    "---",
    `name: ${cluster.slug}`,
    `description: ${description.replace(/\n/g, " ")}`,
    "som-generated: true",
    `som-sources: [${cluster.members.map((p) => p.id).join(", ")}]`,
    `som-confidence: ${cluster.avgConfidence}`,
    `som-generated-at: ${now.toISOString()}`,
    "---",
    "",
    `# ${cluster.label}`,
    "",
    "이 파일은 som 이 자동 생성했습니다. 아래 항목은 **지어낸 조언이 아니라**,",
    `실제 작업에서 ${cluster.uses}회 적용되어 ${cluster.wins}회 맞고 ${cluster.losses}회 빗나간 기록에서 나왔습니다.`,
    "",
  ];

  for (const p of cluster.members) {
    lines.push(`## ${clean(p.title)}`, "");
    if (p.trigger) lines.push(`**언제** ${clean(p.trigger)}`, "");
    if (p.action) lines.push(`**할 것** ${clean(p.action)}`, "");
    if (p.why) lines.push(`**왜** ${clean(p.why)}`, "");
    const ev = (p.evidence ?? [])[0];
    if (ev) {
      lines.push(`근거: \`${clean(ev.kind)} ${clean(ev.ref)}\`` +
                 (ev.note ? ` — ${clean(ev.note)}` : ""), "");
    }
    lines.push(`<sub>신뢰도 ${num(p.confidence, 0)}/100 · 적용 ${num(p.uses, 0)}회 · ` +
               `빗나감 ${num(p.losses, 0)}회 · id \`${p.id}\`</sub>`, "");
  }

  lines.push(
    "---",
    "",
    "## 이 스킬이 안 맞으면",
    "",
    "지금 상황에 맞지 않으면 따르지 말고, 왜 안 맞았는지 말해 주세요.",
    "이 파일은 관찰에서 나온 것이라 틀릴 수 있고, 틀렸다는 사실이 다음 판단을 고칩니다.",
    "",
    "지우려면 이 디렉토리를 삭제하거나:",
    "",
    "```bash",
    `som learn skills retire ${cluster.slug}`,
    "```",
    "");

  let text = lines.join("\n");
  if (text.length > PROMOTE.bodyMax) {
    text = `${text.slice(0, PROMOTE.bodyMax)}\n\n<!-- 분량 상한에서 잘렸습니다 -->\n`;
  }
  return { name: cluster.slug, text, description };
}
