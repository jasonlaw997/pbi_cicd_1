#!/usr/bin/env python3
"""Publish a PBIX file to a Power BI workspace using the REST API."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
import uuid
import http.client
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


POWER_BI_API_ROOT = "https://api.powerbi.com/v1.0/myorg"
POWER_BI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"


class ApiError(RuntimeError):
    def __init__(self, method: str, url: str, status: int, body: str) -> None:
        self.method = method
        self.url = url
        self.status = status
        self.body = body
        super().__init__(f"{method} {url} failed with HTTP {status}: {body}")


def require_value(name: str, value: str | None) -> str:
    if value is None or not value.strip():
        raise SystemExit(f"Missing required value: {name}")
    return value


def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    expected_statuses: tuple[int, ...] = (200,),
) -> dict[str, Any]:
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                body = response.read().decode("utf-8")
                if response.status not in expected_statuses:
                    raise ApiError(method, url, response.status, body)
                break
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ApiError(method, url, exc.code, body) from exc
        except (urllib.error.URLError, http.client.RemoteDisconnected) as exc:
            if attempt == 3:
                raise
            wait_seconds = 2 * attempt
            print(f"{method} {url} connection failed on attempt {attempt}; retrying in {wait_seconds}s...")
            time.sleep(wait_seconds)

    if not body.strip():
        return {}
    return json.loads(body)


def get_access_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    form = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
            "scope": POWER_BI_SCOPE,
        }
    ).encode("utf-8")

    token = request_json(
        "POST",
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data=form,
    )

    access_token = token.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise SystemExit(f"Token endpoint did not return an access_token: {token}")
    return access_token


def build_multipart_file(field_name: str, file_path: Path) -> tuple[bytes, str]:
    boundary = f"----PowerBICICD{uuid.uuid4().hex}"
    content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"

    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field_name}"; filename="{file_path.name}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    footer = f"\r\n--{boundary}--\r\n".encode("utf-8")

    return header + file_path.read_bytes() + footer, boundary


def publish_pbix(
    *,
    access_token: str,
    workspace_id: str,
    pbix_file: Path,
    report_display_name: str,
    name_conflict: str,
) -> dict[str, Any]:
    data, boundary = build_multipart_file("file", pbix_file)
    display_name = urllib.parse.quote(report_display_name, safe="")
    conflict = urllib.parse.quote(name_conflict, safe="")
    url = (
        f"{POWER_BI_API_ROOT}/groups/{workspace_id}/imports"
        f"?datasetDisplayName={display_name}&nameConflict={conflict}"
    )

    return request_json(
        "POST",
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        data=data,
        expected_statuses=(200, 201, 202),
    )


def get_import(access_token: str, workspace_id: str, import_id: str) -> dict[str, Any]:
    return request_json(
        "GET",
        f"{POWER_BI_API_ROOT}/groups/{workspace_id}/imports/{import_id}",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
        },
    )


def list_items(access_token: str, workspace_id: str, item_type: str) -> list[dict[str, Any]]:
    payload = request_json(
        "GET",
        f"{POWER_BI_API_ROOT}/groups/{workspace_id}/{item_type}",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
        },
    )
    value = payload.get("value", [])
    if not isinstance(value, list):
        raise SystemExit(f"Unexpected {item_type} response: {payload}")
    return [item for item in value if isinstance(item, dict)]


def find_item_by_name(
    items: list[dict[str, Any]],
    name: str,
    *,
    item_type: str,
) -> dict[str, Any]:
    matches = [item for item in items if item.get("name") == name]
    if not matches and name.lower().endswith(".pbix"):
        stem = name[:-5]
        matches = [item for item in items if item.get("name") == stem]

    if not matches:
        available = ", ".join(str(item.get("name", "<unnamed>")) for item in items)
        raise SystemExit(f"Could not find {item_type} named '{name}'. Available: {available}")
    if len(matches) > 1:
        ids = ", ".join(str(item.get("id", "<no id>")) for item in matches)
        raise SystemExit(f"Found multiple {item_type} items named '{name}': {ids}")

    return matches[0]


def get_import_report_id(final_import: dict[str, Any]) -> str | None:
    reports = final_import.get("reports")
    if isinstance(reports, list) and reports:
        first_report = reports[0]
        if isinstance(first_report, dict):
            report_id = first_report.get("id")
            if isinstance(report_id, str) and report_id:
                return report_id
    return None


def rebind_report(
    *,
    access_token: str,
    workspace_id: str,
    report_id: str,
    dataset_id: str,
) -> None:
    request_json(
        "POST",
        f"{POWER_BI_API_ROOT}/groups/{workspace_id}/reports/{report_id}/Rebind",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        data=json.dumps({"datasetId": dataset_id}).encode("utf-8"),
        expected_statuses=(200, 202),
    )


def wait_for_import(
    *,
    access_token: str,
    workspace_id: str,
    import_id: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    last_state = ""

    while time.time() < deadline:
        payload = get_import(access_token, workspace_id, import_id)
        state = str(payload.get("importState", ""))
        if state != last_state:
            print(f"Import state: {state}")
            last_state = state

        if state == "Succeeded":
            return payload
        if state == "Failed":
            raise SystemExit(f"PBIX import failed: {json.dumps(payload, ensure_ascii=False)}")

        time.sleep(5)

    raise SystemExit(f"Timed out waiting for import {import_id} after {timeout_seconds}s")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish a PBIX file to Power BI.")
    parser.add_argument("--pbix-file", required=True, help="Path to the PBIX file.")
    parser.add_argument(
        "--report-display-name",
        required=True,
        help="Display name used by the Power BI import API.",
    )
    parser.add_argument(
        "--semantic-model-name",
        help="If set, rebind the imported report to this semantic model/dataset name.",
    )
    parser.add_argument(
        "--name-conflict",
        default=os.getenv("PBI_IMPORT_NAME_CONFLICT", "CreateOrOverwrite"),
        choices=("Abort", "Ignore", "Overwrite", "CreateOrOverwrite", "GenerateUniqueName"),
        help="Power BI import nameConflict behavior.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=600,
        help="How long to wait for the import to finish.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pbix_file = Path(args.pbix_file).resolve()
    if not pbix_file.is_file():
        raise SystemExit(f"PBIX file not found: {pbix_file}")

    tenant_id = require_value("PBI_TENANT_ID", os.getenv("PBI_TENANT_ID"))
    client_id = require_value("PBI_CLIENT_ID", os.getenv("PBI_CLIENT_ID"))
    client_secret = require_value("PBI_CLIENT_SECRET", os.getenv("PBI_CLIENT_SECRET"))
    workspace_id = require_value("PBI_WORKSPACE_ID", os.getenv("PBI_WORKSPACE_ID"))

    print(f"Publishing PBIX: {pbix_file}")
    print(f"Workspace ID: {workspace_id}")
    print(f"Report display name: {args.report_display_name}")
    print(f"Name conflict mode: {args.name_conflict}")

    access_token = get_access_token(tenant_id, client_id, client_secret)
    import_result = publish_pbix(
        access_token=access_token,
        workspace_id=workspace_id,
        pbix_file=pbix_file,
        report_display_name=args.report_display_name,
        name_conflict=args.name_conflict,
    )

    import_id = import_result.get("id")
    if not isinstance(import_id, str) or not import_id:
        print(json.dumps(import_result, indent=2, ensure_ascii=False))
        raise SystemExit("Import response did not include an id.")

    print(f"Import ID: {import_id}")
    final_import = wait_for_import(
        access_token=access_token,
        workspace_id=workspace_id,
        import_id=import_id,
        timeout_seconds=args.timeout_seconds,
    )

    print("PBIX import succeeded.")
    print(json.dumps(final_import, indent=2, ensure_ascii=False))

    if args.semantic_model_name:
        print(f"Looking up semantic model: {args.semantic_model_name}")
        datasets = list_items(access_token, workspace_id, "datasets")
        dataset = find_item_by_name(
            datasets,
            args.semantic_model_name,
            item_type="semantic model/dataset",
        )
        dataset_id = dataset.get("id")
        if not isinstance(dataset_id, str) or not dataset_id:
            raise SystemExit(f"Dataset did not include an id: {dataset}")

        report_id = get_import_report_id(final_import)
        if not report_id:
            reports = list_items(access_token, workspace_id, "reports")
            report = find_item_by_name(
                reports,
                args.report_display_name,
                item_type="report",
            )
            report_id = report.get("id")

        if not isinstance(report_id, str) or not report_id:
            raise SystemExit("Could not determine report id for rebind.")

        print(f"Rebinding report {report_id} to semantic model {dataset_id}...")
        rebind_report(
            access_token=access_token,
            workspace_id=workspace_id,
            report_id=report_id,
            dataset_id=dataset_id,
        )
        print("Report rebind completed.")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ApiError as exc:
        print(f"Power BI API call failed: HTTP {exc.status}", file=sys.stderr)
        try:
            print(json.dumps(json.loads(exc.body), indent=2, ensure_ascii=False), file=sys.stderr)
        except json.JSONDecodeError:
            print(exc.body, file=sys.stderr)
        raise SystemExit(1) from exc
