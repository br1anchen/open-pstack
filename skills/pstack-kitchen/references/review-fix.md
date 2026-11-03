### Review fix

**Fix the findings the brief lists, inside its Scope, and nothing else.** A review, a rejected verdict or a landing review already found each one; the unit that raised them already settled the design.

1. Read each finding, the code it names, and the parent unit's brief and report. A finding you cannot place, or one whose fix would change the parent's design, goes in the report under Questions; do not redesign around it.
2. Fix one finding at a time, smallest change that answers it. Each finding is one commit with the test that pins it, in the same commit. A finding with no testable behaviour (a comment, a name, a log line) says so in its commit message.
3. Run the brief's Verify commands and the checks each finding names. Nothing in this workflow asks for a failing test committed before its fix.

**Reply:** each finding with its commit, the test that pins it, and the command output that shows it fixed. A finding left open says why.
