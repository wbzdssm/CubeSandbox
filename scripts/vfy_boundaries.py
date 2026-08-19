# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""Boundary tables for the template API.

WHY THESE ROWS AND NOT OTHERS
-----------------------------
Every row here is derived from a rule that actually exists in the server, read
off the implementation rather than guessed:

  alias                CubeMaster/pkg/templatecenter/request_validation.go
                       aliasValidationRe = ^[a-z0-9][a-z0-9-]{0,63}$
                       plus: must not start with tpl-/snap-, empty is allowed
  source_image_ref     required, then image.ValidateImageRef: rejects "",
                       a leading "-", a doubled docker:// prefix, and anything
                       name.ParseReference cannot parse
  writable_layer_size  required (non-empty after trimming)
  exposed_ports        1..65535, duplicates are deduplicated rather than
                       rejected, and at most 3 CUSTOM ports (49983 is reserved
                       and does not count toward the limit)
  requestID            required on /cube/*; CubeAPI has no equivalent

A boundary is only interesting in pairs: the largest accepted value AND the
smallest rejected one. A table with only invalid rows cannot distinguish "the
rule is enforced" from "everything is rejected", so both sides are always
present. Where the two links legitimately disagree - the SDK validates some
inputs locally and never sends a request - that shows up as a different outcome
and is left visible instead of being smoothed over.

Nothing here asserts. Each row is submitted and the RAW answer is recorded; the
verdict comes from comparing two links.
"""

from __future__ import annotations

from typing import Any

from vfy_links import OMIT

# 64 chars total is the documented maximum (1 leading + 63 trailing), so 65 is
# the first rejected length. Both are generated rather than typed out to keep
# the intent obvious.
ALIAS_MAX = "a" * 64
ALIAS_TOO_LONG = "a" * 65

# Identifiers that must never reach a filesystem path or a SQL string. The
# artifact store is addressed by id, so a traversal that escaped would read
# outside the store; ids are also interpolated into queries in places.
HOSTILE_IDENTIFIERS: list[tuple[str, str]] = [
    ("empty", ""),
    ("whitespace", "   "),
    ("absent-but-well-formed", "tpl-0123456789abcdef0123456789abcdef"),
    ("absent-with-snap-prefix", "snap-0123456789abcdef0123456789abcdef"),
    ("no-prefix", "just-a-name-with-no-prefix"),
    ("very-long", "tpl-" + "f" * 300),
    ("path-traversal", "../../../../etc/passwd"),
    ("path-traversal-encoded", "..%2f..%2fetc%2fpasswd"),
    ("absolute-path", "/etc/passwd"),
    ("sql-tautology", "' OR '1'='1"),
    ("sql-comment", "tpl-x'--"),
    ("null-byte", "tpl-x\x00y"),
    ("newline", "tpl-x\ny"),
    ("unicode", "模板-中文"),
    ("shell-metacharacters", "tpl-$(id)`whoami`"),
]


def create_boundary_rows(image: str, instance_type: str) -> list[dict[str, Any]]:
    """Boundary rows for create-from-image.

    `image` and `instance_type` are the environment's known-good values, so a
    row varies exactly one dimension and any difference is attributable.

    COST MODEL
    ----------
    A row that is ACCEPTED starts a real build: pull the image, bake the rootfs,
    mkfs an ext4. Whether that is one build or many depends on the template spec
    fingerprint (see buildTemplateSpecFingerprint), which covers the source
    image digest, writable_layer_size, exposed_ports, instance_type,
    network_type, container overrides and the CA — but NOT the alias.

    So the accepted alias rows all collapse onto a single artifact and are
    nearly free, while every distinct exposed_ports or writable_layer_size value
    is a separate multi-minute build and gigabytes of disk. Those rows are
    tagged costly=True and are skipped unless explicitly requested; a run that
    submitted all of them would take hours and could try to allocate 1000Ti.
    """
    def row(case: str, why: str, costly: bool = False, **over: Any) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "image": image,
            "instance_type": instance_type,
            "writable_layer_size": "1G",
            "request_id": f"vfy-bnd-{case}",
        }
        fields.update(over)
        return {"case": case, "why": why, "costly": costly, "fields": fields}

    rows: list[dict[str, Any]] = []

    # ---- alias / name -------------------------------------------------
    # The accepted side first: without it, a link that rejects everything would
    # look identical to one that enforces the rule correctly.
    rows += [
        row("alias-omitted", "no alias requested at all: explicitly allowed",
            name=OMIT),
        row("alias-empty", "empty alias is treated as 'no alias', not as invalid",
            name=""),
        row("alias-single-char", "shortest accepted alias", name="a"),
        row("alias-single-digit", "the charset starts at [a-z0-9], so a digit is a valid start",
            name="0"),
        row("alias-max-length", "64 chars: the largest accepted length", name=ALIAS_MAX),
        row("alias-trailing-hyphen",
            "the regex permits a trailing hyphen even though it looks unintended",
            name="vfy-trailing-"),
        row("alias-inner-hyphens", "hyphens are allowed after the first char",
            name="vfy-a-b-c"),
    ]
    # The rejected side.
    rows += [
        row("alias-too-long", "65 chars: the first rejected length", name=ALIAS_TOO_LONG),
        row("alias-uppercase", "the charset is lowercase only", name="VfyUpper"),
        row("alias-underscore", "underscore is outside the charset", name="vfy_under"),
        row("alias-leading-hyphen", "must start with an alphanumeric", name="-vfy"),
        row("alias-leading-dot", "dot is outside the charset", name=".vfy"),
        row("alias-space-inside", "space is outside the charset", name="vfy name"),
        row("alias-unicode", "non-ASCII is outside the charset", name="模板"),
        # These two are rejected by a separate rule: the id prefixes are
        # reserved because storage naming depends on them.
        row("alias-reserved-tpl-prefix", "tpl- is reserved for generated ids",
            name="tpl-stolen"),
        row("alias-reserved-snap-prefix", "snap- is reserved for snapshot ids",
            name="snap-stolen"),
        # Boundary of the prefix rule itself: "tpl" without the hyphen is fine.
        row("alias-tpl-without-hyphen", "only the 'tpl-' form is reserved, 'tpl' is not",
            name="tplfine"),
    ]

    # ---- source image reference ---------------------------------------
    rows += [
        row("image-omitted", "required field missing entirely", image=OMIT),
        row("image-empty", "present but empty", image=""),
        row("image-whitespace", "trimmed to empty", image="   "),
        row("image-leading-hyphen",
            "a leading hyphen could be read as a CLI flag by a downstream tool",
            image="-oops/nginx:latest"),
        row("image-doubled-transport", "docker:// must not appear twice",
            image="docker://docker://nginx:latest"),
        row("image-with-spaces", "not a parseable reference", image="nginx latest"),
        row("image-shell-metacharacters",
            "must never reach a shell: the pull runs an external tool",
            image="nginx:latest;id"),
        row("image-command-substitution", "same, in $() form",
            image="nginx:$(id)"),
        row("image-null-byte", "a null byte truncates C strings", image="nginx:latest\x00"),
        row("image-tag-and-digest", "ambiguous reference carrying both",
            image="nginx:latest@sha256:" + "0" * 64),
        row("image-bad-digest-length", "digest of the wrong length",
            image="nginx@sha256:abc"),
    ]

    # ---- writable layer size ------------------------------------------
    rows += [
        row("size-omitted", "required field missing entirely", writable_layer_size=OMIT),
        row("size-empty", "present but empty", writable_layer_size=""),
        row("size-whitespace", "trimmed to empty", writable_layer_size="   "),
        # Only non-emptiness is checked at validation time; the quantity is
        # parsed later and that error is ignored. Whether these are accepted is
        # a genuine open question, which is exactly why they are recorded — and
        # why they are costly: acceptance means a distinct fingerprint and a
        # full build, and "1000Ti" would try to size a real volume.
        row("size-unparseable", "non-empty but not a quantity", costly=True,
            writable_layer_size="not-a-size"),
        row("size-negative", "negative quantity", costly=True,
            writable_layer_size="-1G"),
        row("size-zero", "zero quantity", costly=True, writable_layer_size="0"),
        row("size-absurd", "far beyond any real disk", costly=True,
            writable_layer_size="1000Ti"),
        row("size-lowercase-unit", "unit case differs from the k8s convention",
            costly=True, writable_layer_size="1g"),
    ]

    # ---- exposed ports -------------------------------------------------
    # Every distinct accepted port set is its own fingerprint, hence its own
    # multi-minute build; the rejected ones cost nothing.
    rows += [
        row("ports-omitted", "optional: absent is valid", costly=True,
            exposed_ports=OMIT),
        row("ports-empty-list", "empty list is valid", costly=True, exposed_ports=[]),
        row("ports-min", "1 is the lowest valid port", costly=True, exposed_ports=[1]),
        row("ports-max", "65535 is the highest valid port", costly=True,
            exposed_ports=[65535]),
        row("ports-zero", "0 is the first rejected value below the range",
            exposed_ports=[0]),
        row("ports-negative", "negative port", exposed_ports=[-1]),
        row("ports-above-max", "65536 is the first rejected value above the range",
            exposed_ports=[65536]),
        row("ports-duplicates", "duplicates are deduplicated, not rejected",
            costly=True, exposed_ports=[80, 80, 80]),
        row("ports-unsorted", "order must not matter: the server sorts",
            costly=True, exposed_ports=[443, 80, 8080]),
        row("ports-three-custom", "3 custom ports: the largest accepted count",
            costly=True, exposed_ports=[80, 443, 8080]),
        row("ports-four-custom", "4 custom ports: the first rejected count",
            exposed_ports=[80, 443, 8080, 9090]),
        row("ports-three-custom-plus-reserved",
            "49983 is reserved and must not count toward the 3-port limit",
            costly=True, exposed_ports=[80, 443, 8080, 49983]),
        row("ports-only-reserved", "the reserved port alone", costly=True,
            exposed_ports=[49983]),
        row("ports-wrong-type", "a string where an int is expected",
            exposed_ports=["80"]),
        row("ports-nested-type", "a structure where a list of ints is expected",
            exposed_ports=[[80]]),
    ]

    # ---- request id / instance type ------------------------------------
    rows += [
        row("request-id-omitted", "required on /cube/*; the SDK has no equivalent",
            request_id=OMIT),
        row("request-id-empty", "present but empty", request_id=""),
        row("instance-type-omitted", "optional: the server defaults it", costly=True,
            instance_type=OMIT),
        row("instance-type-empty", "empty is defaulted, not rejected", costly=True,
            instance_type=""),
        # Node resolution happens inside distributeRootfsArtifact, i.e. AFTER
        # the ext4 has been built, so an instance type nobody provides still
        # costs a full build before it fails.
        row("instance-type-unknown", "an instance type no node provides", costly=True,
            instance_type="cubebox-does-not-exist"),
        row("instance-type-wrong-case",
            "instance types are matched exactly, so case matters", costly=True,
            instance_type="CUBEBOX"),
    ]

    return rows
