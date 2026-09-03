#!/usr/bin/env python3
"""Upload a dotenv file to Google Cloud Secret Manager.

Two storage modes are supported:

1. Bundle mode stores the complete dotenv file as one secret. Mount that secret
   at ``/app/.env`` in this project's Cloud Run service, jobs, and worker pools.
2. Individual mode stores each dotenv variable as a separate secret and prints
   the Cloud Run ``--set-secrets`` mapping.

Secret values are sent to ``gcloud`` through stdin. They are never included in
the process arguments or printed by this script.
"""

from __future__ import annotations

import argparse
import base64
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from dotenv import dotenv_values

ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SECRET_NAME = re.compile(r"^[A-Za-z0-9_-]{1,255}$")
MAX_SECRET_BYTES = 64 * 1024


class UploadError(RuntimeError):
    """Raised when an upload cannot be completed safely."""


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upload a dotenv file to Google Cloud Secret Manager.",
    )
    parser.add_argument(
        "env_file",
        nargs="?",
        type=Path,
        default=Path(".env"),
        help="dotenv file to upload (default: .env)",
    )
    parser.add_argument(
        "--project",
        help="Google Cloud project ID (default: active gcloud project)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--bundle-secret",
        metavar="SECRET_ID",
        help=(
            "store the complete dotenv file in one secret; for this project, "
            "mount it at /app/.env"
        ),
    )
    mode.add_argument(
        "--individual",
        action="store_true",
        help="store each variable as a separate secret (the default mode)",
    )
    parser.add_argument(
        "--prefix",
        default="",
        help="prefix for individual secret IDs, for example 'ent-prod-'",
    )
    parser.add_argument(
        "--include-empty",
        action="store_true",
        help="upload variables whose resolved value is empty (individual mode)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="perform uploads; without this flag the script only shows a plan",
    )
    return parser.parse_args()


def run_gcloud(
    arguments: Sequence[str],
    *,
    payload: bytes | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    command = ["gcloud", *arguments]
    result = subprocess.run(
        command,
        input=payload,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise UploadError(f"gcloud command failed: {error or 'unknown error'}")
    return result


def resolve_project(requested_project: str | None) -> str:
    if requested_project:
        return requested_project

    result = run_gcloud(
        ["config", "get-value", "project"],
        check=True,
    )
    project = result.stdout.decode("utf-8").strip()
    if not project or project == "(unset)":
        raise UploadError(
            "no Google Cloud project selected; pass --project or run gcloud init"
        )
    return project


def secret_exists(secret_id: str, project: str) -> bool:
    result = run_gcloud(
        ["secrets", "describe", secret_id, f"--project={project}"],
        check=False,
    )
    if result.returncode == 0:
        return True

    error = result.stderr.decode("utf-8", errors="replace")
    if "NOT_FOUND" in error or "not found" in error.lower():
        return False
    raise UploadError(f"cannot inspect secret {secret_id!r}: {error.strip()}")


def access_latest_secret(secret_id: str, project: str) -> bytes:
    result = run_gcloud(
        [
            "secrets",
            "versions",
            "access",
            "latest",
            f"--secret={secret_id}",
            f"--project={project}",
            "--format=get(payload.data)",
        ]
    )
    encoded = result.stdout.decode("ascii").strip()
    encoded += "=" * (-len(encoded) % 4)
    try:
        return base64.urlsafe_b64decode(encoded)
    except ValueError as error:
        raise UploadError(
            f"could not decode the current value of {secret_id!r}"
        ) from error


def latest_version(secret_id: str, project: str) -> str:
    result = run_gcloud(
        [
            "secrets",
            "versions",
            "list",
            secret_id,
            f"--project={project}",
            "--filter=state=ENABLED",
            "--sort-by=~createTime",
            "--limit=1",
            "--format=value(name)",
        ]
    )
    name = result.stdout.decode("utf-8").strip()
    if not name:
        raise UploadError(f"secret {secret_id!r} has no enabled version")
    return name.rsplit("/", maxsplit=1)[-1]


def upload_secret(secret_id: str, value: bytes, project: str) -> tuple[str, str]:
    if len(value) > MAX_SECRET_BYTES:
        raise UploadError(
            f"secret {secret_id!r} is {len(value)} bytes; Secret Manager allows "
            f"at most {MAX_SECRET_BYTES} bytes"
        )

    if secret_exists(secret_id, project):
        if access_latest_secret(secret_id, project) == value:
            return "unchanged", latest_version(secret_id, project)
        run_gcloud(
            [
                "secrets",
                "versions",
                "add",
                secret_id,
                f"--project={project}",
                "--data-file=-",
            ],
            payload=value,
        )
        return "updated", latest_version(secret_id, project)

    run_gcloud(
        [
            "secrets",
            "create",
            secret_id,
            f"--project={project}",
            "--replication-policy=automatic",
            "--data-file=-",
        ],
        payload=value,
    )
    return "created", latest_version(secret_id, project)


def validate_secret_id(secret_id: str) -> None:
    if not SECRET_NAME.fullmatch(secret_id):
        raise UploadError(
            f"invalid Secret Manager ID {secret_id!r}; use only letters, numbers, "
            "underscores, and hyphens (maximum 255 characters)"
        )


def load_individual_values(
    env_file: Path,
    *,
    include_empty: bool,
) -> tuple[dict[str, bytes], list[str]]:
    parsed: Mapping[str, str | None] = dotenv_values(env_file, interpolate=True)
    values: dict[str, bytes] = {}
    skipped_empty: list[str] = []

    for name, value in parsed.items():
        if not ENVIRONMENT_NAME.fullmatch(name):
            raise UploadError(f"invalid environment-variable name {name!r}")
        resolved = value or ""
        if not resolved and not include_empty:
            skipped_empty.append(name)
            continue
        values[name] = resolved.encode("utf-8")

    return values, skipped_empty


def upload_bundle(arguments: argparse.Namespace, project: str | None) -> int:
    secret_id = arguments.bundle_secret
    validate_secret_id(secret_id)
    payload = arguments.env_file.read_bytes()
    if len(payload) > MAX_SECRET_BYTES:
        raise UploadError(
            f"{arguments.env_file} is {len(payload)} bytes; Secret Manager allows "
            f"at most {MAX_SECRET_BYTES} bytes per secret"
        )

    print("Mode: bundled dotenv file")
    print(f"File: {arguments.env_file}")
    print(f"Secret: {secret_id}")

    if not arguments.apply:
        print("Dry run only; add --apply to upload it.")
        print(f"Cloud Run mount: --update-secrets=/app/.env={secret_id}:VERSION")
        return 0

    assert project is not None
    status, version = upload_secret(secret_id, payload, project)
    print(f"Result: {status} ({secret_id} version {version})")
    print("\nAttach it to each Cloud Run resource using:")
    print(f"--update-secrets=/app/.env={secret_id}:{version}")
    return 0


def upload_individual(arguments: argparse.Namespace, project: str | None) -> int:
    values, skipped_empty = load_individual_values(
        arguments.env_file,
        include_empty=arguments.include_empty,
    )
    if not values:
        raise UploadError("the dotenv file contains no variables to upload")

    mappings: list[tuple[str, str]] = []
    for environment_name in values:
        secret_id = f"{arguments.prefix}{environment_name}"
        validate_secret_id(secret_id)
        mappings.append((environment_name, secret_id))

    print("Mode: one secret per variable")
    print(f"File: {arguments.env_file}")
    print(f"Variables to upload: {len(mappings)}")
    if skipped_empty:
        print(f"Skipped empty variables: {', '.join(skipped_empty)}")

    if not arguments.apply:
        print("Dry run only; add --apply to upload them.")
        print("Secret IDs:")
        for environment_name, secret_id in mappings:
            print(f"  {environment_name} -> {secret_id}")
        return 0

    assert project is not None
    versions: dict[str, str] = {}
    counts = {"created": 0, "updated": 0, "unchanged": 0}
    for index, (environment_name, secret_id) in enumerate(mappings, start=1):
        status, version = upload_secret(secret_id, values[environment_name], project)
        counts[status] += 1
        versions[environment_name] = version
        print(f"[{index}/{len(mappings)}] {environment_name}: {status}")

    cloud_run_mapping = ",".join(
        f"{environment_name}={secret_id}:{versions[environment_name]}"
        for environment_name, secret_id in mappings
    )
    print(
        "\nSummary: "
        f"{counts['created']} created, {counts['updated']} updated, "
        f"{counts['unchanged']} unchanged"
    )
    print("\nCloud Run mapping (values are pinned to uploaded versions):")
    print(f"--set-secrets='{cloud_run_mapping}'")
    return 0


def main() -> int:
    arguments = parse_arguments()
    if not arguments.env_file.is_file():
        raise UploadError(f"dotenv file not found: {arguments.env_file}")

    if arguments.apply and shutil.which("gcloud") is None:
        raise UploadError("gcloud is not installed or is not on PATH")

    project = resolve_project(arguments.project) if arguments.apply else arguments.project
    if arguments.apply:
        print(f"Google Cloud project: {project}")

    if arguments.bundle_secret:
        return upload_bundle(arguments, project)
    return upload_individual(arguments, project)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, UploadError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
