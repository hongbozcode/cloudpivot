import base64
import mimetypes

def file_to_base64(file_path):
    """将文件转换为Base64字符串"""
    with open(file_path, 'rb') as file:
        binary_data = file.read()
        base64_str = base64.b64encode(binary_data).decode('utf-8')
        return base64_str

def file_to_data_uri(file_path):
    """将文件转换为 data URI（data:<mime>;base64,<数据>），用于 image_url 等接口字段"""
    mime_type = mimetypes.guess_type(file_path)[0] or 'application/octet-stream'
    base64_str = file_to_base64(file_path)
    return f"data:{mime_type};base64,{base64_str}"

def base64_to_file(base64_str, output_path):
    """将 Base64 字符串写入新文件。"""
    binary_data = base64.b64decode(base64_str)

    with open(output_path, 'xb') as file:
        file.write(binary_data)
