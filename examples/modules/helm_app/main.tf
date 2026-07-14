locals {
  derived_release_name = replace(var.chart, "/", "-")
  release_name         = coalesce(var.name, "${var.namespace}-${local.derived_release_name}")
  values               = [for values_file in var.values_files : file(abspath(values_file))]
}

resource "helm_release" "app" {
  name             = local.release_name
  chart            = var.chart
  repository       = var.repository
  version          = var.chart_version
  namespace        = var.namespace
  create_namespace = var.create_namespace
  wait             = var.wait
  atomic           = var.atomic
  values           = local.values
}
