"""How a command ends: the two ways a command function ends itself, the domain failures helpers raise, and the one translation from either to
stderr text and an exit code."""
import os


class Die(Exception):
    """A command ends itself with a message after the program prefix; the second part is the exit code (1 when absent)."""

    def __init__(self, *parts):
        self.message = str(parts[0])
        self.code = int(parts[1]) if len(parts) >= 2 else 1
        super().__init__(self.message)


class Exit(Exception):
    """A command ends itself with a code after the given text on stderr, printed as it is and without the program prefix."""

    def __init__(self, code, stderr=''):
        self.code = code
        self.stderr = stderr
        super().__init__(stderr)


class PairError(Exception):
    """A failure a helper reports. The class carries the exit code a caller branches on; a command that pins another code for it catches it
    and ends itself with that one."""

    code = 1

    def __init__(self, message=''):
        self.message = str(message)
        super().__init__(self.message)


class ToolFailed(PairError):
    """A step the command ran ended with a status. The status is the fact, and what the step printed was already shown unless the message
    carries it."""

    def __init__(self, status, message=''):
        self.code = status
        super().__init__(message)


class RotationFailed(ToolFailed):
    """The rotation a brief or plan waits on ended with the status of the rotate command, whose output the message carries."""


class MissingStore(PairError):
    """The store has no pair.json."""

    def __init__(self, store):
        super().__init__(f'no pair.json in {store}; run: pair.sh init <slug>')


class DamagedStore(PairError):
    """pair.json is there and holds something the code cannot read."""

    def __init__(self, what):
        super().__init__(f'pair.json is damaged: {what}')


class LockHeld(PairError):
    """The mkdir lock on pair.json stayed held for the whole wait."""

    def __init__(self, store):
        super().__init__(f'pair.json lock held for 10s; remove {store}/.pair.lock if no pair.sh is running')


class BadNumber(PairError):
    """A flag or an environment variable that should hold a number does not."""


class BadSlug(PairError):
    """A name for a file under the store is not lowercase letters, digits, underscore and hyphen."""


class NotInHerdr(PairError):
    """The command needs Herdr and HERDR_ENV is not 1."""

    def __init__(self, message=None):
        super().__init__(message or 'not running inside Herdr (HERDR_ENV != 1)')


class Paused(PairError):
    """The store is paused, so nothing new starts."""

    code = 5


class PlanNotAgreed(PairError):
    """A brief that needs an agreed plan has none."""

    code = 6


class BadScope(PairError):
    """A brief's Scope lists several paths on one line."""


class Unfilled(PairError):
    """A file still has template placeholders."""


class PromptFailed(PairError):
    """Herdr would not take a message for an agent."""

    code = 2


class NoPaneId(PairError):
    """Herdr made a tab or a split and named no pane."""

    code = 2


class AgentStartFailed(PairError):
    """Herdr could not start the agent in its pane."""

    code = 2


class NoFallback(PairError):
    """An agent failed and the store records no other kind to start."""

    code = 2


class AgentNotLive(PairError):
    """The agent a command needs is not there."""

    code = 2


class AgentBlocked(PairError):
    """The agent a command needs waits on an approval."""

    code = 3


class AgentBusy(PairError):
    """The agent a command needs is working."""

    code = 5


class FreshSessionRequired(PairError):
    """The sidekick must be rotated or bootstrapped before it takes another task."""

    code = 5


class FreshStartOnly(PairError):
    """An argument would resume a session of a kind that starts every task fresh."""

    code = 5


class TrustPrompt(PairError):
    """The agent asks whether to trust the folder, which is the human's decision."""

    code = 5


class PrimeFailed(PairError):
    """Prime Agent did not start, report its session, or clean up."""

    code = 5


class PaneSizeUnreadable(PairError):
    """Herdr reported a pane size that is not a number."""


class BadPlacement(PairError):
    """Placement is neither split nor tab."""


class PairJsonGone(PairError):
    """pair.json went missing while a command was ending the run."""

    code = 2

    def __init__(self, store):
        super().__init__(f'pair.json is missing from {store}')


class BadStepRow(PairError):
    """The last row of the step log does not start with a step number."""

    code = 2


class PathFailed(PairError):
    """A path cannot be used: `<path>: <the system's reason>`."""

    def __init__(self, path, error):
        super().__init__(f'{path}: {error.strerror}')


class NoBrief(PairError):
    """No brief carries the unit's number."""


class MasterKindUnknown(PairError):
    """Herdr does not say which agent kind the master is."""

    code = 2


class NoDiverseKinds(PairError):
    """All three agents of a trio would be of one kind."""

    code = 8


class KitchenPyFailed(PairError):
    """kitchen.py, which classifies and stamps a brief, ended with a failure."""


class EmptyScope(PairError):
    """A brief lists nothing it may write."""


class KindRequired(PairError):
    """spawn was given no agent kind."""

    def __init__(self):
        super().__init__('--kind is required (run: herdr agent, for the kind list)')


class BadScratch(PairError):
    """A scratch id, the store's repository or the commit a scratch starts at cannot be used."""


class ScratchNotMade(PairError):
    """git or jj would not add the scratch worktree."""

    code = 2


class SetupFailed(PairError):
    """The run's [scratch].setup failed in a linked worktree."""

    code = 2


class HeadNotCarried(PairError):
    """A verification's head does not carry a unit's commits."""

    code = 2


class BadLandingBase(PairError):
    """No base for the landing range can be named."""


class UnitNotCheckable(PairError):
    """A unit has no dispatch, report, status or head the range can start from."""


class BadRevision(PairError):
    """A revision named on the command line is not a commit, or not an ancestor where it must be."""


class BadVerifierEntry(PairError):
    """The verifier entry a retry carries is not JSON."""

    code = 2


class UnknownVerifierKind(PairError):
    """The roster does not say which agent kind verifies."""


class VerifierStartFailed(PairError):
    """The verifier's agent did not start."""

    code = 5


class CloseHeld(PairError):
    """Another call held the close of a verdict past the wait."""

    code = 5


class VerificationTaken(PairError):
    """Another call closed the verification this one worked on, so this one touches nothing more."""

    code = 5


class NoVerification(PairError):
    """No verification is open to cancel."""

    code = 5


class VerdictMissing(PairError):
    """The verifier left no verdict."""

    code = 4


class FeedbackLocked(PairError):
    """The feedback inbox stayed locked."""


class EmptyFeedbackReport(PairError):
    """A report in the feedback inbox names no gap, which ends the listing."""


class FileFailed(PairError):
    """A file operation failed: `cannot <verb> <what>: <the system's reason>`."""

    def __init__(self, verb, what, error):
        super().__init__(f'cannot {verb} {what}: {os.strerror(error.errno)}')


class StepLogUnreadable(FileFailed):
    """The step log exists and cannot be read."""

    code = 2


class EmptyStepLog(PairError):
    """The step log exists and holds no row, which a command ends on without a word."""


def translate(exc, prog):
    """(stderr text, exit code) for an exception that ends a command; anything else propagates. Exit prints as it is, the rest after
    the program's name."""
    if isinstance(exc, Exit):
        return exc.stderr, exc.code
    if isinstance(exc, (Die, PairError)):
        return (f'{prog}: {exc.message}\n' if exc.message else ''), exc.code
    if isinstance(exc, (OSError, ValueError)):
        return f'{prog}: {exc}\n', 1
    raise exc


def refuse_as(exc, prog, code):
    """The Exit that ends a command with code and exc's text, for a command whose pinned code for this failure is not the class's."""
    return Exit(code, translate(exc, prog)[0])
