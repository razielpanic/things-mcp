"""Pydantic models matching Cultured Code's actual Things 3 data model.

Things has two orthogonal dimensions for every item:
1. Context (structural): Area > Project > Heading > To-do
2. Temporal placement (when): derived from `start` flag + `start_date`

The temporal "lists" (Inbox, Today, Upcoming, Anytime, Someday, Logbook) are
computed views, not storage containers. An item's list membership is derived
from its properties -- see derivation.py for the logic.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ItemType(str, Enum):
    """Things item types as stored in the database (TMTask.type)."""

    TODO = "to-do"
    PROJECT = "project"
    HEADING = "heading"


class ItemStatus(str, Enum):
    """Things item status as stored in the database (TMTask.status)."""

    INCOMPLETE = "incomplete"
    COMPLETED = "completed"
    CANCELED = "canceled"


class StartFlag(str, Enum):
    """The sticky `start` flag. This is NOT the current list.

    This flag records the item's base temporal state. Actual list membership
    is derived by combining this flag with `start_date` -- see derivation.py.

    Database values: 0=Inbox, 1=Anytime, 2=Someday
    """

    INBOX = "Inbox"
    ANYTIME = "Anytime"
    SOMEDAY = "Someday"


class DerivedList(str, Enum):
    """The actual list an item appears in, computed from start + start_date.

    This is what the user sees in Things' sidebar. It is never stored directly
    in the database -- it is always derived.
    """

    INBOX = "Inbox"
    TODAY = "Today"
    UPCOMING = "Upcoming"
    ANYTIME = "Anytime"
    SOMEDAY = "Someday"
    LOGBOOK = "Logbook"


class WhenValue(str, Enum):
    """Valid values for the `when` scheduling parameter.

    These map to specific write operations:
    - today: schedule for current date (AppleScript)
    - tomorrow: schedule for tomorrow (AppleScript)
    - evening: schedule for this evening (AppleScript)
    - anytime: clear start_date, set start=Anytime (AppleScript move)
    - someday: move to Someday list (AppleScript move)
    - A date string YYYY-MM-DD is also valid but not in this enum.
    """

    TODAY = "today"
    TOMORROW = "tomorrow"
    EVENING = "evening"
    ANYTIME = "anytime"
    SOMEDAY = "someday"


class ChecklistItem(BaseModel):
    """A lightweight sub-item of a to-do. No dates, tags, or notes."""

    title: str
    completed: bool = False


class TemporalState(BaseModel):
    """Temporal placement state for a Things item.

    Groups the fields that drive list derivation: the sticky start flag,
    optional start_date, the computed derived_list, item status, and the
    evening flag (a visual sub-section of Today, not a separate list).
    """

    model_config = ConfigDict(use_enum_values=True)

    start: StartFlag = StartFlag.ANYTIME
    start_date: Optional[date] = None
    derived_list: DerivedList
    status: ItemStatus = ItemStatus.INCOMPLETE
    # None means "could not be read", which is deliberately not False. This
    # field was a hardcoded False for its whole history (see evening.py), and
    # three dev issues were filed against writes that had actually landed. A
    # falsy-but-honest null tells a caller that verification is unavailable;
    # False tells it the item is not in the evening.
    evening: Optional[bool] = None


class ItemContext(BaseModel):
    """Structural context for a Things item (project, area, heading).

    Represents the item's position in Things' structural hierarchy,
    orthogonal to its temporal placement.
    """

    model_config = ConfigDict(use_enum_values=True)

    project_uuid: Optional[str] = None
    project_title: Optional[str] = None
    area_uuid: Optional[str] = None
    area_title: Optional[str] = None
    heading_title: Optional[str] = None


class RepeatInfo(BaseModel):
    """An item's place in a Things repeat, read from the rt1_* columns.

    role is "template" (the hidden item the repeat UI edits), "instance" (a
    copy the template generated), or "unknown" (the repeat columns could not
    be read -- not the same as having no repeat, which is repeat=None).

    The template_* fields describe the template in both roles, so an instance
    shows whether the repeat behind it is still live. Stopping or pausing a
    repeat is UI-only in Things 3.24; no tool here can do it.
    """

    role: Literal["template", "instance", "unknown"]
    template_uuid: Optional[str] = None
    template_found: Optional[bool] = None
    template_trashed: Optional[bool] = None
    template_status: Optional[str] = None
    template_paused: Optional[bool] = None
    next_instance_date: Optional[date] = None


class ThingsItem(BaseModel):
    """A Things 3 item (to-do, project, or heading) with derived list.

    Every response from this MCP includes `derived_list` inside
    `temporal_state` -- the computed actual list the item appears in.
    Consumers should use `temporal_state.derived_list` for all
    list-related logic, never the raw `start` field.
    """

    model_config = ConfigDict(use_enum_values=True)

    uuid: str
    title: str
    type: ItemType
    temporal_state: TemporalState
    context: ItemContext = Field(default_factory=ItemContext)
    deadline: Optional[date] = None

    # Content
    tags: list[str] = Field(default_factory=list)
    notes: Optional[str] = None
    checklist: list[ChecklistItem] = Field(default_factory=list)

    # Timestamps
    created: Optional[datetime] = None
    modified: Optional[datetime] = None
    completed_date: Optional[datetime] = None

    # Sort order
    today_index: Optional[int] = None
    index: Optional[int] = None

    # Repeat relation (populated only by get_item; None = not repeating)
    repeat: Optional[RepeatInfo] = None

    # Children (populated only when include_items=True for projects)
    items: list[ThingsItem] = Field(default_factory=list)


class AreaItem(BaseModel):
    """A Things 3 area -- an ongoing, never-completed structural container.

    Areas have no temporal state (no start, start_date, or status).
    They hold projects and loose to-dos.
    """

    model_config = ConfigDict(use_enum_values=True)

    uuid: str
    title: str
    type: str = "area"
    tags: list[str] = Field(default_factory=list)
    items: list[ThingsItem] = Field(default_factory=list)


class ViewResponse(BaseModel):
    """Self-describing wrapper for list-level responses.

    Every list query returns this wrapper so the LLM agent sees
    the CC concept explanation alongside the items.
    """

    view: str
    description: str
    items: list[ThingsItem]
    count: int


class ErrorResponse(BaseModel):
    """Standard error response."""

    success: bool = False
    error: str
    message: str


class SuccessResponse(BaseModel):
    """Standard success response for write operations.

    Includes the item's post-write temporal_state so the agent sees
    the state transition without needing a follow-up get_item call.
    """

    model_config = ConfigDict(use_enum_values=True)

    success: bool = True
    uuid: Optional[str] = None
    message: str = ""
    action: Optional[str] = None
    temporal_state: Optional[TemporalState] = None
