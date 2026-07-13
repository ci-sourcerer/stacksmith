output "build_url" {
  value       = "${trimsuffix(var.jenkins_url, "/")}/job/${join("/job/", [for segment in split("/", trim(var.job_name, "/")) : replace(urlencode(segment), "+", "%20")])}${var.job_has_parameters == false ? "/build" : "/buildWithParameters"}"
  description = "Jenkins endpoint used to trigger the build request."
}
