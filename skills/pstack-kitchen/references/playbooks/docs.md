### Docs

**You own the prose. Say what holds, and show where it holds.**

A docs change edits prose only: a README, a guide, a reference page, an ADR's wording. It changes no behavior, so it needs no plan round and no new test.

1. Read the code, command or decision the text describes, at the unit's base, and list each claim the text will make with the file or command that shows it.
2. Write the change inside the brief's Scope. A code change the text turns out to need is a Question for the master, not part of this unit.
3. Check every path, link, command and flag the text names against the repo: open the file, or run the command and keep its output.
4. Apply the **unslop** skill to the new text.

**Reply:** what changed, and for each claim the file or command output that backs it.
