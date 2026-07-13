variable "jenkins_url" {
  type        = string
  description = "Base URL of the Jenkins instance, such as `https://jenkins.example.com`."

  validation {
    condition     = can(regex("^https?://", var.jenkins_url))
    error_message = "jenkins_url must use the http or https scheme."
  }
}

variable "job_name" {
  type        = string
  description = "Jenkins job name, including folder names separated by `/` when applicable."

  validation {
    condition     = trim(var.job_name, "/") != ""
    error_message = "job_name must not be empty."
  }
}

variable "parameters" {
  type        = map(string)
  default     = {}
  sensitive   = true
  description = "Parameters submitted to the Jenkins build. Marked sensitive because build parameters can contain secrets."
}

variable "job_has_parameters" {
  type        = bool
  default     = null
  nullable    = true
  description = "Whether the Jenkins job defines build parameters. Set false for non-parameterized jobs so Stacksmith uses `/build` instead of `/buildWithParameters`."
}

variable "rebuild_token" {
  type        = string
  default     = null
  nullable    = true
  description = "Optional arbitrary value that forces a new Jenkins build whenever it changes."
}

variable "username" {
  type        = string
  sensitive   = true
  description = "Username used for Jenkins HTTP basic authentication."
}

variable "api_token" {
  type        = string
  sensitive   = true
  description = "Jenkins API token or password used for HTTP basic authentication."
}
