<!-- BEGIN_TF_DOCS -->
## Requirements

| Name | Version |
| ---- | ------- |
| <a name="requirement_kubernetes"></a> [kubernetes](#requirement\_kubernetes) | ~> 3.2 |

## Providers

| Name | Version |
| ---- | ------- |
| <a name="provider_kubernetes"></a> [kubernetes](#provider\_kubernetes) | ~> 3.2 |

## Modules

No modules.

## Resources

| Name | Type |
| ---- | ---- |
| [kubernetes_manifest.app](https://registry.terraform.io/providers/hashicorp/kubernetes/latest/docs/resources/manifest) | resource |

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_field_manager"></a> [field\_manager](#input\_field\_manager) | Field manager name used when applying managed fields to Kubernetes resources. | `string` | `"stacksmith"` | no |
| <a name="input_manifest_files"></a> [manifest\_files](#input\_manifest\_files) | Paths to Kubernetes manifest YAML files to apply. | `list(string)` | n/a | yes |
| <a name="input_namespace"></a> [namespace](#input\_namespace) | Kubernetes namespace for the manifests. | `string` | n/a | yes |
| <a name="input_wait"></a> [wait](#input\_wait) | Whether Terraform waits for each manifest apply operation to complete. | `bool` | `true` | no |

## Outputs

No outputs.
<!-- END_TF_DOCS -->