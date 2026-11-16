from markitdown import MarkItDown

md = MarkItDown()


def get_file_content(file_path):

    result = md.convert(file_path)
    return result.text_content