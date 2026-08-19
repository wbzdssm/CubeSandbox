########################
# CubeTemplateCenter — standalone template build service
#
# Deployed only when create_tke=true AND deploy_templatecenter=true.
#
# WHY IT IS OPT-IN
# ----------------
# With it off, CubeMaster builds template ext4 images in-process, which is the
# pre-split behaviour and the default. Turning it on requires BOTH sides to
# agree: this file deploys TC, and CubeMaster must additionally be told to use
# it (template_build_mode=remote + template_center_endpoint). Flipping only one
# leaves the other doing exactly what it did before, silently.
#
# WHY IT IS A SINGLE REPLICA CO-LOCATED WITH CUBEMASTER
# ----------------------------------------------------
# TC writes the ext4; CubeMaster serves it to Cubelet over
# /cube/template/artifact/download (design 9.7). So both processes must see the
# same artifact directory, and there is no cross-node sharing layer by design
# (no object storage, no RWX). Two consequences:
#   * TC cannot scale horizontally — a second replica would neither read the
#     first one's artifacts nor be able to take over its builds.
#   * TC has to run on the same NODE as CubeMaster. In the default no-CFS mode
#     the store is a pod-local emptyDir, which cannot be shared at all, so TC
#     mounts the SAME CFS share as CubeMaster and use_cfs is required.
########################

locals {
  cube_templatecenter_image = var.cubetemplatecenter_image != "" ? var.cubetemplatecenter_image : "${local.image_registry}/${local.image_namespace}/cube-templatecenter:${var.image_tag}"

  # What the operator asked for, vs. what is actually deployable.
  #
  # These are kept separate so an unsatisfiable request fails with the preflight's
  # explanation below instead of an index-out-of-range on
  # tencentcloud_cfs_file_system.cubemaster_data[0], which exists only when
  # use_cfs=true and would otherwise be the first error Terraform reports.
  templatecenter_requested = local.deploy_addons && var.deploy_templatecenter
  deploy_templatecenter    = local.templatecenter_requested && var.use_cfs

  # Where TC reports build results. In-cluster Service DNS, same reasoning as
  # local.cubemaster_url: the internal CLB VIP is unnecessary for traffic that
  # never leaves the cluster network.
  templatecenter_master_endpoint = local.cubemaster_url
}

# ---------------------------------------------------------------
# Preflight: refuse a configuration that cannot possibly work
#
# Without CFS the artifact store is a pod-local emptyDir. TC would build into
# its own copy, CubeMaster would serve from a different one, and every download
# would 404 — with nothing in either log pointing at the cause. Failing at plan
# time is far cheaper than debugging that.
# ---------------------------------------------------------------
resource "null_resource" "templatecenter_storage_preflight" {
  count = local.templatecenter_requested && !var.use_cfs ? 1 : 0

  lifecycle {
    precondition {
      # Always false by construction: this resource only exists when use_cfs is
      # false. The point is to surface the message, not to test anything.
      condition     = var.use_cfs
      error_message = "deploy_templatecenter=true requires use_cfs=true: CubeTemplateCenter writes the template ext4 and CubeMaster serves it, so both need the same artifact directory. Without CFS each Pod gets its own emptyDir and every artifact download would fail with 404."
    }
  }
}

# ---------------------------------------------------------------
# Configuration Secret
#
# Carries MySQL and Redis credentials, hence a Secret rather than a ConfigMap.
# Deliberately narrower than cube-master's conf: TC has no scheduler, serves no
# sandbox API, and talks to no Cubelet in the default mode, so emitting those
# sections would be dead config implying capabilities this process does not use.
# ---------------------------------------------------------------
resource "kubernetes_secret" "cubetemplatecenter_conf" {
  count = local.deploy_templatecenter ? 1 : 0
  type  = "Opaque"
  metadata {
    name      = "cubetemplatecenter-conf"
    namespace = kubernetes_namespace.cubesandbox[0].metadata[0].name
  }

  data = {
    "conf.yaml" = yamlencode({
      common = {
        http_port        = 8090
        http_readtimeout = 120
        # Generous: a build streams progress for as long as the pull and mkfs take.
        http_writetimeout         = 360
        http_idletimeout          = 360
        sync_meta_data_interval   = "30s"
        sync_metric_data_interval = "1s"
        collect_metric_interval   = "1s"
      }
      log = {
        module    = "templatecenter"
        path      = "/data/log/CubeTemplateCenter"
        file_size = 100
        file_num  = 10
        level     = "info"
      }
      auth = {
        enable = false
      }
      # Same database as cube-master, used only for schema migration and the
      # reconciler's session lock. Business state is written by cube-master from
      # TC's status callback (design 2.2), so the two do not contend on rows.
      instance_db_config = {
        addr                       = "${tencentcloud_mysql_instance.mysql.intranet_ip}:3306"
        user                       = local.cube_user
        pwd                        = local.cube_password
        db_name                    = local.cube_db
        conn_timeout               = 5
        read_timeout               = 5
        write_timeout              = 5
        max_idle_conns             = 5
        max_open_conns             = 20
        max_conn_life_time_seconds = 300
      }
      # Redis carries the live pull-progress snapshots, written with the same
      # keys cube-master reads when answering a progress query.
      redis = {
        nodes        = "${tencentcloud_redis_instance.redis.ip}:6379"
        password     = var.redis_password
        db_no        = 0
        max_idle     = 8
        max_active   = 32
        idle_timeout = 30
        max_retry    = 2
      }
    })
  }

  depends_on = [tencentcloud_mysql_instance.mysql, tencentcloud_redis_instance.redis]
}

# ---------------------------------------------------------------
# Deployment
# ---------------------------------------------------------------
resource "kubernetes_deployment" "cubetemplatecenter" {
  count      = local.deploy_templatecenter ? 1 : 0
  depends_on = [null_resource.mysql_init_db, kubernetes_deployment.cubemaster]

  metadata {
    name      = "cubetemplatecenter"
    namespace = kubernetes_namespace.cubesandbox[0].metadata[0].name
    labels    = { app = "cubetemplatecenter" }
  }
  spec {
    # Hardcoded, not a variable: see the header comment. A second replica cannot
    # read the first one's artifacts.
    replicas = 1

    # Recreate, never RollingUpdate: a surge Pod would contend with the outgoing
    # one for the artifact store.
    strategy {
      type = "Recreate"
    }

    selector {
      match_labels = { app = "cubetemplatecenter" }
    }
    template {
      metadata {
        labels = { app = "cubetemplatecenter" }
      }
      spec {
        # Same node as cube-master, so both see the same artifact directory.
        # required rather than preferred: a TC scheduled elsewhere would build
        # into a different disk and every download would 404.
        affinity {
          pod_affinity {
            required_during_scheduling_ignored_during_execution {
              topology_key = "kubernetes.io/hostname"
              label_selector {
                match_labels = { app = "cubemaster" }
              }
            }
          }
        }
        container {
          name  = "cubetemplatecenter"
          image = local.cube_templatecenter_image

          # mkfs.ext4, losetup, and `umoci unpack` (which must preserve the image
          # layers' original uid/gid) all need privileges.
          security_context {
            privileged = true
          }

          # All CUBE_TEMPLATE_CENTER_*: the pre-rename CUBE_TC_* / CUBE_MASTER_* /
          # CUBEMASTER_* spellings still work as a fallback and log a deprecation
          # notice. See CubeTemplateCenter/pkg/tcconfig.
          #
          # Config comes from this variable, NOT a -conf flag: the process reuses
          # CubeMaster's config loader.
          env {
            name  = "CUBE_TEMPLATE_CENTER_CONFIG_PATH"
            value = "/usr/local/services/cubetoolbox/CubeTemplateCenter/conf.yaml"
          }
          # Must resolve to the same directory cube-master serves downloads from.
          env {
            name  = "CUBE_TEMPLATE_CENTER_ARTIFACT_STORE_DIR"
            value = "/data/CubeMaster/storage"
          }
          env {
            name  = "CUBE_TEMPLATE_CENTER_MASTER_ENDPOINT"
            value = local.templatecenter_master_endpoint
          }
          # Off: cube-master owns the public template API and drives distribution
          # after TC reports BUILT. Enabling it would also make TC load the node
          # view, which it otherwise does not need.
          env {
            name  = "CUBE_TEMPLATE_CENTER_SERVE_TEMPLATE_API"
            value = tostring(var.templatecenter_serve_public_api)
          }
          # Node address for the log identity only. It does NOT become the listen
          # address in a Pod: the node IP is outside the Pod netns, so binding to
          # it would fail and the readiness probe targets the Pod IP. tcconfig
          # detects that and keeps 0.0.0.0.
          env {
            name = "CUBE_TEMPLATE_CENTER_NODE_IP"
            value_from {
              field_ref {
                field_path = "status.hostIP"
              }
            }
          }

          port {
            name           = "http"
            container_port = 8090
          }
          readiness_probe {
            http_get {
              path = "/health"
              port = 8090
            }
            initial_delay_seconds = 10
            period_seconds        = 10
          }
          # Slower than cube-master's on purpose: restarting TC mid-build
          # abandons the ext4 and leaves the job for the reconciler, so the probe
          # must tolerate a busy process.
          liveness_probe {
            http_get {
              path = "/health"
              port = 8090
            }
            initial_delay_seconds = 60
            period_seconds        = 30
            failure_threshold     = 8
          }

          volume_mount {
            name       = "conf"
            mount_path = "/usr/local/services/cubetoolbox/CubeTemplateCenter/conf.yaml"
            sub_path   = "conf.yaml"
            read_only  = true
          }
          # The SAME CFS share cube-master mounts.
          volume_mount {
            name       = "data"
            mount_path = "/data/CubeMaster/storage"
          }
          # Read by the --with-cube-ca bake, which TC performs before mkfs.
          volume_mount {
            name       = "cube-egress-ca"
            mount_path = "/etc/cube/ca"
            read_only  = true
          }
        }
        volume {
          name = "conf"
          secret {
            secret_name = kubernetes_secret.cubetemplatecenter_conf[0].metadata[0].name
          }
        }
        # No emptyDir fallback here, unlike cube-master: the preflight above
        # rejects use_cfs=false, because a pod-local store cannot be served by
        # cube-master.
        volume {
          name = "data"
          nfs {
            server = tencentcloud_cfs_file_system.cubemaster_data[0].mount_ip
            path   = "/"
          }
        }
        volume {
          name = "cube-egress-ca"
          secret {
            secret_name = kubernetes_secret.cube_egress_ca[0].metadata[0].name
            items {
              key  = "cube-root-ca.crt"
              path = "cube-root-ca.crt"
            }
            items {
              key  = "cube-root-ca.key"
              path = "cube-root-ca.key"
            }
          }
        }
        dns_config {
          nameservers = ["183.60.83.19", "183.60.82.98"]
        }
      }
    }
  }
}

# ---------------------------------------------------------------
# Private-network CLB Service
#
# Always VPC-internal, like cube-master's and for the same reason: the build
# endpoint is unauthenticated. It therefore never needs replace_triggered_by on
# enable_public_network — its CLB type does not change.
#
# cube-master reaches TC over cluster DNS, so this CLB exists for access from
# outside the cluster (debugging, or a cube-master deployed outside it).
# ---------------------------------------------------------------
resource "kubernetes_service" "cubetemplatecenter" {
  count = local.deploy_templatecenter ? 1 : 0
  metadata {
    name      = "cubetemplatecenter"
    namespace = kubernetes_namespace.cubesandbox[0].metadata[0].name
    annotations = {
      "service.kubernetes.io/qcloud-loadbalancer-internal-subnetid" = tencentcloud_subnet.cluster.id
      "service.cloud.tencent.com/modification-protection"           = "false"
      "service.cloud.tencent.com/pass-to-target"                    = "true"
      "service.cloud.tencent.com/security-groups"                   = tencentcloud_security_group.clb.id
    }
  }
  lifecycle {
    # TKE controller-manager injects runtime annotations; ignore to avoid drift.
    ignore_changes = [
      metadata[0].annotations,
    ]
  }

  spec {
    type     = "LoadBalancer"
    selector = { app = "cubetemplatecenter" }
    port {
      name     = "http"
      port     = 8090
      protocol = "TCP"
    }
  }
}
