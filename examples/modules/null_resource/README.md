# Null resource module

This module creates one credential-free Terraform `null_resource` using the supplied trigger values.

<!-- BEGIN_TF_DOCS -->
## Requirements

| Name | Version |
| ---- | ------- |
| <a name="requirement_null"></a> [null](#requirement\_null) | ~> 3.2 |

## Providers

| Name | Version |
| ---- | ------- |
| <a name="provider_null"></a> [null](#provider\_null) | ~> 3.2 |

## Modules

No modules.

## Resources

| Name | Type |
| ---- | ---- |
| [null_resource.this](https://registry.terraform.io/providers/hashicorp/null/latest/docs/resources/resource) | resource |

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_triggers"></a> [triggers](#input\_triggers) | Values that cause the null resource to be replaced when they change. | `map(string)` | n/a | yes |

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_id"></a> [id](#output\_id) | ID of the null resource. |
<!-- END_TF_DOCS -->