import os
import zipfile
from pathlib import Path

from utils.doc_utils.os_utils import iter_files


def zip_folder(
    folder_path: str,
    output_path: str | None = None,
    ignore: list[str] | None = None,
    compression: int = zipfile.ZIP_DEFLATED,
    *,
    exclude=(),
) -> str:
    """将文件夹压缩成 zip。

    Args:
        folder_path: 要压缩的文件夹路径。
        output_path: 输出 zip 文件路径，默认为 folder_path 同级目录下的
            `<文件夹名>.zip`。
        ignore: 需要跳过的文件/目录名列表（只按名字匹配，不按路径），
            例如 [".git", "__pycache__"]。
        compression: 压缩方式，默认 ZIP_DEFLATED。
        exclude: 需要跳过的文件或目录，按相对于 folder_path 的路径匹配。

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

    folder = Path(folder_path)
    archive = Path(output_path)

    with zipfile.ZipFile(output_path, "w", compression) as zf:
        for file in iter_files(folder, ignore=ignore or (), exclude=exclude):
            if file == archive:
                continue
            try:
                zf.write(file, file.relative_to(folder))
            except FileNotFoundError:
                continue

    return output_path
