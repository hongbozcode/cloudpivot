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
    :param:
        file_path: 输入文件路径
        output_format: 输出格式 (如: 'html', 'pdf', 'docx', 'md')
    :return 输出文件路径
    """

    # 生成输出文件路径：用splitext只替换扩展名，避免路径中同名子串被误替换
    base, _ = os.path.splitext(write_file_path)
    output_file = base + '.' + output_format

    # 获取输入文件的格式（扩展名）
    input_format = write_file_path.split('.')[-1]

    # 注释掉的日志记录代码
    # logger.info(f"开始转换文件:{filepath}")
    # logger.info(f"输出文件:{output_file}")

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
                '--pdf-engine=xelatex',  # 指定使用xelatex引擎生成PDF（支持Unicode和字体嵌入）
                '-V', mainfont,  # 设置LaTeX变量：主字体
                '--variable', cjkmainfont,  # 设置LaTeX变量：CJK字体
                '--resource-path', config.ROOT_PATH_AGENT,  # 设置资源查找路径（如图片、样式表等）
            ],
        )
    else:
        # 非PDF格式的转换（如html、docx、md等）
        pypandoc.convert_file(
            write_file_path,  # 输入文件路径
            output_format,  # 输出格式
            outputfile=output_file,  # 输出文件路径
            format=input_format,  # 输入格式
            # 不传递额外参数，使用Pandoc默认配置
        )

    # 返回生成的输出文件路径
    return output_file