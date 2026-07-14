variable "spec" {
  type      = any
  sensitive = true
}

resource "terraform_data" "operation" {
  triggers_replace = [sha256(nonsensitive(jsonencode(var.spec)))]

  provisioner "local-exec" {
    command = "python3 ${path.module}/${var.spec.runner}.py"
    environment = {
      STACKSMITH_OPERATION_SPEC = jsonencode(var.spec)
    }
  }
}
