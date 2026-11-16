# 从langchain.tools导入tool装饰器，用于将函数注册为工具
from langchain.tools import tool

# 导入pypandoc库，这是Python的Pandoc封装，用于调用Pandoc功能
import pypandoc
import os

from core import config
# 从content.utils导入runtime_util模块，并重命名为rt

# 从utils.general_utils.globle_util导入get_platform函数
from utils.general_utils.globle_utils import get_platform


# 使用@tool装饰器，将此函数注册为LangChain工具
@tool
def convert_file(write_file_path, output_format):
    """
    转换文件格式,支持txt,md,pdf,html,docx等
    输入md文件中可以用![](图片路径)语法嵌入图片(支持png/jpg等),图片会被打包进输出文件
    输入文件必须已经存在:新内容先用write_file写成md文件,再调用本工具转换
    :param:
        file_path: 输入文件路径
        output_format: 输出格式 (如: 'html', 'pdf', 'docx', 'md')
    :return 输出文件路径,失败时返回错误说明
    """

    # 规范化路径,防止模型传入带引号/多余空格/正斜杠混杂的路径
    write_file_path = os.path.normpath(str(write_file_path))

    # 输入文件必须存在,提前校验避免 pypandoc 抛异常导致整个 run 失败
    if not os.path.isfile(write_file_path):
        return (f"输入文件不存在: {write_file_path}。"
                "请先确认文件已创建(可用 ls 工具查看目录结构),"
                "新文件请先用 write_file 写入后再转换,请勿用不存在的路径重复调用本工具。")

    # 生成输出文件路径：用splitext只替换扩展名，避免路径中同名子串被误替换
    base, _ = os.path.splitext(write_file_path)
    output_file = base + '.' + output_format

    # 获取输入文件的格式（扩展名）
    input_format = write_file_path.split('.')[-1]

    # 图片等资源的查找路径:必须是绝对路径。
    # config.ROOT_PATH_AGENT 是盘符相对路径(如 \agent_files),pandoc 会按自身 cwd 解析导致找不到,
    # 所以这里转成绝对路径;同时加入输入文件所在目录,支持 md 内相对当前文件的图片引用
    resource_paths = [
        os.path.abspath(config.ROOT_PATH_AGENT),
        os.path.dirname(write_file_path),
    ]

    # 注释掉的日志记录代码
    # logger.info(f"开始转换文件:{filepath}")
    # logger.info(f"输出文件:{output_file}")

    # 执行转换,捕获所有异常并转成文本返回给模型,
    # 避免异常冒泡导致整个 langgraph run 失败
    try:
        # 特殊处理PDF输出格式的情况
        if output_format.lower() == 'pdf':

            # 如果输入格式是txt，将其视为md（Markdown）格式处理
            # 因为Pandoc对txt支持有限，而txt和md语法相似
            if input_format == 'txt':
                input_format = 'md'  # 把txt当作md

            # 根据操作系统平台选择不同的字体配置
            # get_platform()返回0表示Windows，非0表示其他系统（如Linux/Mac）
            if get_platform() == 0:
                # Windows平台配置
                # pdf_engine = '--pdf-engine=wkhtmltopdf'  # 注释掉的备选引擎
                mainfont = 'mainfont=SimSun'  # 设置主字体为宋体（Windows）
                cjkmainfont = 'CJKmainfont=SimSun'  # 设置CJK（中日韩）字体为宋体
            else:
                # 非Windows平台配置（Linux/Mac）
                # pdf_engine = '--pdf-engine=weasyprint'  # 注释掉的备选引擎
                mainfont = 'mainfont=Noto Sans CJK SC'  # 设置主字体为思源黑体（Linux/Mac）
                cjkmainfont = 'CJKmainfont=Noto Sans CJK SC'  # 设置CJK字体为思源黑体

            # 使用pypandoc转换文件为PDF格式
            pypandoc.convert_file(
                write_file_path,  # 输入文件路径
                output_format,  # 输出格式（'pdf'）
                outputfile=output_file,  # 输出文件路径
                format=input_format,  # 输入格式
                extra_args=[  # 额外参数列表，传递给Pandoc
                    '--pdf-engine=tectonic',  # tectonic
                    '-V', mainfont,  # 主字体
                    '--variable', cjkmainfont,  # CJK字体
                    '--resource-path', os.pathsep.join(resource_paths),  # 资源查找路径(图片等),支持多个路径
                ],
            )
        else:
            # 非PDF格式的转换（如html、docx、md等）
            pypandoc.convert_file(
                write_file_path,  # 输入文件路径
                output_format,  # 输出格式
                outputfile=output_file,  # 输出文件路径
                format=input_format,  # 输入格式
                extra_args=[
                    # 非pdf格式同样需要资源查找路径,否则md中引用的图片不会被嵌入
                    '--resource-path', os.pathsep.join(resource_paths),
                ],
            )
    except Exception as e:
        return (f"文件转换失败: {e}\n"
                f"输入文件: {write_file_path}, 目标格式: {output_format}。\n"
                "常见原因:输入文件内容或格式不受支持、缺少对应转换引擎(tectonic)、"
                "或嵌入的图片路径无效。请根据原因调整后重试,勿盲目重复调用。")

    # 返回生成的输出文件路径
    return output_file