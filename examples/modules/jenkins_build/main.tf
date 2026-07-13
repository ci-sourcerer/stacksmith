resource "terraform_data" "build" {
  triggers_replace = {
    jenkins_url   = var.jenkins_url
    job_name      = var.job_name
    parameters    = var.parameters
    rebuild_token = var.rebuild_token
  }

  provisioner "local-exec" {
    command = "python3 ${path.module}/trigger_build.py"

    environment = {
      JENKINS_API_TOKEN       = var.api_token
      JENKINS_JOB_NAME        = var.job_name
      JENKINS_PARAMETERS_JSON = jsonencode(var.parameters)
      JENKINS_URL             = var.jenkins_url
      JENKINS_USERNAME        = var.username
    }
  }
}
