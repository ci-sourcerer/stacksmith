variable "spec" {
  type      = any
  sensitive = true
}

variable "executable" {
  type        = string
  description = "The executable to run the operation runner script."
  default     = "python3"
}

resource "terraform_data" "operation" {
  triggers_replace = [sha256(nonsensitive(jsonencode(var.spec)))]

  provisioner "local-exec" {
    command = "${var.executable} ${path.module}/${var.spec.runner}.py"
    environment = {
      STACKSMITH_OPERATION_SPEC = jsonencode(var.spec)
    }
  }
}
