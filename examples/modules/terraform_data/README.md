# terraform_data component

This module creates one credential-free Terraform `terraform_data` using the supplied input values.

## Usage

```hcl
module "example" {
  source = "./modules/terraform_data"

  input = {
    message = "hello"
  }
}
```

<!-- BEGIN_TF_DOCS -->
## Requirements

| Name | Version |
|------|---------|
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.5.0 |

## Providers

No providers.

## Modules

No modules.

## Resources

| Name | Type |
|------|------|
| [terraform_data.this](https://registry.terraform.io/providers/hashicorp/terraform/latest/docs/resources/data) | resource |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| <a name="input_input"></a> [input](#input\_input) | Values that cause the resource to be replaced or computed values to state when they change. | `map(string)` | n/a | yes |

## Outputs

| Name | Description |
|------|-------------|
| <a name="output_id"></a> [id](#output\_id) | ID of the terraform_data resource. |
<!-- END_TF_DOCS -->
