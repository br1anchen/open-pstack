from pstack_core.errors import Die
from pstack_core.commands.status import latest
from pstack_core.variant import Variant


class TrioVariant(Variant):
    def plan_gate_extra(self, ctx, store, plan, review, seq):
        """A plan the consultant took part in also needs its advice on file."""
        if not latest(store, 'advice', seq):
            raise Die(f'plan {plan} has no consultant advice under advice/{seq}-*; run pair.sh discuss so both agents respond', 6)


VARIANT = TrioVariant
