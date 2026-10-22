# Behaviour spec

What an agent, a skill doc or a script depends on in pair.sh (pair, guided, trio) and kitchen.sh. Plan 043's contract: the golden
transcripts plus this file; a behaviour no line names may change. Generated at 881d119 by the commands below; regenerate with `python3 tests/spec_gen.py`.

Line format: `- <behaviour> — <source file:line> — <test id | decided, U<n> | not a caller contract: reason>`.
- A test id counts only when the test asserts the behaviour: the literal appears in an assert expression or in a string the test holds next to an assert, or the item is a command that a golden transcript runs (`$ pair.sh <command>`). `(+n)` says how many other tests also do.
- The match is a literal-string heuristic. A line no test matches is counted in the table below and needs a test, a decision, or a `not a caller contract` entry with its reason.

## How this was built

```
# commands (help output of each entry script)
for s in pstack-pair pstack-pair-guided pstack-trio; do PATH=tests/pstack-pair/bin:$PATH HERDR_ENV=1 bash skills/$s/scripts/pair.sh help; done
PATH=tests/pstack-pair/bin:$PATH HERDR_ENV=1 bash skills/pstack-kitchen/scripts/kitchen.sh help
# exit codes: every Die(..., 2-8) and bash die "..." N in the runtime scripts
git grep -nE 'Die\(|die ".*" [2-8]' -- skills/pstack-pair/scripts skills/pstack-pair-guided/scripts skills/pstack-trio/scripts skills/pstack-kitchen/scripts
git grep -nE 'return [2-8]$|exit [2-8]' -- skills/pstack-pair/scripts skills/pstack-kitchen/scripts
# messages: each prefix the reference docs tell an agent to look for
git grep -noE '`[A-Za-z_-]+( [a-z]+)?:`' -- 'skills/*.md'
git grep -nF '<prefix>' -- skills/pstack-pair/scripts skills/pstack-pair-guided/scripts skills/pstack-trio/scripts skills/pstack-kitchen/scripts
# store files: the `| \`path\` |` tables in each SKILL.md; pair.json fields
git grep -nE '^\| `' -- skills/pstack-pair/SKILL.md skills/pstack-pair-guided/SKILL.md skills/pstack-trio/SKILL.md skills/pstack-kitchen/SKILL.md
git grep -noE '[.\'"](sidekick|master|consultant|verifier|dispatch|sent|verifying)\.[a-z_]+' -- skills/pstack-pair/scripts/pstack_core skills/pstack-kitchen/scripts/kitchen_core
```

Exit codes callers branch on: 0 ok; 1 usage or precondition; 2 herdr or gate failure; 3 agent blocked; 4 no report, queue empty, check-in or still running; 5 wrong state; 6 plan not agreed; 7 steer, consult or ask cap; 8 kinds not diverse.

## Counts

| section | tested | A/B only | missing | decided | not a contract |
| --- | --- | --- | --- | --- | --- |
| Commands | 143 | 0 | 0 | 0 | 0 |
| Exit codes | 113 | 0 | 0 | 0 | 5 |
| Messages | 32 | 0 | 0 | 0 | 0 |
| Store files | 28 | 0 | 0 | 0 | 0 |
| pair.json fields | 49 | 0 | 0 | 0 | 0 |
| Decided | 10 | 0 | 0 | 3 | 0 |
| total | 375 | 0 | 0 | 3 | 5 |

## Commands

- `pair.sh init <slug> [--store DIR]` — skills/pstack-pair/scripts/pair.sh help:3 — test_pstack_pair.PairScriptTests.test_pair
- `pair.sh spawn <store> --kind KIND [--fallback KIND [--fallback-arg ARG]...] [--tab | --split] [--permission MODE|none] [--direction right|down] [--pane ID] [--timeout MS] [-- agent-args...]` — skills/pstack-pair/scripts/pair.sh help:4 — test_pstack_pair.PairScriptTests.test_pair
- `pair.sh rotate <store>` — skills/pstack-pair/scripts/pair.sh help:10 — test_pstack_pair.PairScriptTests.test_pair
- `pair.sh failover <store> [--reason TEXT] [--force]` — skills/pstack-pair/scripts/pair.sh help:12 — test_pstack_core.CoreTests.test_a_wrong_typed_value_is_damage_through_a_real_read_command_and_a_real_write_command
- `pair.sh permission print the master's detected permission mode` — skills/pstack-pair/scripts/pair.sh help:15 — test_pstack_pair.PairScriptTests.test_pair
- `pair.sh new-plan <store> <slug>` — skills/pstack-pair/scripts/pair.sh help:16 — test_pstack_core.CoreTests.test_new_steer_counts_files_caps_fresh_steers_and_answers_objections
- `pair.sh discuss <store> <plan-path> [--timeout MS]` — skills/pstack-pair/scripts/pair.sh help:17 — test_pstack_pair.PairScriptTests.test_pair
- `pair.sh new-brief <store> <slug>` — skills/pstack-pair/scripts/pair.sh help:19 — test_pstack_core.CoreTests.test_new_steer_counts_files_caps_fresh_steers_and_answers_objections
- `pair.sh dispatch <store> <brief-path> [--timeout MS | --every MIN] [--send-only]` — skills/pstack-pair/scripts/pair.sh help:20 — test_pstack_pair.PairScriptTests.test_pair
- `pair.sh wait <store> [--timeout MS | --every MIN] wait for the report of the unit the sidekick is on; prints its path` — skills/pstack-pair/scripts/pair.sh help:24 — test_pstack_pair.PairScriptTests.test_pair
- `pair.sh queue <store> <brief-path> [--replace]` — skills/pstack-pair/scripts/pair.sh help:27 — test_pstack_pair.PairScriptTests.test_pair
- `pair.sh step <store> <sha> <summary> [--resolves n1,n2 | 001-n1]` — skills/pstack-pair/scripts/pair.sh help:29 — test_pstack_pair.PairScriptTests.test_pair
- `pair.sh notes <store>` — skills/pstack-pair/scripts/pair.sh help:32 — test_pstack_pair.PairScriptTests.test_pair
- `pair.sh new-note <store> <NNN>` — skills/pstack-pair/scripts/pair.sh help:33 — test_pstack_pair.PairScriptTests.test_pair
- `pair.sh note <store> <note-draft-path>` — skills/pstack-pair/scripts/pair.sh help:34 — test_pstack_pair.PairScriptTests.test_pair
- `pair.sh finish <store> <report-path>` — skills/pstack-pair/scripts/pair.sh help:35 — test_pstack_pair.PairScriptTests.test_pair
- `pair.sh next <store>` — skills/pstack-pair/scripts/pair.sh help:37 — test_pstack_pair.PairScriptTests.test_pair
- `pair.sh progress <store> <text>` — skills/pstack-pair/scripts/pair.sh help:38 — test_pstack_pair.PairScriptTests.test_pair
- `pair.sh new-steer <store> <NNN> [--supersedes STEER | --force]` — skills/pstack-pair/scripts/pair.sh help:39 — test_pstack_pair.PairScriptTests.test_pair
- `pair.sh steer <store> <steer-path> [--interrupt] [--force] [--timeout MS | --every MIN]` — skills/pstack-pair/scripts/pair.sh help:42 — test_pstack_pair.PairScriptTests.test_pair
- `pair.sh report <store> [NNN]` — skills/pstack-pair/scripts/pair.sh help:46 — test_pstack_pair.PairScriptTests.test_pair
- `pair.sh notify <store> <report-path>` — skills/pstack-pair/scripts/pair.sh help:47 — test_pstack_pair.PairScriptTests.test_pair
- `pair.sh stop <store> [--timeout MS]` — skills/pstack-pair/scripts/pair.sh help:48 — test_pstack_pair.PairScriptTests.test_pair
- `pair.sh scratch <store> <id> [--at SHA] [--remove]` — skills/pstack-pair/scripts/pair.sh help:49 — test_pstack_pair.PairScriptTests.test_pair
- `pair.sh pause <store> [--reason TEXT]` — skills/pstack-pair/scripts/pair.sh help:52 — test_pstack_pair.PairScriptTests.test_pair
- `pair.sh resume <store>` — skills/pstack-pair/scripts/pair.sh help:54 — test_pstack_pair.PairScriptTests.test_pair
- `pair.sh status <store>` — skills/pstack-pair/scripts/pair.sh help:55 — test_pstack_pair.PairScriptTests.test_pair
- `pair.sh log <store> <phase> <decision> <why> <evidence> <result>` — skills/pstack-pair/scripts/pair.sh help:56 — test_pstack_pair.PairScriptTests.test_pair
- `pair.sh metrics <store>` — skills/pstack-pair/scripts/pair.sh help:58 — test_pstack_pair.PairScriptTests.test_pair
- `pair.sh (guided) init <slug> [--store DIR]` — skills/pstack-pair-guided/scripts/pair.sh help:3 — test_pstack_pair.PairScriptTests.test_guided
- `pair.sh (guided) spawn <store> --kind KIND [--fallback KIND [--fallback-arg ARG]...] [--tab | --split] [--permission MODE|none] [--direction right|down] [--pane ID] [--timeout MS] [-- agent-args...]` — skills/pstack-pair-guided/scripts/pair.sh help:4 — test_pstack_pair.PairScriptTests.test_guided
- `pair.sh (guided) failover <store> [--reason TEXT] [--force]` — skills/pstack-pair-guided/scripts/pair.sh help:10 — test_pstack_core.CoreTests.test_a_wrong_typed_value_is_damage_through_a_real_read_command_and_a_real_write_command
- `pair.sh (guided) permission print the master's detected permission mode` — skills/pstack-pair-guided/scripts/pair.sh help:13 — test_pstack_pair.PairScriptTests.test_guided
- `pair.sh (guided) new-plan <store> <slug>` — skills/pstack-pair-guided/scripts/pair.sh help:14 — test_pstack_core.CoreTests.test_new_steer_counts_files_caps_fresh_steers_and_answers_objections
- `pair.sh (guided) discuss <store> <plan-path> [--timeout MS]` — skills/pstack-pair-guided/scripts/pair.sh help:15 — test_pstack_pair.PairScriptTests.test_guided
- `pair.sh (guided) new-direction <store> <plan-path>` — skills/pstack-pair-guided/scripts/pair.sh help:17 — test_pstack_pair.PairScriptTests.test_guided
- `pair.sh (guided) new-answer <store> <NNN> [--force]` — skills/pstack-pair-guided/scripts/pair.sh help:18 — test_pstack_pair.PairScriptTests.test_guided
- `pair.sh (guided) answer <store> <answer-path> [--timeout MS | --every MIN]` — skills/pstack-pair-guided/scripts/pair.sh help:19 — test_pstack_pair.PairScriptTests.test_guided
- `pair.sh (guided) new-brief <store> <slug>` — skills/pstack-pair-guided/scripts/pair.sh help:21 — test_pstack_core.CoreTests.test_new_steer_counts_files_caps_fresh_steers_and_answers_objections
- `pair.sh (guided) dispatch <store> <brief-path> [--timeout MS | --every MIN] [--send-only]` — skills/pstack-pair-guided/scripts/pair.sh help:22 — test_pstack_pair.PairScriptTests.test_guided
- `pair.sh (guided) wait <store> [--timeout MS | --every MIN] wait for the report of the unit the sidekick is on; prints its path` — skills/pstack-pair-guided/scripts/pair.sh help:27 — test_pstack_pair.PairScriptTests.test_guided
- `pair.sh (guided) queue <store> <brief-path> [--replace]` — skills/pstack-pair-guided/scripts/pair.sh help:30 — test_pstack_pair.PairScriptTests.test_guided
- `pair.sh (guided) step <store> <sha> <summary> [--resolves n1,n2 | 001-n1]` — skills/pstack-pair-guided/scripts/pair.sh help:32 — test_pstack_pair.PairScriptTests.test_guided
- `pair.sh (guided) notes <store>` — skills/pstack-pair-guided/scripts/pair.sh help:35 — test_pstack_pair.PairScriptTests.test_guided
- `pair.sh (guided) new-note <store> <NNN>` — skills/pstack-pair-guided/scripts/pair.sh help:36 — test_pstack_pair.PairScriptTests.test_guided
- `pair.sh (guided) note <store> <note-draft-path>` — skills/pstack-pair-guided/scripts/pair.sh help:37 — test_pstack_pair.PairScriptTests.test_guided
- `pair.sh (guided) finish <store> <report-path>` — skills/pstack-pair-guided/scripts/pair.sh help:38 — test_pstack_pair.PairScriptTests.test_guided
- `pair.sh (guided) next <store>` — skills/pstack-pair-guided/scripts/pair.sh help:40 — test_pstack_pair.PairScriptTests.test_guided
- `pair.sh (guided) progress <store> <text>` — skills/pstack-pair-guided/scripts/pair.sh help:41 — test_pstack_pair.PairScriptTests.test_guided
- `pair.sh (guided) new-steer <store> <NNN> [--supersedes STEER | --force]` — skills/pstack-pair-guided/scripts/pair.sh help:42 — test_pstack_pair.PairScriptTests.test_guided
- `pair.sh (guided) steer <store> <steer-path> [--interrupt] [--force] [--timeout MS | --every MIN]` — skills/pstack-pair-guided/scripts/pair.sh help:45 — test_pstack_pair.PairScriptTests.test_guided
- `pair.sh (guided) report <store> [NNN]` — skills/pstack-pair-guided/scripts/pair.sh help:49 — test_pstack_pair.PairScriptTests.test_guided
- `pair.sh (guided) notify <store> <report-path>` — skills/pstack-pair-guided/scripts/pair.sh help:50 — test_pstack_pair.PairScriptTests.test_guided
- `pair.sh (guided) stop <store> [--timeout MS]` — skills/pstack-pair-guided/scripts/pair.sh help:51 — test_pstack_pair.PairScriptTests.test_guided
- `pair.sh (guided) scratch <store> <id> [--at SHA] [--remove]` — skills/pstack-pair-guided/scripts/pair.sh help:52 — test_pstack_pair.PairScriptTests.test_guided
- `pair.sh (guided) pause <store> [--reason TEXT]` — skills/pstack-pair-guided/scripts/pair.sh help:55 — test_pstack_pair.PairScriptTests.test_guided
- `pair.sh (guided) resume <store>` — skills/pstack-pair-guided/scripts/pair.sh help:57 — test_pstack_pair.PairScriptTests.test_guided
- `pair.sh (guided) status <store>` — skills/pstack-pair-guided/scripts/pair.sh help:58 — test_pstack_pair.PairScriptTests.test_guided
- `pair.sh (guided) log <store> <phase> <decision> <why> <evidence> <result>` — skills/pstack-pair-guided/scripts/pair.sh help:59 — test_pstack_pair.PairScriptTests.test_guided
- `pair.sh (guided) metrics <store>` — skills/pstack-pair-guided/scripts/pair.sh help:61 — test_pstack_pair.PairScriptTests.test_guided
- `pair.sh (trio) init <slug> [--store DIR]` — skills/pstack-trio/scripts/pair.sh help:3 — test_pstack_pair.PairScriptTests.test_trio
- `pair.sh (trio) spawn <store> --sidekick KIND --consultant KIND [--only sidekick|consultant] [--permission MODE|none]` — skills/pstack-trio/scripts/pair.sh help:4 — test_pstack_pair.PairScriptTests.test_trio
- `pair.sh (trio) rotate <store>` — skills/pstack-trio/scripts/pair.sh help:11 — test_pstack_pair.PairScriptTests.test_trio
- `pair.sh (trio) failover <store> [--reason TEXT] [--force]` — skills/pstack-trio/scripts/pair.sh help:13 — test_pstack_core.CoreTests.test_a_wrong_typed_value_is_damage_through_a_real_read_command_and_a_real_write_command
- `pair.sh (trio) permission print the master's detected permission mode` — skills/pstack-trio/scripts/pair.sh help:16 — test_pstack_pair.PairScriptTests.test_trio
- `pair.sh (trio) new-plan <store> <slug>` — skills/pstack-trio/scripts/pair.sh help:17 — test_pstack_core.CoreTests.test_new_steer_counts_files_caps_fresh_steers_and_answers_objections
- `pair.sh (trio) discuss <store> <plan-path> [--timeout MS]` — skills/pstack-trio/scripts/pair.sh help:18 — test_pstack_pair.PairScriptTests.test_trio
- `pair.sh (trio) new-consult <store> <NNN> --kind design|finding|objection|review|glance [--force]` — skills/pstack-trio/scripts/pair.sh help:21 — test_pstack_pair.PairScriptTests.test_trio
- `pair.sh (trio) consult <store> <consult-path> [--timeout MS]` — skills/pstack-trio/scripts/pair.sh help:24 — test_pstack_pair.PairScriptTests.test_trio
- `pair.sh (trio) advice <store> [NNN]` — skills/pstack-trio/scripts/pair.sh help:27 — test_pstack_pair.PairScriptTests.test_trio
- `pair.sh (trio) new-brief <store> <slug>` — skills/pstack-trio/scripts/pair.sh help:28 — test_pstack_core.CoreTests.test_new_steer_counts_files_caps_fresh_steers_and_answers_objections
- `pair.sh (trio) dispatch <store> <brief-path> [--timeout MS | --every MIN] [--send-only]` — skills/pstack-trio/scripts/pair.sh help:29 — test_pstack_pair.PairScriptTests.test_trio
- `pair.sh (trio) wait <store> [--timeout MS | --every MIN] wait for the report of the unit the sidekick is on; prints its path` — skills/pstack-trio/scripts/pair.sh help:33 — test_pstack_pair.PairScriptTests.test_trio
- `pair.sh (trio) queue <store> <brief-path> [--replace]` — skills/pstack-trio/scripts/pair.sh help:36 — test_pstack_pair.PairScriptTests.test_trio
- `pair.sh (trio) step <store> <sha> <summary> [--resolves n1,n2 | 001-n1]` — skills/pstack-trio/scripts/pair.sh help:38 — test_pstack_pair.PairScriptTests.test_trio
- `pair.sh (trio) notes <store>` — skills/pstack-trio/scripts/pair.sh help:41 — test_pstack_pair.PairScriptTests.test_trio
- `pair.sh (trio) new-note <store> <NNN>` — skills/pstack-trio/scripts/pair.sh help:42 — test_pstack_pair.PairScriptTests.test_trio
- `pair.sh (trio) note <store> <note-draft-path>` — skills/pstack-trio/scripts/pair.sh help:43 — test_pstack_pair.PairScriptTests.test_trio
- `pair.sh (trio) finish <store> <report-path>` — skills/pstack-trio/scripts/pair.sh help:44 — test_pstack_pair.PairScriptTests.test_trio
- `pair.sh (trio) next <store>` — skills/pstack-trio/scripts/pair.sh help:46 — test_pstack_pair.PairScriptTests.test_trio
- `pair.sh (trio) progress <store> <text>` — skills/pstack-trio/scripts/pair.sh help:47 — test_pstack_pair.PairScriptTests.test_trio
- `pair.sh (trio) new-steer <store> <NNN> [--supersedes STEER | --force]` — skills/pstack-trio/scripts/pair.sh help:48 — test_pstack_pair.PairScriptTests.test_trio
- `pair.sh (trio) steer <store> <steer-path> [--interrupt] [--force] [--timeout MS | --every MIN]` — skills/pstack-trio/scripts/pair.sh help:51 — test_pstack_pair.PairScriptTests.test_trio
- `pair.sh (trio) report <store> [NNN]` — skills/pstack-trio/scripts/pair.sh help:55 — test_pstack_pair.PairScriptTests.test_trio
- `pair.sh (trio) notify <store> <path>` — skills/pstack-trio/scripts/pair.sh help:56 — test_pstack_pair.PairScriptTests.test_trio
- `pair.sh (trio) stop <store> [--only sidekick|consultant] [--timeout MS]` — skills/pstack-trio/scripts/pair.sh help:58 — test_pstack_pair.PairScriptTests.test_trio
- `pair.sh (trio) scratch <store> <id> [--at SHA] [--remove]` — skills/pstack-trio/scripts/pair.sh help:60 — test_pstack_pair.PairScriptTests.test_trio
- `pair.sh (trio) pause <store> [--reason TEXT]` — skills/pstack-trio/scripts/pair.sh help:63 — test_pstack_pair.PairScriptTests.test_trio
- `pair.sh (trio) resume <store>` — skills/pstack-trio/scripts/pair.sh help:65 — test_pstack_pair.PairScriptTests.test_trio
- `pair.sh (trio) status <store>` — skills/pstack-trio/scripts/pair.sh help:66 — test_pstack_pair.PairScriptTests.test_trio
- `pair.sh (trio) log <store> <phase> <decision> <why> <evidence> <result>` — skills/pstack-trio/scripts/pair.sh help:67 — test_pstack_pair.PairScriptTests.test_trio
- `pair.sh (trio) metrics <store>` — skills/pstack-trio/scripts/pair.sh help:69 — test_pstack_pair.PairScriptTests.test_trio
- `kitchen.sh log <store> <phase> <decision> <why> <evidence> <result>` — skills/pstack-kitchen/scripts/kitchen.sh help:8 — test_pstack_kitchen.KitchenScriptTests.test_kitchen_log_records_the_decision_and_a_master_log_event
- `kitchen.sh spawn <store> [--kind KIND] [--fallback KIND [--fallback-arg ARG]...] [--tab | --split] [--permission MODE|none] [-- args...]` — skills/pstack-kitchen/scripts/kitchen.sh help:11 — test_pstack_core.CoreTests.test_spawn_behaviour
- `kitchen.sh consultant <store> --reason TEXT [--kind KIND] [-- args...]` — skills/pstack-kitchen/scripts/kitchen.sh help:15 — test_pstack_core.CoreTests.test_consultant_behaviour
- `kitchen.sh classify <store> <brief-path>` — skills/pstack-kitchen/scripts/kitchen.sh help:17 — test_pstack_core.CoreTests.test_classify_behaviour
- `kitchen.sh discuss <store> <plan-path>` — skills/pstack-kitchen/scripts/kitchen.sh help:19 — test_pstack_core.CoreTests.test_discuss_behaviour
- `kitchen.sh dispatch <store> <brief-path> [...] [--max MIN] [--send-only]` — skills/pstack-kitchen/scripts/kitchen.sh help:20 — test_pstack_core.CoreTests.test_dispatch_behaviour
- `kitchen.sh wait <store> [...] [--max MIN]` — skills/pstack-kitchen/scripts/kitchen.sh help:25 — test_pstack_core.CoreTests.test_the_quiet_wait_behaviour
- `kitchen.sh step <store> <sha> <summary> [--resolves n1,n2 | 001-n1]` — skills/pstack-kitchen/scripts/kitchen.sh help:27 — test_pstack_core.CoreTests.test_step_behaviour
- `kitchen.sh finish <store> <report>` — skills/pstack-kitchen/scripts/kitchen.sh help:31 — test_pstack_core.CoreTests.test_finish_behaviour
- `kitchen.sh verify <store> <NNN>... [--covers NNN]... [--base REV] [--every MIN | --until-verdict] [--timeout MIN] [--kind KIND]` — skills/pstack-kitchen/scripts/kitchen.sh help:35 — test_pstack_core.CoreTests.test_verify_wait_behaviour_for_the_wait_itself
- `kitchen.sh verify <store> --landing [--base REV] [--parent REF|none] [--head REV]` — skills/pstack-kitchen/scripts/kitchen.sh help:44 — test_pstack_core.CoreTests.test_verify_wait_behaviour_for_the_wait_itself
- `kitchen.sh verify <store> --wait [--every MIN | --until-verdict]` — skills/pstack-kitchen/scripts/kitchen.sh help:50 — test_pstack_core.CoreTests.test_verify_wait_behaviour_for_the_wait_itself
- `kitchen.sh verify <store> --cancel` — skills/pstack-kitchen/scripts/kitchen.sh help:53 — test_pstack_core.CoreTests.test_verify_wait_behaviour_for_the_wait_itself
- `kitchen.sh revise <store> <verdict-path>` — skills/pstack-kitchen/scripts/kitchen.sh help:56 — test_pstack_core.CoreTests.test_revise_behaviour
- `kitchen.sh land-check <store>` — skills/pstack-kitchen/scripts/kitchen.sh help:57 — test_pstack_core.CoreTests.test_land_check_behaviour
- `kitchen.sh catch <store> <layer> <text> [<unit>]` — skills/pstack-kitchen/scripts/kitchen.sh help:58 — test_pstack_core.CoreTests.test_catch_behaviour
- `kitchen.sh new-handoff <store>` — skills/pstack-kitchen/scripts/kitchen.sh help:61 — test_pstack_core.CoreTests.test_new_handoff_behaviour
- `kitchen.sh rotate-master <store> [--model M]` — skills/pstack-kitchen/scripts/kitchen.sh help:62 — test_pstack_core.CoreTests.test_rotate_master_behaviour
- `kitchen.sh take-master <store>` — skills/pstack-kitchen/scripts/kitchen.sh help:66 — test_pstack_core.CoreTests.test_take_master_behaviour
- `kitchen.sh close <store> [--force]` — skills/pstack-kitchen/scripts/kitchen.sh help:68 — test_pstack_core.CoreTests.test_close_behaviour
- `kitchen.sh retro <store>` — skills/pstack-kitchen/scripts/kitchen.sh help:71 — test_pstack_core.CoreTests.test_retro_behaviour
- `kitchen.sh feedback <store> <file>` — skills/pstack-kitchen/scripts/kitchen.sh help:73 — test_pstack_core.CoreTests.test_feedback_filing_behaviour
- `kitchen.sh feedback --inbox` — skills/pstack-kitchen/scripts/kitchen.sh help:75 — test_pstack_core.CoreTests.test_feedback_filing_behaviour
- `kitchen.sh feedback --close <id> <commit|none> <note>` — skills/pstack-kitchen/scripts/kitchen.sh help:76 — test_pstack_core.CoreTests.test_feedback_filing_behaviour
- `kitchen.sh maintainer on [--pane ID] | off | status` — skills/pstack-kitchen/scripts/kitchen.sh help:78 — test_pstack_core.CoreTests.test_maintainer_behaviour
- `kitchen.sh fleet [--since YYYY-MM-DD] [--runs DIR] [--json]` — skills/pstack-kitchen/scripts/kitchen.sh help:79 — test_pstack_core.CoreTests.test_fleet_behaviour
- `kitchen.sh init` (the pair command, unchanged) — skills/pstack-kitchen/scripts/kitchen.sh help:3 — test_pstack_core.CoreTests.test_init_refuses_under_the_verifier_role_before_any_herdr_call_or_store_write
- `kitchen.sh rotate` (the pair command, unchanged) — skills/pstack-kitchen/scripts/kitchen.sh help:3 — test_pstack_core.CoreTests.test_rotate_master_behaviour
- `kitchen.sh failover` (the pair command, unchanged) — skills/pstack-kitchen/scripts/kitchen.sh help:3 — test_pstack_core.CoreTests.test_consultant_behaviour
- `kitchen.sh permission` (the pair command, unchanged) — skills/pstack-kitchen/scripts/kitchen.sh help:3 — test_pstack_kitchen.KitchenScriptTests.test_the_pairs_permission_report_and_notify_work_through_kitchen_sh
- `kitchen.sh new-plan` (the pair command, unchanged) — skills/pstack-kitchen/scripts/kitchen.sh help:3 — test_pstack_kitchen.KitchenScriptTests.test_kitchen_new_plan_new_brief_and_new_steer_fill_the_kitchen_templates
- `kitchen.sh new-brief` (the pair command, unchanged) — skills/pstack-kitchen/scripts/kitchen.sh help:3 — test_pstack_kitchen.KitchenScriptTests.test_kitchen_new_plan_new_brief_and_new_steer_fill_the_kitchen_templates
- `kitchen.sh queue` (the pair command, unchanged) — skills/pstack-kitchen/scripts/kitchen.sh help:3 — test_pstack_core.CoreTests.test_queue_behaviour
- `kitchen.sh notes` (the pair command, unchanged) — skills/pstack-kitchen/scripts/kitchen.sh help:3 — test_pstack_kitchen.KitchenScriptTests.test_kitchen_notes_lists_a_blocking_note_until_a_step_resolves_it_and_followups_are_tagged
- `kitchen.sh new-note` (the pair command, unchanged) — skills/pstack-kitchen/scripts/kitchen.sh help:3 — test_pstack_kitchen.KitchenScriptTests.test_kitchen_notes_lists_a_blocking_note_until_a_step_resolves_it_and_followups_are_tagged
- `kitchen.sh note` (the pair command, unchanged) — skills/pstack-kitchen/scripts/kitchen.sh help:3 — test_pstack_kitchen.KitchenScriptTests.test_kitchen_notes_lists_a_blocking_note_until_a_step_resolves_it_and_followups_are_tagged
- `kitchen.sh next` (the pair command, unchanged) — skills/pstack-kitchen/scripts/kitchen.sh help:3 — test_pstack_core.CoreTests.test_rotate_master_behaviour
- `kitchen.sh progress` (the pair command, unchanged) — skills/pstack-kitchen/scripts/kitchen.sh help:3 — test_pstack_kitchen.KitchenScriptTests.test_kitchen_pause_reaches_the_sidekick_at_progress_until_resume
- `kitchen.sh new-steer` (the pair command, unchanged) — skills/pstack-kitchen/scripts/kitchen.sh help:3 — test_pstack_kitchen.KitchenScriptTests.test_kitchen_new_plan_new_brief_and_new_steer_fill_the_kitchen_templates
- `kitchen.sh steer` (the pair command, unchanged) — skills/pstack-kitchen/scripts/kitchen.sh help:3 — test_pstack_core.CoreTests.test_steering_a_settled_sidekick_after_a_done_unit_advises_covers_only_in_the_kitchen_and_reads_the_units_own_report
- `kitchen.sh report` (the pair command, unchanged) — skills/pstack-kitchen/scripts/kitchen.sh help:3 — test_pstack_kitchen.KitchenScriptTests.test_the_pairs_permission_report_and_notify_work_through_kitchen_sh
- `kitchen.sh notify` (the pair command, unchanged) — skills/pstack-kitchen/scripts/kitchen.sh help:3 — test_pstack_kitchen.KitchenScriptTests.test_the_pairs_permission_report_and_notify_work_through_kitchen_sh
- `kitchen.sh stop` (the pair command, unchanged) — skills/pstack-kitchen/scripts/kitchen.sh help:3 — test_pstack_core.CoreTests.test_close_behaviour
- `kitchen.sh scratch` (the pair command, unchanged) — skills/pstack-kitchen/scripts/kitchen.sh help:3 — test_pstack_kitchen.KitchenScriptTests.test_kitchen_scratch_makes_a_worktree_at_the_sha_and_removes_it
- `kitchen.sh pause` (the pair command, unchanged) — skills/pstack-kitchen/scripts/kitchen.sh help:3 — test_pstack_kitchen.KitchenScriptTests.test_kitchen_pause_reaches_the_sidekick_at_progress_until_resume
- `kitchen.sh resume` (the pair command, unchanged) — skills/pstack-kitchen/scripts/kitchen.sh help:3 — test_pstack_kitchen.KitchenScriptTests.test_kitchen_pause_reaches_the_sidekick_at_progress_until_resume
- `kitchen.sh status` (the pair command, unchanged) — skills/pstack-kitchen/scripts/kitchen.sh help:3 — test_pstack_kitchen.KitchenScriptTests.test_kitchen_status_lists_the_three_roles_the_advice_column_and_saves_status_md
- `kitchen.sh metrics` (the pair command, unchanged) — skills/pstack-kitchen/scripts/kitchen.sh help:3 — test_pstack_kitchen.KitchenScriptTests.test_kitchen_metrics_reports_wall_time_and_review_latency_from_events_tsv
- `kitchen.sh new-consult` (the pair command, unchanged) — skills/pstack-kitchen/scripts/kitchen.sh help:3 — test_pstack_kitchen.KitchenScriptTests.test_the_consultants_new_consult_consult_and_advice_work_through_kitchen_sh
- `kitchen.sh consult` (the pair command, unchanged) — skills/pstack-kitchen/scripts/kitchen.sh help:3 — test_pstack_kitchen.KitchenScriptTests.test_the_consultants_new_consult_consult_and_advice_work_through_kitchen_sh
- `kitchen.sh advice` (the pair command, unchanged) — skills/pstack-kitchen/scripts/kitchen.sh help:3 — test_pstack_kitchen.KitchenScriptTests.test_the_consultants_new_consult_consult_and_advice_work_through_kitchen_sh

## Exit codes

- exit 2 (herdr or gate failure): landing refused: kitchen.sh land-check … fails — skills/pstack-kitchen/scripts/kitchen_core/dispatch.py:69 — test_pstack_core.CoreTests.test_dispatch_of_a_landing_brief_goes_through_the_python_land_check (+1)
- exit 2 (herdr or gate failure): …: risk: must start with routine or escalated, as kitchen.sh classify prints it — skills/pstack-kitchen/scripts/kitchen_core/dispatch.py:116 — test_pstack_core.CoreTests.test_discuss_behaviour
- exit 2 (herdr or gate failure): the next master did not start in …; you stay the master (generation …), so carry on and  — skills/pstack-kitchen/scripts/kitchen_core/master.py:176 — test_pstack_core.CoreTests.test_rotate_master_behaviour
- exit 2 (herdr or gate failure): this run is in a linked worktree and its [scratch].setup failed:
… — skills/pstack-kitchen/scripts/kitchen_core/setup.py:110 — test_pstack_core.CoreTests.test_init_behaviour
- exit 2 (herdr or gate failure): consultant … failed its preflight and the roster names no other fallback: … — skills/pstack-kitchen/scripts/kitchen_core/setup.py:223 — test_pstack_core.CoreTests.test_consultant_behaviour
- exit 2 (herdr or gate failure): unit …'s commits match by patch-id but … reverts them in the checked-out head … (unit { — skills/pstack-kitchen/scripts/kitchen_core/units.py:273 — test_pstack_core.CoreTests.test_heads_in_names_the_units_a_verifications_head_does_not_carry
- exit 2 (herdr or gate failure): unit …'s head … is not in the checked-out head … (unit …); name the unit that carries  — skills/pstack-kitchen/scripts/kitchen_core/units.py:275 — test_pstack_core.CoreTests.test_heads_in_names_the_units_a_verifications_head_does_not_carry (+1)
- exit 2 (herdr or gate failure): verifier entry is not JSON: … — skills/pstack-kitchen/scripts/kitchen_core/verify.py:166 — test_pstack_core.CoreTests.test_verify_behaviour
- exit 2 (herdr or gate failure): batch of … diff lines is over review.max_batch_diff; verify each unit — skills/pstack-kitchen/scripts/kitchen_core/verify.py:518 — test_pstack_kitchen.KitchenScriptTests.test_a_batch_over_review_max_batch_diff_is_refused_but_a_fix_verified_with_its_unit_is_not
- exit 2 (herdr or gate failure): pane … is not a live Herdr pane; HERDR_PANE_ID is stale when a harness runs commands thro — skills/pstack-pair/scripts/pstack_core/commands/init.py:91 — test_pstack_core.CoreTests.test_init_names_the_pane_flag_when_herdr_pane_id_is_stale
- exit 2 (herdr or gate failure): (returns 2 in run()) — skills/pstack-pair/scripts/pstack_core/commands/metrics.py:239 — not a caller contract: the events log that init creates is unreadable; no caller sends it
- exit 2 (herdr or gate failure): Prime delivery failed for … — skills/pstack-pair/scripts/pstack_core/commands/notes.py:112 — not a caller contract: the Prime daemon refusing a delivery; a caller reads the exit code 2, which the herdr prompt failure case shows for every other kind
- exit 2 (herdr or gate failure): herdr prompt failed for … — skills/pstack-pair/scripts/pstack_core/commands/notes.py:114 — test_pstack_core.CoreTests.test_a_blocking_note_for_prime_goes_through_prime_steer_and_a_failed_prompt_exits_2_after_the_published_line (+3)
- exit 2 (herdr or gate failure): … has no valid status: line (one of …) — skills/pstack-pair/scripts/pstack_core/commands/report.py:77 — test_pstack_core.CoreTests.test_finish_refuses_a_report_with_no_valid_status_line
- exit 2 (herdr or gate failure): … exit failed: … — skills/pstack-pair/scripts/pstack_core/commands/rotate.py:48 — test_pstack_core.CoreTests.test_rotate_refuses_and_reports_what_a_caller_reads
- exit 2 (herdr or gate failure): jj workspace add failed — skills/pstack-pair/scripts/pstack_core/commands/scratch.py:90 — not a caller contract: jj workspace add failing in a valid repo; the exit code is the same 2 as every herdr or git failure
- exit 2 (herdr or gate failure): git worktree add failed — skills/pstack-pair/scripts/pstack_core/commands/scratch.py:92 — test_pstack_kitchen.KitchenScriptTests.test_a_parent_is_not_recorded_when_the_scratch_worktree_cannot_be_made
- exit 2 (herdr or gate failure): herdr send-keys failed for … — skills/pstack-pair/scripts/pstack_core/commands/steer.py:80 — test_pstack_core.CoreTests.test_steer_refuses_and_reports_what_a_caller_reads
- exit 2 (herdr or gate failure): Prime steer failed for … — skills/pstack-pair/scripts/pstack_core/commands/steer.py:93 — test_pstack_core.CoreTests.test_steer_refuses_and_reports_what_a_caller_reads
- exit 2 (herdr or gate failure): herdr prompt failed for … — skills/pstack-pair/scripts/pstack_core/commands/steer.py:97 — test_pstack_core.CoreTests.test_a_blocking_note_for_prime_goes_through_prime_steer_and_a_failed_prompt_exits_2_after_the_published_line (+3)
- exit 2 (herdr or gate failure): herdr prompt failed for … — skills/pstack-pair/scripts/pstack_core/commands/steer.py:125 — test_pstack_core.CoreTests.test_a_blocking_note_for_prime_goes_through_prime_steer_and_a_failed_prompt_exits_2_after_the_published_line (+3)
- exit 2 (herdr or gate failure): sidekick … is not live — skills/pstack-pair/scripts/pstack_core/commands/steer.py:128 — test_pstack_core.CoreTests.test_commands_load_the_variant_they_need_and_no_kitchen_code_they_do_not (+4)
- exit 2 (herdr or gate failure): sidekick … is not live — skills/pstack-pair/scripts/pstack_core/commands/stop.py:131 — test_pstack_core.CoreTests.test_commands_load_the_variant_they_need_and_no_kitchen_code_they_do_not (+4)
- exit 2 (herdr or gate failure): herdr prompt failed for … — skills/pstack-pair/scripts/pstack_core/consult.py:70 — test_pstack_core.CoreTests.test_a_blocking_note_for_prime_goes_through_prime_steer_and_a_failed_prompt_exits_2_after_the_published_line (+3)
- exit 2 (herdr or gate failure): … … is not live; run: pair.sh spawn … --only … --… <kind> — skills/pstack-pair/scripts/pstack_core/consult.py:125 — test_pstack_core.CoreTests.test_discuss_behaviour (+2)
- exit 2 (herdr or gate failure): consultant … is not live; run: pair.sh spawn … --only consultant --consultant <kind> — skills/pstack-pair/scripts/pstack_core/consult.py:280 — test_pstack_core.CoreTests.test_trio_spawn_and_discuss_report_the_state_they_stop_on (+1)
- exit 2 (herdr or gate failure): tab create returned no pane id — skills/pstack-pair/scripts/pstack_core/panes.py:94 — test_pstack_core.CoreTests.test_spawn_reports_the_failure_a_caller_branches_on
- exit 2 (herdr or gate failure): pane split returned no pane id — skills/pstack-pair/scripts/pstack_core/panes.py:101 — test_pstack_core.CoreTests.test_a_pane_that_will_not_split_ends_verify_saying_no_pane_id_came_back (+1)
- exit 2 (herdr or gate failure): herdr prompt failed for … — skills/pstack-pair/scripts/pstack_core/send.py:83 — test_pstack_core.CoreTests.test_a_blocking_note_for_prime_goes_through_prime_steer_and_a_failed_prompt_exits_2_after_the_published_line (+3)
- exit 2 (herdr or gate failure): sidekick … is not live; run: pair.sh spawn … --kind <kind> — skills/pstack-pair/scripts/pstack_core/send.py:107 — test_pstack_core.CoreTests.test_discuss_behaviour (+2)
- exit 2 (herdr or gate failure): agent start failed in …; inspect: herdr pane read … --source visible — skills/pstack-pair/scripts/pstack_core/spawn.py:126 — test_pstack_core.CoreTests.test_failover_refuses_and_reports_what_a_caller_reads
- exit 2 (herdr or gate failure): no fallback recorded for a failed …; pass --fallback KIND or fix the agent — skills/pstack-pair/scripts/pstack_core/spawn.py:242 — test_pstack_core.CoreTests.test_spawn_reports_the_failure_a_caller_branches_on
- exit 2 (herdr or gate failure): cannot read the master's agent kind from herdr; run pair.sh init first — skills/pstack-trio/scripts/variant.py:74 — test_pstack_core.CoreTests.test_trio_spawn_and_discuss_report_the_state_they_stop_on
- exit 3 (agent blocked): sidekick … is …; resolve that before queueing — skills/pstack-pair/scripts/pstack_core/commands/rotate.py:129 — test_pstack_core.CoreTests.test_queue_behaviour (+1)
- exit 3 (agent blocked): sidekick … did not settle after esc; inspect: herdr agent read … --source visible --line — skills/pstack-pair/scripts/pstack_core/commands/steer.py:82 — test_pstack_pair.PairScriptTests.test_trio (+2)
- exit 3 (agent blocked): sidekick … is blocked; inspect: herdr agent read … --source visible --lines 60 — skills/pstack-pair/scripts/pstack_core/commands/steer.py:130 — test_pstack_core.CoreTests.test_discuss_behaviour (+4)
- exit 3 (agent blocked): sidekick … is blocked; inspect it before stopping — skills/pstack-pair/scripts/pstack_core/commands/stop.py:133 — test_pstack_core.CoreTests.test_stop_of_a_blocked_sidekick_names_what_to_inspect
- exit 3 (agent blocked): … … is blocked; inspect: herdr agent read … --source visible --lines 60 — skills/pstack-pair/scripts/pstack_core/consult.py:127 — test_pstack_core.CoreTests.test_discuss_behaviour (+4)
- exit 3 (agent blocked): consultant … is blocked; inspect: herdr agent read … --source visible --lines 60 — skills/pstack-pair/scripts/pstack_core/consult.py:282 — test_pstack_core.CoreTests.test_discuss_behaviour (+4)
- exit 3 (agent blocked): (returns 3 in consult()) — skills/pstack-pair/scripts/pstack_core/consult.py:307 — test_pstack_pair.PairScriptTests.test_trio (+3)
- exit 3 (agent blocked): (returns 3 in consult()) — skills/pstack-pair/scripts/pstack_core/consult.py:311 — test_pstack_core.CoreTests.test_consult_and_advice_report_what_the_consultant_is_doing (+1)
- exit 3 (agent blocked): sidekick … is blocked; inspect: herdr agent read … --source visible --lines 60 — skills/pstack-pair/scripts/pstack_core/send.py:109 — test_pstack_core.CoreTests.test_discuss_behaviour (+4)
- exit 4 (no report, queue empty, check-in or still running): no verdict at …: the verifier sat idle …m with nothing running; rerun: kitchen.sh verify — skills/pstack-kitchen/scripts/kitchen_core/verify_wait.py:330 — test_pstack_core.CoreTests.test_verify_wait_behaviour_for_the_wait_itself (+1)
- exit 4 (no report, queue empty, check-in or still running): no verdict at … (verifier …); read the packet and verify by hand, or rerun — skills/pstack-kitchen/scripts/kitchen_core/verify_wait.py:334 — test_pstack_core.CoreTests.test_verify_wait_behaviour_for_the_wait_itself
- exit 4 (no report, queue empty, check-in or still running): no report found — skills/pstack-pair/scripts/pstack_core/commands/report.py:27 — test_pstack_core.CoreTests.test_report_next_and_finish_say_what_is_missing (+1)
- exit 4 (no report, queue empty, check-in or still running): (returns 4 in consult()) — skills/pstack-pair/scripts/pstack_core/consult.py:313 — test_pstack_core.CoreTests.test_consult_and_advice_report_what_the_consultant_is_doing (+1)
- exit 4 (no report, queue empty, check-in or still running): no advice found — skills/pstack-pair/scripts/pstack_core/consult.py:321 — test_pstack_core.CoreTests.test_consult_and_advice_report_what_the_consultant_is_doing (+1)
- exit 4 (no report, queue empty, check-in or still running): (returns 4 in owned_wait()) — skills/pstack-pair/scripts/pstack_core/wait.py:342 — test_pstack_core.CoreTests.test_a_newer_wait_supersedes_the_older_and_the_wait_that_returns_releases_the_store (+5)
- exit 5 (wrong state): an escalated plan goes to the consultant too; start it: kitchen.sh consultant … --reason  — skills/pstack-kitchen/scripts/kitchen_core/dispatch.py:119 — test_pstack_core.CoreTests.test_discuss_behaviour
- exit 5 (wrong state): a verification is open (…); finish it (kitchen.sh verify … --wait) or pass --force — skills/pstack-kitchen/scripts/kitchen_core/master.py:66 — test_pstack_core.CoreTests.test_close_behaviour
- exit 5 (wrong state): the sidekick is working; kitchen.sh stop … first, or pass --force — skills/pstack-kitchen/scripts/kitchen_core/master.py:68 — test_pstack_core.CoreTests.test_close_behaviour
- exit 5 (wrong state): no handoff; write one first: kitchen.sh new-handoff … — skills/pstack-kitchen/scripts/kitchen_core/master.py:133 — test_pstack_core.CoreTests.test_rotate_master_behaviour
- exit 5 (wrong state): …: the … section is empty — skills/pstack-kitchen/scripts/kitchen_core/master.py:137 — test_pstack_core.CoreTests.test_rotate_master_behaviour
- exit 5 (wrong state): a hand-over is open: … has not taken over yet; it runs kitchen.sh take-master … first — skills/pstack-kitchen/scripts/kitchen_core/master.py:141 — test_pstack_core.CoreTests.test_rotate_master_behaviour
- exit 5 (wrong state): master … is not live; run this from the master's own session — skills/pstack-kitchen/scripts/kitchen_core/master.py:143 — test_pstack_core.CoreTests.test_rotate_master_behaviour
- exit 5 (wrong state): rotate-master restarts a Claude master; … is … — skills/pstack-kitchen/scripts/kitchen_core/master.py:146 — test_pstack_core.CoreTests.test_rotate_master_behaviour
- exit 5 (wrong state): take-master runs from the new master's pane …, not … — skills/pstack-kitchen/scripts/kitchen_core/master.py:226 — test_pstack_core.CoreTests.test_take_master_behaviour (+1)
- exit 5 (wrong state): (message built at runtime) — skills/pstack-kitchen/scripts/kitchen_core/verify.py:428 — test_pstack_core.CoreTests.test_verify_behaviour (+1)
- exit 5 (wrong state): Prime verifier start failed in …; its pane and scratch stay for inspection (verify-wait c — skills/pstack-kitchen/scripts/kitchen_core/verify.py:457 — test_pstack_kitchen.KitchenScriptTests.test_prime_verifier_failed_start_preserves_its_scratch
- exit 5 (wrong state): Prime verifier did not report its session; its pane and scratch stay for inspection — skills/pstack-kitchen/scripts/kitchen_core/verify.py:459 — test_pstack_kitchen.KitchenScriptTests.test_a_prime_verifier_that_reports_no_session_keeps_its_pane_scratch_and_the_record_of_how_it_was_launched
- exit 5 (wrong state): no verification is open; start one with kitchen.sh verify … <NNN> — skills/pstack-kitchen/scripts/kitchen_core/verify.py:498 — test_pstack_core.CoreTests.test_verify_wait_behaviour_for_the_wait_itself (+1)
- exit 5 (wrong state): a verification is open (…); kitchen.sh verify … --wait — skills/pstack-kitchen/scripts/kitchen_core/verify.py:506 — test_pstack_core.CoreTests.test_close_behaviour (+1)
- exit 5 (wrong state): another call has held the close of … for …s; kitchen.sh verify … --wait — skills/pstack-kitchen/scripts/kitchen_core/verify.py:615 — test_pstack_core.CoreTests.test_a_verify_that_finds_the_open_verifications_close_held_by_a_live_call_waits_then_gives_up
- exit 5 (wrong state): Prime verifier cleanup failed; its pane and scratch stay for inspection — skills/pstack-kitchen/scripts/kitchen_core/verify_state.py:151 — test_pstack_core.CoreTests.test_verify_cancel_behaviour
- exit 5 (wrong state): no verification open — skills/pstack-kitchen/scripts/kitchen_core/verify_state.py:267 — test_pstack_core.CoreTests.test_verify_cancel_behaviour (+1)
- exit 5 (wrong state): the verdict is written (…); kitchen.sh verify … --wait reads it — skills/pstack-kitchen/scripts/kitchen_core/verify_state.py:269 — test_pstack_core.CoreTests.test_verify_cancel_behaviour
- exit 5 (wrong state): no verification open (another call closed it) — skills/pstack-kitchen/scripts/kitchen_core/verify_state.py:276 — test_pstack_core.CoreTests.test_verify_cancel_behaviour
- exit 5 (wrong state): a … sidekick leaves the queue for the master to dispatch after a fresh session — skills/pstack-pair/scripts/pstack_core/commands/report.py:64 — test_pstack_pair.PairScriptTests.test_trio (+3)
- exit 5 (wrong state): rotation requires a completed task; partial or blocked work keeps its session — skills/pstack-pair/scripts/pstack_core/commands/rotate.py:35 — test_pstack_core.CoreTests.test_rotate_refuses_and_reports_what_a_caller_reads
- exit 5 (wrong state): no recorded startup arguments; exit the idle sidekick and spawn it with its original model — skills/pstack-pair/scripts/pstack_core/commands/rotate.py:37 — test_pstack_core.CoreTests.test_rotate_refuses_and_reports_what_a_caller_reads
- exit 5 (wrong state): Prime session cleanup failed; no new session was started — skills/pstack-pair/scripts/pstack_core/commands/rotate.py:43 — test_pstack_core.CoreTests.test_rotate_refuses_and_reports_what_a_caller_reads
- exit 5 (wrong state): … has not exited; inspect … before retrying rotate — skills/pstack-pair/scripts/pstack_core/commands/rotate.py:52 — test_pstack_core.CoreTests.test_discuss_behaviour (+1)
- exit 5 (wrong state): Prime session cleanup failed; no new session was started — skills/pstack-pair/scripts/pstack_core/commands/rotate.py:56 — test_pstack_core.CoreTests.test_rotate_refuses_and_reports_what_a_caller_reads
- exit 5 (wrong state): sidekick … is …; wait for its report and turn to end before rotating — skills/pstack-pair/scripts/pstack_core/commands/rotate.py:58 — test_pstack_pair.PairScriptTests.test_trio (+3)
- exit 5 (wrong state): the queue holds …; pass --replace to swap it — skills/pstack-pair/scripts/pstack_core/commands/rotate.py:123 — test_pstack_pair.PairScriptTests.test_trio (+3)
- exit 5 (wrong state): sidekick … is …; dispatch instead: pair.sh dispatch … … — skills/pstack-pair/scripts/pstack_core/commands/rotate.py:127 — test_pstack_pair.PairScriptTests.test_trio (+4)
- exit 5 (wrong state): no fallback recorded; spawn the sidekick with --fallback KIND — skills/pstack-pair/scripts/pstack_core/commands/rotate.py:161 — test_pstack_core.CoreTests.test_failover_refuses_and_reports_what_a_caller_reads
- exit 5 (wrong state): the sidekick already runs the fallback (…) — skills/pstack-pair/scripts/pstack_core/commands/rotate.py:163 — test_pstack_core.CoreTests.test_failover_refuses_and_reports_what_a_caller_reads
- exit 5 (wrong state): sidekick … is working; pair.sh stop … first, or pass --force to interrupt it — skills/pstack-pair/scripts/pstack_core/commands/rotate.py:168 — test_pstack_core.CoreTests.test_failover_refuses_and_reports_what_a_caller_reads
- exit 5 (wrong state): Prime session cleanup failed; no fallback was started — skills/pstack-pair/scripts/pstack_core/commands/rotate.py:173 — test_pstack_core.CoreTests.test_failover_refuses_and_reports_what_a_caller_reads
- exit 5 (wrong state): … sidekick has not exited; close it by hand, then rerun failover — skills/pstack-pair/scripts/pstack_core/commands/rotate.py:179 — test_pstack_core.CoreTests.test_failover_refuses_and_reports_what_a_caller_reads
- exit 5 (wrong state): sidekick … is … and no objection is open on unit …; … — skills/pstack-pair/scripts/pstack_core/commands/steer.py:113 — test_pstack_core.CoreTests.test_steering_a_settled_sidekick_says_what_to_do_by_the_units_report (+2)
- exit 5 (wrong state): sidekick … is unknown; read the pane, then pass --force to steer anyway — skills/pstack-pair/scripts/pstack_core/commands/steer.py:123 — test_pstack_core.CoreTests.test_steer_refuses_and_reports_what_a_caller_reads
- exit 5 (wrong state): … … is …; wait for it before a plan round — skills/pstack-pair/scripts/pstack_core/consult.py:130 — test_pstack_core.CoreTests.test_discuss_behaviour (+2)
- exit 5 (wrong state): sidekick … is working and the consultant has nothing to answer; wait for the sidekick bef — skills/pstack-pair/scripts/pstack_core/consult.py:133 — not a caller contract: unreachable: the loop above leaves the consultant sent the plan or answered, or ends the round on its state, so a working sidekick never meets a consultant with nothing to do
- exit 5 (wrong state): consultant … is …; wait for its current advice first — skills/pstack-pair/scripts/pstack_core/consult.py:284 — test_pstack_core.CoreTests.test_consult_and_advice_report_what_the_consultant_is_doing
- exit 5 (wrong state): the store is paused (…): start nothing new; record where the work stands and end your tur — skills/pstack-pair/scripts/pstack_core/gates.py:19 — test_pstack_pair.PairScriptTests.test_trio (+4)
- exit 5 (wrong state): … asks whether to trust …, and that decision is yours: open … there once and trust the  — skills/pstack-pair/scripts/pstack_core/panes.py:117 — test_pstack_core.CoreTests.test_spawn_reports_the_failure_a_caller_branches_on
- exit 5 (wrong state): the … sidekick needs a fresh session or a successful bootstrap before another task; inspe — skills/pstack-pair/scripts/pstack_core/send.py:45 — test_pstack_core.CoreTests.test_answer_names_the_state_that_blocks_it
- exit 5 (wrong state): sidekick … is …; run: pair.sh wait … — skills/pstack-pair/scripts/pstack_core/send.py:110 — test_pstack_core.CoreTests.test_discuss_behaviour (+2)
- exit 5 (wrong state): the queue holds …; dispatch that first, or: pair.sh queue … --clear — skills/pstack-pair/scripts/pstack_core/send.py:209 — test_pstack_pair.PairScriptTests.test_trio (+3)
- exit 5 (wrong state): … task sessions start fresh; recover partial work from the store instead of passing … — skills/pstack-pair/scripts/pstack_core/spawn.py:87 — test_pstack_core.CoreTests.test_consultant_behaviour (+1)
- exit 5 (wrong state): Prime start failed in …; its pane and recorded launch stay for inspection — skills/pstack-pair/scripts/pstack_core/spawn.py:141 — not a caller contract: the Prime daemon failing after its preflight passed; exit 5 like every spawn refusal, the pane stays for the human
- exit 5 (wrong state): Prime did not report its session; inspect … before prompting — skills/pstack-pair/scripts/pstack_core/spawn.py:143 — test_pstack_core.CoreTests.test_a_prime_sidekick_that_reports_no_session_ends_spawn_with_5_and_keeps_its_pane_and_launch_record
- exit 6 (plan not agreed): medium plan needs approval: posted or human in … (found '…') — skills/pstack-pair-guided/scripts/variant.py:119 — test_pstack_pair.PairScriptTests.test_guided (+1)
- exit 6 (plan not agreed): large plan needs approval: human in … (found '…'); post the direction summary and wait f — skills/pstack-pair-guided/scripts/variant.py:122 — test_pstack_core.CoreTests.test_a_plan_needs_the_approval_and_advice_its_variant_asks_for
- exit 6 (plan not agreed): plan … has scale '…'; set small, medium, or large — skills/pstack-pair-guided/scripts/variant.py:124 — test_pstack_core.CoreTests.test_a_plan_needs_the_approval_and_advice_its_variant_asks_for
- exit 6 (plan not agreed): playbook … needs an agreed plan; brief has plan: … — skills/pstack-pair/scripts/pstack_core/gates.py:31 — test_pstack_core.CoreTests.test_queue_behaviour (+1)
- exit 6 (plan not agreed): plan not found: … — skills/pstack-pair/scripts/pstack_core/gates.py:33 — test_pstack_core.CoreTests.test_discuss_behaviour (+2)
- exit 6 (plan not agreed): plan … has no review; run pair.sh discuss and write reviews/…-<slug>.md with verdict: ag — skills/pstack-pair/scripts/pstack_core/gates.py:37 — test_pstack_pair.PairScriptTests.test_trio (+3)
- exit 6 (plan not agreed): plan … review verdict is '…', not agreed — skills/pstack-pair/scripts/pstack_core/gates.py:41 — test_pstack_core.CoreTests.test_queue_and_dispatch_refuse_with_the_exit_code_a_caller_branches_on
- exit 6 (plan not agreed): plan … has no consultant advice under advice/…-*; run pair.sh discuss so both agents res — skills/pstack-trio/scripts/variant.py:192 — test_pstack_core.CoreTests.test_a_plan_needs_the_approval_and_advice_its_variant_asks_for
- exit 7 (steer, consult or ask cap): ask … exceeds the two-ask cap for unit …; re-plan the unit, or pass --force to answer an — skills/pstack-pair-guided/scripts/variant.py:81 — test_pstack_core.CoreTests.test_a_plan_needs_the_approval_and_advice_its_variant_asks_for (+1)
- exit 7 (steer, consult or ask cap): unit … already has two fresh steers; stop the unit and re-brief, or pass --force — skills/pstack-pair/scripts/pstack_core/commands/notes.py:203 — test_pstack_pair.PairScriptTests.test_trio (+3)
- exit 7 (steer, consult or ask cap): blocking review notes are still open on …:
…
Resolve each as a fixup commit, record it w — skills/pstack-pair/scripts/pstack_core/commands/report.py:91 — test_pstack_pair.PairScriptTests.test_trio (+2)
- exit 7 (steer, consult or ask cap): …: …; rewrite the report at the unit's last commit (head:), then finish again — skills/pstack-pair/scripts/pstack_core/commands/report.py:93 — test_pstack_core.CoreTests.test_report_next_and_finish_say_what_is_missing (+1)
- exit 7 (steer, consult or ask cap): unit … already had its glance; send a finding consult, or pass --force — skills/pstack-pair/scripts/pstack_core/consult.py:235 — test_pstack_pair.PairScriptTests.test_trio (+2)
- exit 7 (steer, consult or ask cap): unit … already has three consults; take the question to a plan round, or pass --force — skills/pstack-pair/scripts/pstack_core/consult.py:237 — test_pstack_pair.PairScriptTests.test_trio (+1)
- exit 8 (kinds not diverse): all three agents would be …; pstack-trio needs at least two distinct kinds (master …, si — skills/pstack-trio/scripts/variant.py:76 — test_pstack_pair.PairScriptTests.test_trio
- exit 4: wait returns 4 with the report path already shown (review-delta printed) — skills/pstack-pair/scripts/pstack_core/wait.py:134 — test_pstack_pair.PairScriptTests.test_trio (+3)
- exit 3: wait returns 3 when the sidekick is blocked — skills/pstack-pair/scripts/pstack_core/wait.py:144 — test_pstack_pair.PairScriptTests.test_trio (+33)
- exit 4: wait returns 4 for a check-in digest or a missing report — skills/pstack-pair/scripts/pstack_core/wait.py:152 — test_pstack_pair.PairScriptTests.test_trio (+5)
- exit 4: spawn returns 4 while the bootstrap READY report is missing — skills/pstack-pair/scripts/pstack_core/spawn.py:95 — test_pstack_core.CoreTests.test_spawn_reports_the_failure_a_caller_branches_on
- exit 3: spawn returns 3 when the bootstrap did not settle — skills/pstack-pair/scripts/pstack_core/spawn.py:171 — test_pstack_pair.PairScriptTests.test_trio (+5)
- exit 4: spawn returns 4 when the agent settled but READY is missing — skills/pstack-pair/scripts/pstack_core/spawn.py:182 — test_pstack_core.CoreTests.test_spawn_behaviour (+3)
- exit 4: kitchen step and finish return 4 with `gates: still running` — skills/pstack-kitchen/scripts/kitchen_core/step.py — test_pstack_core.CoreTests.test_step_behaviour (+1)
- exit 2: kitchen step returns 2 when a gate or the policy check fails; the failing command log tail is printed — skills/pstack-kitchen/scripts/kitchen_core/step.py — test_pstack_pair.PairScriptTests.test_trio (+76)
- exit 4: verify returns 4 with progress at the interval, grace or stalled — skills/pstack-kitchen/scripts/kitchen_core/verify_wait.py — test_pstack_pair.PairScriptTests.test_trio (+7)

## Messages agents read

- `PAUSE:` (read by skills/pstack-kitchen/references/brief-template.md:75) — skills/pstack-pair/scripts/pstack_core/store.py:339 — test_pstack_pair.PairScriptTests.test_trio (+4)
- `NOTE:` (read by skills/pstack-kitchen/references/sidekick.md:57) — skills/pstack-pair/scripts/pstack_core/store.py:403 — test_pstack_pair.PairScriptTests.test_trio (+3)
- `STEER:` (read by skills/pstack-pair-guided/SKILL.md:184) — skills/pstack-pair/scripts/pstack_core/store.py:409 — test_pstack_pair.PairScriptTests.test_trio (+2)
- `REPORT` (read by skills/pstack-kitchen/SKILL.md:3) — skills/pstack-pair/scripts/pstack_core/commands/report.py:40 — test_pstack_pair.PairScriptTests.test_trio (+5)
- `running:` (read by skills/pstack-kitchen/references/master.md:71) — skills/pstack-pair/scripts/pstack_core/checkin.py:231 — test_pstack_pair.PairScriptTests.test_trio (+8)
- `steps:` (read by skills/pstack-pair-guided/SKILL.md:257) — skills/pstack-kitchen/scripts/kitchen_core/dispatch.py:27 — test_pstack_pair.PairScriptTests.test_trio (+6)
- `idle:` (read by skills/pstack-kitchen/references/master.md:60) — skills/pstack-pair/scripts/pstack_core/wait.py:127 — test_pstack_pair.PairScriptTests.test_trio (+3)
- `queued:` (read by skills/pstack-pair-guided/references/master.md:211) — skills/pstack-kitchen/scripts/kitchen_core/handoff.py:45 — test_pstack_pair.PairScriptTests.test_trio (+5)
- `rotated:` (read by skills/pstack-kitchen/references/master.md:67) — skills/pstack-pair/scripts/pstack_core/commands/rotate.py:88 — test_pstack_pair.PairScriptTests.test_trio (+13)
- `report_status:` (read by skills/pstack-pair-guided/SKILL.md:153) — skills/pstack-pair/scripts/pstack_core/wait.py:96 — test_pstack_pair.PairScriptTests.test_trio (+16)
- `review-delta:` (read by skills/pstack-pair-guided/references/master.md:102) — skills/pstack-pair/scripts/pstack_core/replies.py:166 — test_pstack_pair.PairScriptTests.test_trio (+3)
- `provider_error:` (read by skills/pstack-pair/references/master.md:242) — skills/pstack-kitchen/scripts/kitchen_core/verify_wait.py:316 — test_pstack_core.CoreTests.test_verify_wait_behaviour_for_the_wait_itself (+6)
- `editing:` (read by skills/pstack-kitchen/references/master.md:71) — skills/pstack-pair/scripts/pstack_core/checkin.py:235 — test_pstack_pair.PairScriptTests.test_trio (+6)
- `written but not ready` (read by skills/pstack-kitchen/references/master.md:75) — skills/pstack-kitchen/scripts/kitchen_core/wait.py:67 — test_pstack_core.CoreTests.test_the_quiet_wait_behaviour (+4)
- `closed:` (read by skills/pstack-kitchen/references/master.md:81) — skills/pstack-kitchen/scripts/kitchen_core/feedback.py:214 — test_pstack_core.CoreTests.test_verify_behaviour (+5)
- `cancelled:` (read by skills/pstack-kitchen/references/master.md:81) — skills/pstack-kitchen/scripts/kitchen_core/verify_state.py:281 — test_pstack_core.CoreTests.test_verify_cancel_behaviour (+4)
- `audit:` (read by skills/pstack-kitchen/SKILL.md:143) — skills/pstack-kitchen/scripts/kitchen_core/verify_wait.py:341 — test_pstack_core.CoreTests.test_verify_wait_behaviour_for_verdicts (+2)
- `covered:` (read by skills/pstack-kitchen/references/master.md:86) — skills/pstack-kitchen/scripts/kitchen_core/landing.py:249 — test_pstack_core.CoreTests.test_land_check_behaviour (+15)
- `warning: unreviewed:` (read by skills/pstack-kitchen/references/master.md:81) — skills/pstack-kitchen/scripts/kitchen_core/verify_wait.py:395 — test_pstack_core.CoreTests.test_take_master_behaviour (+3)
- `redelivered:` (read by skills/pstack-kitchen/references/master.md:111) — skills/pstack-kitchen/scripts/kitchen_core/verify_state.py:227 — test_pstack_core.CoreTests.test_verify_wait_behaviour_for_the_wait_itself (+4)
- `deadline` (read by skills/pstack-kitchen/references/master.md:100) — skills/pstack-kitchen/scripts/kitchen.py:786 — test_pstack_core.CoreTests.test_the_verify_wait_hides_the_minute_count_it_prints (+8)
- `advice:` (read by skills/pstack-trio/references/master.md:54) — skills/pstack-pair/scripts/pstack_core/commands/metrics.py:172 — test_pstack_pair.PairScriptTests.test_trio (+2)
- `advice_status:` — skills/pstack-pair/scripts/pstack_core/consult.py:304 — test_pstack_pair.PairScriptTests.test_trio (+3)
- `paused:` (read by skills/pstack-pair-guided/SKILL.md:290) — skills/pstack-kitchen/scripts/kitchen_core/wait.py:43 — test_pstack_pair.PairScriptTests.test_trio (+3)
- `gates: still running` (read by skills/pstack-kitchen/references/brief-template.md:67) — skills/pstack-kitchen/scripts/kitchen_core/step.py:179 — test_pstack_core.CoreTests.test_step_behaviour (+1)
- `gates: queued` (read by skills/pstack-kitchen/SKILL.md:70) — docs only — test_pstack_kitchen.KitchenScriptTests.test_a_step_waiting_for_a_gate_slot_says_where_it_stands (+1)
- `unowned:` — skills/pstack-kitchen/scripts/kitchen_core/wait.py:118 — test_pstack_kitchen.KitchenScriptTests.test_a_failed_first_claim_leaves_the_quiet_wait_unowned_not_superseded
- `ready:` (read by skills/pstack-pair/references/master.md:82) — skills/pstack-kitchen/scripts/variant.py:193 — test_pstack_pair.PairScriptTests.test_trio (+8)
- `superseded` (read by skills/pstack-kitchen/references/master.md:60) — skills/pstack-pair/scripts/pstack_core/commands/notes.py:198 — test_pstack_core.CoreTests.test_a_newer_wait_supersedes_the_older_and_the_wait_that_returns_releases_the_store (+8)
- `sidekick_response:` — skills/pstack-pair/scripts/pstack_core/consult.py:84 — test_pstack_pair.PairScriptTests.test_trio (+3)
- `report: missing` (read by skills/pstack-pair-guided/SKILL.md:105) — skills/pstack-kitchen/scripts/kitchen_core/dispatch.py:27 — test_pstack_pair.PairScriptTests.test_trio (+5)
- `next:` (read by skills/pstack-pair-guided/references/sidekick.md:85) — skills/pstack-kitchen/scripts/kitchen.py:2028 — test_pstack_pair.PairScriptTests.test_trio (+21)

## Store files and fields

- `pair.json` written by master via `pair.sh`: agent names, pane ids, cwd, git root, the sidekick's fallback and failovers, pane placement (split or tab) — skills/pstack-pair/SKILL.md:58 — test_pstack_pair.PairScriptTests.test_trio (+103)
- `standing-orders.md` written by master: numbered constraints pasted by path into every brief — skills/pstack-pair/SKILL.md:59 — test_pstack_pair.PairScriptTests.test_trio (+8)
- `plans/NNN-<slug>.md` written by master: one plan round, from [the plan template](references/plan-template.md) — skills/pstack-pair/SKILL.md:60 — test_pstack_pair.PairScriptTests.test_trio (+25)
- `briefs/NNN-<slug>.md` written by master: one unit, from [the brief template](references/brief-template.md) — skills/pstack-pair/SKILL.md:61 — test_pstack_pair.PairScriptTests.test_trio (+54)
- `progress/NNN-<slug>.md` written by sidekick via `pair.sh progress`: one timestamped line per completed step or change of approach; a check-in shows only the lines the master has  — skills/pstack-pair/SKILL.md:62 — test_pstack_pair.PairScriptTests.test_trio (+14)
- `steers/NNN-<slug>-s<k>.md` written by master: mid-brief direction k for unit NNN, from [the steer template](references/steer-template.md); `supersedes:` nam — skills/pstack-pair/SKILL.md:63 — test_pstack_pair.PairScriptTests.test_trio (+17)
- `reports/NNN-<slug>-s<k>.md` written by sidekick: objection to steer k, from [the steer response template](references/steer-response-template.md) — skills/pstack-pair/SKILL.md:64 — test_pstack_pair.PairScriptTests.test_trio (+76)
- `reports/NNN-<slug>.md` written by sidekick: evidence for brief NNN, from [the report template](references/report-template.md), or the agree/object respons — skills/pstack-pair/SKILL.md:65 — test_pstack_pair.PairScriptTests.test_trio (+76)
- `reviews/NNN-<slug>.md` written by master: verdict on report NNN, from [the review template](references/review-template.md); `agreed` on a plan unlocks i — skills/pstack-pair/SKILL.md:66 — test_pstack_pair.PairScriptTests.test_trio (+25)
- `scratch/<id>` written by master via `pair.sh scratch`: link to a throwaway worktree at a unit's commit for rerunning its checks, kept inside the repo's git directory — skills/pstack-pair/SKILL.md:67 — test_pstack_pair.PairScriptTests.test_trio (+34)
- `gates.md` written by master: open questions for the human — skills/pstack-pair/SKILL.md:68 — test_pstack_pair.PairScriptTests.test_trio (+2)
- `decisions.tsv` written by master via `pair.sh log`: the show-me-your-work trail — skills/pstack-pair/SKILL.md:69 — test_pstack_pair.PairScriptTests.test_trio (+4)
- `status.md` written by `pair.sh status`: derived table; never hand-edited — skills/pstack-pair/SKILL.md:70 — test_pstack_pair.PairScriptTests.test_trio (+3)
- `steps/NNN-<slug>.tsv` written by sidekick via `pair.sh step`: one row per committed step: number, commit, time, notes it resolves, summary — skills/pstack-pair/SKILL.md:72 — test_pstack_pair.PairScriptTests.test_trio (+26)
- `notes/NNN-<slug>-n<k>.md` written by master via `pair.sh new-note` and `note`: review of the unit's steps through step k, from [the note template](references/note-template.md): blocking ite — skills/pstack-pair/SKILL.md:73 — test_pstack_pair.PairScriptTests.test_trio (+27)
- `followups.md` written by master via `pair.sh note`: follow-ups from every note, for a later brief or an issue — skills/pstack-pair/SKILL.md:74 — test_pstack_pair.PairScriptTests.test_trio (+4)
- `session-handoff.md` written by master: compact recovery and next-task context, read at bootstrap — skills/pstack-pair/SKILL.md:75 — test_pstack_core.CoreTests.test_new_handoff_behaviour (+3)
- `sessions/` written by `pair.sh spawn` / `rotate`: prior ready reports; retained session exports and timing evidence — skills/pstack-pair/SKILL.md:76 — test_pstack_pair.PairScriptTests.test_trio (+20)
- `events.tsv` written by every `pair.sh` command: one row per message, report, wake, and log entry, read by `pair.sh metrics` — skills/pstack-pair/SKILL.md:77 — test_pstack_pair.PairScriptTests.test_trio (+38)
- `plans/NNN-<slug>.direction.md` written by master: the human's plain-language summary, from [the direction template](references/direction-template.md) — skills/pstack-pair-guided/SKILL.md:65 — test_pstack_pair.PairScriptTests.test_trio (+25)
- `reports/NNN-<slug>-q<k>.md` written by sidekick: ask k on unit NNN, from [the ask template](references/ask-template.md) — skills/pstack-pair-guided/SKILL.md:66 — test_pstack_pair.PairScriptTests.test_trio (+76)
- `answers/NNN-<slug>-a<k>.md` written by master: the answer to ask k, from [the answer template](references/answer-template.md) — skills/pstack-pair-guided/SKILL.md:67 — test_pstack_pair.PairScriptTests.test_trio (+9)
- `consults/NNN-<slug>-c<k>.md` written by master: question k about unit NNN for the consultant, from [the consult template](references/consult-template.md), wit — skills/pstack-trio/SKILL.md:77 — test_pstack_pair.PairScriptTests.test_trio (+7)
- `advice/NNN-<slug>.md` written by consultant: design critique of plan NNN, from [the plan advice template](references/plan-advice-template.md) — skills/pstack-trio/SKILL.md:80 — test_pstack_pair.PairScriptTests.test_trio (+17)
- `advice/NNN-<slug>-c<k>.md` written by consultant: answer to consult k, from [the advice template](references/advice-template.md) — skills/pstack-trio/SKILL.md:81 — test_pstack_pair.PairScriptTests.test_trio (+17)
- `gates.tsv` written by `kitchen.sh step`: one row per step check: time, unit, commit, pass or fail, profiles — skills/pstack-kitchen/SKILL.md:207 — test_pstack_core.CoreTests.test_step_and_finish_outlive_the_shell_that_ran_them (+5)
- `verdicts/NNN-<slug>-v<k>.md` written by verifier, or `verify` for gates mode: the verdict, from [the verdict template](references/verdict-template.md) — skills/pstack-kitchen/SKILL.md:208 — test_pstack_pair.PairScriptTests.test_trio (+51)
- `verdicts/NNN-<slug>-v<k>-packet.md` written by `kitchen.sh verify`: what the verifier was given — skills/pstack-kitchen/SKILL.md:209 — test_pstack_pair.PairScriptTests.test_trio (+51)
- `pair.json` `.consultant.failovers` — skills/pstack-kitchen/scripts/kitchen_core/setup.py:245 — test_pstack_core.CoreTests.test_consultant_behaviour (+9)
- `pair.json` `.consultant.kind` — skills/pstack-pair/scripts/pstack_core/spawn.py:131 — test_pstack_pair.PairScriptTests.test_trio (+41)
- `pair.json` `.master.generation` — skills/pstack-kitchen/scripts/kitchen_core/master.py:149 — test_pstack_pair.PairScriptTests.test_trio (+20)
- `pair.json` `.master.name` — skills/pstack-kitchen/scripts/kitchen_core/master.py:183 — test_pstack_pair.PairScriptTests.test_trio (+7)
- `pair.json` `.master.pane_id` — skills/pstack-kitchen/scripts/kitchen_core/master.py:183 — test_pstack_pair.PairScriptTests.test_trio (+12)
- `pair.json` `.master.permission_mode` — skills/pstack-kitchen/scripts/kitchen_core/master.py:160 — test_pstack_pair.PairScriptTests.test_trio (+4)
- `pair.json` `.master.previous` — skills/pstack-kitchen/scripts/kitchen_core/master.py:140 — test_pstack_kitchen.KitchenScriptTests.test_new_handoff_lists_the_units_and_rotate_master_hands_the_run_to_a_new_master_beside_it (+1)
- `pair.json` `.master.registered_at` — skills/pstack-kitchen/scripts/kitchen_core/master.py:183 — test_pstack_pair.PairScriptTests.test_trio (+2)
- `pair.json` `.master.sessions` — skills/pstack-kitchen/scripts/kitchen_core/master.py:153 — test_pstack_core.CoreTests.test_a_prime_session_list_that_is_not_JSON_says_so (+3)
- `pair.json` `.master.tab_id` — skills/pstack-pair/scripts/pstack_core/commands/init.py:50 — test_pstack_pair.PairScriptTests.test_trio (+2)
- `pair.json` `.master.units_since_handoff` — skills/pstack-kitchen/scripts/kitchen_core/master.py:183 — test_pstack_core.CoreTests.test_verify_wait_behaviour_for_verdicts (+4)
- `pair.json` `.master.workspace_id` — skills/pstack-pair/scripts/pstack_core/commands/init.py:50 — test_pstack_pair.PairScriptTests.test_trio (+2)
- `pair.json` `.sent.epoch` — skills/pstack-pair/scripts/pstack_core/commands/steer.py:34 — test_pstack_pair.PairScriptTests.test_trio (+2)
- `pair.json` `.sent.seen` — skills/pstack-pair/scripts/pstack_core/send.py:145 — test_pstack_pair.PairScriptTests.test_trio (+3)
- `pair.json` `.sidekick.bootstrap_pending` — skills/pstack-pair/scripts/pstack_core/commands/rotate.py:70 — test_pstack_pair.PairScriptTests.test_trio (+6)
- `pair.json` `.sidekick.failovers` — skills/pstack-pair/scripts/pstack_core/spawn.py:200 — test_pstack_core.CoreTests.test_consultant_behaviour (+9)
- `pair.json` `.sidekick.fallback` — skills/pstack-kitchen/scripts/kitchen_core/verify.py:153 — test_pstack_kitchen.ClassifyTests.test_roster_and_statedir (+6)
- `pair.json` `.sidekick.generation` — skills/pstack-pair/scripts/pstack_core/send.py:145 — test_pstack_pair.PairScriptTests.test_trio (+20)
- `pair.json` `.sidekick.kind` — skills/pstack-pair/scripts/pstack_core/store.py:215 — test_pstack_pair.PairScriptTests.test_trio (+41)
- `pair.json` `.sidekick.rotation_required` — skills/pstack-pair/scripts/pstack_core/commands/rotate.py:68 — test_pstack_pair.PairScriptTests.test_trio (+9)
- `pair.json` `.sidekick.start_args` — skills/pstack-pair/scripts/pstack_core/commands/rotate.py:59 — test_pstack_pair.PairScriptTests.test_trio (+15)
- `pair.json` `.verifier.closed` — skills/pstack-kitchen/scripts/kitchen_core/verify.py:441 — test_pstack_core.CoreTests.test_a_prime_sidekick_that_reports_no_session_ends_spawn_with_5_and_keeps_its_pane_and_launch_record (+1)
- `pair.json` `.verifier.kind` — skills/pstack-kitchen/scripts/kitchen_core/verify.py:441 — test_pstack_pair.PairScriptTests.test_trio (+41)
- `pair.json` `.verifier.launch_cwd` — skills/pstack-kitchen/scripts/kitchen_core/verify.py:441 — test_pstack_core.CoreTests.test_a_prime_sidekick_that_reports_no_session_ends_spawn_with_5_and_keeps_its_pane_and_launch_record (+1)
- `pair.json` `.verifier.name` — skills/pstack-kitchen/scripts/kitchen_core/verify_state.py:222 — test_pstack_pair.PairScriptTests.test_trio (+7)
- `pair.json` `.verifier.pane_id` — skills/pstack-kitchen/scripts/kitchen_core/verify.py:441 — test_pstack_pair.PairScriptTests.test_trio (+12)
- `pair.json` `.verifier.session` — skills/pstack-kitchen/scripts/kitchen_core/verify.py:441 — test_pstack_core.CoreTests.test_maintainer_behaviour (+11)
- `pair.json` `.verifier.session_stopped` — skills/pstack-kitchen/scripts/kitchen_core/verify.py:441 — test_pstack_core.CoreTests.test_a_prime_sidekick_that_reports_no_session_ends_spawn_with_5_and_keeps_its_pane_and_launch_record (+1)
- `pair.json` `.verifier.start_args` — skills/pstack-kitchen/scripts/kitchen_core/verify.py:441 — test_pstack_pair.PairScriptTests.test_trio (+15)
- `pair.json` `.verifying.argv` — skills/pstack-kitchen/scripts/kitchen_core/verify_wait.py:383 — test_pstack_kitchen.KitchenScriptTests.test_a_prime_verifier_that_reports_no_session_keeps_its_pane_scratch_and_the_record_of_how_it_was_launched
- `pair.json` `.verifying.brief` — skills/pstack-kitchen/scripts/kitchen_core/verify.py:597 — test_pstack_pair.PairScriptTests.test_trio (+9)
- `pair.json` `.verifying.closing` — skills/pstack-kitchen/scripts/kitchen_core/verify_state.py:3 — test_pstack_core.CoreTests.test_claim_close_takes_a_claim_only_from_a_holder_that_is_gone (+6)
- `pair.json` `.verifying.deadline` — skills/pstack-kitchen/scripts/kitchen_core/verify_wait.py:237 — test_pstack_kitchen.KitchenScriptTests.test_verify_deadline_scales_with_measured_gate_time
- `pair.json` `.verifying.delivered` — skills/pstack-kitchen/scripts/kitchen_core/verify_wait.py:220 — test_pstack_kitchen.KitchenScriptTests.test_wait_sends_the_packet_a_stopped_verify_never_sent
- `pair.json` `.verifying.entry` — skills/pstack-kitchen/scripts/kitchen_core/verify_wait.py:373 — test_pstack_kitchen.KitchenScriptTests.test_a_prime_verifier_that_reports_no_session_keeps_its_pane_scratch_and_the_record_of_how_it_was_launched (+2)
- `pair.json` `.verifying.grace_at` — skills/pstack-kitchen/scripts/kitchen_core/verify_wait.py:159 — test_pstack_core.CoreTests.test_verify_wait_behaviour_for_the_wait_itself
- `pair.json` `.verifying.grace_delivery` — skills/pstack-kitchen/scripts/kitchen_core/verify_wait.py:119 — test_pstack_core.CoreTests.test_verify_wait_behaviour_for_the_wait_itself (+3)
- `pair.json` `.verifying.grace_until` — skills/pstack-kitchen/scripts/kitchen_core/verify_wait.py:147 — test_pstack_kitchen.KitchenScriptTests.test_a_later_wait_resumes_the_same_grace_and_never_prompts_again
- `pair.json` `.verifying.idle_since` — skills/pstack-kitchen/scripts/kitchen_core/verify_wait.py:248 — test_pstack_core.CoreTests.test_update_verifying_drop_verifying_and_release_close_touch_only_the_verification_they_name (+1)
- `pair.json` `.verifying.packet` — skills/pstack-kitchen/scripts/kitchen_core/verify_state.py:223 — test_pstack_core.CoreTests.test_a_waiter_mid_wait_when_another_call_cancels_the_verification_says_it_was_closed_elsewhere (+5)
- `pair.json` `.verifying.retried` — skills/pstack-kitchen/scripts/kitchen_core/verify_wait.py:213 — test_pstack_kitchen.GateTests.test_a_failure_under_load_is_rerun_once_and_marked_flaky
- `pair.json` `.verifying.risk` — skills/pstack-kitchen/scripts/kitchen_core/verify_wait.py:207 — test_pstack_kitchen.ClassifyTests.test_routine_change_takes_its_profile_settings (+6)
- `pair.json` `.verifying.sample` — skills/pstack-kitchen/scripts/kitchen_core/verify_wait.py:211 — test_pstack_kitchen.ClassifyTests.test_routine_change_takes_its_profile_settings (+2)
- `pair.json` `.verifying.scratch` — skills/pstack-kitchen/scripts/kitchen_core/verify.py:219 — test_pstack_pair.PairScriptTests.test_trio (+11)
- `pair.json` `.verifying.session` — skills/pstack-kitchen/scripts/kitchen_core/verify_wait.py:45 — test_pstack_core.CoreTests.test_maintainer_behaviour (+11)
- `pair.json` `.verifying.started` — skills/pstack-kitchen/scripts/kitchen_core/verify_wait.py:79 — test_pstack_kitchen.EventRowTests.test_run_counters_counts_the_first_event_of_a_headerless_store (+4)
- `pair.json` `.verifying.unit` — skills/pstack-kitchen/scripts/kitchen_core/verify_wait.py:212 — test_pstack_core.CoreTests.test_a_waiter_mid_wait_when_another_call_cancels_the_verification_says_it_was_closed_elsewhere (+5)
- `pair.json` `.verifying.units` — skills/pstack-kitchen/scripts/kitchen_core/verify.py:506 — test_pstack_core.CoreTests.test_a_waiter_mid_wait_when_another_call_cancels_the_verification_says_it_was_closed_elsewhere (+1)
- `pair.json` `.verifying.verdict` — skills/pstack-kitchen/scripts/kitchen_core/verify.py:497 — test_pstack_core.CoreTests.test_verify_wait_behaviour_for_the_wait_itself (+11)

## Decided in plan 043

- a damaged pair.json gives one message `pair.json is damaged: <reason>` and exit 1; an absent optional field is not damage — plan 043 (b) — decided, U3
- a brief Scope keeps `**` globs and backticked paths — plan 043 (c); skills/pstack-pair/scripts/pstack_core/checkin.py — test_pstack_kitchen.GlobTests.test_globs_cross_directories_only_with_double_star; test_pstack_pair.ReadinessTests.test_a_scope_line_listing_several_paths_is_refused_at_dispatch_and_queue
- delivery (dispatch, queue send, steer) protects TERM, INT and HUP until the message is in; detached gate and Verify jobs take their own session and outlive the caller — plan 043 (e); skills/pstack-pair/scripts/pstack_core/send.py:132 — test_pstack_pair.PiSidekickTests.test_a_timeout_cannot_cut_a_delivery_short (delivery); test_pstack_core.CoreTests.test_step_and_finish_outlive_the_shell_that_ran_them (detached jobs)
- `--timeout` and `--every` take integers (ms or minutes as each help line says); zero is the documented immediate or none, a negative or non-integer value is a usage error exit 1 — plan 043 (f) — decided, U5
- the verify job key and the audit sample draw use zlib.crc32; an old-format completed job neither counts as done nor collides with the new key — plan 043 (g), consultant objection 3 — decided, U5
- `finish` refuses a report that is not ready (open blocking note, head not a commit) with exit 7 and names why — skills/pstack-pair/scripts/pstack_core/commands/report.py:93 — test_pstack_core.CoreTests.test_report_next_and_finish_say_what_is_missing (+1)
- a known limit of the revert scan: land-check and verify call a unit reverted when a later stack commit has the same no-context patch-id as the inverse of the unit's patch, even if no real revert happened (an identical one-line deletion elsewhere in the file); the answer fails closed — skills/pstack-kitchen/scripts/kitchen_core/units.py:rebased_in — test_pstack_kitchen.KitchenScriptTests.test_an_unrelated_identical_one_line_deletion_is_called_reverted_by_the_u0_scan
- a close claim held by a live process of another user (kill -0 fails with EPERM) reads as a dead owner's and is taken over; a pid reused by another process holds the claim while that process lives — skills/pstack-kitchen/scripts/kitchen_core/verify_state.py:alive — test_pstack_core.CoreTests.test_claim_close_takes_a_claim_only_from_a_holder_that_is_gone
- `--every`, `--timeout` and `--max` words are whole numbers (digits only, in the unit each help line gives); a sign, a fraction, a name or an expression ends the command with status 1 and `<flag> must be a whole number of <unit>` — skills/pstack-pair/scripts/pstack_core/store.py:whole_number — test_pstack_core.CoreTests.test_whole_numbers_and_seconds_are_read_as_digits_and_nothing_else
- a polling knob in seconds (`PAIR_BUSY_POLL`, `PAIR_SETTLE_POLL_S`, `KITCHEN_VERIFY_POLL_S` and the like) takes a fraction; every other `KITCHEN_*_S` and `PAIR_*_S` knob is a whole number; a word is a usage error naming the variable — skills/pstack-pair/scripts/pstack_core/store.py:env_float — test_pstack_core.CoreTests.test_pair_sh_refuses_a_timeout_that_is_no_whole_number_and_takes_a_fraction_for_a_poll
- a TERM, INT or HUP ends a command with the signal as its status and nothing on stderr (a signal the parent ignored stays ignored), and a background wait ends when its parent does — skills/pstack-pair/scripts/pstack_core/wait.py:end_with_parent — test_pstack_core.CoreTests.test_end_with_parent_ends_a_background_command_when_its_parent_dies; test_pstack_core.CoreTests.test_end_with_parent_makes_a_signal_end_the_command_untraced_and_leaves_an_ignored_one_ignored
- `pair.sh permission` prints the last permissionMode of the session transcript, else the settings defaultMode, else `auto`, and exits 0; a transcript that names no mode no longer ends it with 1 — skills/pstack-pair/scripts/pstack_core/permission.py:detect — test_pstack_core.CoreTests.test_permission_reads_the_session_transcript_and_falls_back_to_auto_when_it_names_no_mode
- a landing verdict `landing-vN.md` whose N is not a plain decimal (`1x`, `1_0`, a non-ASCII digit) is taken as the newest — skills/pstack-kitchen/scripts/kitchen_core/landing.py:latest_landing_verdict — test_pstack_core.CoreTests.test_a_landing_verdict_with_a_version_that_is_not_a_number_is_the_newest
