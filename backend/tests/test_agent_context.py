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


def test_build_skill_prompt_sections_respects_user_invocable_flag(monkeypatch, tmp_path):
    agent_id = uuid.uuid4()
    tool_root = tmp_path / "tool"
    data_root = tmp_path / "data"
    monkeypatch.setattr(agent_context, "TOOL_WORKSPACE", tool_root)
    monkeypatch.setattr(agent_context, "PERSISTENT_DATA", data_root)

    _write_skill(
        data_root,
        agent_id,
        "db-migration",
        """---
name: db-migration
description: Perform safe schema and data migrations
user-invocable: false
keywords:
  - database migration
  - schema change
---

# DB Migration

Use this skill for schema and data migrations.
""",
    )

    index_text, activated_text = agent_context._build_skill_prompt_sections(
        agent_id,
        activation_hint="Help me migrate a database schema for production safely.",
    )

    assert "db-migration" in index_text
    assert "db-migration" not in activated_text


def test_build_skill_prompt_sections_allows_explicit_skill_name_for_non_invocable(monkeypatch, tmp_path):
    agent_id = uuid.uuid4()
    tool_root = tmp_path / "tool"
    data_root = tmp_path / "data"
    monkeypatch.setattr(agent_context, "TOOL_WORKSPACE", tool_root)
    monkeypatch.setattr(agent_context, "PERSISTENT_DATA", data_root)

    _write_skill(
        data_root,
        agent_id,
        "db-migration",
        """---
name: db-migration
description: Perform safe schema and data migrations
user-invocable: false
---

# DB Migration
""",
    )

    _index_text, activated_text = agent_context._build_skill_prompt_sections(
        agent_id,
        activation_hint="Please use db-migration for this request.",
    )

    assert "db-migration" in activated_text


def test_build_skill_prompt_sections_blocks_missing_runtime_requirements(monkeypatch, tmp_path):
    agent_id = uuid.uuid4()
    tool_root = tmp_path / "tool"
    data_root = tmp_path / "data"
    monkeypatch.setattr(agent_context, "TOOL_WORKSPACE", tool_root)
    monkeypatch.setattr(agent_context, "PERSISTENT_DATA", data_root)

    _write_skill(
        data_root,
        agent_id,
        "mysql-audit",
        """---
name: mysql-audit
description: Audit mysql schema and constraints
requires:
  bins: [mysql]
  env: [MYSQL_PWD]
keywords:
  - mysql
  - schema audit
---

# MySQL Audit
""",
    )

    monkeypatch.setattr(agent_context, "_which_binary", lambda _name: None)
    monkeypatch.setattr(agent_context, "_has_env_var", lambda _name: False)

    _index_text, activated_text = agent_context._build_skill_prompt_sections(
        agent_id,
        activation_hint="Please do a mysql schema audit for this database.",
    )

    assert "### mysql-audit" not in activated_text
    assert "Skipped Skills For Current Request" in activated_text
    assert "mysql-audit" in activated_text
    assert "missing bins: mysql" in activated_text
    assert "missing env: MYSQL_PWD" in activated_text


def test_build_skill_prompt_sections_uses_openclaw_metadata_requires(monkeypatch, tmp_path):
    agent_id = uuid.uuid4()
    tool_root = tmp_path / "tool"
    data_root = tmp_path / "data"
    monkeypatch.setattr(agent_context, "TOOL_WORKSPACE", tool_root)
    monkeypatch.setattr(agent_context, "PERSISTENT_DATA", data_root)

    _write_skill(
        data_root,
        agent_id,
        "ops-rollback",
        """---
name: ops-rollback
description: Rollback deployment safely
metadata:
  openclaw:
    requires:
      bins: [kubectl]
      env: [KUBECONFIG]
keywords:
  - rollback
  - deploy
---

# Ops Rollback
""",
    )

    monkeypatch.setattr(agent_context, "_which_binary", lambda _name: "/usr/bin/fake")
    monkeypatch.setattr(agent_context, "_has_env_var", lambda _name: True)

    _index_text, activated_text = agent_context._build_skill_prompt_sections(
        agent_id,
        activation_hint="We need to rollback deployment now.",
    )

    assert "ops-rollback" in activated_text
    assert "Skipped Skills For Current Request" not in activated_text
