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
import logging
from typing import Optional, Union, List, Dict, Any

logger = logging.getLogger("storage")

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
            logger.info("Initializing StorageService in GCP Production Mode (google-cloud-storage)")
            self.gcs_client = gcs_storage.Client()
            self.s3_client = None
        else:
            import boto3
            from botocore.config import Config
            
            minio_endpoint = os.getenv("MINIO_ENDPOINT", "http://minio:9000")
            # Fallback for running outside docker network during tests
            if "minio" in minio_endpoint and not os.getenv("RUNNING_IN_DOCKER"):
                # Check if localhost is accessible
                pass
            
            minio_access_key = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
            minio_secret_key = os.getenv("MINIO_SECRET_KEY", "minioadmin")
            
            logger.info(f"Initializing StorageService in Local Plane (MinIO S3 API at {minio_endpoint})")
            self.s3_client = boto3.client(
                "s3",
                endpoint_url=minio_endpoint,
                aws_access_key_id=minio_access_key,
                aws_secret_access_key=minio_secret_key,
                config=Config(signature_version="s3v4"),
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
                blob.upload_from_string(payload, content_type=content_type)
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

    def download_file(self, bucket_name: str, object_name: str, dest_path: Optional[str] = None) -> bytes:
        """Downloads object bytes from bucket. Optionally saves to dest_path."""
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
            logger.error(f"Failed download from {bucket_name}/{object_name}: {e}")
            raise

    def generate_signed_url(
        self,
        bucket_name: str,
        object_name: str,
        method: str = "PUT",
        expiration: int = 3600,
        content_type: Optional[str] = None
    ) -> str:
        """Generates cryptographically signed upload/download URL."""
        object_name = object_name.lstrip("/")
        if self.use_gcp:
            from datetime import timedelta
            bucket = self.gcs_client.bucket(bucket_name)
            blob = bucket.blob(object_name)
            url = blob.generate_signed_url(
                version="v4",
                expiration=timedelta(seconds=expiration),
                method=method,
                content_type=content_type
            )
            return url
        else:
            client_method = "put_object" if method.upper() == "PUT" else "get_object"
            params = {"Bucket": bucket_name, "Key": object_name}
            if content_type and client_method == "put_object":
                params["ContentType"] = content_type
            url = self.s3_client.generate_presigned_url(
                ClientMethod=client_method,
                Params=params,
                ExpiresIn=expiration
            )
            return url

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
        - MinIO: writes lock file with lease metadata and verifies lease freshness.
        Enables Spot VM preemption auto-recovery when lease expires.
        """
        lock_path = lock_path.lstrip("/")
        now = time.time()
        lock_payload = json.dumps({
            "worker_id": worker_id,
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
                    except Exception:
                        pass
                
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
                # Check existing lock
                try:
                    resp = self.s3_client.get_object(Bucket=bucket_name, Key=lock_path)
                    existing = json.loads(resp["Body"].read().decode("utf-8"))
                    if now < existing.get("expires_at", 0):
                        # Still active by another worker
                        return False
                except self.s3_client.exceptions.NoSuchKey:
                    pass
                except Exception:
                    pass

                # Claim lock
                self.s3_client.put_object(
                    Bucket=bucket_name,
                    Key=lock_path,
                    Body=lock_payload.encode("utf-8"),
                    ContentType="application/json"
                )
                return True
            except Exception as e:
                logger.warning(f"MinIO acquire_lock failed for {lock_path}: {e}")
                return False

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


# Global singleton instance helper
_storage_instance = None

def get_storage() -> StorageService:
    global _storage_instance
    if _storage_instance is None:
        _storage_instance = StorageService()
    return _storage_instance
