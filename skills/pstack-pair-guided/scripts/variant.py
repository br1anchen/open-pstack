from pstack_core.errors import Die
from pstack_core.store import header_field
from pstack_core.variant import Variant


class GuidedVariant(Variant):
    def plan_gate_extra(self, ctx, store, plan, review, seq):
        """An agreed plan also needs the human approval its scale calls for."""
        scale = header_field(plan, 'scale')
        approval = header_field(review, 'approval')
        if scale == 'small':
            return
        if scale == 'medium':
            if approval not in ('posted', 'human'):
                raise Die(f"medium plan needs approval: posted or human in {review} (found '{approval or 'missing'}')", 6)
        elif scale == 'large':
            if approval != 'human':
                raise Die(f"large plan needs approval: human in {review} (found '{approval or 'missing'}'); post the direction summary and wait for the human", 6)
        else:
            raise Die(f"plan {plan} has scale '{scale or 'missing'}'; set small, medium, or large", 6)


VARIANT = GuidedVariant
