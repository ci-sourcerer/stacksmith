output "release_name" {
  description = "Deployed Helm release name."
  value       = helm_release.app.name
}
