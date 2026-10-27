"""
Crowd-Sourced Collaborative Art & High-Compute Raster Platform
Unified Storage Abstraction Layer (Task 1.2)
"Write Once, Run Anywhere" abstraction supporting:
- Local Development Plane: MinIO (S3 API via boto3)
- GCP Production Plane: Google Cloud Storage (google-cloud-storage)
Strictly Bucket-Only Data Plane (Zero SQL, Zero NoSQL)
"""

import os
import io
import time
import json
import uuid
import logging
import botocore.exceptions
from typing import Optional, Union, List, Dict, Any

logger = logging.getLogger("storage")

try:
    from google.cloud.storage.retry import DEFAULT_RETRY as _DEFAULT_RETRY
    _GCS_WRITE_RETRY = _DEFAULT_RETRY.with_deadline(30)
except ImportError:  # MinIO-only images
    _GCS_WRITE_RETRY = None

# Standard 4-Bucket Architecture
BUCKET_PHOTO_LIBRARY = os.getenv("BUCKET_PHOTO_LIBRARY", "bucket-photo-library")
BUCKET_TARGET_POSTERS = os.getenv("BUCKET_TARGET_POSTERS", "bucket-target-posters")
BUCKET_TASK_COORDINATION = os.getenv("BUCKET_TASK_COORDINATION", "bucket-task-coordination")
BUCKET_MOSAIC_OUTPUTS = os.getenv("BUCKET_MOSAIC_OUTPUTS", "bucket-mosaic-outputs")

ALL_REQUIRED_BUCKETS = [
    BUCKET_PHOTO_LIBRARY,
    BUCKET_TARGET_POSTERS,
    BUCKET_TASK_COORDINATION,
    BUCKET_MOSAIC_OUTPUTS,
]


class StorageService:
    def __init__(self):
        self.use_gcp = os.getenv("USE_GCP", "false").lower() in ("true", "1", "yes")
        
        if self.use_gcp:
            from google.cloud import storage as gcs_storage
            # On a GCE VM the client authenticates as the VM's service account via the metadata
            # server; GCP_PROJECT_ID is optional there. STORAGE_EMULATOR_HOST (if set) targets a
            # local GCS emulator for testing the production code path.
            logger.info("Initializing StorageService in GCP Production Mode (google-cloud-storage)")
            project = os.getenv("GCP_PROJECT_ID") or None
            if os.getenv("STORAGE_EMULATOR_HOST"):
                from google.auth.credentials import AnonymousCredentials
                self.gcs_client = gcs_storage.Client(project=project or "local-emulator",
                                                     credentials=AnonymousCredentials())
            else:
                self.gcs_client = gcs_storage.Client(project=project)
            # The default HTTP pool keeps 10 connections; workers and the assembler fetch tiles
            # with up to 16-32 threads, which otherwise churns connections ("pool is full").
            from requests.adapters import HTTPAdapter
            adapter = HTTPAdapter(pool_connections=64, pool_maxsize=64)
            self.gcs_client._http.mount("https://", adapter)
            self.gcs_client._http.mount("http://", adapter)
            self.s3_client = None
        else:
            import boto3
            from botocore.config import Config

            # Every service gets its storage endpoint explicitly: on separate VMs there is no
            # shared Docker network name to fall back on.
            minio_endpoint = os.getenv("MINIO_ENDPOINT", "http://localhost:9000")
            logger.info(f"Initializing StorageService in Local Plane (MinIO S3 API at {minio_endpoint})")
            self.s3_client = boto3.client(
                "s3",
                endpoint_url=minio_endpoint,
                aws_access_key_id=os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
                aws_secret_access_key=os.getenv("MINIO_SECRET_KEY", "minioadmin"),
                config=Config(signature_version="s3v4", max_pool_connections=32,
                              connect_timeout=10, read_timeout=60, retries={"max_attempts": 4}),
                region_name="us-east-1"
            )
            self.gcs_client = None

    def ensure_buckets_exist(self) -> None:
        """Auto-provisions the 4 required buckets if they do not exist."""
        for bucket in ALL_REQUIRED_BUCKETS:
            try:
                if self.use_gcp:
                    b = self.gcs_client.bucket(bucket)
                    if not b.exists():
                        self.gcs_client.create_bucket(bucket)
                        logger.info(f"Created GCS bucket: {bucket}")
                else:
                    existing = [b["Name"] for b in self.s3_client.list_buckets().get("Buckets", [])]
                    if bucket not in existing:
                        self.s3_client.create_bucket(Bucket=bucket)
                        logger.info(f"Created MinIO bucket: {bucket}")
            except Exception as e:
                logger.warning(f"Bucket check/creation notice for '{bucket}': {e}")

    def upload_file(
        self,
        bucket_name: str,
        object_name: str,
        data_or_path: Union[str, bytes, io.BytesIO],
        content_type: Optional[str] = None
    ) -> bool:
        """Uploads raw bytes, BytesIO, or file from disk to bucket."""
        object_name = object_name.lstrip("/")
        try:
            if isinstance(data_or_path, str) and os.path.isfile(data_or_path):
                with open(data_or_path, "rb") as f:
                    payload = f.read()
            elif isinstance(data_or_path, io.BytesIO):
                payload = data_or_path.getvalue()
            elif isinstance(data_or_path, (bytes, bytearray)):
                payload = bytes(data_or_path)
            elif isinstance(data_or_path, str):
                payload = data_or_path.encode("utf-8")
            else:
                raise ValueError("Unsupported data type for upload")

            if self.use_gcp:
                bucket = self.gcs_client.bucket(bucket_name)
                blob = bucket.blob(object_name)
                # GCS allows ~1 write/sec to one object and throttles bursts with 429; whole-object
                # overwrites are safe to repeat, so retry with backoff instead of failing.
                blob.upload_from_string(payload, content_type=content_type, retry=_GCS_WRITE_RETRY)
            else:
                extra_args = {}
                if content_type:
                    extra_args["ContentType"] = content_type
                self.s3_client.put_object(
                    Bucket=bucket_name,
                    Key=object_name,
                    Body=payload,
                    **extra_args
                )
            return True
        except Exception as e:
            logger.error(f"Failed upload to {bucket_name}/{object_name}: {e}")
            raise

    def download_file(self, bucket_name: str, object_name: str, dest_path: Optional[str] = None,
                      quiet: bool = False) -> bytes:
        """Downloads object bytes from bucket. Optionally saves to dest_path. quiet=True: expected misses aren't logged."""
        object_name = object_name.lstrip("/")
        try:
            if self.use_gcp:
                bucket = self.gcs_client.bucket(bucket_name)
                blob = bucket.blob(object_name)
                data = blob.download_as_bytes()
            else:
                resp = self.s3_client.get_object(Bucket=bucket_name, Key=object_name)
                data = resp["Body"].read()

            if dest_path:
                os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
                with open(dest_path, "wb") as f:
                    f.write(data)

            return data
        except Exception as e:
            if not quiet:
                logger.error(f"Failed download from {bucket_name}/{object_name}: {e}")
            raise

    def upload_stream(self, bucket_name: str, object_name: str, fileobj, content_type: Optional[str] = None) -> None:
        """
        Uploads from a file-like object without loading it into memory (multipart/resumable
        transfer). Used by the API Gateway to relay browser uploads into the private bucket.
        """
        object_name = object_name.lstrip("/")
        if self.use_gcp:
            blob = self.gcs_client.bucket(bucket_name).blob(object_name)
            blob.upload_from_file(fileobj, content_type=content_type, rewind=True)
        else:
            extra = {"ContentType": content_type} if content_type else None
            self.s3_client.upload_fileobj(fileobj, bucket_name, object_name, ExtraArgs=extra)

    def create_if_absent(self, bucket_name: str, object_name: str, data: bytes,
                         content_type: str = "application/json") -> bool:
        """Atomically creates an object; False if it already exists (first writer wins)."""
        if self.use_gcp:
            from google.api_core.exceptions import PreconditionFailed
            try:
                self.gcs_client.bucket(bucket_name).blob(object_name).upload_from_string(
                    data, content_type=content_type, if_generation_match=0)
                return True
            except PreconditionFailed:
                return False
        try:
            self.s3_client.put_object(Bucket=bucket_name, Key=object_name, Body=data,
                                      ContentType=content_type, IfNoneMatch="*")
            return True
        except botocore.exceptions.ClientError as ce:
            if ce.response.get("Error", {}).get("Code") in ("PreconditionFailed", "412"):
                return False
            raise

    def acquire_lock(
        self,
        bucket_name: str,
        lock_path: str,
        worker_id: str,
        ttl_seconds: int = 60
    ) -> bool:
        """
        Atomic Worker Lock Acquisition:
        - GCP: uses `if_generation_match=0` precondition.
        - MinIO: writes lock file with lease metadata, IfNoneMatch='*' atomic conditional put,
          and nonce verification to guarantee mutual exclusion under high multi-worker concurrency.
        Enables Spot VM preemption auto-recovery when lease expires.
        """
        lock_path = lock_path.lstrip("/")
        now = time.time()
        nonce = uuid.uuid4().hex
        lock_payload = json.dumps({
            "worker_id": worker_id,
            "nonce": nonce,
            "acquired_at": now,
            "expires_at": now + ttl_seconds
        })

        if self.use_gcp:
            try:
                from google.api_core.exceptions import PreconditionFailed
                bucket = self.gcs_client.bucket(bucket_name)
                blob = bucket.blob(lock_path)
                
                # Check for stale lock (preempted worker recovery)
                if blob.exists():
                    try:
                        content = json.loads(blob.download_as_text())
                        if now > content.get("expires_at", 0):
                            # Stale lock expired, override
                            blob.delete()
                        else:
                            return False
                    except Exception:
                        return False
                
                # Atomic creation: fails if generation != 0
                blob.upload_from_string(
                    lock_payload,
                    content_type="application/json",
                    if_generation_match=0
                )
                return True
            except PreconditionFailed:
                return False
            except Exception as e:
                logger.warning(f"GCS acquire_lock failed for {lock_path}: {e}")
                return False
        else:
            # S3 / MinIO atomic acquisition
            try:
                # 1. Check existing lock and lease freshness
                try:
                    resp = self.s3_client.get_object(Bucket=bucket_name, Key=lock_path)
                    existing = json.loads(resp["Body"].read().decode("utf-8"))
                    if now < existing.get("expires_at", 0):
                        # Still actively held by another worker
                        return False
                    else:
                        # Stale lock expired (Spot VM preemption auto-recovery): remove stale lock
                        try:
                            self.s3_client.delete_object(Bucket=bucket_name, Key=lock_path)
                        except Exception:
                            pass
                except self.s3_client.exceptions.NoSuchKey:
                    pass
                except botocore.exceptions.ClientError as ce:
                    if ce.response.get("Error", {}).get("Code") not in ("NoSuchKey", "404"):
                        pass

                # 2. Atomic conditional put (fails with PreconditionFailed if already exists)
                try:
                    self.s3_client.put_object(
                        Bucket=bucket_name,
                        Key=lock_path,
                        Body=lock_payload.encode("utf-8"),
                        ContentType="application/json",
                        IfNoneMatch="*"
                    )
                except botocore.exceptions.ClientError as ce:
                    code = ce.response.get("Error", {}).get("Code")
                    if code in ("PreconditionFailed", "412"):
                        # Another worker won the atomic race
                        return False
                    # Fallback for storage backends without IfNoneMatch support
                    pass

                # 3. Read-after-write verification to guarantee strict mutual exclusion
                resp = self.s3_client.get_object(Bucket=bucket_name, Key=lock_path)
                holder = json.loads(resp["Body"].read().decode("utf-8"))
                if holder.get("worker_id") == worker_id and holder.get("nonce") == nonce:
                    return True
                return False
            except Exception as e:
                logger.warning(f"MinIO acquire_lock failed for {lock_path}: {e}")
                return False

    def renew_lock(self, bucket_name: str, lock_path: str, worker_id: str, ttl_seconds: int) -> bool:
        """Extends a lease this worker still holds (long tasks must not expire mid-run)."""
        lock_path = lock_path.lstrip("/")
        try:
            holder = json.loads(self.download_file(bucket_name, lock_path, quiet=True))
        except Exception:
            return False
        if holder.get("worker_id") != worker_id:
            return False
        holder["expires_at"] = time.time() + ttl_seconds
        self.upload_file(bucket_name, lock_path, json.dumps(holder).encode("utf-8"), "application/json")
        return True

    def release_lock(self, bucket_name: str, lock_path: str, worker_id: Optional[str] = None) -> bool:
        """Releases/deletes the lock object."""
        lock_path = lock_path.lstrip("/")
        try:
            if self.use_gcp:
                bucket = self.gcs_client.bucket(bucket_name)
                blob = bucket.blob(lock_path)
                if blob.exists():
                    blob.delete()
            else:
                self.s3_client.delete_object(Bucket=bucket_name, Key=lock_path)
            return True
        except Exception as e:
            logger.warning(f"release_lock failed for {lock_path}: {e}")
            return False

    def list_objects(self, bucket_name: str, prefix: str = "") -> List[str]:
        """Lists object keys matching prefix."""
        prefix = prefix.lstrip("/")
        keys = []
        try:
            if self.use_gcp:
                bucket = self.gcs_client.bucket(bucket_name)
                blobs = bucket.list_blobs(prefix=prefix)
                keys = [b.name for b in blobs]
            else:
                paginator = self.s3_client.get_paginator("list_objects_v2")
                for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix):
                    for item in page.get("Contents", []):
                        keys.append(item["Key"])
            return keys
        except Exception as e:
            logger.error(f"list_objects failed for {bucket_name}/{prefix}: {e}")
            return []

    def copy_object(self, bucket_name: str, source: str, dest: str) -> None:
        """Server-side copy within one bucket (no download/upload through this VM)."""
        if self.use_gcp:
            bucket = self.gcs_client.bucket(bucket_name)
            bucket.copy_blob(bucket.blob(source), bucket, dest, retry=_GCS_WRITE_RETRY)
        else:
            self.s3_client.copy_object(Bucket=bucket_name, Key=dest, CopySource={"Bucket": bucket_name, "Key": source})

    def prefix_usage(self, bucket_name: str, prefix: str) -> int:
        """Total bytes stored under a prefix (raises on listing errors, unlike list_objects)."""
        prefix = prefix.lstrip("/")
        if self.use_gcp:
            return sum(b.size or 0 for b in self.gcs_client.bucket(bucket_name).list_blobs(prefix=prefix))
        total = 0
        for page in self.s3_client.get_paginator("list_objects_v2").paginate(Bucket=bucket_name, Prefix=prefix):
            total += sum(item.get("Size", 0) for item in page.get("Contents", []))
        return total

    def delete_object(self, bucket_name: str, object_name: str) -> bool:
        """Deletes a single object from storage."""
        object_name = object_name.lstrip("/")
        try:
            if self.use_gcp:
                bucket = self.gcs_client.bucket(bucket_name)
                blob = bucket.blob(object_name)
                if blob.exists():
                    blob.delete()
            else:
                self.s3_client.delete_object(Bucket=bucket_name, Key=object_name)
            return True
        except Exception as e:
            logger.error(f"Failed deleting {bucket_name}/{object_name}: {e}")
            return False

    def object_exists(self, bucket_name: str, object_name: str) -> bool:
        """Exact-key existence check (list_objects is a prefix match)."""
        object_name = object_name.lstrip("/")
        try:
            if self.use_gcp:
                return self.gcs_client.bucket(bucket_name).blob(object_name).exists()
            self.s3_client.head_object(Bucket=bucket_name, Key=object_name)
            return True
        except Exception:
            return False

    def delete_prefix(self, bucket_name: str, prefix: str) -> int:
        """Deletes all objects matching prefix (batched: 1000 keys per S3 request)."""
        prefix = prefix.lstrip("/")
        if not prefix:
            raise ValueError("delete_prefix refuses an empty prefix")
        keys = self.list_objects(bucket_name, prefix)
        if not keys:
            return 0
        deleted = 0
        if self.use_gcp:
            bucket = self.gcs_client.bucket(bucket_name)
            for k in keys:
                try:
                    bucket.blob(k).delete()
                    deleted += 1
                except Exception as e:
                    logger.error(f"Failed deleting {bucket_name}/{k}: {e}")
            return deleted
        for i in range(0, len(keys), 1000):
            batch = keys[i:i + 1000]
            resp = self.s3_client.delete_objects(
                Bucket=bucket_name,
                Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
            )
            errors = resp.get("Errors", [])
            for err in errors:
                logger.error(f"Failed deleting {bucket_name}/{err.get('Key')}: {err.get('Message')}")
            deleted += len(batch) - len(errors)
        return deleted


# Global singleton instance helper
_storage_instance = None

def get_storage() -> StorageService:
    global _storage_instance
    if _storage_instance is None:
        _storage_instance = StorageService()
    return _storage_instance
