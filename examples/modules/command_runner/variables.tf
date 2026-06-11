variable "command_name" {
  type        = string
  description = "Name of the approved command to run. Approved values are enforced through Stacksmith validation."
}

variable "vars" {
  type        = map(any)
  default     = {}
  description = "Inputs passed through to the selected approved command as environment variables."
}

variable "cwd" {
  type        = string
  default     = null
  description = "Optional working directory for the approved command."
}
