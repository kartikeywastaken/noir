import hashlib
import io
from pathlib import Path

from noir.domain.config import NoirConfig
from noir.infrastructure.artifacts import ArtifactStore


class FakeS3:
    def __init__(self):
        self.uploads = []
        self.objects = {}
        self.presigns = []
        self.multipart_creates = []
        self.multipart_completions = []
        self.multipart_aborts = []
        self.multipart_metadata = {}
        self.payload = b""

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

    def create_multipart_upload(self, **kwargs):
        self.multipart_creates.append(kwargs)
        self.multipart_metadata[(kwargs["Bucket"], kwargs["Key"])] = kwargs["Metadata"]
        return {"UploadId": "multipart-id"}

    def complete_multipart_upload(self, **kwargs):
        self.multipart_completions.append(kwargs)
        object_id = (kwargs["Bucket"], kwargs["Key"])
        self.objects[object_id] = {
            "ContentLength": len(self.payload),
            "Metadata": self.multipart_metadata[object_id],
        }

    def abort_multipart_upload(self, **kwargs):
        self.multipart_aborts.append(kwargs)

    def get_object(self, **kwargs):
        metadata = self.objects[(kwargs["Bucket"], kwargs["Key"])]["Metadata"]
        return {
            "ContentLength": len(self.payload),
            "Metadata": metadata,
            "Body": io.BytesIO(self.payload),
        }


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


def test_direct_multipart_original_is_private_checksum_bound_and_stream_verified(tmp_path):
    client = FakeS3()
    store = ArtifactStore(_config(tmp_path), client=client)
    client.payload = b"direct-s3-apk"
    digest = hashlib.sha256(client.payload).hexdigest()

    key, upload_id = store.begin_multipart_original(
        "alice",
        "project1",
        sha256=digest,
        size=len(client.payload),
    )
    checksum = "YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXphYmNkZWY="
    url = store.presign_multipart_part(
        key=key,
        upload_id=upload_id,
        part_number=1,
        checksum_sha256=checksum,
    )
    store.complete_multipart_original(
        key=key,
        upload_id=upload_id,
        parts=[{"PartNumber": 1, "ETag": '"' + ("a" * 32) + '"', "ChecksumSHA256": checksum}],
    )
    store.verify_original_object(
        key=key,
        expected_size=len(client.payload),
        expected_sha256=digest,
    )
    destination = tmp_path / "scratch" / "input.apk"
    actual_digest, actual_size = store.download_verified(
        key=key,
        destination=destination,
        expected_size=len(client.payload),
        expected_sha256=digest,
    )

    create = client.multipart_creates[0]
    assert create["ServerSideEncryption"] == "AES256"
    assert create["ChecksumAlgorithm"] == "SHA256"
    assert create["Metadata"]["sha256"] == digest
    assert client.presigns[-1][0] == "upload_part"
    assert client.presigns[-1][1]["ChecksumSHA256"] == checksum
    assert url == f"https://signed.example/{key}"
    assert (actual_digest, actual_size) == (digest, len(client.payload))
    assert destination.read_bytes() == client.payload
