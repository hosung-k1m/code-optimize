"""Strict YAML problem configuration loading and capability validation."""

from pathlib import Path

from omegaconf import OmegaConf
from slimconfig import build_output, load_yaml
from slimconfig.config import compose

from contract import ProblemConfig
from verifier.checks import validate_config_values


def load_problem_config(path: Path) -> ProblemConfig:
    """Parse self-contained YAML through slimconfig into the shared typed contract model."""
    raw = load_yaml(path)
    _reject_external_config_inputs(raw)
    composed = compose(path)
    expected = {
        "contract.ProblemConfig",
        "contract.ExecutionSettings",
        "contract.MeasurementSettings",
        "contract.Policy",
        "contract.CaseConfig",
    }
    root_claims = [claim for claim in composed.claims if not claim.node]
    if len(root_claims) != 1 or root_claims[0].schema != "contract.ProblemConfig":
        raise ValueError("problem YAML must declare contract.ProblemConfig as its root schema")
    nested_schemas = {
        ("execution",): "contract.ExecutionSettings",
        ("measurement",): "contract.MeasurementSettings",
        ("policy",): "contract.Policy",
    }
    for claim in composed.claims:
        if claim.schema not in expected:
            raise ValueError(f"problem YAML declares unsupported schema {claim.schema!r}")
        if claim.node:
            expected_schema = nested_schemas.get(claim.node)
            is_case = claim.node[0] == "cases" and claim.schema == "contract.CaseConfig"
            if claim.schema != expected_schema and not is_case:
                raise ValueError(f"schema {claim.schema!r} is declared at an unsupported YAML location")
    config = build_output(OmegaConf.to_container(composed.config, resolve=True), ProblemConfig)
    valid, reason = validate_config_values(config)
    if not valid:
        raise ValueError(reason)
    return config


def _reject_external_config_inputs(value: object, location: str = "problem.yaml") -> None:
    """Refuse slimconfig defaults and interpolations that make config depend on ambient state."""
    if isinstance(value, dict):
        if any(str(key).split(">", 1)[0].strip() == "_default" for key in value):
            raise ValueError(f"{location} uses _default, which is not allowed in a problem bundle")
        for key, item in value.items():
            _reject_external_config_inputs(str(key), location)
            _reject_external_config_inputs(item, f"{location}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_external_config_inputs(item, f"{location}[{index}]")
    elif isinstance(value, str) and "${" in value:
        raise ValueError(f"{location} uses interpolation, which is not allowed in a problem bundle")
