<!-- BEGIN_TF_DOCS -->
## Requirements

| Name | Version |
| ---- | ------- |
| <a name="requirement_helm"></a> [helm](#requirement\_helm) | ~> 3.1 |

## Providers

| Name | Version |
| ---- | ------- |
| <a name="provider_helm"></a> [helm](#provider\_helm) | ~> 3.1 |

## Modules

No modules.

## Resources

| Name | Type |
| ---- | ---- |
| [helm_release.app](https://registry.terraform.io/providers/hashicorp/helm/latest/docs/resources/release) | resource |

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_atomic"></a> [atomic](#input\_atomic) | Whether rollbacks are attempted if the Helm release deployment fails. | `bool` | `true` | no |
| <a name="input_chart"></a> [chart](#input\_chart) | Helm chart name to deploy. | `string` | n/a | yes |
| <a name="input_chart_version"></a> [chart\_version](#input\_chart\_version) | Helm chart version. | `string` | n/a | yes |
| <a name="input_create_namespace"></a> [create\_namespace](#input\_create\_namespace) | Whether to create the target namespace if it does not already exist. | `bool` | `false` | no |
| <a name="input_name"></a> [name](#input\_name) | Optional Helm release name. When omitted, a default name is derived from the chart and namespace. | `string` | `null` | no |
| <a name="input_namespace"></a> [namespace](#input\_namespace) | Target Kubernetes namespace for the Helm release. | `string` | n/a | yes |
| <a name="input_repository"></a> [repository](#input\_repository) | Helm chart repository URL. | `string` | n/a | yes |
| <a name="input_values_files"></a> [values\_files](#input\_values\_files) | List of Helm values YAML files to render into the chart deployment. | `list(string)` | `[]` | no |
| <a name="input_wait"></a> [wait](#input\_wait) | Whether Terraform waits for the Helm release to become ready. | `bool` | `true` | no |

## Outputs

No outputs.
<!-- END_TF_DOCS -->