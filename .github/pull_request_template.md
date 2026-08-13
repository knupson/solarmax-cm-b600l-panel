<!--
By opening this pull request you accept the CLA in CONTRIBUTING.md: you grant the
project owner a non-exclusive licence to use and relicense your contribution, and
you keep ownership of your own work.
-->

## What changes, and why

<!-- The concrete problem, not the name of the change. If it fixes a bug, say what
     the cause was: that is what keeps it from coming back. -->

## How it was verified

- [ ] `python -m pytest` passes in full
- [ ] The test for this change **fails without the fix** (if it is a fix)
- [ ] I looked at the result, not just the tests: `python -m vmaxpanel --save preview.png`

<!-- If it is visual, paste the before and after. Four bugs survived 590 green tests
     in this repo until someone looked at a PNG. -->

## Checks

- [ ] No new Python dependencies
- [ ] `daemon/` untouched
- [ ] No third-party fonts or DLLs committed
- [ ] Anything that opens a process or a file closes it in `close()`
