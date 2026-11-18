# 需求，做一个方法，返回清晰的目录结构

import os
import stat
from pathlib import Path, PureWindowsPath


def resolve_within(path, root) -> Path:
    """解析物理路径，并要求目标位于指定目录内。"""
    path = Path(path)
    if os.name == 'nt' and any(
        ':' in part or PureWindowsPath(part).is_reserved()
        for part in path.parts if part != path.anchor
    ):
        raise ValueError('文件路径包含无效的名称。')

    root = Path(root).resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError('文件路径必须位于工作目录内。')
    return resolved


def resolve_workspace_path(path, root) -> Path:
    """将虚拟路径或工作区内的物理路径解析为受约束的物理路径。"""
    configured_root = Path(root)
    root = configured_root.resolve()
    path = Path(path)
    if path.is_relative_to(root) or path.is_relative_to(configured_root):
        candidate = path
    elif path.drive:
        raise ValueError('文件路径必须位于工作目录内。')
    else:
        relative = path.relative_to(path.anchor) if path.anchor else path
        candidate = root / relative
    return resolve_within(candidate, root)


def workspace_path(path, root) -> str:
    """将物理路径转为供智能体使用的 POSIX 虚拟路径。"""
    root = Path(root).resolve()
    return '/' + resolve_within(path, root).relative_to(root).as_posix()


def is_link(path, *, missing=True):
    """识别符号链接和 Windows 重解析点，避免递归进入其目标。"""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        # A file can disappear between enumeration and inspection.
        return missing
    return stat.S_ISLNK(info.st_mode) or (
        os.name == 'nt' and bool(info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    )

def iter_files(root, *, ignore=(), exclude=()):
    """遍历常规文件。ignore 匹配名称，exclude 匹配相对于根目录的路径。"""
    root = Path(root)
    ignored_names = set(ignore)
    excluded_paths = {Path(path) for path in exclude}

    def included(path):
        return (
            path.name not in ignored_names
            and path.relative_to(root) not in excluded_paths
            and not is_link(path)
        )

    for directory, dirs, files in os.walk(root):
        directory = Path(directory)
        dirs[:] = [name for name in dirs if included(directory / name)]
        for name in files:
            path = directory / name
            if included(path):
                yield path


def get_latest_modification_time(path, *, exclude=()):
    """递归获取目录下所有文件的最新修改时间。

    目录本身不参与比较，只比较其包含的文件；空目录会忽略。

    Args:
        path: 目录或文件路径。是目录时递归取其中所有文件的最晚 mtime，
            是文件时直接返回该文件的 mtime。
        exclude: 跳过的文件或目录，按相对于 path 的路径匹配。

    Returns:
        float: 最晚的修改时间戳（epoch 秒）。目录下没有任何文件时返回 None。

    Raises:
        FileNotFoundError: 路径不存在时抛出。
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"路径不存在: {path}")

    latest_time = None

    if os.path.isfile(path):
        return os.path.getmtime(path)

    for file in iter_files(path, exclude=exclude):
        try:
            modified_at = file.stat().st_mtime
        except FileNotFoundError:
            continue
        if latest_time is None or modified_at > latest_time:
            latest_time = modified_at

    return latest_time


def get_all_files(path, all_paths=None, exclude=None):
    """递归获取目录下所有文件的路径集合。

    Args:
        path: 要遍历的目录路径
        all_paths: 递归过程中累积结果的集合，外部调用时无需传入
        exclude: 跳过的目录名集合

    Returns:
        set: 目录下所有文件的路径集合（不包含目录本身）

    Raises:
        FileNotFoundError: 目录不存在时抛出（由 os.listdir 触发）
    """
    if all_paths is None:
        # 首次调用时初始化结果集合，避免可变默认参数被多次调用共享
        all_paths = set()
    exclude = exclude or set()
    items = os.listdir(path)  # 获取当前目录下所有条目（文件和目录）的名称
    for item in items:
        item_path = os.path.join(path, item)  # 拼接为完整路径
        if is_link(item_path):
            continue
        if os.path.isdir(item_path):
            # 是目录则递归处理，结果累积到同一个集合中
            if item not in exclude:
                get_all_files(item_path, all_paths, exclude=exclude)
        else:
            # 是文件则直接加入结果集合
            all_paths.add(item_path)
    return all_paths  # 返回当前目录及所有子目录下的文件路径集合

def list_directory(path, exclude=None):
    """以树形结构返回目录内容。
    Args:
        path: 要遍历的目录路径
        exclude: 需要跳过的子目录名集合（set[str]），这些目录不会出现在树中
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"路径不存在: {path}")

    exclude = exclude or set()
    entries = sorted(
        (entry for entry in os.listdir(path) if entry not in exclude and not is_link(os.path.join(path, entry))),
        key=lambda entry: (os.path.isfile(os.path.join(path, entry)), entry.lower()),
    )
    lines = []
    for index, entry in enumerate(entries):
        is_last = index == len(entries) - 1
        connector = '└── ' if is_last else '├── '
        lines.append(connector + entry)
        child = os.path.join(path, entry)
        if os.path.isdir(child):
            # 递归结果不缩进，这里对子树的每一行统一加缩进
            extension = '    ' if is_last else '│   '
            sub_tree = list_directory(child, exclude)
            lines.extend(extension + line for line in sub_tree.split('\n'))
    return '\n'.join(lines)


if __name__ == '__main__':
    print(list_directory('./'))
    print(get_all_files('./'))
    print(get_latest_modification_time('./'))
