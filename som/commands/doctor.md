---
description: som 환경 점검 — Python 의존성, 시크릿 노출, 대용량 파일, 골든 테스트, Orca 상태.
allowed-tools: [Bash, Read]
---

# /som:doctor

```bash
PYTHONUTF8=1 python "${CLAUDE_PLUGIN_ROOT}/standard/scripts/doctor.py" --project "${CLAUDE_PROJECT_DIR}"
```

Report the output as-is. Exit codes:

| Exit | Meaning |
|---|---|
| 0 | ready |
| 1 | a hard failure -- fix it before running a pipeline |
| 2 | warnings only -- usable, but say what is degraded |

A hard failure on a private key found in the tree is not a false positive.
Report it prominently and do not offer to move or copy the file.
