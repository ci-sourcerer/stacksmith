output "build_url" {
  value       = "${trimsuffix(var.jenkins_url, "/")}/job/${join("/job/", [for segment in split("/", trim(var.job_name, "/")) : replace(urlencode(segment), "+", "%20")])}/buildWithParameters"
  description = "Jenkins endpoint used to trigger the parameterized build."
}
