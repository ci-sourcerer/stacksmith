variable "manifest_files" {
  type = list(string)
  description = "Paths to Kubernetes manifest YAML files to apply."
}

variable "namespace" {
  type = string
  description = "Kubernetes namespace for the manifests."
}

variable "field_manager" {
  type    = string
  default = "stacksmith"
  description = "Field manager name used when applying managed fields to Kubernetes resources."
}

variable "wait" {
  type    = bool
  default = true
  description = "Whether Terraform waits for each manifest apply operation to complete."
}
