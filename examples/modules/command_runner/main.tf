locals {
  approved_commands = {
    deploy  = "echo approved command: deploy"
    migrate = "echo approved command: migrate"
    prune   = "echo approved command: prune"
  }

  command_line = local.approved_commands[var.command_name]
  vars_as_env  = { for k, v in var.vars : k => tostring(v) }
  environment  = local.vars_as_env
}

resource "terraform_data" "run" {
  triggers_replace = [
    local.command_line,
    coalesce(var.cwd, ""),
    jsonencode(var.vars)
  ]

  provisioner "local-exec" {
    command     = local.command_line
    environment = local.environment
    working_dir = var.cwd
  }
}
