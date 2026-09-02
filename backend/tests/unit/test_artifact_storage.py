from pathlib import Path

from noir.domain.config import NoirConfig
from noir.infrastructure.artifacts import ArtifactStore


class FakeS3:
    def __init__(self):
        self.uploads = []
        self.objects = {}
        self.presigns = []

    def upload_file(self, path, bucket, key, **kwargs):
        extra_args = kwargs["ExtraArgs"]
        self.uploads.append((path, bucket, key, extra_args))
        self.objects[(bucket, key)] = {"Metadata": extra_args["Metadata"]}

    def head_object(self, **kwargs):
        return self.objects[(kwargs["Bucket"], kwargs["Key"])]

    def generate_presigned_url(self, operation, **kwargs):
        params = kwargs["Params"]
        expires_in = kwargs["ExpiresIn"]
        self.presigns.append((operation, params, expires_in))
        return f"https://signed.example/{params['Key']}"


def _config(tmp_path: Path, **overrides):
    return NoirConfig(
        _env_file=None,
        data_dir=str(tmp_path),
        artifact_store="s3",
        s3_bucket="private-test-bucket",
        s3_region="eu-north-1",
        **overrides,
    )


def test_owner_scoped_keys_are_deterministic_and_isolated(tmp_path):
    store = ArtifactStore(_config(tmp_path), client=FakeS3())
    one = store.signed_key("alice", "project1", "build1")
    two = store.signed_key("bob", "project1", "build1")

    assert one == "noir/users/alice/projects/project1/builds/build1/signed.apk"
    assert two != one


def test_verified_signed_artifact_upload_and_presign(tmp_path):
    client = FakeS3()
    store = ArtifactStore(_config(tmp_path), client=client)
    apk = tmp_path / "signed.apk"
    apk.write_bytes(b"signed-apk")
    digest = "a" * 64

    key = store.store_signed("alice", "project1", "build1", apk, sha256=digest)
    url = store.signed_download_url(
        "alice",
        "project1",
        "build1",
        expected_sha256=digest,
        filename="NOIR result.apk",
    )

    assert key == "noir/users/alice/projects/project1/builds/build1/signed.apk"
    assert client.uploads[0][3]["ServerSideEncryption"] == "AES256"
    assert client.uploads[0][3]["Metadata"]["sha256"] == digest
    assert url == f"https://signed.example/{key}"
    assert client.presigns[0][2] == 900
    assert "NOIR_result.apk" in client.presigns[0][1]["ResponseContentDisposition"]


def test_local_mode_never_constructs_an_s3_client(tmp_path):
    config = NoirConfig(_env_file=None, data_dir=str(tmp_path), artifact_store="local")
    store = ArtifactStore(config)
    apk = tmp_path / "input.apk"
    apk.write_bytes(b"apk")

    assert store.store_original("alice", "project1", apk, sha256="b" * 64) is None
