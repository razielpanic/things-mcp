"""Opt-in notes-shape warning on write responses (THINGS_MCP_NOTES_WARN_LINES)."""

from unittest.mock import patch

import pytest

from things_mcp.models import SuccessResponse
from things_mcp.server import NOTES_WARN_ENV, create_project, create_todo, update_item

pytestmark = pytest.mark.asyncio

UUID = "B" * 22
LONG = "\n".join(f"Step {n}: water the fern" for n in range(1, 7))


def ok():
    return SuccessResponse(uuid=UUID, message="ok", action="created")


@patch("things_mcp.server.writes.create_todo")
async def test_no_env_means_no_warning(mock_fn, monkeypatch):
    monkeypatch.delenv(NOTES_WARN_ENV, raising=False)
    mock_fn.return_value = ok()
    result = await create_todo(title="Water the fern", notes=LONG)
    assert "notes_warning" not in result


@patch("things_mcp.server.writes.create_todo")
async def test_over_limit_warns_but_still_writes(mock_fn, monkeypatch):
    monkeypatch.setenv(NOTES_WARN_ENV, "4")
    mock_fn.return_value = ok()
    result = await create_todo(title="Water the fern", notes=LONG)
    assert result["success"] is True
    assert "6 lines (limit 4)" in result["notes_warning"]
    mock_fn.assert_called_once()


@patch("things_mcp.server.writes.create_todo")
async def test_blocker_block_lines_do_not_count(mock_fn, monkeypatch):
    monkeypatch.setenv(NOTES_WARN_ENV, "2")
    mock_fn.return_value = ok()
    notes = (
        "Repot the fern\nSee plant-care.md\n\n"
        "Gated by:\nthings:///show?id=" + "C" * 22 + "\n"
        "Gates:\nthings:///show?id=" + "D" * 22
    )
    result = await create_todo(title="Repot the fern", notes=notes)
    assert "notes_warning" not in result


@patch("things_mcp.server.writes.create_project")
async def test_markdown_warns_under_limit(mock_fn, monkeypatch):
    monkeypatch.setenv(NOTES_WARN_ENV, "10")
    mock_fn.return_value = ok()
    result = await create_project(title="Balcony Garden", notes="See [the plan](https://example.com)")
    assert "Markdown" in result["notes_warning"]


@patch("things_mcp.server.writes.update_item")
async def test_update_item_warns(mock_fn, monkeypatch):
    monkeypatch.setenv(NOTES_WARN_ENV, "1")
    mock_fn.return_value = ok()
    result = await update_item(uuid=UUID, notes="**Buy soil**\nand a pot")
    assert "2 lines (limit 1)" in result["notes_warning"]
    assert "Markdown" in result["notes_warning"]


@pytest.mark.parametrize("raw", ["", "abc", "0", "-3"])
@patch("things_mcp.server.writes.create_todo")
async def test_unusable_env_is_off(mock_fn, raw, monkeypatch):
    monkeypatch.setenv(NOTES_WARN_ENV, raw)
    mock_fn.return_value = ok()
    result = await create_todo(title="Water the fern", notes=LONG)
    assert "notes_warning" not in result


@patch("things_mcp.server.writes.create_todo")
async def test_no_warning_on_failed_write(mock_fn, monkeypatch):
    monkeypatch.setenv(NOTES_WARN_ENV, "1")
    mock_fn.side_effect = RuntimeError("boom")
    result = await create_todo(title="Water the fern", notes=LONG)
    assert result["success"] is False
    assert "notes_warning" not in result
