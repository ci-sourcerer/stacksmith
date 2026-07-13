resource "terraform_data" "build" {
  triggers_replace = {
    jenkins_url        = var.jenkins_url
    job_name           = var.job_name
    parameters         = var.parameters
    job_has_parameters = var.job_has_parameters
    rebuild_token      = var.rebuild_token
  }

  lifecycle {
    precondition {
      condition = var.job_has_parameters != null || length(var.parameters) > 0
      error_message = "Set job_has_parameters=false for non-parameterized jobs when parameters is empty, or provide at least one parameter."
    }

    precondition {
      condition = var.job_has_parameters != false || length(var.parameters) == 0
      error_message = "parameters must be empty when job_has_parameters is false."
    }
  }

  provisioner "local-exec" {
    command = "python3 ${path.module}/trigger_build.py"

    environment = {
      JENKINS_API_TOKEN          = var.api_token
      JENKINS_JOB_HAS_PARAMETERS = var.job_has_parameters == null ? "" : tostring(var.job_has_parameters)
      JENKINS_JOB_NAME           = var.job_name
      JENKINS_PARAMETERS_JSON    = jsonencode(var.parameters)
      JENKINS_URL                = var.jenkins_url
      JENKINS_USERNAME           = var.username
    }
  }
}
