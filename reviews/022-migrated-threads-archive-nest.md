# Review 022: migrated-threads-archive-nest

verdict: accept
report: /home/user/workspace/repo/.amp/in/orb-backup/pstack/pstack/kitchen/runs/kitchen-followups/reports/022-migrated-threads-archive-nest.md
head: ece578550c5019b3e36b6d0b37a25039d12859b7
engine: joo 2026-11-21T07-45-41-938Z-9dfb1ae5

## Amendments

none

## Checked

- verdicts/022-migrated-threads-archive-nest-v1.md: clean, no findings. A thread from before store-to-git archives by the local rule; `nest --discover` records a run's unnested threads and its anchor.
- Deviations accepted: one assertion in tests/test_pstack_core.py outside Scope (it compared the whole backend record, which now carries migrated_at); an extra SPEC commit; timebox overrun on gate runs.
- joo: four lows, none blocking, all to followups. Using the retirement time `at` as a start time errs toward keeping a thread, never toward archiving a live one. The archived filter that runs before the name merge only lists extra candidates. The duplicated thread-set helper and the direct Amp call in `nest --discover` are structure.
