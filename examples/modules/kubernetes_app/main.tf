locals {
  manifests = [for path in var.manifest_files : yamldecode(file(abspath(path)))]
  manifests_with_namespace = [
    for manifest in local.manifests :
    merge(
      manifest,
      {
        metadata = merge(
          manifest.metadata == null ? {} : manifest.metadata,
          { namespace = var.namespace }
        )
      }
    )
  ]
}

resource "kubernetes_manifest" "app" {
  for_each = { for idx, manifest in local.manifests_with_namespace : tostring(idx) => manifest }

  manifest = each.value

  dynamic "field_manager" {
    for_each = var.field_manager != "" ? [1] : []
    content {
      name = var.field_manager
    }
  }

  dynamic "wait" {
    for_each = var.wait ? [1] : []
    content {}
  }
}
