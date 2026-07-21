FROM ubuntu:22.04

ARG DEBIAN_FRONTEND=noninteractive
<<<<<<< HEAD
ARG KUBECTL_CHANNEL=v1.30
=======
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      e2fsprogs \
<<<<<<< HEAD
      gnupg \
=======
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
      iproute2 \
      kmod \
      mount \
      tini \
      util-linux \
      xfsprogs \
<<<<<<< HEAD
    && curl -fsSL "https://pkgs.k8s.io/core:/stable:/${KUBECTL_CHANNEL}/deb/Release.key" \
      | gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/${KUBECTL_CHANNEL}/deb/ /" \
      > /etc/apt/sources.list.d/kubernetes.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends kubectl \
=======
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
    && rm -rf /var/lib/apt/lists/*

COPY scripts/node-prep-lib.sh /scripts/node-prep-lib.sh
COPY scripts/cube-node-init.sh /scripts/cube-node-init.sh
<<<<<<< HEAD
COPY scripts/wait-pvm-host.sh /scripts/wait-pvm-host.sh

RUN chmod +x /scripts/cube-node-init.sh /scripts/wait-pvm-host.sh \
=======

RUN chmod +x /scripts/cube-node-init.sh \
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
    && chmod 644 /scripts/node-prep-lib.sh

ENTRYPOINT ["/usr/bin/tini", "-s", "--", "/scripts/cube-node-init.sh"]
