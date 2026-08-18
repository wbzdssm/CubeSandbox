# TemplateCenter k8s PVC 持久化方案

**目的**：避免 TC pod 重启后 `/data/CubeMaster/storage/<artifactID>/<artifactID>.ext4` 全部丢失。

参照 `CubeMaster/pkg/templatecenter/image/paths.go:18`：`defaultArtifactStoreDir = "/data/CubeMaster/storage"`。

## 一、为什么必须 PVC

| 场景 | 无 PVC | 有 PVC |
|---|---|---|
| TC pod 滚动升级 | ext4 全丢，build 失败 case A1-A14 全部重来 | 磁盘保留，新 pod 直接命中 existing artifact，无需重建 |
| TC pod 崩溃 / OOM kill | 同上 | 同上 |
| TC pod 跨节点调度 | 同上 | 同上（ReadWriteOnce 即可，不需要跨节点共享） |
| 多 TC 副本并行 | 副本间互相看不到对方产物 → 重复构建 | 共享 ext4，命中即复用（构建锁保一致） |

ext4 文件规格（`image/ext4.go:41-43`）：**最小 1 GiB**，256 MiB 对齐，按 `config.size_mb × 1.5` 估算。100 个模板 ≈ 100-200 GiB。

## 二、与 CubeMaster 同构的 chart 模板

复用现有 `_helpers.tpl` 模式，新增 `templatecenter-pvc.yaml` + `templatecenter.yaml` + values 块。

### 2.1 `deploy/kubernetes/chart/templates/templatecenter-pvc.yaml`

```yaml
{{- /*
TemplateCenter 持久化 PVC.
与 master-pvc.yaml 同构 — 三个分支:
  1. persistence.enabled=false      → 不渲染 (emptyDir 兜底)
  2. persistence.existingClaim 非空 → 不渲染 (复用外部 PVC)
  3. persistence.enabled=true       → 渲染本 PVC
*/ -}}
{{- if and .Values.controlPlane.templatecenter.persistence.enabled (not .Values.controlPlane.templatecenter.persistence.hostPath) (not .Values.controlPlane.templatecenter.persistence.existingClaim) }}
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {{ include "cube.templatecenterStoragePVCName" . }}
  labels: {{- include "cube.labels" . | nindent 4 }}
    app.kubernetes.io/component: templatecenter
spec:
  accessModes: {{- .Values.controlPlane.templatecenter.persistence.accessModes | toYaml | nindent 4 }}
  resources:
    requests:
      storage: {{ .Values.controlPlane.templatecenter.persistence.size | quote }}
  {{- with .Values.controlPlane.templatecenter.persistence.storageClassName }}
  storageClassName: {{ include "cube.persistenceStorageClassName" (dict "root" $ "component" .) }}
  {{- end }}
{{- end }}
```

### 2.2 `deploy/kubernetes/chart/templates/templatecenter.yaml`

```yaml
{{- /*
TemplateCenter Deployment.
关键决策:
  - deploymentStrategy.type: Recreate   (ReadWriteOnce PVC + RollingUpdate maxSurge 会双挂载死锁)
  - readinessProbe 走 /health            (含 nodemeta.Ready + templatecenter.IsReady)
  - livenessProbe 走 /health             (简单起见也用 /health, 5 分钟容忍)
*/ -}}
{{- if .Values.controlPlane.templatecenter.enabled }}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "cube.fullname" . }}-templatecenter
  labels: {{- include "cube.labels" . | nindent 4 }}
    app.kubernetes.io/component: templatecenter
spec:
  replicas: {{ .Values.controlPlane.templatecenter.replicas }}
  strategy:
    type: {{ .Values.controlPlane.templatecenter.deploymentStrategy.type | default "Recreate" }}
    {{- if eq .Values.controlPlane.templatecenter.deploymentStrategy.type "RollingUpdate" }}
    rollingUpdate: {{- .Values.controlPlane.templatecenter.deploymentStrategy.rollingUpdate | toYaml | nindent 6 }}
    {{- end }}
  selector:
    matchLabels:
      app.kubernetes.io/name: {{ include "cube.name" . }}
      app.kubernetes.io/component: templatecenter
      app.kubernetes.io/instance: {{ .Release.Name }}
  template:
    metadata:
      labels:
        app.kubernetes.io/name: {{ include "cube.name" . }}
        app.kubernetes.io/component: templatecenter
        app.kubernetes.io/instance: {{ .Release.Name }}
    spec:
      serviceAccountName: {{ include "cube.serviceAccountName" . }}
      priorityClassName: {{ include "cube.priorityClassName" . }}
      # loop 设备: mkfs ext4 需要 /dev/loop0-31. 见 6.2.
      hostPID: false
      securityContext:
        runAsUser: 0
        runAsGroup: 0
      containers:
        - name: templatecenter
          image: "{{ .Values.global.registry | default .Values.image.registry }}/{{ .Values.image.repositoryPrefix }}cubetemplatecenter:{{ .Values.image.tag }}"
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          command:
            - /usr/local/services/cubetoolbox/CubeTemplateCenter/templatecenter
          args:
            - -conf=/usr/local/services/cubetoolbox/CubeTemplateCenter/conf.yaml
          env:
            - name: POD_NAME
              valueFrom:
                fieldRef: { fieldPath: metadata.name }
            - name: POD_NAMESPACE
              valueFrom:
                fieldRef: { fieldPath: metadata.namespace }
            # 显式覆盖 artifact store 路径 (源码默认 /data/CubeMaster/storage, 见 paths.go:18)
            - name: CUBEMASTER_ROOTFS_ARTIFACT_STORE_DIR
              value: /data/CubeTemplateCenter/storage
            - name: CUBE_AUTO_MIGRATION
              value: {{ .Values.controlPlane.templatecenter.autoMigration | default true | quote }}
            {{- with .Values.controlPlane.templatecenter.env }}
            {{- toYaml . | nindent 12 }}
            {{- end }}
          ports:
            - { name: http, containerPort: 8090, protocol: TCP }
          volumeMounts:
            - name: templatecenter-config
              mountPath: /usr/local/services/cubetoolbox/CubeTemplateCenter/conf.yaml
              subPath: conf.yaml
            - name: templatecenter-log
              mountPath: /data/log/CubeTemplateCenter
            - name: templatecenter-storage
              mountPath: /data/CubeTemplateCenter/storage
          readinessProbe:
            httpGet:
              path: /health
              port: http
            initialDelaySeconds: 5
            periodSeconds: 5
            timeoutSeconds: 3
            failureThreshold: 6
          livenessProbe:
            httpGet:
              path: /health
              port: http
            initialDelaySeconds: 30
            periodSeconds: 10
            timeoutSeconds: 5
            failureThreshold: 3
          {{- with .Values.controlPlane.templatecenter.resources }}
          resources: {{- toYaml . | nindent 12 }}
          {{- end }}
      volumes:
        - name: templatecenter-config
          secret:
            secretName: {{ include "cube.fullname" . }}-templatecenter-config
        - name: templatecenter-log
          emptyDir: {}
        - name: templatecenter-storage
          {{- if and .Values.controlPlane.templatecenter.persistence.enabled .Values.controlPlane.templatecenter.persistence.hostPath }}
          hostPath:
            path: {{ .Values.controlPlane.templatecenter.persistence.hostPath | quote }}
            type: DirectoryOrCreate
          {{- else if .Values.controlPlane.templatecenter.persistence.enabled }}
          persistentVolumeClaim:
            claimName: {{ include "cube.templatecenterStoragePVCName" . }}
          {{- else }}
          emptyDir: {}
          {{- end }}
      {{- with .Values.controlPlane.templatecenter.nodeSelector }}
      nodeSelector: {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.controlPlane.templatecenter.tolerations }}
      tolerations: {{- toYaml . | nindent 8 }}
      {{- end }}
---
apiVersion: v1
kind: Service
metadata:
  name: {{ include "cube.fullname" . }}-templatecenter
  labels: {{- include "cube.labels" . | nindent 4 }}
    app.kubernetes.io/component: templatecenter
spec:
  type: {{ .Values.controlPlane.templatecenter.service.type }}
  selector:
    app.kubernetes.io/name: {{ include "cube.name" . }}
    app.kubernetes.io/component: templatecenter
    app.kubernetes.io/instance: {{ .Release.Name }}
  ports:
    - { name: http, port: {{ .Values.controlPlane.templatecenter.service.port }}, targetPort: http, protocol: TCP }
{{- end }}
```

### 2.3 `_helpers.tpl` 追加

```yaml
{{- define "cube.templatecenterStoragePVCName" -}}
{{- if .Values.controlPlane.templatecenter.persistence.existingClaim -}}
{{- .Values.controlPlane.templatecenter.persistence.existingClaim -}}
{{- else -}}
{{- printf "%s-templatecenter-storage" (include "cube.fullname" .) -}}
{{- end -}}
{{- end }}
```

### 2.4 `values.yaml` 追加

```yaml
controlPlane:
  templatecenter:
    enabled: true
    replicas: 1
    image:
      repository: cubetemplatecenter
      tag: ""        # 默认跟 .Values.image.tag
    service:
      type: ClusterIP
      port: 8090
    # ReadWriteOnce 上 RollingUpdate + maxSurge 会双挂载死锁, 必须 Recreate
    deploymentStrategy:
      type: Recreate
    persistence:
      enabled: true
      hostPath: ""            # dev 用 (e.g. /data/templatecenter)
      existingClaim: ""       # 生产用 (用户自己管理 PVC)
      storageClassName: ""    # 空 = 走 cube.persistenceStorageClassName fallback
      size: 200Gi             # 100 个模板 × 1-2 GiB, 留冗余
      accessModes: [ReadWriteOnce]
    autoMigration: true
    env: []
    nodeSelector: {}
    tolerations: []
    resources:
      requests: { cpu: 500m, memory: 512Mi }
      limits:   { cpu: 2,    memory: 4Gi }
```

`values-tke.yaml` 追加（绑定到同一 storageClass）：

```yaml
controlPlane:
  templatecenter:
    persistence:
      storageClassName: cube-cbs-wffc
```

## 三、ConfigMap / Secret 接线

### 3.1 `templatecenter-config-secret.yaml`

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: {{ include "cube.fullname" . }}-templatecenter-config
  labels: {{- include "cube.labels" . | nindent 4 }}
    app.kubernetes.io/component: templatecenter
type: Opaque
stringData:
  conf.yaml: |
    common:
      http_port: 8090
      http_readtimeout: 120
      http_writetimeout: 360
      http_idletimeout: 360
    log:
      module: templatecenter
      path: /data/log/CubeTemplateCenter
      file_size: 100
      file_num: 10
      level: {{ .Values.controlPlane.templatecenter.logLevel | default "info" | quote }}
    instance_db_config:
      addr: {{ include "cube.mysqlAddr" . | quote }}
      user: {{ .Values.mysql.auth.username | quote }}
      pwd: {{ .Values.mysql.auth.password | quote }}
      db_name: {{ .Values.mysql.auth.database | quote }}
      conn_timeout: 5
      read_timeout: 5
      write_timeout: 5
      max_idle_conns: 5
      max_open_conns: 20
      max_conn_life_time_seconds: 300
    redis:
      nodes: {{ include "cube.redisAddr" . | quote }}
      password: {{ .Values.redis.auth.password | quote }}
      db_no: 0
      max_idle: 8
      max_active: 32
      idle_timeout: 30
      max_retry: 2
    auth:
      enable: false
```

## 四、与 CubeMaster 的存储关系

### 4.1 推荐：**TC 和 CubeMaster 用同一个 PVC**

理由：
- TC 写 ext4 到 `/data/CubeTemplateCenter/storage`
- CubeMaster 通过 `artifact download` 端点（`GET /cube/template/artifact/download`）**进程内**读这个文件推给 Cubelet
- 如果两个 pod 用**同一个 PVC**（`existingClaim: cube-master-storage`），master 直接 `os.Open` 读，零网络
- 如果两个 pod 用**不同 PVC**，master 必须通过 HTTP 从 TC 拉（`GET /cube/template/artifact/download` 转发），多一层拷贝

### 4.2 同 PVC 方案（推荐）

```yaml
controlPlane:
  master:
    persistence: { existingClaim: "" }   # 自动创建 cube-master-storage
  templatecenter:
    persistence:
      enabled: true
      existingClaim: {{ include "cube.fullname" . }}-master-storage   # 复用 master 的 PVC
```

限制：
- PVC 是 `ReadWriteOnce` → master 和 TC **必须调度到同一节点**（用 podAffinity 或固定 nodeSelector）
- 多副本时不能用（RWO 只能挂一个节点）

### 4.3 独立 PVC 方案（生产）

```yaml
controlPlane:
  master:
    persistence: { existingClaim: "" }
  templatecenter:
    persistence:
      enabled: true
      existingClaim: ""    # 独立 PVC cube-templatecenter-storage
```

master 拉 ext4 走 HTTP `GET /cube/template/artifact/download`（走 TC 的 ClusterIP service），不依赖共享存储。

## 五、CFS 多副本共享（可选）

如果需要**多副本 TC 共享存储**（ReadWriteMany）：

```yaml
controlPlane:
  templatecenter:
    replicas: 3
    persistence:
      existingClaim: cube-cfs-templatecenter   # 预先创建好的 CFS PVC
      accessModes: [ReadWriteMany]
```

预先创建：

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata: { name: cube-cfs-templatecenter }
spec:
  accessModes: [ReadWriteMany]
  resources: { requests: { storage: 500Gi } }
  storageClassName: cfs    # TKE 的 CFS CSI driver
```

参照 `docs/guide/tencentcloud-terraform-deploy.md:64` 的 `USE_CFS=true` 模式。

## 六、loop 设备预创建（必做）

`mkfs.ext4` 需要 `/dev/loop0-31`。容器里默认没有 loop 模块，必须：

### 6.1 方案 A：宿主机预创建（推荐）

通过 init daemonset 在节点上加载 loop 模块并预创建节点：

```yaml
# templates/node-loop-preflight-daemonset.yaml
apiVersion: apps/v1
kind: DaemonSet
metadata: { name: cube-loop-preflight }
spec:
  selector:
    matchLabels: { app: cube-loop-preflight }
  template:
    metadata: { labels: { app: cube-loop-preflight } }
    spec:
      hostPID: true
      hostNetwork: true
      containers:
        - name: loop-preflight
          image: busybox:1.36
          command:
            - sh
            - -c
            - |
              modprobe loop max_loop=32 || true
              for i in $(seq 0 31); do
                [ -b /dev/loop$i ] || mknod /dev/loop$i b 7 $i
              done
              # 持续运行保持 DaemonSet
              tail -f /dev/null
          securityContext: { privileged: true }
          volumeMounts:
            - { name: dev, mountPath: /dev }
      volumes:
        - { name: dev, hostPath: { path: /dev } }
```

### 6.2 方案 B：TC pod 挂 /dev（简单但有安全风险）

```yaml
securityContext:
  privileged: true
volumeMounts:
  - { name: dev, mountPath: /dev }
volumes:
  - { name: dev, hostPath: { path: /dev } }
```

不推荐生产用（privileged = 全权）。

### 6.3 方案 C：kubelet 配置 + device plugin

用 device plugin 把 loop 设备作为资源调度，最优但实现复杂。**先按方案 A**。

## 七、验证

```bash
# 1. 渲染 chart 看 PVC 是否生成
helm template cube ./deploy/kubernetes/chart \
  --set controlPlane.templatecenter.enabled=true \
  | grep -A 5 "kind: PersistentVolumeClaim"

# 2. 部署
helm upgrade --install cube ./deploy/kubernetes/chart \
  -f ./deploy/kubernetes/chart/values-tke.yaml

# 3. 验证 PVC 绑定
kubectl get pvc cube-templatecenter-storage
# 应输出 STATUS=Bound

# 4. 验证 pod 挂载
kubectl exec -it cube-templatecenter-0 -- df -h /data/CubeTemplateCenter/storage
# 应输出 PVC 挂载点

# 5. 重启 pod 看 ext4 是否保留
kubectl delete pod cube-templatecenter-0
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/component=templatecenter
kubectl exec -it cube-templatecenter-0 -- ls /data/CubeTemplateCenter/storage/
# 应有 <artifactID>/ 目录, 文件未丢

# 6. /health 验证
kubectl port-forward svc/cube-templatecenter 8090:8090 &
curl -sf http://localhost:8090/health | jq .
# 应输出 {"status":"ok","checks":{"nodemeta":true,"templatecenter_store":true}}
```

## 八、回滚 / 故障处理

| 场景 | 处理 |
|---|---|
| PVC 满 | `kubectl edit pvc cube-templatecenter-storage` 扩容（cbs-wffc 支持在线扩容） |
| TC 起不来 + 日志报 loop 满 | 删孤儿 loop 设备：`losetup -a \| grep deleted \| awk '{print $1}' \| xargs -I{} losetup -d {}`（PR #1295 已自动做 reclaim） |
| 多副本构建冲突 | 确认 DB 会话锁生效：`SELECT * FROM performance_schema.metadata_locks WHERE lock_type='User-level lock'` |
| PVC 被误删 | `kubectl create -f` 重建 + 手动 `mkfs.ext4` 重建丢失模板（**设计兜底**） |
