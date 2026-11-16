# 需求，做一个方法，返回清晰的目录结构

import os

# 递归获取每个文件的修改时间,取最晚的时间返回
def get_latest_modification_time(path):
    """递归获取目录下所有文件的最新修改时间。

    目录本身不参与比较，只比较其包含的文件；空目录会忽略。

    Args:
        path: 目录或文件路径。是目录时递归取其中所有文件的最晚 mtime，
            是文件时直接返回该文件的 mtime。

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

    for entry in os.listdir(path):
        entry_path = os.path.join(path, entry)
        if os.path.isdir(entry_path):
            # 目录 -> 递归取子树的最晚时间
            sub_time = get_latest_modification_time(entry_path)
        else:
            sub_time = os.path.getmtime(entry_path)
        if sub_time is not None and (latest_time is None or sub_time > latest_time):
            latest_time = sub_time

    return latest_time


def get_all_files(path, all_paths=None):
    """递归获取目录下所有文件的路径集合。

    Args:
        path: 要遍历的目录路径
        all_paths: 递归过程中累积结果的集合，外部调用时无需传入

    Returns:
        set: 目录下所有文件的路径集合（不包含目录本身）

    Raises:
        FileNotFoundError: 目录不存在时抛出（由 os.listdir 触发）
    """
    if all_paths is None:
        # 首次调用时初始化结果集合，避免可变默认参数被多次调用共享
        all_paths = set()
    items = os.listdir(path)  # 获取当前目录下所有条目（文件和目录）的名称
    for item in items:
        item_path = os.path.join(path, item)  # 拼接为完整路径
        if os.path.isdir(item_path):
            # 是目录则递归处理，结果累积到同一个集合中
            get_all_files(item_path, all_paths)
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
    entries = sorted(os.listdir(path), key=lambda e: (os.path.isfile(os.path.join(path, e)), e.lower()))
    lines = []
    for index, entry in enumerate(entries):
        if entry in exclude:
            continue
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
