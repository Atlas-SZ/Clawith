import uuid
from pathlib import Path

from app.services import agent_context


def _write_skill(
    base_dir: Path,
    agent_id: uuid.UUID,
    folder_name: str,
    skill_md: str,
    extra_files: dict[str, str] | None = None,
) -> None:
    skill_dir = base_dir / str(agent_id) / "skills" / folder_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
    for rel_path, content in (extra_files or {}).items():
        target = skill_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def test_build_skill_prompt_sections_preloads_matching_skill(monkeypatch, tmp_path):
    agent_id = uuid.uuid4()
    tool_root = tmp_path / "tool"
    data_root = tmp_path / "data"
    monkeypatch.setattr(agent_context, "TOOL_WORKSPACE", tool_root)
    monkeypatch.setattr(agent_context, "PERSISTENT_DATA", data_root)

    _write_skill(
        data_root,
        agent_id,
        "web-research",
        """---
name: web-research
description: Research current information on the web and synthesize sources
---

# Web Research

**Keywords**: web search, research, competitor analysis, source evaluation

Use this skill when the user asks for web research or competitor research.
""",
        extra_files={"scripts/search_helper.py": "print('search')\n"},
    )
    _write_skill(
        data_root,
        agent_id,
        "calendar-helper",
        """---
name: calendar-helper
description: Create or update calendar events
---

# Calendar Helper

**Keywords**: calendar, meeting, schedule
""",
    )

    index_text, activated_text = agent_context._build_skill_prompt_sections(
        agent_id,
        activation_hint="Please research our competitors on the web and summarize the latest findings.",
    )

    assert "web-research" in index_text
    assert "calendar-helper" in index_text
    assert "Activated Skills For Current Request" in activated_text
    assert "web-research" in activated_text
    assert "search_helper.py" in activated_text
    assert "calendar-helper" not in activated_text


def test_build_skill_prompt_sections_skips_disable_model_invocation(monkeypatch, tmp_path):
    agent_id = uuid.uuid4()
    tool_root = tmp_path / "tool"
    data_root = tmp_path / "data"
    monkeypatch.setattr(agent_context, "TOOL_WORKSPACE", tool_root)
    monkeypatch.setattr(agent_context, "PERSISTENT_DATA", data_root)

    _write_skill(
        data_root,
        agent_id,
        "secret-research",
        """---
name: secret-research
description: Research confidential competitor intelligence
disable-model-invocation: true
---

# Secret Research

**Keywords**: competitor, research, intelligence
""",
    )

    index_text, activated_text = agent_context._build_skill_prompt_sections(
        agent_id,
        activation_hint="Research competitor intelligence for me.",
    )

    assert "secret-research" not in index_text
    assert activated_text == ""
