"""Real deployed two-user isolation check. Secrets never printed or put in argv."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx

DIRECTORY = Path.home() / ".noir/deployments/ec2-stockholm"


def require(condition: object, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def admin(*arguments: str) -> dict:
    remote = shlex.join(
        [
            "sudo",
            "-u",
            "noir",
            "env",
            "NOIR_DATA_DIR=/var/lib/noir/data",
            "/opt/noir/venv/bin/noir",
            "--json",
            "users",
            *arguments,
        ]
    )
    result = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "ConnectTimeout=15",
            "-i",
            "/Users/kartik/Desktop/noir-server.pem",
            "ubuntu@16.171.197.228",
            remote,
        ],
        capture_output=True,
        text=True,
        timeout=40,
        check=True,
    )
    return json.loads(result.stdout)


def main() -> None:
    connection = json.loads((DIRECTORY / "connection.json").read_text())
    url = connection["backend_url"]
    fixture = (DIRECTORY / "noir_test_v2.apk").read_bytes()
    run_id = uuid4().hex[:10]
    people = []
    evidence = []
    try:
        for letter in ("A", "B"):
            invitation = admin("invite", f"Deployment test {letter} {run_id}", "--hours", "1")
            client = httpx.Client(base_url=url, timeout=600)
            response = client.post("/v1/auth/redeem", json={"code": invitation["invite_code"]})
            response.raise_for_status()
            token = response.json()["token"]
            client.headers["Authorization"] = f"Bearer {token}"
            people.append((client, invitation["user_id"]))
            require(
                client.post("/v1/auth/redeem", json={"code": invitation["invite_code"]}).status_code
                == 400,
                "Invite replay accepted",
            )

        def call(client, method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            if response.status_code >= 400:
                raise RuntimeError(
                    f"{method} {path}: {response.status_code}: {response.text[:1500]}"
                )
            return response.json()

        def wait(client, job):
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                state = call(client, "GET", f"/v1/jobs/{job['job_id']}")
                if state["state"] in {"succeeded", "failed", "cancelled", "interrupted"}:
                    require(
                        state["state"] == "succeeded", f"Job failed: {state.get('error_message')}"
                    )
                    return state
                time.sleep(0.5)
            raise TimeoutError("Cloud test job timed out")

        for index, (client, user_id) in enumerate(people):
            letter = "AB"[index]
            other = people[1 - index][0]
            profile = call(client, "POST", "/v1/keys/personal")
            require(
                call(other, "GET", "/v1/keys")["profiles"]
                == ([] if index == 0 else [evidence[0]["profile"]]),
                "Signing profile list leaked",
            )
            job = call(
                client,
                "POST",
                "/v1/import?authorized=true",
                files={"file": ("owned-fixture.apk", fixture)},
                headers={"Idempotency-Key": run_id},
            )
            require(
                other.get(f"/v1/jobs/{job['job_id']}").status_code == 404,
                "Job leaked before import finished",
            )
            imported = wait(client, job)
            project = imported["project_id"]
            prefix = f"/v1/projects/{project}"
            label = f"NOIR Private {letter}"
            print(f"User {letter}: real import and private signing key ready", flush=True)
            if index == 0:
                plan = call(
                    client,
                    "POST",
                    prefix + "/plans",
                    json={
                        "allow_ai_upload": True,
                        "user_request": (
                            "In this NOIR-owned test APK, change ONLY the app_name string in "
                            f"res/values/strings.xml from NOIR Test to {label}. "
                            "Use one minimal replace_block. No other changes, no manifest edits, "
                            "no Smali, no network or permission changes."
                        ),
                    },
                )
                require(
                    {change["relative_path"] for change in plan["file_changes"]}
                    == {"res/values/strings.xml"},
                    "Plan outside requested scope",
                )
                require(
                    not plan["permission_changes"] and not plan["network_destinations"],
                    "Plan introduced extra behavior",
                )
                call(
                    client,
                    "POST",
                    prefix + f"/plans/{plan['plan_id']}/approve",
                    json={"hash": plan["plan_hash"]},
                )
                patch = call(
                    client, "POST", prefix + "/patches", params={"plan_id": plan["plan_id"]}
                )
                require(len(patch["operations"]) == 1, "Patch is not a single label operation")
                operation = patch["operations"][0]
                require(
                    operation["relative_path"] == "res/values/strings.xml"
                    and operation["operation"] == "replace_block",
                    "Unexpected patch target",
                )
                require(
                    operation["new_content"]
                    == operation["match_content"].replace("NOIR Test", label),
                    "Patch modifies more than the label",
                )
                call(
                    client,
                    "POST",
                    prefix + f"/patches/{patch['patch_id']}/approve",
                    json={"hash": patch["patch_hash"]},
                )
                call(client, "POST", prefix + f"/patches/{patch['patch_id']}/apply")
            else:
                before = call(
                    client, "GET", prefix + "/files/read", params={"path": "res/values/strings.xml"}
                )["content"]
                call(client, "POST", prefix + "/manual/begin")
                call(
                    client,
                    "PUT",
                    prefix + "/files",
                    json={
                        "relative_path": "res/values/strings.xml",
                        "content": before.replace("NOIR Test", label),
                        "expected_revision": 0,
                    },
                )
                call(
                    client,
                    "POST",
                    prefix + "/manual/record",
                    json={"message": "Owned fixture private workspace test"},
                )
            require(call(client, "POST", prefix + "/validate")["passed"], "Validation failed")
            wait(
                client,
                call(
                    client,
                    "POST",
                    prefix + "/build",
                    headers={"Idempotency-Key": "build-" + run_id},
                ),
            )
            build = call(client, "GET", prefix + "/builds")["builds"][0]
            signed = call(
                client,
                "POST",
                prefix + "/sign",
                json={"build_id": build["build_id"], "profile": profile["name"], "confirm": True},
            )
            require(
                call(client, "GET", prefix + f"/builds/{build['build_id']}/verify")["verified"],
                "Signature invalid",
            )
            download = client.get(prefix + f"/builds/{build['build_id']}/download")
            download.raise_for_status()
            require(
                hashlib.sha256(download.content).hexdigest() == signed["signed_apk_hash"],
                "Download hash mismatch",
            )
            (DIRECTORY / f"private-workspace-{letter.lower()}-signed.apk").write_bytes(
                download.content
            )
            history = call(client, "GET", "/v1/history")
            require(
                history["total"] == 1 and history["builds"][0]["project_id"] == project,
                "History not private",
            )
            for path in (
                prefix,
                prefix + "/files/read?path=res/values/strings.xml",
                prefix + "/audit",
                prefix + f"/builds/{build['build_id']}/download",
                f"/v1/jobs/{job['job_id']}/events",
            ):
                require(other.get(path).status_code == 404, f"Cross-user data accessible: {path}")
            require(
                other.post(f"/v1/jobs/{job['job_id']}/cancel").status_code == 404,
                "Cross-user cancellation allowed",
            )
            evidence.append(
                {
                    "user_id": user_id,
                    "project_id": project,
                    "build_id": build["build_id"],
                    "profile": profile,
                    "signed_sha256": signed["signed_apk_hash"],
                    "label": label,
                }
            )
            print(
                f"User {letter}: real build/sign/download/history passed; other user denied",
                flush=True,
            )

        for index, (client, _) in enumerate(people):
            mine = evidence[index]
            theirs = evidence[1 - index]
            require(
                client.get(
                    f"/v1/projects/{mine['project_id']}/builds/{theirs['build_id']}/download"
                ).status_code
                == 404,
                "Foreign build accepted under owned project",
            )
            require(
                client.post(
                    f"/v1/projects/{mine['project_id']}/sign",
                    json={
                        "build_id": mine["build_id"],
                        "profile": theirs["profile"]["name"],
                        "confirm": True,
                    },
                ).status_code
                == 404,
                "Foreign signer accepted",
            )
        (DIRECTORY / "private-workspaces-verification.json").write_text(
            json.dumps(evidence, indent=2) + "\n"
        )
        owner_invite = admin("invite", "Kartik", "--user", "local")
        with os.fdopen(
            os.open(DIRECTORY / "owner-invite.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
            "w",
        ) as handle:
            json.dump(owner_invite, handle, indent=2)
        print("Two-user live verification passed. Owner invitation saved privately.", flush=True)
    finally:
        for client, user_id in people:
            try:
                admin("revoke", user_id)
                require(
                    client.get("/v1/history").status_code == 401, "Revoked user still authenticated"
                )
            finally:
                client.close()
        print("Temporary test-user sessions revoked.", flush=True)


if __name__ == "__main__":
    main()
