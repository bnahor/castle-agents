from __future__ import annotations

from pydantic import BaseModel, Field

from ._enums import Action, Kind


class Route(BaseModel, frozen=True):
    msg_id: str = Field(description="The msg_id of the message this route is for.")
    action: Action = Field(
        description=(
            "new: a separate question. amend_restart: changes what an earlier request asks "
            "(different ticker, different question) so its work must restart. amend_steer: adds "
            "detail or scope to an earlier request without invalidating its work so far. "
            "cancel: the user withdraws an earlier request."
        )
    )
    complexity: Kind = Field(
        description=(
            "For new: simple if answerable from general knowledge in a sentence or two; heavy if "
            "it needs live market data, multiple steps, or analysis. Ignored for other actions."
        )
    )
    target: str | None = Field(
        default=None,
        description=(
            "Request id, or the msg_id of an earlier message in this batch, that this amends "
            "or cancels. Null for new."
        ),
    )
    text: str = Field(
        default="",
        description=(
            "new: empty, unless the question must be rewritten to stand on its own. "
            "amend_restart: the full rewritten question. amend_steer: only the addition. "
            "cancel: empty."
        ),
    )
    reason: str = Field(default="", description="Leave empty.")

    @classmethod
    def new(cls, msg_id: str, text: str, complexity: Kind, reason: str) -> Route:
        return cls(
            msg_id=msg_id, action=Action.NEW, complexity=complexity, text=text, reason=reason
        )


class RouteBatch(BaseModel, frozen=True):
    routes: list[Route]
