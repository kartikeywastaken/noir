"""Opt-in real cloud smoke: three disposable invites, one Gemini-assisted fixture build.

No existing user's codes are redeemed. Temporary identities are disabled in finally.
Only redacted stage/timing evidence is printed; no credentials are written to disk.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from uuid import uuid4

import httpx
from private_smoke import DIRECTORY, admin, require


def main():
    connection = json.loads((DIRECTORY / "connection.json").read_text())
    url = connection["backend_url"]
    fixture = (DIRECTORY / "noir_test_v2.apk").read_bytes()
    users, clients = [], []
    run = uuid4().hex[:8]
    timings = {}
    try:
        invitations = []
        for letter in "ABC":
            invite = admin("invite", f"Three-step test {run} {letter}", "--hours", "1")
            users.append(invite["user_id"])
            invitations.append(invite)
        for invite in invitations:
            client = httpx.Client(base_url=url, timeout=30)
            clients.append(client)
            response = client.post("/v1/auth/redeem", json={"code": invite["invite_code"]})
            response.raise_for_status()
            client.headers["Authorization"] = "Bearer " + response.json()["token"]
        print("Three distinct invitations activated independently", flush=True)
        client = clients[0]

        def call(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

        def wait(job):
            started = time.monotonic()
            last = None
            while time.monotonic() - started < 900:
                current = call("GET", f"/v1/jobs/{job['job_id']}")
                stage = (current["state"], current["stage"])
                if stage != last:
                    print(f"{stage[0]}: {stage[1]} ({time.monotonic() - started:.1f}s)", flush=True)
                    last = stage
                if current["state"] in {"failed", "cancelled", "interrupted"}:
                    raise RuntimeError(current.get("error_message", "Job failed"))
                if current["state"] == "succeeded":
                    timings[current["result_data"]["operation"]] = round(
                        time.monotonic() - started, 2
                    )
                    return current
                time.sleep(2)
            raise TimeoutError("Cloud workflow did not complete")

        imported = wait(
            call(
                "POST",
                "/v1/import?authorized=true",
                files={"file": ("noir-owned-fixture.apk", fixture)},
                headers={"Idempotency-Key": run + "-import"},
            )
        )
        project = imported["project_id"]
        prefix = f"/v1/projects/{project}"
        started = time.monotonic()
        job = call(
            "POST",
            prefix + "/workflow/prepare",
            headers={"Idempotency-Key": run + "-preview"},
            json={
                "revision": 0,
                "allow_ai_upload": True,
                "user_request": "In this NOIR-owned fixture, change ONLY the app_name string "
                "in res/values/strings.xml "
                "from NOIR Test to NOIR Three Step. Use one minimal replace_block. No other edits, "
                "no manifest or Smali changes, no permissions or network behavior.",
            },
        )
        timings["prepare_submission_seconds"] = round(time.monotonic() - started, 2)
        # Real disconnection: replace the HTTP client while server generation continues.
        headers = dict(client.headers)
        client.close()
        client = httpx.Client(base_url=url, headers=headers, timeout=30)
        clients[0] = client
        prepared = wait(job)
        result = prepared["result_data"]["result"]
        plan = call("GET", prefix + "/plans/" + result["plan_id"])
        patch = call("GET", prefix + "/patches/" + result["patch_id"])
        blocked = client.post(prefix + f"/patches/{patch['patch_id']}/apply")
        require(
            blocked.status_code == 400 and "approval" in blocked.text.lower(),
            "Preview could apply without approval",
        )
        require(
            {c["relative_path"] for c in plan["file_changes"]} == {"res/values/strings.xml"},
            "Plan escaped requested scope",
        )
        require(
            not plan["permission_changes"] and not plan["network_destinations"], "Extra behavior"
        )
        require(len(patch["operations"]) == 1, "Expected a single label operation")
        operation = patch["operations"][0]
        require(
            operation["relative_path"] == "res/values/strings.xml"
            and operation["operation"] == "replace_block",
            "Unexpected patch operation",
        )
        require(
            operation["match_content"].replace("NOIR Test", "NOIR Three Step")
            == operation["new_content"],
            "Patch was not solely the requested label replacement",
        )
        require(call("GET", prefix + f"/patches/{patch['patch_id']}/diff")["diff"], "Empty preview")
        require(call("GET", prefix)["workspace_revision"] == 0, "Preview changed files")
        payload = {
            "plan_id": plan["plan_id"],
            "patch_id": patch["patch_id"],
            "plan_hash": plan["plan_hash"],
            "patch_hash": patch["patch_hash"],
            "revision": 0,
            "confirm": True,
        }
        for other in clients[1:]:
            require(other.get(prefix).status_code == 404, "Private workspace leaked")
            require(
                other.post(
                    prefix + "/workflow/finish", json=payload, headers={"Idempotency-Key": run}
                ).status_code
                == 404,
                "Foreign approval accepted",
            )
        finished = wait(
            call(
                "POST",
                prefix + "/workflow/finish",
                json=payload,
                headers={"Idempotency-Key": run + "-finish"},
            )
        )
        build_id = finished["result_data"]["result"]["build_id"]
        build = next(
            b for b in call("GET", prefix + "/builds")["builds"] if b["build_id"] == build_id
        )
        require(call("GET", prefix + f"/builds/{build_id}/verify")["verified"], "Signature invalid")
        download = client.get(prefix + f"/builds/{build_id}/download")
        download.raise_for_status()
        require(
            hashlib.sha256(download.content).hexdigest() == build["signed_apk_hash"],
            "Hash mismatch",
        )
        require(len(call("GET", "/v1/history")["builds"]) == 1, "Unexpected history")
        output = Path(__file__).resolve().parents[3] / "frontend/output/cloud-three-step"
        output.mkdir(parents=True, exist_ok=True)
        (output / "signed-fixture.apk").write_bytes(download.content)
        (output / "verification.json").write_text(
            json.dumps(
                {
                    "project_id": project,
                    "build_id": build_id,
                    "signed_sha256": build["signed_apk_hash"],
                    "timings": timings,
                    "independent_invites": True,
                    "foreign_access_denied": True,
                    "disconnect_recovery": True,
                },
                indent=2,
            )
            + "\n"
        )
        print("Real Gemini preview → combined approval → signed APK passed", flush=True)
        print(json.dumps(timings), flush=True)
    finally:
        for user in users:
            admin("revoke", user)
        for client in clients:
            client.close()
        print("Temporary test users disabled; existing users/invites untouched", flush=True)


if __name__ == "__main__":
    main()
