# CLAUDE.md

## Working with Claude in this repo

### Cost discipline (ENG-COST-2)

Claude sessions are billed on tokens. Most of the waste is not in the work —
it is in the waiting, the re-reading, and the check-ins nobody reads.

**Never poll.** Do not loop on `sleep`, and do not re-check a PR on a timer
"just in case". Subscribe to PR activity and end the turn. Ending the turn is
how you wait; events wake the session.

**Cadence.** A PR that is green, mergeable and review-clean is *finished* —
say so once and schedule nothing. If something genuinely needs a fallback
check-in, arm at most **one at a time**, at most **two over the PR's whole
lifetime**, and never closer than **6 hours**. Cancel any check-in you armed
the moment its reason is gone. This governs *when to wake* — never what gets
fixed, tested, or verified. A red or conflicted PR is work now, not a
check-in.

**Read the cheapest call that answers the question.** The cost of a GitHub
read is the size of what comes back, not the number of calls:

| Question | Call | Cost |
|---|---|---|
| Did CI pass? | `get_check_runs` | small |
| Why did CI fail? | `get_job_logs` (failed job only) | medium |
| What did reviewers say? | `get_review_comments` | small |
| What is the diff? | `get_diff` | proportional to the change |
| Anything else | `pull_request_read: get` | **echoes the entire PR body** |

`pull_request_read: get` is the expensive one. Use it to learn a PR's state
(mergeable, draft, base) — not as a habit, and never for facts a cheaper call
already gave you.

**One validated push.** Run the repo's own fast checks locally before pushing.
A push that turns CI red costs a full cycle plus the reviewers' attention.
One validated push beats three speculative ones.

**Size the writing to the change.** A one-file fix does not need a design
document. Pack notes, PR bodies and commit messages should be as long as the
change earns and no longer.

**Don't re-derive what you already know.** Within a session, do not re-read
files you have read, re-run searches you have run, or restate decisions the
user has already made.

### Before starting a piece of work

- `main` may have moved under you — fetch before branching.
- More than one Claude session may be working this repo. Check for collisions
  (branch names, numbered artifacts, open PRs) before assuming you are alone.
