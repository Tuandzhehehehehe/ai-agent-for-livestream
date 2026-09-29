"""Comment classification reusing the customer-Q&A intent and safety rules."""

from collections.abc import Iterable

from ai.customer_qa.intent_classifier import classify_intent
from ai.customer_qa.safety import MAX_CUSTOMER_MESSAGE_LENGTH
from ai.customer_qa.schemas import Intent
from ai.operations.schemas import CommentClassification, SimulatedComment

_REVIEW_REASONS: dict[Intent, str] = {
    "prompt_injection": "Untrusted instructions were detected in the comment.",
    "refund_request": "Refund requests require human handling.",
    "price_change_request": "Price changes or discounts require human handling.",
    "consequential_action": "Order, payment, return, or account actions require human handling.",
    "complaint": "A customer complaint may require operator intervention.",
    "unknown": "The comment is unsupported or unclear and needs human review.",
}


def classify_comment(
    comment: SimulatedComment,
    *,
    product_names: Iterable[str] = (),
) -> CommentClassification:
    """Classify one validated comment without treating it as an instruction."""
    if not isinstance(comment, SimulatedComment):
        raise TypeError("comment must be a SimulatedComment")
    if not isinstance(comment.comment_id, str) or not comment.comment_id.strip():
        raise ValueError("comment.comment_id must be a non-empty string")
    if not isinstance(comment.text, str):
        raise ValueError("comment.text must be a string")

    if len(comment.text) > MAX_CUSTOMER_MESSAGE_LENGTH:
        return CommentClassification(
            comment_id=comment.comment_id,
            intent="unknown",
            needs_human_review=True,
            reason=(
                f"The comment exceeds the {MAX_CUSTOMER_MESSAGE_LENGTH}-character "
                "safety limit."
            ),
        )

    intent = classify_intent(comment.text, product_names=product_names).intent
    reason = _REVIEW_REASONS.get(intent)
    return CommentClassification(
        comment_id=comment.comment_id,
        intent=intent,
        needs_human_review=reason is not None,
        reason=reason,
    )