variable "triggers" {
  description = "Values that cause the null resource to be replaced when they change."
  type        = map(string)
}
