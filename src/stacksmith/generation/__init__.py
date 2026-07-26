"""OpenTofu, Terragrunt, provider, and operation generation."""

from .operations import build_operation_module_spec
from .terraform import generate_tf_json, operation_module_name, write_tf_json
from .terragrunt import generate_terragrunt_json, write_terragrunt_json

__all__ = [
    "build_operation_module_spec",
    "generate_terragrunt_json",
    "generate_tf_json",
    "operation_module_name",
    "write_terragrunt_json",
    "write_tf_json",
]
