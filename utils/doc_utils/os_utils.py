# 需求，做一个方法，返回清晰的目录结构

import os


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

def list_directory(path, prefix=''):
    """以树形结构返回目录内容。
    Args:
        path: 当前要遍历的目录路径
        prefix: 当前层级的前缀字符串，用于生成缩进和连接线

    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"路径不存在: {path}")

    entries = sorted(os.listdir(path), key=lambda e: (os.path.isfile(os.path.join(path, e)), e.lower()))
    lines = []
    for index, entry in enumerate(entries):
        is_last = index == len(entries) - 1
        connector = '└── ' if is_last else '├── '
        lines.append(prefix + connector + entry)
        child = os.path.join(path, entry)
        if os.path.isdir(child):
            extension = '    ' if is_last else '│   '
            lines.append(list_directory(child, prefix + extension))
    return '\n'.join(lines)


if __name__ == '__main__':
    print(list_directory('./'))
    print(get_all_files('./'))
