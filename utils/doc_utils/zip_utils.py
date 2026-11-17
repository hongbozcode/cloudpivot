import os
import zipfile

from utils.doc_utils.os_utils import is_link


def zip_folder(
    folder_path: str,
    output_path: str | None = None,
    ignore: list[str] | None = None,
    compression: int = zipfile.ZIP_DEFLATED,
) -> str:
    """将文件夹压缩成 zip。

    Args:
        folder_path: 要压缩的文件夹路径。
        output_path: 输出 zip 文件路径，默认为 folder_path 同级目录下的
            `<文件夹名>.zip`。
        ignore: 需要跳过的文件/目录名列表（只按名字匹配，不按路径），
            例如 [".git", "__pycache__"]。
        compression: 压缩方式，默认 ZIP_DEFLATED。

    Returns:
        生成的 zip 文件路径。

    Raises:
        FileNotFoundError: folder_path 不存在。
        NotADirectoryError: folder_path 不是文件夹。
    """
    folder_path = os.path.abspath(folder_path)
    if not os.path.exists(folder_path):
        raise FileNotFoundError(f"文件夹不存在: {folder_path}")
    if not os.path.isdir(folder_path):
        raise NotADirectoryError(f"不是文件夹: {folder_path}")

    if output_path is None:
        parent = os.path.dirname(folder_path)
        output_path = os.path.join(parent, f"{os.path.basename(folder_path)}.zip")
    output_path = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    ignore_set = set(ignore or [])

    with zipfile.ZipFile(output_path, "w", compression) as zf:
        for root, dirs, files in os.walk(folder_path):
            # 原地过滤，让 os.walk 不再深入被忽略的目录
            dirs[:] = [d for d in dirs if d not in ignore_set and not is_link(os.path.join(root, d))]
            for name in files:
                if name in ignore_set:
                    continue
                file_path = os.path.join(root, name)
                # 跳过 zip 自身，避免输出文件在源文件夹内时被重复打包
                if os.path.abspath(file_path) == output_path or is_link(file_path):
                    continue
                arcname = os.path.relpath(file_path, folder_path)
                zf.write(file_path, arcname)

    return output_path
