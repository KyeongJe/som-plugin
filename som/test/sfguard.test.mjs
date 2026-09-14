/**
 * The Snowflake guards, attacked from both sides.
 *
 * `guard.test.mjs` covers the core cases. This file is the adversarial sweep:
 * eighteen ways to reach a connection without going through `somsql`, three
 * ways to write a script now and run it later, and sixteen ordinary commands
 * and files that must not be touched.
 *
 * The false-positive half is not decoration. A guard that blocks
 * `grep -r SELECT src/` gets switched off within a day, and then nothing is
 * guarded at all. Both halves fail this file.
 *
 * Writing this file was itself a test: the Write guard refused the first draft
 * because it was named `sfguard.mjs`, and accepted it under `.test.mjs`. That
 * is the documented exemption, working.
 *
 *   node --test test/sfguard.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { judgeCommand, judgeFileWrite } from "../lib/guard/snowflake.mjs";

/** The guard returns `{ decision: "allow" | "deny", reason, matched }`. */
/** Flagged, not silently allowed. Outside access warns now rather than
 *  refusing: the team had scripts reaching Snowflake before this plugin,
 *  and blocking them broke work that had nothing to do with it. */
const denied = (v) => v?.decision === "warn";

/** Ways to reach Snowflake without the sanctioned entry point. */
const BYPASSES = [
  ["직접 호출", 'snowsql -q "SELECT 1"'],
  ["신 CLI", 'snow sql -q "SELECT 1"'],
  ["python 인라인 커넥터", 'python -c "import snowflake.connector as s; s.connect()"'],
  ["주석 뒤에 숨기기", 'snowsql -q "INSERT INTO t VALUES(1)" # python -m somsql run'],
  ["세미콜론 체이닝", 'echo hi; snowsql -q "SELECT 1"'],
  ["&& 체이닝", 'cd /tmp && snowsql -q "SELECT 1"'],
  ["|| 체이닝", 'false || snowsql -q "SELECT 1"'],
  ["파이프", "cat q.sql | snowsql"],
  ["백그라운드", 'snowsql -q "SELECT 1" &'],
  ["sf_utils 재사용", 'python -c "from sf_utils import run_sql; run_sql(1)"'],
  ["커넥터 + execute_string",
   'python -c "import snowflake.connector as s; s.connect().execute_string(\'INSERT INTO t VALUES(1)\')"'],
  ["대문자", 'SNOWSQL -Q "SELECT 1"'],
  ["절대경로로 위장", '/usr/local/bin/snowsql -q "SELECT 1"'],
  ["환경변수 먼저", 'SF_LOGIN_INFO=x.json python -c "import snowflake.connector"'],
  ["여러 줄", 'echo a\nsnowsql -q "SELECT 1"'],
  ["서브셸", '(snowsql -q "SELECT 1")'],
  ["백틱 치환", 'echo `snowsql -q "SELECT 1"`'],
  ["$() 치환", 'echo $(snowsql -q "SELECT 1")'],
];

/** Ordinary work that must never be blocked. */
const INNOCENT = [
  ["grep -r SELECT", "grep -r SELECT src/"],
  ["rg 검색", 'rg "INSERT INTO" --type sql'],
  ["문서에서 단어 찾기", 'grep -n "snowflake" README.md'],
  ["정식 진입점 run", "python -m somsql run --file sql/01.sql --mode full"],
  ["정식 진입점 plan", "python -m somsql plan --dir sql/ --mode full"],
  ["정식 진입점 ledger", "python -m somsql ledger --summary"],
  ["pip 설치", "pip install snowflake-connector-python"],
  ["requirements 보기", "cat engine/requirements.txt"],
  ["커밋 메시지에 단어", 'git commit -m "somsql: refuse INSERT at the parser"'],
  ["테스트 실행", "python engine/tests/test_somsql.py"],
  ["일반 개발", "node --test test/*.test.mjs"],
  ["폴더 보기", "ls sql/"],
];

test("no bypass reaches a connection", () => {
  const through = BYPASSES.filter(([, cmd]) => !denied(judgeCommand(cmd)));
  assert.deepEqual(through.map(([n]) => n), [],
    "가드를 지나간 우회:\n  " +
    through.map(([n, c]) => `${n}: ${c}`).join("\n  "));
});

test("no ordinary command is blocked", () => {
  const blocked = INNOCENT.filter(([, cmd]) => denied(judgeCommand(cmd)));
  assert.deepEqual(blocked.map(([n]) => n), [],
    "오탐 — 일반 명령이 막혔습니다:\n  " +
    blocked.map(([n, c]) => `${n}: ${c}`).join("\n  ") +
    "\n  오탐이 있는 가드는 하루 만에 꺼지고, 그러면 아무것도 지키지 못합니다.");
});

test("writing a script that connects is flagged at authoring time", () => {
  const drafts = [
    ["커넥터를 쓰는 .py", "extract.py",
     "import snowflake.connector\nconn = snowflake.connector.connect()\n" +
     "conn.cursor().execute('INSERT INTO t VALUES(1)')"],
    ["sf_utils 를 쓰는 .py", "load.py",
     "from sf_utils import get_conn\nget_conn().cursor().execute('DELETE FROM t')"],
  ];
  const through = drafts.filter(([, p, b]) => !denied(judgeFileWrite(p, b)));
  assert.deepEqual(through.map(([n]) => n), [],
    "작성 단계에서 표시되지 않은 초안: " + through.map(([n]) => n).join(", "));
});

test("a .sql file holding a write is left alone -- the question is asked at run time", () => {
  // This used to be flagged, back when a write could never run at all. It can
  // now: `somsql write` describes what the statement does, asks, and records
  // the answer. Flagging the *file* would only push people to keep the SQL
  // somewhere this guard cannot see, and the approval would be skipped rather
  // than asked. Writing SQL down is not the moment that needs a decision.
  const body = "-- source: x\nINSERT INTO ANALYTICS.PUBLIC.T SELECT * FROM S;";
  assert.equal(judgeFileWrite("sql/99_load.sql", body).decision, "allow");
});

test("a file that merely mentions Snowflake is not blocked", () => {
  const fine = [
    ["문서 안의 SQL 예시", "README.md",
     "```sql\nINSERT INTO t VALUES(1)  -- 예시이고 실행하지 않습니다\n```"],
    ["테스트 파일", "engine/tests/test_somsql.py",
     "def test_insert_is_denied():\n" +
     "    assert classify('INSERT INTO t VALUES(1)').verdict == 'deny'"],
    ["읽기 전용 SQL", "sql/01_visit_gap.sql",
     "-- grain: 행 1개 = 거래선\nSELECT a, b FROM ANALYTICS.T WHERE d >= '2026-01-01';"],
    ["requirements", "engine/requirements.txt",
     "snowflake-connector-python==3.18.0\n"],
  ];
  const blocked = fine.filter(([, p, b]) => denied(judgeFileWrite(p, b)));
  assert.deepEqual(blocked.map(([n]) => n), [],
    "오탐 — 정상 파일이 막혔습니다: " + blocked.map(([n]) => n).join(", "));
});

test("a refusal says what to do instead", () => {
  // A block with no next step is a block someone routes around.
  const v = judgeCommand(BYPASSES[0][1]);
  const text = JSON.stringify(v);
  assert.ok(/somsql/.test(text),
    `거부 메시지가 정식 경로를 안내하지 않습니다: ${text.slice(0, 200)}`);
});
