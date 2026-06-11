variable "name" {
  type        = string
  default     = null
  description = "Optional Helm release name. When omitted, a default name is derived from the chart and namespace."
}

variable "chart" {
  type        = string
  description = "Helm chart name to deploy."
}

variable "repository" {
  type        = string
  description = "Helm chart repository URL."
}

variable "chart_version" {
  type        = string
  description = "Helm chart version."
}

variable "namespace" {
  type        = string
  description = "Target Kubernetes namespace for the Helm release."
}

variable "create_namespace" {
  type        = bool
  default     = false
  description = "Whether to create the target namespace if it does not already exist."
}

variable "wait" {
  type        = bool
  default     = true
  description = "Whether Terraform waits for the Helm release to become ready."
}

variable "atomic" {
  type        = bool
  default     = true
  description = "Whether rollbacks are attempted if the Helm release deployment fails."
}

variable "values_files" {
  type    = list(string)
  default = []
  description = "List of Helm values YAML files to render into the chart deployment."
}
