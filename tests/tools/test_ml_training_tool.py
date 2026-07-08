"""Tests for Sinria's ML training/fine-tuning helper tool."""

import json

from model_tools import get_tool_definitions
from tools.ml_training_tool import ml_training
from toolsets import resolve_toolset, validate_toolset


def _loads(payload: str):
    return json.loads(payload)


def test_ml_training_toolset_is_registered():
    assert validate_toolset("ml_training") is True
    assert "ml_training" in resolve_toolset("ml_training")

    tools = get_tool_definitions(enabled_toolsets=["ml_training"], quiet_mode=True)
    names = {tool["function"]["name"] for tool in tools}
    assert "ml_training" in names


def test_env_check_returns_structured_readiness_payload():
    result = _loads(ml_training("env_check"))

    assert result["success"] is True
    assert "python" in result
    assert "packages" in result
    assert "ready_for_local_lora" in result
    assert "install_command" in result


def test_scaffold_lora_writes_reproducible_project(tmp_path):
    project_dir = tmp_path / "finetune"
    result = _loads(
        ml_training(
            "scaffold_lora",
            project_dir=str(project_dir),
            base_model="Qwen/Qwen2.5-0.5B-Instruct",
            max_steps=3,
        )
    )

    assert result["success"] is True
    assert (project_dir / "requirements.txt").exists()
    assert (project_dir / "train_lora.py").exists()
    assert (project_dir / "data" / "sample.jsonl").exists()
    assert (project_dir / "README.md").exists()
    assert "python train_lora.py" in result["run_command"]


def test_validate_dataset_checks_text_field(tmp_path):
    dataset = tmp_path / "data.jsonl"
    dataset.write_text('{"text": "hello"}\n{"text": "world"}\n', encoding="utf-8")

    result = _loads(ml_training("validate_dataset", dataset_path=str(dataset), text_field="text"))

    assert result["success"] is True
    assert result["valid"] is True
    assert len(result["sampled_rows"]) == 2


def test_scaffold_shell_quotes_generated_commands(tmp_path):
    project_dir = tmp_path / "fine tune project"
    result = _loads(
        ml_training(
            "scaffold_lora",
            project_dir=str(project_dir),
            base_model="org/model with spaces",
            output_dir="outputs/lora adapter",
        )
    )

    assert result["success"] is True
    assert f"cd '{project_dir}'" in result["install_command"]
    assert "--base-model 'org/model with spaces'" in result["run_command"]
    assert "--output-dir 'outputs/lora adapter'" in result["run_command"]


def test_scaffold_refuses_to_overwrite_non_empty_directory(tmp_path):
    project_dir = tmp_path / "finetune"
    project_dir.mkdir()
    (project_dir / "existing.txt").write_text("keep", encoding="utf-8")

    result = _loads(ml_training("scaffold_lora", project_dir=str(project_dir)))

    assert result["success"] is False
    assert "not empty" in result["error"]
