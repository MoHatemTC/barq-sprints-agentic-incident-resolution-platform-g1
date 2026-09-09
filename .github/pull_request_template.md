## What and why

<!-- What changes, and what problem it solves. Link the issue: Closes #123 -->

## How it was verified

<!-- Commands you actually ran and their output. "Should work" is not verification. -->

```
```

## Checklist

- [ ] `just check` passes locally (lint, format, type-check, tests)
- [ ] No credential, token or instance secret added — including in `.env.example`
- [ ] ServiceNow scripts use **internal** choice values (`in_progress`), not display labels
- [ ] Any new ServiceNow artifact is in scope `x_2215032_ai_inc_0`, never Global
- [ ] Docs updated if behaviour, setup or the field model changed
