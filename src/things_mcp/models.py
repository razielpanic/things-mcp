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
from typing import Optional

from pydantic import BaseModel, Field


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


class ThingsItem(BaseModel):
    """A Things 3 item (to-do, project, or heading) with derived list.

    Every response from this MCP includes `derived_list` -- the computed
    actual list the item appears in. Consumers should use `derived_list`
    for all list-related logic, never the raw `start` field.
    """

    uuid: str
    title: str
    type: ItemType
    status: ItemStatus = ItemStatus.INCOMPLETE

    # The sticky start flag -- NOT the current list
    start: StartFlag = StartFlag.ANYTIME

    # Temporal fields that drive list derivation
    start_date: Optional[date] = None
    deadline: Optional[date] = None

    # THE KEY FIELD: computed from start + start_date
    derived_list: DerivedList

    # Context hierarchy
    project_uuid: Optional[str] = None
    project_title: Optional[str] = None
    area_uuid: Optional[str] = None
    area_title: Optional[str] = None
    heading_title: Optional[str] = None

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


class ErrorResponse(BaseModel):
    """Standard error response."""

    success: bool = False
    error: str
    message: str


class SuccessResponse(BaseModel):
    """Standard success response for write operations."""

    success: bool = True
    uuid: Optional[str] = None
    message: str = ""
