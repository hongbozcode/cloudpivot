from functools import lru_cache

from minio import Minio
from minio.error import S3Error

from core import agent_setting as config


@lru_cache(maxsize=1)
def _get_client() -> Minio:
    settings = config.settings
    host, port, access_key, secret_key, _ = config.require_values(
        HOST_IP=settings.minio_host,
        MINIO_PORT=settings.minio_port,
        MINIO_ACCESS_KEY=settings.minio_access_key,
        MINIO_SECRET_KEY=settings.minio_secret_key,
        MINIO_BUCKET_NAME=settings.minio_bucket_name,
    )
    return Minio(endpoint=f'{host}:{port}', access_key=access_key, secret_key=secret_key, secure=False)

# 创建bucket
def create_bucket(bucket_name):
    client = _get_client()
    if not client.bucket_exists(bucket_name):
        try:
            client.make_bucket(bucket_name)
        except S3Error as exc:
            if exc.code != 'BucketAlreadyOwnedByYou':
                raise

# 展示bucket
def list_buckets():
    buckets = _get_client().list_buckets()
    for bucket in buckets:
        print(bucket.name, bucket.creation_date)

# 上传文件
def upload_file(bucket_name, object_name, file_path):
    _get_client().fput_object(bucket_name, object_name, file_path)

# 下载文件
def download_file(bucket_name, object_name, file_path):
    _get_client().fget_object(bucket_name, object_name, file_path)


# 生成下载的url
def gen_presigned_url(bucket_name, object_name):
    """
    生成下载的url
    :param object_name: minio文件名
    :return: 下载的url
    """
    presigned_url = _get_client().presigned_get_object(bucket_name, object_name)
    return presigned_url

def upload_file_and_download_url(bucket_name, object_name, file_path):
    upload_file(bucket_name, object_name, file_path)
    return gen_presigned_url(bucket_name, object_name)

if __name__ == '__main__':
    print(_get_client().list_buckets())
