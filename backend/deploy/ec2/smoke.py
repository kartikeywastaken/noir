"""Live HTTPS deployment check, using only the generated NOIR-owned test APK.

Run the plan, patch and finish stages in order, reviewing saved plan/diff before
the next stage. No mocked services; credentials are never printed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import uuid
from pathlib import Path

import httpx

DIRECTORY = Path.home() / ".noir/deployments/ec2-stockholm"
LABEL = "NOIR Cloud Verified"


def save(name: str, data: object) -> None:
    (DIRECTORY / name).write_text(json.dumps(data, indent=2) + "\n")


def require(condition: object, message: object = "Deployment check failed") -> None:
    if not condition:
        raise RuntimeError(str(message))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("plan", "patch", "finish", "check"))
    args = parser.parse_args()
    connection = json.loads((DIRECTORY / "connection.json").read_text())
    base = connection["backend_url"]
    headers = {"Authorization": f"Bearer {connection['bearer_token']}"}
    with httpx.Client(base_url=base, headers=headers, timeout=600) as client:

        def call(method: str, path: str, **kwargs):
            response = client.request(method, path, **kwargs)
            if response.status_code >= 400:
                raise RuntimeError(
                    f"{method} {path}: HTTP {response.status_code}: {response.text[:3000]}"
                )
            return response.json()

        def wait(job: dict) -> dict:
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                current = call("GET", f"/v1/jobs/{job['job_id']}")
                if current["state"] in {"succeeded", "failed", "interrupted", "cancelled"}:
                    if current["state"] != "succeeded":
                        raise RuntimeError(json.dumps(current))
                    events = client.get(f"/v1/jobs/{job['job_id']}/events")
                    require(events.status_code == 200 and "event: done" in events.text)
                    (DIRECTORY / f"{job['job_id']}-events.txt").write_text(events.text)
                    return current
                time.sleep(0.5)
            raise TimeoutError("Deployment test job timed out")

        if args.stage == "check":
            with httpx.Client(base_url=base, timeout=20) as anonymous:
                require(anonymous.get("/v1/health").status_code == 200)
                require(anonymous.get("/v1/projects").status_code == 401)
                require(
                    anonymous.get(
                        "/v1/projects", headers={"Authorization": "Bearer invalid"}
                    ).status_code
                    == 401
                )
            result = {
                "health": call("GET", "/v1/health"),
                "keys": call("GET", "/v1/keys"),
                "projects": call("GET", "/v1/projects"),
                "unauthorized_requests_rejected": True,
            }
            save("connection-check.json", result)
            print(json.dumps(result, indent=2))
            return

        if args.stage == "plan":
            fixture = DIRECTORY / "noir_test_v2.apk"
            with fixture.open("rb") as stream:
                imported = wait(
                    call(
                        "POST",
                        "/v1/import?authorized=true",
                        files={
                            "file": (
                                fixture.name,
                                stream,
                                "application/vnd.android.package-archive",
                            )
                        },
                        headers={"Idempotency-Key": f"deployment-{uuid.uuid4()}"},
                    )
                )
            project = imported["project_id"]
            save("import.json", imported)
            plan = call(
                "POST",
                f"/v1/projects/{project}/plans",
                json={
                    "allow_ai_upload": True,
                    "user_request": (
                        "This is the NOIR-owned synthetic test APK. "
                        "Change only the app_name string "
                        f"in res/values/strings.xml from NOIR Test to exactly {LABEL}. "
                        "The existing manifest already refers to this resource. Use a minimal "
                        "replace_block operation on that one string. Do not change the manifest, "
                        "package ID, components, permissions, Smali, other strings, or behavior. "
                        "No network functionality. "
                        "This tests the cloud deployment with our own fixture."
                    ),
                },
            )
            save("plan.json", plan)
            require(
                {entry["relative_path"] for entry in plan["file_changes"]}
                == {"res/values/strings.xml"}
            )
            require(not plan["permission_changes"] and not plan["network_destinations"])
            print(json.dumps(plan, indent=2))
            return

        plan = json.loads((DIRECTORY / "plan.json").read_text())
        project = plan["project_id"]
        prefix = f"/v1/projects/{project}"
        if args.stage == "patch":
            call(
                "POST",
                f"{prefix}/plans/{plan['plan_id']}/approve",
                json={"hash": plan["plan_hash"]},
            )
            patch = call("POST", f"{prefix}/patches", params={"plan_id": plan["plan_id"]})
            save("patch.json", patch)
            require(
                {operation["relative_path"] for operation in patch["operations"]}
                == {"res/values/strings.xml"}
            )
            diff = call("GET", f"{prefix}/patches/{patch['patch_id']}/diff")
            save("diff.json", diff)
            print(json.dumps({"patch": patch, "diff": diff}, indent=2))
            return

        patch = json.loads((DIRECTORY / "patch.json").read_text())
        call(
            "POST",
            f"{prefix}/patches/{patch['patch_id']}/approve",
            json={"hash": patch["patch_hash"]},
        )
        applied = call("POST", f"{prefix}/patches/{patch['patch_id']}/apply")
        validation = call("POST", f"{prefix}/validate")
        require(validation["passed"], validation)
        built = wait(
            call(
                "POST",
                f"{prefix}/build",
                headers={"Idempotency-Key": f"deployment-build-{uuid.uuid4()}"},
            )
        )
        builds = call("GET", f"{prefix}/builds")["builds"]
        build = next(item for item in builds if item["success"] and item["workspace_revision"] == 1)
        signed = call(
            "POST",
            f"{prefix}/sign",
            json={"build_id": build["build_id"], "profile": "cloud-test", "confirm": True},
        )
        verified = call("GET", f"{prefix}/builds/{build['build_id']}/verify")
        require(verified["verified"], verified)
        download = client.get(
            f"{prefix}/builds/{build['build_id']}/download",
            params={"artifact": "signed"},
            follow_redirects=True,
        )
        download.raise_for_status()
        output = DIRECTORY / "noir-cloud-verified-signed.apk"
        output.write_bytes(download.content)
        audit = call("GET", f"{prefix}/audit", params={"format": "markdown"})
        (DIRECTORY / "audit-report.md").write_text(audit["markdown"])
        save(
            "live-workflow.json",
            {
                "project_id": project,
                "plan_id": plan["plan_id"],
                "patch_id": patch["patch_id"],
                "build_id": build["build_id"],
                "applied": applied,
                "validation": validation,
                "build_job": built,
                "signing": signed,
                "verification": verified,
                "download_sha256": hashlib.sha256(download.content).hexdigest(),
                "download": str(output),
            },
        )
        print(
            "Real HTTPS AI/import/patch/validate/build/sign/verify/download workflow passed. "
            f"Project: {project}. APK: {output}"
        )


if __name__ == "__main__":
    main()
