import platform


def get_platform():
    """
    获取当前操作系统平台
    :return: 0 表示 Windows，1 表示 Linux，2 表示 macOS，3 表示其他未知系统
    """
    system = platform.system().lower()
    if system == "windows":
        return 0
    elif system == "linux":
        return 1
    elif system == "darwin":
        return 2
    return 3
