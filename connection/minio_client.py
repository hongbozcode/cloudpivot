from minio import Minio
from minio.error import S3Error
from core import config

client = Minio(
    endpoint=config.MINIO_ENDPOINT,
    access_key=config.MINIO_ACCESS_KEY, # 账户
    secret_key=config.MINIO_SECRET_KEY, # 密码
    secure=False # 目前无https域名，所以安全设置为false
)

# 创建bucket
def create_bucket(bucket_name):
    if not client.bucket_exists(bucket_name):
        try:
            client.make_bucket(bucket_name)
        except S3Error as exc:
            if exc.code != 'BucketAlreadyOwnedByYou':
                raise

# 展示bucket
def list_buckets():
    buckets = client.list_buckets()
    for bucket in buckets:
        print(bucket.name, bucket.creation_date)

# 上传文件
def upload_file(bucket_name, object_name, file_path):
    client.fput_object(bucket_name, object_name, file_path)

# 下载文件
def download_file(bucket_name, object_name, file_path):
    client.fget_object(bucket_name, object_name, file_path)


# 生成下载的url
def gen_presigned_url(bucket_name, object_name):
    """
    生成下载的url
    :param object_name: minio文件名
    :return: 下载的url
    """
    presigned_url = client.presigned_get_object(bucket_name, object_name)
    return presigned_url

def upload_file_and_download_url(bucket_name, object_name, file_path):
    upload_file(bucket_name, object_name, file_path)
    return gen_presigned_url(bucket_name, object_name)

if __name__ == '__main__':
   print(client.list_buckets())
